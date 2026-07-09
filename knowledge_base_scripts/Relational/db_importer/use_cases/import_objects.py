# db_importer/use_cases/import_objects.py

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple
import logging

from ..domain.entities import (
    Object,
    ObjectType,
    ObjectNameSynonym,
    DbId,
)
from .interfaces import (
    ObjectRepository,
    ObjectTypeRepository,
    SynonymRepository,
    ObjectPropertyRepository,
    ObjectObjectRelationTypeRepository,
    CaseNormalizer,
)


@dataclass
class ImportObjectsUseCase:
    object_repo: ObjectRepository
    object_type_repo: ObjectTypeRepository
    synonym_repo: SynonymRepository
    property_repo: ObjectPropertyRepository
    object_object_relation_type_repo: ObjectObjectRelationTypeRepository
    case_normalizer: CaseNormalizer

    _logger = logging.getLogger(__name__)

    def execute(self, objects_data: List[Dict[str, Any]]) -> Dict[str, int]:
        result = {'created': 0, 'updated': 0, 'errors': 0}
        object_relations_to_process = []

        for obj_data in objects_data:
            try:
                identificator = obj_data.get('identificator', {})
                db_id_value = identificator.get('db_id')

                if not db_id_value:
                    self._logger.warning(f"Object without db_id, skipping: {obj_data}")
                    result['errors'] += 1
                    continue

                db_id = DbId(db_id_value)

                object_type_name = obj_data.get('type')
                if not object_type_name:
                    self._logger.warning(f"Object without type, skipping: {obj_data}")
                    result['errors'] += 1
                    continue

                object_type = self.object_type_repo.get_or_create(object_type_name)
                existing = self.object_repo.find_by_db_id(str(db_id))

                name_synonyms = obj_data.get('name_synonyms', {})
                primary_name = self._get_primary_name(name_synonyms)

                # Нормализуем регистр значений один раз здесь: дальше нормализованный
                # словарь уходит и в object.object_properties, и в каталог
                # object_property — так они не расходятся между собой (см.
                # tasks/normalizaciya-registra-v-katalogah.md).
                raw_properties = obj_data.get('properties', {})
                normalized_properties, extracted_props = self._process_properties(
                    object_type.id, raw_properties
                )

                if existing:
                    existing.object_properties = normalized_properties
                    existing.object_type_id = object_type.id
                    updated_obj = self.object_repo.save(existing)
                    object_id = updated_obj.id
                    result['updated'] += 1
                    self._logger.debug(f"Updated object {db_id}")
                else:
                    object_obj = Object(
                        db_id=db_id,
                        object_type_id=object_type.id,
                        object_properties=normalized_properties
                    )
                    saved_obj = self.object_repo.save(object_obj)
                    object_id = saved_obj.id
                    result['created'] += 1
                    self._logger.debug(f"Created object {db_id}")

                if primary_name:
                    self._process_synonyms(object_id, name_synonyms, primary_name)

                for prop_name, values in extracted_props:
                    self.property_repo.add_or_update_property(object_type.id, prop_name, values)

                # Process object relations and add relation types to dictionary
                for relation in obj_data.get('object_relations', []):
                    related_db_id = relation.get('db_id')
                    relation_type = relation.get('type')
                    if related_db_id and relation_type:
                        # Add relation type to dictionary if not exists
                        try:
                            self.object_object_relation_type_repo.get_or_create(relation_type)
                        except Exception as e:
                            self._logger.warning(f"Failed to add relation type '{relation_type}': {e}")
                        object_relations_to_process.append((object_id, related_db_id, relation_type))

            except Exception as e:
                self._logger.error(f"Error importing object: {e}", exc_info=True)
                result['errors'] += 1

        # Process object-object links
        for object_id, related_db_id, relation_type in object_relations_to_process:
            try:
                related_obj = self.object_repo.find_by_db_id(related_db_id)
                if related_obj:
                    self.object_repo.link_object_to_object(object_id, related_obj.id, relation_type)
                    self._logger.debug(f"Linked object {object_id} -> {related_obj.id} ({relation_type})")
                else:
                    self._logger.warning(f"Related object not found for db_id: {related_db_id}")
            except Exception as e:
                self._logger.error(f"Error linking objects: {e}", exc_info=True)

        self._logger.info(f"Objects import: created={result['created']}, updated={result['updated']}, errors={result['errors']}")
        return result

    def _get_primary_name(self, name_synonyms: Dict[str, List[str]]) -> Optional[str]:
        if name_synonyms.get('ru_names'):
            return name_synonyms['ru_names'][0]
        if name_synonyms.get('scientific_names'):
            return name_synonyms['scientific_names'][0]
        return None

    def _process_synonyms(self, object_id: int, name_synonyms: Dict[str, List[str]], primary_name: str) -> None:
        normalized_primary = primary_name.lower().strip() if primary_name else None

        if normalized_primary:
            synonym = self.synonym_repo.get_or_create(normalized_primary, 'ru')
            self.object_repo.add_synonym_link(object_id, synonym.id)

        for name in name_synonyms.get('ru_names', []):
            if name and name.strip():
                normalized_name = name.strip().lower()
                synonym = self.synonym_repo.get_or_create(normalized_name, 'ru')
                self.object_repo.add_synonym_link(object_id, synonym.id)

        for name in name_synonyms.get('scientific_names', []):
            if name and name.strip():
                normalized_name = name.strip().lower()
                if normalized_name != normalized_primary:
                    synonym = self.synonym_repo.get_or_create(normalized_name, 'sn')
                    self.object_repo.add_synonym_link(object_id, synonym.id)

        for name in name_synonyms.get('en_names', []):
            if name and name.strip():
                normalized_name = name.strip().lower()
                if normalized_name != normalized_primary:
                    synonym = self.synonym_repo.get_or_create(normalized_name, 'en')
                    self.object_repo.add_synonym_link(object_id, synonym.id)

    def _process_properties(
        self, object_type_id: int, properties: Dict[str, Any], max_depth: int = 2
    ) -> Tuple[Dict[str, Any], List[Tuple[str, List[str]]]]:
        """Строит нормализованную копию `properties` (для object.object_properties) и
        параллельно собирает список (prop_name, values) для каталога object_property.

        Нормализация регистра применяется только к строковым листьям; типы данных
        (числа, bool) не трогаются. Оба результата используют один и тот же
        case_normalizer, поэтому канонический регистр в object_properties всегда
        совпадает с тем, что лежит в каталоге.
        """
        extracted: List[Tuple[str, List[str]]] = []

        def process(prefix: str, value: Any, depth: int) -> Any:
            if depth > max_depth:
                return value

            if isinstance(value, dict):
                return {
                    k: process(f"{prefix}.{k}" if prefix else k, v, depth + 1)
                    for k, v in value.items()
                }

            if isinstance(value, list):
                normalized_list = []
                catalog_values = []
                for item in value:
                    if item is None:
                        continue
                    normalized_item = (
                        self.case_normalizer.normalize_object_property_value(object_type_id, prefix, item)
                        if isinstance(item, str) else item
                    )
                    normalized_list.append(normalized_item)
                    catalog_values.append(str(normalized_item))
                if catalog_values:
                    extracted.append((prefix, catalog_values))
                return normalized_list

            if value is None:
                return value

            normalized_value = (
                self.case_normalizer.normalize_object_property_value(object_type_id, prefix, value)
                if isinstance(value, str) else value
            )
            extracted.append((prefix, [str(normalized_value)]))
            return normalized_value

        normalized_properties = {
            key: process(key, val, 1) for key, val in properties.items()
        }

        return normalized_properties, extracted