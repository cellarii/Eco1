"""Одноразовая чистка регистровых дублей, накопленных ДО фикса в db_importer.

Контекст и обоснование алгоритма — см. tasks/normalizaciya-registra-v-katalogah.md.
Короткая версия: импорт писал значения свойств/признаков как есть, без учёта
регистра, поэтому "Гидрологический"/"ГИДРОЛОГИЧЕСКИЙ"/"гидрологический" в каталогах
object_property/resource_feature и в самих object.object_properties/resource.features
накопились как РАЗНЫЕ строки. Фикс в самом импортёре (CatalogCaseNormalizer)
предотвращает появление новых дублей, но не трогает уже накопленные — для них нужен
этот скрипт.

Что делает:
  1. Находит группы значений в object_property.property_values и
     resource_feature.feature_values, совпадающие без учёта регистра.
  2. Для каждой группы выбирает канонический вариант:
     - если property_name/feature_name есть в белом списке CATEGORY_* (проверено,
       что это простые категориальные слова без вложенных собственных имён) —
       канон = capitalize_category(...);
     - иначе — вариант, который реально используется большим числом объектов
       (по object.object_properties), при равенстве — вариант с минимумом
       "неожиданных" заглавных букв (не ВСЕ_КАПС), при дальнейшем равенстве —
       лексикографически первый. Это НЕ применяет капитализацию к свободному
       тексту (топонимам и т.п.) — просто выбирает уже существующий вариант,
       никогда не придумывает новый регистр для непредвиденных данных.
  3. По умолчанию работает в режиме --dry-run: только печатает план, ничего не
     пишет в БД.
  4. С флагом --apply выполняет обновление в ОДНОЙ транзакции:
     - схлопывает property_values/feature_values в каталогах;
     - заменяет дублирующие варианты на канон внутри object.object_properties
       (резолвится по тому же property_name, что и в каталоге) и
       resource.features (по feature_name).

Запуск (из knowledge_base_scripts/Relational):
    python -m db_importer.scripts.normalize_case_duplicates --dry-run
    python -m db_importer.scripts.normalize_case_duplicates --apply

ВНИМАНИЕ: перед --apply на проде (см. DB_HOST в shared.env/окружении) сначала
снимите бэкап (`pg_dump --schema=eco_assistant`) и прогоните --dry-run, чтобы
самостоятельно проверить план изменений.
"""

import argparse
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

from ..config import DatabaseConfig
from ..adapters.database_client import PostgresClient
from ..services.case_normalizer import (
    CATEGORY_PROPERTY_NAMES,
    CATEGORY_FEATURE_NAMES,
    capitalize_category,
)


def _ugliness(value: str) -> int:
    """Чем больше "неожиданных" заглавных букв (не считая первую) - тем хуже вариант."""
    return sum(1 for i, ch in enumerate(value) if i > 0 and ch.isupper())


def _choose_canonical(name: str, variants: List[str], category_names: frozenset,
                       usage_counts: Dict[str, int]) -> str:
    if name in category_names:
        return capitalize_category(variants[0])

    def sort_key(v: str) -> Tuple[int, int, str]:
        return (-usage_counts.get(v, 0), _ugliness(v), v)

    return sorted(variants, key=sort_key)[0]


def _find_duplicate_groups(rows: List[Tuple[int, str, List[str]]]) -> Dict[Tuple, List[str]]:
    """rows: (scope_id, name, values[]). Возвращает {(scope_id, name): [варианты группы]}
    только для групп, где реально есть >1 варианта написания одного значения."""
    groups: Dict[Tuple, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
    for scope_id, name, values in rows:
        for v in (values or []):
            groups[(scope_id, name)][v.strip().lower()].append(v)

    result: Dict[Tuple, List[str]] = {}
    for key, by_lower in groups.items():
        dup_groups = [vs for vs in by_lower.values() if len(set(vs)) > 1]
        if dup_groups:
            result[key] = dup_groups
    return result


def _resolve_jsonb_key(known_keys: List[str], name: str) -> str:
    """object_property.property_name/resource_feature.feature_name всегда хранятся в
    нижнем регистре (см. *_repository.py), а реальный ключ в
    object.object_properties/resource.features сохраняет регистр исходных данных
    импорта (например, "Подтип объекта"). Поэтому ключ для JSONB-обновлений нужно
    резолвить отдельно через реальные ключи объектов/ресурсов, а не брать
    property_name из каталога буквально - иначе UPDATE по ключу из каталога не
    найдёт ни одной строки (ключи не совпадут регистром) и тихо ничего не сделает.

    Возвращает name как есть, если подходящий реальный ключ не нашёлся (тогда
    apply_plan просто не тронет object.object_properties/resource.features для
    этой группы - в plan это будет видно по предупреждению).
    """
    target = name.strip().lower()
    for k in known_keys:
        if k.strip().lower() == target:
            return k
    return name


def _object_type_jsonb_keys(client: PostgresClient, object_type_id: int) -> List[str]:
    rows = client.fetchall(
        "SELECT DISTINCT jsonb_object_keys(object_properties) FROM eco_assistant.object "
        "WHERE object_type_id = %s",
        (object_type_id,)
    )
    return [row[0] for row in rows]


def _resource_jsonb_keys(client: PostgresClient) -> List[str]:
    rows = client.fetchall(
        "SELECT DISTINCT jsonb_object_keys(features) FROM eco_assistant.resource "
        "WHERE features IS NOT NULL"
    )
    return [row[0] for row in rows]


def _object_property_usage_counts(client: PostgresClient, object_type_id: int,
                                   property_name: str) -> Dict[str, int]:
    """Сколько объектов данного типа фактически используют каждое написание значения
    property_name в object.object_properties (значение может быть и массивом, и
    скаляром - обе формы встречаются в данных)."""
    rows = client.fetchall(
        """
        SELECT value, count(*) FROM eco_assistant.object o,
            LATERAL (
                SELECT jsonb_array_elements_text(o.object_properties->%(pname)s) AS value
                WHERE jsonb_typeof(o.object_properties->%(pname)s) = 'array'
                UNION ALL
                SELECT o.object_properties->>%(pname)s AS value
                WHERE jsonb_typeof(o.object_properties->%(pname)s) IS DISTINCT FROM 'array'
                  AND o.object_properties->>%(pname)s IS NOT NULL
            ) sub
        WHERE o.object_type_id = %(otid)s
        GROUP BY value
        """,
        {'pname': property_name, 'otid': object_type_id}
    )
    return {row[0]: row[1] for row in rows}


def build_plan(client: PostgresClient) -> dict:
    """Строит план изменений: для object_property и resource_feature - какие группы
    дублей найдены и какой вариант выбран канонической формой."""
    plan = {'object_property': [], 'resource_feature': []}

    object_type_keys_cache: Dict[int, List[str]] = {}

    op_rows = client.fetchall(
        "SELECT object_type_id, property_name, property_values FROM eco_assistant.object_property"
    )
    op_groups = _find_duplicate_groups(op_rows)
    for (object_type_id, property_name), dup_groups in op_groups.items():
        if object_type_id not in object_type_keys_cache:
            object_type_keys_cache[object_type_id] = _object_type_jsonb_keys(client, object_type_id)
        actual_key = _resolve_jsonb_key(object_type_keys_cache[object_type_id], property_name)
        usage_counts = _object_property_usage_counts(client, object_type_id, actual_key)
        for variants in dup_groups:
            canonical = _choose_canonical(property_name, variants, CATEGORY_PROPERTY_NAMES, usage_counts)
            plan['object_property'].append({
                'object_type_id': object_type_id,
                'property_name': property_name,
                'actual_key': actual_key,
                'key_resolved': actual_key.lower() == property_name.lower(),
                'variants': sorted(set(variants)),
                'canonical': canonical,
                'usage_counts': {v: usage_counts.get(v, 0) for v in set(variants)},
            })

    resource_keys = _resource_jsonb_keys(client)

    rf_rows = client.fetchall(
        "SELECT modality_id, feature_name, feature_values FROM eco_assistant.resource_feature"
    )
    rf_groups = _find_duplicate_groups(rf_rows)
    for (modality_id, feature_name), dup_groups in rf_groups.items():
        actual_key = _resolve_jsonb_key(resource_keys, feature_name)
        for variants in dup_groups:
            # Для resource_feature usage-счётчик не строим (на момент анализа дублей
            # там не нашлось, см. tasks/...) - выбираем без учёта частоты использования.
            canonical = _choose_canonical(feature_name, variants, CATEGORY_FEATURE_NAMES, {})
            plan['resource_feature'].append({
                'modality_id': modality_id,
                'feature_name': feature_name,
                'actual_key': actual_key,
                'key_resolved': actual_key.lower() == feature_name.lower(),
                'variants': sorted(set(variants)),
                'canonical': canonical,
            })

    return plan


def print_plan(plan: dict) -> None:
    total = len(plan['object_property']) + len(plan['resource_feature'])
    if total == 0:
        print("Регистровых дублей не найдено - чистка не требуется.")
        return

    print(f"Найдено {total} групп регистровых дублей:\n")

    for item in plan['object_property']:
        print(f"[object_property] object_type_id={item['object_type_id']} "
              f"property_name={item['property_name']!r} (ключ в object_properties: "
              f"{item['actual_key']!r}{'' if item['key_resolved'] else ', НЕ НАЙДЕН - JSONB не будет тронут'})")
        for v in item['variants']:
            marker = " <- канон" if v == item['canonical'] else ""
            print(f"    {v!r} (используется у {item['usage_counts'].get(v, 0)} объектов){marker}")
        print()

    for item in plan['resource_feature']:
        print(f"[resource_feature] modality_id={item['modality_id']} "
              f"feature_name={item['feature_name']!r} (ключ в resource.features: "
              f"{item['actual_key']!r}{'' if item['key_resolved'] else ', НЕ НАЙДЕН - JSONB не будет тронут'})")
        for v in item['variants']:
            marker = " <- канон" if v == item['canonical'] else ""
            print(f"    {v!r}{marker}")
        print()


def apply_plan(client: PostgresClient, plan: dict) -> None:
    for item in plan['object_property']:
        object_type_id = item['object_type_id']
        property_name = item['property_name']
        canonical = item['canonical']

        row = client.fetchone(
            "SELECT id, property_values FROM eco_assistant.object_property "
            "WHERE object_type_id = %s AND property_name = %s",
            (object_type_id, property_name)
        )
        if row:
            prop_id, values = row
            seen = []
            for v in values:
                mapped = canonical if v in item['variants'] else v
                if mapped not in seen:
                    seen.append(mapped)
            client.execute(
                "UPDATE eco_assistant.object_property SET property_values = %s, updated_at = now() "
                "WHERE id = %s",
                (seen, prop_id)
            )

        if not item['key_resolved']:
            # Реального ключа в object.object_properties не нашлось - значит, ни
            # один объект этого типа сейчас не использует это свойство; трогать
            # JSONB нечего, чистим только каталог (уже сделано выше).
            continue

        actual_key = item['actual_key']
        for v in item['variants']:
            if v == canonical:
                continue
            client.execute(
                """
                UPDATE eco_assistant.object
                SET object_properties = jsonb_set(
                    object_properties,
                    ARRAY[%(pname)s],
                    CASE jsonb_typeof(object_properties->%(pname)s)
                        WHEN 'array' THEN (
                            SELECT jsonb_agg(
                                CASE WHEN elem = %(old)s THEN to_jsonb(%(new)s::text) ELSE to_jsonb(elem) END
                            )
                            FROM jsonb_array_elements_text(object_properties->%(pname)s) elem
                        )
                        ELSE to_jsonb(%(new)s::text)
                    END
                ),
                updated_at = now()
                WHERE object_type_id = %(otid)s
                  AND (
                    (jsonb_typeof(object_properties->%(pname)s) = 'array'
                     AND object_properties->%(pname)s @> jsonb_build_array(%(old)s::text))
                    OR (object_properties->>%(pname)s = %(old)s)
                  )
                """,
                {'pname': actual_key, 'old': v, 'new': canonical, 'otid': object_type_id}
            )

    for item in plan['resource_feature']:
        modality_id = item['modality_id']
        feature_name = item['feature_name']
        canonical = item['canonical']

        row = client.fetchone(
            "SELECT id, feature_values FROM eco_assistant.resource_feature "
            "WHERE modality_id = %s AND feature_name = %s",
            (modality_id, feature_name)
        )
        if row:
            feat_id, values = row
            seen = []
            for v in values:
                mapped = canonical if v in item['variants'] else v
                if mapped not in seen:
                    seen.append(mapped)
            client.execute(
                "UPDATE eco_assistant.resource_feature SET feature_values = %s, updated_at = now() "
                "WHERE id = %s",
                (seen, feat_id)
            )

        if not item['key_resolved']:
            continue

        actual_key = item['actual_key']
        for v in item['variants']:
            if v == canonical:
                continue
            client.execute(
                """
                UPDATE eco_assistant.resource
                SET features = jsonb_set(
                    features,
                    ARRAY[%(fname)s],
                    CASE jsonb_typeof(features->%(fname)s)
                        WHEN 'array' THEN (
                            SELECT jsonb_agg(
                                CASE WHEN elem = %(old)s THEN to_jsonb(%(new)s::text) ELSE to_jsonb(elem) END
                            )
                            FROM jsonb_array_elements_text(features->%(fname)s) elem
                        )
                        ELSE to_jsonb(%(new)s::text)
                    END
                ),
                updated_at = now()
                WHERE features IS NOT NULL
                  AND (
                    (jsonb_typeof(features->%(fname)s) = 'array'
                     AND features->%(fname)s @> jsonb_build_array(%(old)s::text))
                    OR (features->>%(fname)s = %(old)s)
                  )
                """,
                {'fname': actual_key, 'old': v, 'new': canonical}
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                         help='Реально выполнить изменения в БД (по умолчанию dry-run)')
    parser.add_argument('--dry-run', action='store_true',
                         help='Явно запросить dry-run (поведение по умолчанию и без флага)')
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error('--apply и --dry-run несовместимы')

    config = DatabaseConfig.from_env()
    print(f"Подключение: host={config.host} port={config.port} db={config.dbname}")
    if args.apply and config.host not in ('db', 'localhost', '127.0.0.1'):
        confirm = input(
            f"ВНИМАНИЕ: host={config.host} не похож на локальную БД. "
            f"Вы уверены, что хотите применить изменения? Введите 'yes' для подтверждения: "
        )
        if confirm.strip().lower() != 'yes':
            print("Отменено пользователем.")
            sys.exit(1)

    client = PostgresClient(config)
    client.connect()
    try:
        plan = build_plan(client)
        print_plan(plan)

        if args.apply:
            total = len(plan['object_property']) + len(plan['resource_feature'])
            if total == 0:
                return
            apply_plan(client, plan)
            client.commit()
            print(f"\nПрименено: {total} групп дублей схлопнуто.")
        else:
            print("\nЭто dry-run, изменения НЕ применены. Запустите с --apply, чтобы применить.")
    except Exception:
        client.rollback()
        raise
    finally:
        client.disconnect()


if __name__ == '__main__':
    main()
