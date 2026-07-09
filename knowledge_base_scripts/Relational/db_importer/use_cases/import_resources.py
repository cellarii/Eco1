# db_importer/use_cases/import_resources.py

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple
import logging
import hashlib
import json
from pathlib import Path
from ..adapters.database_client import DatabaseClient

from ..domain.entities import (
    Modality,
    Resource,
    SupportMetadata,
    BibliographicData,
    CreationData,
    ResourceStatic,
    TextValue,
    ImageValue,
    GeodataValue,
)
from .interfaces import (
    ResourceRepository,
    ObjectRepository,
    ResourceStaticRepository,
    SupportMetadataRepository,
    BibliographicRepository,
    CreationRepository,
    ModalityRepository,
    GeodataProvider,
    ResourceFeatureRepository,
    ResourceResourceRelationTypeRepository,
    ObjectObjectRelationTypeRepository,
    ResourceObjectRelationTypeRepository,
    CaseNormalizer,
)


@dataclass
class ImportResourcesUseCase:
    resource_repo: ResourceRepository
    object_repo: ObjectRepository
    resource_static_repo: ResourceStaticRepository
    metadata_repo: SupportMetadataRepository
    bibliographic_repo: BibliographicRepository
    creation_repo: CreationRepository
    modality_repo: ModalityRepository
    geodata_provider: GeodataProvider
    feature_repo: ResourceFeatureRepository
    resource_resource_relation_type_repo: ResourceResourceRelationTypeRepository
    object_object_relation_type_repo: ObjectObjectRelationTypeRepository
    resource_object_relation_type_repo: ResourceObjectRelationTypeRepository
    client: DatabaseClient
    case_normalizer: CaseNormalizer
    missing_geometry_file: Path = Path(__file__).parent.parent.parent / 'missing_geometry.json'

    _logger = logging.getLogger(__name__)
    _current_resource_text_id: Optional[str] = None
    _current_resource_title: Optional[str] = None

    def __post_init__(self):
        self._current_resource_text_id = None
        self._current_resource_title = None

    def execute(self, resources_data: List[Dict[str, Any]], incremental: bool = False) -> Dict[str, int]:
        self._reset_missing_geometry_file()
        result = {'success': 0, 'skipped': 0, 'errors': 0}
        resource_relations_to_process = []
        object_relations_to_process = []

        for i, resource_data in enumerate(resources_data, 1):
            try:
                if incremental:
                    resource_hash = self._calculate_hash(resource_data)
                    if self.resource_repo.resource_exists_by_hash(resource_hash):
                        result['skipped'] += 1
                        continue
                else:
                    resource_hash = None

                resource_id = self._import_single_resource(resource_data, resource_hash)

                if resource_id:
                    result['success'] += 1
                    
                    for relation in resource_data.get('resource_relations', []):
                        related_id = relation.get('id')
                        relation_type = relation.get('type')
                        if related_id and relation_type:
                            try:
                                self.resource_resource_relation_type_repo.get_or_create(relation_type)
                            except Exception as e:
                                self._logger.warning(f"Failed to add resource-resource relation type '{relation_type}': {e}")
                            resource_relations_to_process.append((resource_id, related_id, relation_type))
                    
                    for relation in resource_data.get('object_relations', []):
                        object_db_id = relation.get('db_id')
                        relation_type = relation.get('type')
                        if object_db_id and relation_type:
                            try:
                                self.resource_object_relation_type_repo.get_or_create(relation_type)
                            except Exception as e:
                                self._logger.warning(f"Failed to add resource-object relation type '{relation_type}': {e}")
                            object_relations_to_process.append((resource_id, object_db_id, relation_type))
                else:
                    result['errors'] += 1

            except Exception as e:
                self._logger.error(f"Error importing resource {i}: {e}", exc_info=True)
                result['errors'] += 1

        for resource_id, related_id, relation_type in resource_relations_to_process:
            try:
                related_resource_id = self.resource_repo.find_resource_by_text_id(related_id)
                if related_resource_id:
                    self.resource_repo.link_resource_to_resource(resource_id, related_resource_id, relation_type)
                    self._logger.debug(f"Linked resource {resource_id} -> {related_resource_id} ({relation_type})")
                else:
                    self._logger.warning(f"Related resource not found: {related_id}")
            except Exception as e:
                self._logger.error(f"Error linking resources: {e}", exc_info=True)
        
        for resource_id, object_db_id, relation_type in object_relations_to_process:
            try:
                obj = self.object_repo.find_by_db_id_only(object_db_id)
                if obj:
                    self.resource_repo.link_resource_to_object(resource_id, obj.id, relation_type)
                    self._logger.debug(f"Linked resource {resource_id} -> object {obj.id} ({relation_type})")
                else:
                    self._logger.warning(f"Object not found for db_id: {object_db_id}")
            except Exception as e:
                self._logger.error(f"Error linking resource to object: {e}", exc_info=True)

        self._logger.info(f"Resources import: success={result['success']}, skipped={result['skipped']}, errors={result['errors']}")
        return result

    def _reset_missing_geometry_file(self) -> None:
        try:
            self.missing_geometry_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.missing_geometry_file, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._logger.error(f"Failed to reset missing_geometry.json: {e}")

    def _add_missing_geometry(self, resource_title: str) -> None:
        try:
            existing_data = []
            if self.missing_geometry_file.exists():
                with open(self.missing_geometry_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)

            if resource_title not in existing_data:
                existing_data.append(resource_title)
                with open(self.missing_geometry_file, 'w', encoding='utf-8') as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._logger.error(f"Failed to add missing geometry: {e}")

    def _import_single_resource(self, resource_data: Dict[str, Any], resource_hash: Optional[str] = None) -> Optional[int]:
        title = resource_data.get('title')
        uri = resource_data.get('identificator', {}).get('uri')
        text_id = resource_data.get('identificator', {}).get('id')
        
        self._current_resource_text_id = text_id
        self._current_resource_title = title

        bibliographic_data = resource_data.get('bibliographic', {})
        author = bibliographic_data.get('author')
        source = bibliographic_data.get('source')
        reliability_level = bibliographic_data.get('reliability_level')

        author_id = self.bibliographic_repo.get_or_create_author(author) if author else None
        source_id = self.bibliographic_repo.get_or_create_source(source) if source else None
        reliability_id = self.bibliographic_repo.get_or_create_reliability_level(reliability_level) if reliability_level else None

        bibliographic = BibliographicData(
            author_id=author_id,
            date=None,
            source_id=source_id,
            reliability_level_id=reliability_id
        )
        bibliographic_id = self.bibliographic_repo.get_or_create(bibliographic)

        creation_data = resource_data.get('creation', {})
        creation = CreationData(
            creation_type=creation_data.get('creation_type'),
            creation_tool=creation_data.get('creation_tool'),
            creation_params=None
        )
        creation_id = self.creation_repo.get_or_create(creation)

        resource_static = ResourceStatic(
            static_id=None,
            bibliographic_id=bibliographic_id,
            creation_id=creation_id
        )
        resource_static_id = self.resource_static_repo.get_or_create(resource_static)

        support_metadata_data = resource_data.get('support_metadata', {})
        metadata_params = {
            'external_id': support_metadata_data.get('external_id'),
            'external_url': support_metadata_data.get('external_url'),
            'question': support_metadata_data.get('question')
        }
        if resource_hash:
            metadata_params['resource_hash'] = resource_hash
        metadata = SupportMetadata(parameters=metadata_params)
        metadata_id = self.metadata_repo.get_or_create(metadata)

        # modality_id нужен заранее (до сборки features_json), чтобы нормализация
        # регистра значений признаков шла в том же "пространстве" (modality_id,
        # feature_name), в котором потом ищет дубли каталог resource_feature.
        modality_data = resource_data.get('modality', {})
        modality_type = modality_data.get('type')
        modality_value = modality_data.get('value', {})

        modality_id = None
        if modality_type:
            modality_id = self._get_or_create_modality_by_type(modality_type)

        features = resource_data.get('features', [])
        # Нормализуем регистр один раз здесь: дальше нормализованные значения уходят
        # и в resource.features, и в каталог resource_feature - так они не расходятся
        # между собой (см. tasks/normalizaciya-registra-v-katalogah.md).
        features_json, extracted_features = self._process_features(modality_id, features)

        resource = Resource(
            title=title,
            uri=uri,
            features=features_json,
            text_id=text_id,
            resource_static_id=resource_static_id,
            support_metadata_id=metadata_id
        )
        resource_id = self.resource_repo.save_resource(resource)

        if modality_type:
            self._process_modality(resource_id, modality_type, modality_value)

        if modality_id:
            for feat_name, values in extracted_features:
                self.feature_repo.add_or_update_feature(modality_id, feat_name, values)
        
        self._current_resource_text_id = None
        self._current_resource_title = None
        return resource_id

    def _get_or_create_modality_by_type(self, modality_type: str) -> int:
        if modality_type in ("Текст", "Text"):
            modality = self.modality_repo.get_or_create_modality('Текст', 'text_value')
        elif modality_type in ("Изображение", "Image"):
            modality = self.modality_repo.get_or_create_modality('Изображение', 'image_value')
        elif modality_type in ("Геоданные", "Картографическая информация"):
            modality = self.modality_repo.get_or_create_modality('Геоданные', 'geodata_value')
        else:
            modality = self.modality_repo.get_or_create_modality(modality_type, 'text_value')
        
        if modality.id is None:
            raise RuntimeError(f"Failed to get or create modality for type: {modality_type}")
        
        return modality.id
    
    def _process_modality(self, resource_id: int, modality_type: str, modality_value: Dict[str, Any]) -> None:
        if modality_type in ("Текст", "Text"):
            modality = self.modality_repo.get_or_create_modality('Текст', 'text_value')
            structured_data = modality_value.get('structured_data', {}) if modality_value else {}
            text_value = TextValue(structured_data=structured_data)
            value_id = self.modality_repo.save_text_value(text_value)
            if modality.id is not None:
                self.modality_repo.link_resource_value(resource_id, modality.id, value_id)

        elif modality_type in ("Изображение", "Image"):
            modality = self.modality_repo.get_or_create_modality('Изображение', 'image_value')
            image_value = ImageValue(
                url=modality_value.get('url') if modality_value else None,
                file_path=modality_value.get('file_path') if modality_value else None,
                format=modality_value.get('format') if modality_value else None
            )
            value_id = self.modality_repo.save_image_value(image_value)
            if modality.id is not None:
                self.modality_repo.link_resource_value(resource_id, modality.id, value_id)

        elif modality_type in ("Геоданные", "Картографическая информация"):
            modality = self.modality_repo.get_or_create_modality('Геоданные', 'geodata_value')
            
            geodb_id = modality_value.get('geodb_id') if modality_value else None
            geometry_data = None
            
            if geodb_id:
                geometry_data = self.geodata_provider.get_geometry(geodb_id)
            
            if not geometry_data and self._current_resource_title:
                geometry_data = self.geodata_provider.get_geometry_by_name(self._current_resource_title)
                if not geometry_data:
                    object_id = self._find_object_by_synonym(self._current_resource_title)
                    if object_id:
                        row = self.client.fetchone(
                            "SELECT object_properties->>'primary_name' FROM eco_assistant.object WHERE id = %s",
                            (object_id,)
                        )
                        if row and row[0]:
                            geometry_data = self.geodata_provider.get_geometry_by_name(row[0])
            
            if geometry_data:
                geometry, normalized_type = geometry_data
                geodata_value = GeodataValue(geometry=geometry, geometry_type=normalized_type)
                value_id = self.modality_repo.save_geodata_value(geodata_value)
                if modality.id is not None:
                    self.modality_repo.link_resource_value(resource_id, modality.id, value_id)
                
                search_name = geodb_id or self._current_resource_title
                if search_name:
                    object_id = self._find_object_by_synonym(search_name)
                    if object_id:
                        self.resource_repo.link_resource_to_object(resource_id, object_id, 'geometry')
                        self._logger.debug(f"Linked geometry resource {resource_id} to object {object_id} (by synonym '{search_name}')")
                    else:
                        self._logger.debug(f"No object found for synonym '{search_name}'")
            else:
                missing_id = geodb_id or self._current_resource_title or f"Resource_{resource_id}"
                self._add_missing_geometry(missing_id)
                if modality.id is not None:
                    self.modality_repo.link_resource_value(resource_id, modality.id, None)
                
    def _process_features(
        self, modality_id: Optional[int], features: List[Dict[str, Any]], max_depth: int = 2
    ) -> Tuple[Dict[str, Any], List[Tuple[str, List[str]]]]:
        """Строит features_json (для resource.features) и параллельно список
        (feature_name, values) для каталога resource_feature.

        Нормализация регистра строковых значений выполняется только если известен
        modality_id (без него каталог resource_feature не наполняется вовсе, как и в
        прежней реализации) - оба результата используют один и тот же
        case_normalizer, поэтому канонический регистр в resource.features всегда
        совпадает с тем, что лежит в каталоге.
        """
        extracted: List[Tuple[str, List[str]]] = []

        def process(prefix: str, value: Any, depth: int) -> Any:
            if modality_id is None or depth > max_depth:
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
                        self.case_normalizer.normalize_resource_feature_value(modality_id, prefix, item)
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
                self.case_normalizer.normalize_resource_feature_value(modality_id, prefix, value)
                if isinstance(value, str) else value
            )
            extracted.append((prefix, [str(normalized_value)]))
            return normalized_value

        features_json: Dict[str, Any] = {}
        for feature in features:
            name = feature.get('name')
            value = feature.get('value')
            if name and value is not None:
                features_json[name] = process(name, value, 1)

        return features_json, extracted

    def _calculate_hash(self, resource: Dict[str, Any]) -> str:
        data = {
            'title': resource.get('title'),
            'identificator': resource.get('identificator'),
            'bibliographic': resource.get('bibliographic'),
            'modality': resource.get('modality'),
            'creation': resource.get('creation'),
            'features': resource.get('features')
        }
        return hashlib.md5(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode('utf-8')
        ).hexdigest()

    def ensure_geometries_for_geo_objects(self) -> Dict[str, int]:
        """Создает ресурсы с геометрией для географических объектов, у которых их нет.
        
        Перебирает все русские названия объекта (primary_name + все ru_names) 
        для поиска совпадения в geodb.json.
        """
        result = {'created': 0, 'skipped': 0, 'errors': 0}
        
        # Находим все географические объекты без геометрии
        geo_objects = self._find_geo_objects_without_geometry()
        self._logger.info(f"Found {len(geo_objects)} geo objects without geometry")
        
        if not geo_objects:
            self._logger.info("No geo objects without geometry found")
            return result
        
        # Получаем все геометрии из geodb один раз
        all_geometries = self.geodata_provider.get_all_geometries()
        self._logger.info(f"Found {len(all_geometries)} geometries in geodb")
        
        if not all_geometries:
            self._logger.warning("No geometries found in geodb.json")
            return result
        
        for geo_obj in geo_objects:
            try:
                object_id = geo_obj['id']
                object_db_id = geo_obj['db_id']
                
                # Получаем все русские названия объекта
                names = self._get_object_rus_names(object_id)
                
                if not names:
                    self._logger.debug(f"No Russian names found for object {object_db_id}")
                    result['skipped'] += 1
                    continue
                
                # Ищем геометрию по любому из названий
                matching_geom = self._find_matching_geometry(names, all_geometries)
                
                if not matching_geom:
                    self._logger.debug(f"No matching geometry for {names[0]} (db_id: {object_db_id})")
                    result['skipped'] += 1
                    continue
                
                # Создаем ресурс с геометрией
                geodb_id, geometry, geom_type = matching_geom
                resource_id = self._create_geometry_resource(
                    object_id, names[0], (geodb_id, geometry, geom_type)
                )
                
                if resource_id:
                    result['created'] += 1
                    self._logger.info(f"Created geometry for '{names[0]}' (db_id: {object_db_id})")
                else:
                    result['errors'] += 1
                    self._logger.error(f"Failed to create geometry for '{names[0]}'")
                    
            except Exception as e:
                self._logger.error(f"Error processing object {geo_obj.get('db_id', 'unknown')}: {e}", exc_info=True)
                result['errors'] += 1
        
        self._logger.info(f"Geometry creation completed: created={result['created']}, skipped={result['skipped']}, errors={result['errors']}")
        return result
    
    def _get_object_rus_names(self, object_id: int) -> List[str]:
        """Возвращает список всех русских названий объекта.
        
        Порядок: сначала primary_name (если указан), затем все остальные ru_names.
        """
        sql = """
            SELECT ons.synonym, 
                CASE 
                    WHEN o.object_properties->>'primary_name' = ons.synonym THEN 1 
                    ELSE 2 
                END as priority
            FROM eco_assistant.object o
            JOIN eco_assistant.object_name_synonym_link onsl ON o.id = onsl.object_id
            JOIN eco_assistant.object_name_synonym ons ON onsl.synonym_id = ons.id
            WHERE o.id = %s AND ons.language = 'ru'
            ORDER BY priority, ons.id
        """
        rows = self.client.fetchall(sql, (object_id,))
        
        if not rows:
            return []
        
        # Убираем дубликаты, сохраняя порядок
        seen = set()
        unique_names = []
        for row in rows:
            name = row[0].lower().strip()
            if name not in seen:
                seen.add(name)
                unique_names.append(name)
        
        return unique_names

    def _find_matching_geometry(self, names: List[str], geometries: List[Tuple[str, Dict, str]]) -> Optional[Tuple[str, Dict, str]]:
        """Ищет геометрию по любому из переданных названий.
        
        Args:
            names: Список названий для поиска (уже в нижнем регистре)
            geometries: Список кортежей (key, geometry, geometry_type) из geodb
        
        Returns:
            Первый найденный кортеж (key, geometry, geometry_type) или None
        """
        for name in names:
            name_lower = name.lower().strip()
            if len(name_lower) < 2:  # Пропускаем слишком короткие названия
                continue
                
            for key, geometry, geom_type in geometries:
                if key.lower() == name_lower:
                    self._logger.debug(f"Found geometry for name '{name}' (key: {key})")
                    return (key, geometry, geom_type)
        
        return None

    def _find_geo_objects_without_geometry(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT DISTINCT o.id, o.db_id, o.object_properties
            FROM eco_assistant.object o
            JOIN eco_assistant.object_type ot ON o.object_type_id = ot.id
            WHERE ot.name = 'Географический объект'
            AND o.id NOT IN (
                SELECT DISTINCT ro.object_id
                FROM eco_assistant.resource_object ro
                JOIN eco_assistant.resource r ON ro.resource_id = r.id
                JOIN eco_assistant.resource_value rv ON r.id = rv.resource_id
                JOIN eco_assistant.modality m ON rv.modality_id = m.id
                WHERE m.modality_type = 'Геоданные'
            )
        """
        rows = self.client.fetchall(sql)
        return [{'id': row[0], 'db_id': row[1], 'properties': row[2]} for row in rows]
    
    def _find_object_by_synonym(self, name: str) -> Optional[int]:
        """Find object by any Russian synonym (case-insensitive)."""
        if not name:
            return None
        sql = """
            SELECT DISTINCT o.id
            FROM eco_assistant.object o
            JOIN eco_assistant.object_name_synonym_link onsl ON o.id = onsl.object_id
            JOIN eco_assistant.object_name_synonym ons ON onsl.synonym_id = ons.id
            WHERE ons.language = 'ru'
            AND LOWER(ons.synonym) = LOWER(%s)
        """
        row = self.client.fetchone(sql, (name.strip(),))
        
        return row[0] if row else None
    def _create_geometry_resource(
    self, object_id: int, obj_name: str, geometry_data: Tuple[str, Dict, str]
) -> Optional[int]:
        geodb_id, geometry, geom_type = geometry_data
        text_id = f"auto_geom_{geodb_id}"
        
        existing_resource_id = self.resource_repo.find_by_text_id(text_id)
        if existing_resource_id:
            self.resource_repo.link_resource_to_object(existing_resource_id, object_id, 'geometry')
            self._logger.debug(f"Already exists: {text_id}, linked to object {object_id}")
            return existing_resource_id
        
        modality = self.modality_repo.get_or_create_modality('Геоданные', 'geodata_value')
        
        bibliographic = BibliographicData()
        bibliographic_id = self.bibliographic_repo.get_or_create(bibliographic)
        
        creation = CreationData(
            creation_type='auto_generated',
            creation_tool='geometry_enricher',
            creation_params={'source': 'geodb', 'geodb_id': geodb_id}
        )
        creation_id = self.creation_repo.get_or_create(creation)
        
        resource_static = ResourceStatic(
            static_id=text_id,
            bibliographic_id=bibliographic_id,
            creation_id=creation_id
        )
        resource_static_id = self.resource_static_repo.get_or_create(resource_static)
        
        metadata = SupportMetadata(parameters={
            'auto_generated': True,
            'source': 'geodb',
            'geodb_id': geodb_id,
            'object_name': obj_name
        })
        metadata_id = self.metadata_repo.get_or_create(metadata)
        
        resource = Resource(
            title=f"{obj_name}",
            uri=None,
            features={'source': 'auto_generated', 'geodb_id': geodb_id},
            text_id=text_id,
            resource_static_id=resource_static_id,
            support_metadata_id=metadata_id
        )
        resource_id = self.resource_repo.save_resource(resource)
        
        geodata_value = GeodataValue(geometry=geometry, geometry_type=geom_type)
        value_id = self.modality_repo.save_geodata_value(geodata_value)
        self.modality_repo.link_resource_value(resource_id, modality.id, value_id)
        
        self.resource_repo.link_resource_to_object(resource_id, object_id, 'geometry')
        
        return resource_id