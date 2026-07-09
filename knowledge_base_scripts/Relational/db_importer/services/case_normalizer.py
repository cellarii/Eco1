"""Нормализация регистра для каталожных значений (object_property/resource_feature)
и для тех же значений, которые попадают в object.object_properties / resource.features.

Проблема: одно и то же логическое значение приходит из источников импорта в разном
регистре ("Гидрологический" / "гидрологический" / "ГИДРОЛОГИЧЕСКИЙ"), и из-за точного
сравнения строк в Postgres это плодит дубли в справочниках и расходится с реальными
данными объектов/ресурсов (см. tasks/normalizaciya-registra-v-katalogah.md).

Стратегия — две независимые техники, комбинируемые:

1. Регистронезависимое слияние (безопасно для ЛЮБОГО текста, включая топонимы и
   составные собственные имена): если новое значение совпадает с уже известным без
   учёта регистра — возвращаем уже устоявшуюся (канонический) форму вместо того, чтобы
   добавлять очередной вариант написания. Новые, ранее не встречавшиеся значения
   сохраняются как есть — мы не пытаемся угадать "правильный" регистр для произвольного
   текста (это ломает капитализацию внутри собственных имён типа
   "Усть-Ордынский Бурятский округ").

2. Капитализация категории (первая буква заглавная, остальные строчные) — применяется
   ТОЛЬКО к полям из белого списка ниже, для которых вручную проверено, что значения —
   простые категориальные слова/словосочетания без вложенных собственных имён
   (например "подтип объекта": бухта, гора, гидрологический объект, ...).
   Для остальных полей (топонимы, латинские названия, свободный текст) капитализация
   не применяется, чтобы не повредить корректные данные.
"""

import re
from typing import Dict, Tuple

from ..use_cases.interfaces import CaseNormalizer
from ..adapters.database_client import DatabaseClient

# Поля каталога object_property, для которых проверено, что капитализация первой
# буквы безопасна (значения — простые категориальные слова, без собственных имён).
CATEGORY_PROPERTY_NAMES = frozenset({
    'подтип объекта',
})

# Поля каталога resource_feature, для которых проверено на реальных данных
# (2026-06-16), что значения - простые категориальные слова/словосочетания без
# вложенных топонимов или составных собственных имён.
CATEGORY_FEATURE_NAMES = frozenset({
    'среда обитания',
    'тип животного',
    'тип растения',
    'облачность',
    'время года',
    'цветение',
    'наличие плодов',
})

_WHITESPACE_RE = re.compile(r'\s+')


def _collapse_whitespace(value: str) -> str:
    return _WHITESPACE_RE.sub(' ', value.strip())


def capitalize_category(value: str) -> str:
    """Первая буква заглавная, остальные строчные. Только для белого списка полей."""
    value = _collapse_whitespace(value)
    if not value:
        return value
    return value[0].upper() + value[1:].lower()


class CatalogCaseNormalizer(CaseNormalizer):
    """Регистронезависимое слияние значений каталогов object_property/resource_feature.

    При создании загружает текущее состояние каталогов из БД одним запросом и дальше
    работает с ним в памяти на протяжении всего прогона импорта, обновляя по мере
    появления новых значений.
    """

    def __init__(self, client: DatabaseClient):
        self._client = client
        # (scope_id, lower(name)) -> {lower(value): canonical_value}
        #
        # Имя поля (property_name/feature_name) индексируем по lower(), а не как
        # есть: object_property/resource_feature всегда хранят property_name в
        # нижнем регистре (см. object_property_repository.py), а исходный ключ в
        # object.object_properties/resource.features при этом приходит с тем
        # регистром, что в источнике импорта (например, "Подтип объекта"). Без
        # lower() здесь история каталога и текущий прогон импорта просто не нашли
        # бы друг друга в этом словаре - тогда накопленные варианты написания не
        # подхватывались бы при повторном импорте (см.
        # tasks/normalizaciya-registra-v-katalogah.md).
        self._properties: Dict[Tuple[int, str], Dict[str, str]] = {}
        self._features: Dict[Tuple[int, str], Dict[str, str]] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        try:
            rows = self._client.fetchall(
                "SELECT object_type_id, property_name, property_values "
                "FROM eco_assistant.object_property"
            )
        except Exception:
            rows = []
        for object_type_id, property_name, values in rows:
            bucket = self._properties.setdefault((object_type_id, property_name.lower()), {})
            for v in (values or []):
                bucket.setdefault(v.strip().lower(), v)

        try:
            rows = self._client.fetchall(
                "SELECT modality_id, feature_name, feature_values "
                "FROM eco_assistant.resource_feature"
            )
        except Exception:
            rows = []
        for modality_id, feature_name, values in rows:
            bucket = self._features.setdefault((modality_id, feature_name.lower()), {})
            for v in (values or []):
                bucket.setdefault(v.strip().lower(), v)

    def _normalize(self, bucket: Dict[str, str], name_lower: str, category_names: frozenset,
                    value: str) -> str:
        value = _collapse_whitespace(value)
        if not value:
            return value

        if name_lower in category_names:
            value = capitalize_category(value)

        key = value.lower()
        existing = bucket.get(key)
        if existing is not None:
            return existing

        bucket[key] = value
        return value

    def normalize_object_property_value(self, object_type_id: int, property_name: str,
                                         value: str) -> str:
        name_lower = property_name.strip().lower()
        bucket = self._properties.setdefault((object_type_id, name_lower), {})
        return self._normalize(bucket, name_lower, CATEGORY_PROPERTY_NAMES, value)

    def normalize_resource_feature_value(self, modality_id: int, feature_name: str,
                                          value: str) -> str:
        name_lower = feature_name.strip().lower()
        bucket = self._features.setdefault((modality_id, name_lower), {})
        return self._normalize(bucket, name_lower, CATEGORY_FEATURE_NAMES, value)
