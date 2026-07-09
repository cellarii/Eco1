import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import json
from sqlalchemy.orm import joinedload
from sqlalchemy.dialects import postgresql
from sqlalchemy import func, cast, text, String
from ..domain.entities import ObjectResult, ResourceResult, ObjectCriteria, ResourceCriteria
from .search_repository import SearchRepository
from ..infrastructure.orm.object_models import Object, ObjectNameSynonym, ObjectType
from ..infrastructure.orm.resource_models import Resource, Bibliographic, Author, Source, ResourceStatic
from ..infrastructure.orm.modality_models import Modality, TextValue, ImageValue, GeodataValue, ResourceValue

logger = logging.getLogger(__name__)

class SQLAlchemySearchRepository(SearchRepository):
    def __init__(self, session_factory):
        self._session_factory = session_factory


    def _apply_exact_location_filter(self, query, value: str, key: str = 'exact_location'):
        if not value or not isinstance(value, str):
            return query
        
        json_field = Object.object_properties[key]
        pattern = r'\y' + re.escape(value).replace(r'\ ', '[ -]?').replace('-', '[ -]?') + r'\y'
        
        regex_condition = func.regexp_matches(
            func.cast(json_field.as_string(), String),
            pattern, 'i'
        )
        
        return query.filter(regex_condition.is_not(None))

    def find_objects_by_criteria(self, criteria: ObjectCriteria, limit: int = 20, offset: int = 0) -> List[ObjectResult]:
        if not criteria.db_id and not criteria.name_synonyms and not criteria.properties and not criteria.object_type:
            return []
        
        session = self._session_factory()
        with session:
            query = session.query(Object).options(joinedload(Object.synonyms)).join(Object.object_type)
            
            if criteria.db_id:
                query = query.filter(Object.db_id == criteria.db_id)
            if criteria.object_type:
                query = query.filter(ObjectType.name == criteria.object_type)
            if criteria.name_synonyms:
                names = []
                for lang, name_list in criteria.name_synonyms.items():
                    names.extend(name_list)
                if names:
                    from sqlalchemy import or_
                    conditions = []
                    for name in names:
                        conditions.append(
                            Object.synonyms.any(func.lower(ObjectNameSynonym.synonym) == name.lower())
                        )
                        conditions.append(
                            func.lower(Object.db_id) == name.lower()
                        )
                    query = query.filter(or_(*conditions))
            
            if criteria.properties:
                for key, value in criteria.properties.items():
                    if key == 'subtypes' or key == 'Подтип объекта':
                        if isinstance(value, str):
                            query = query.filter(Object.object_properties[key].op('?')(value))
                        elif isinstance(value, list):
                            for item in value:
                                query = query.filter(Object.object_properties[key].op('?')(item))
                    elif key == 'exact_location' or key == 'Детальное расположение':
                        if isinstance(value, str):
                            pattern = r'\y' + re.escape(value).replace(r'\ ', '[ -]?').replace('-', '[ -]?') + r'\y'
                            query = query.filter(
                                func.cast(Object.object_properties[key].as_string(), String).op('~*')(pattern)
                            )
                        elif isinstance(value, list):
                            from sqlalchemy import or_
                            conditions = []
                            for item in value:
                                pattern = r'\y' + re.escape(item).replace(r'\ ', '[ -]?').replace('-', '[ -]?') + r'\y'
                                conditions.append(
                                    func.cast(Object.object_properties[key].as_string(), String).op('~*')(pattern)
                                )
                            query = query.filter(or_(*conditions))
                    else:
                        if isinstance(value, str):
                            query = query.filter(Object.object_properties[key].as_string().ilike(f"%{value}%"))
                        elif isinstance(value, list):
                            for item in value:
                                query = query.filter(Object.object_properties[key].as_string().ilike(f"%{item}%"))
                        elif isinstance(value, bool):
                            query = query.filter(Object.object_properties[key].as_boolean() == value)
                        elif isinstance(value, (int, float)):
                            query = query.filter(Object.object_properties[key].as_float() == value)
                        else:
                            query = query.filter(Object.object_properties[key].as_string().ilike(f"%{str(value)}%"))
                                
            query = query.limit(limit).offset(offset)
            compiled = query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
            logger.info(f"Executing query: {compiled}")
            objects = query.all()
            
            return [
                ObjectResult(
                    id=obj.id,
                    db_id=obj.db_id,
                    object_type=obj.object_type.name,
                    properties=obj.object_properties,
                    synonyms=[s.synonym for s in obj.synonyms]
                ) for obj in objects
            ]
                          
    def find_resources_by_criteria(self, criteria: ResourceCriteria, object_ids: Optional[List[int]] = None, limit: int = 50, offset: int = 0) -> List[ResourceResult]:
        session = self._session_factory()
        with session:
            query = session.query(Resource).options(
                joinedload(Resource.resource_static).joinedload(ResourceStatic.bibliographic).joinedload(Bibliographic.author),
                joinedload(Resource.resource_static).joinedload(ResourceStatic.bibliographic).joinedload(Bibliographic.source),
                joinedload(Resource.support_metadata)
            ).outerjoin(
                ResourceStatic, Resource.resource_static_id == ResourceStatic.id
            ).outerjoin(
                Bibliographic, ResourceStatic.bibliographic_id == Bibliographic.id
            ).outerjoin(
                Author, Bibliographic.author_id == Author.id
            ).outerjoin(
                Source, Bibliographic.source_id == Source.id
            ).outerjoin(
                ResourceValue, Resource.id == ResourceValue.resource_id
            ).outerjoin(
                Modality, ResourceValue.modality_id == Modality.id
            ).outerjoin(
                TextValue, (ResourceValue.value_id == TextValue.id) & (Modality.modality_type == 'Текст')
            ).outerjoin(
                ImageValue, (ResourceValue.value_id == ImageValue.id) & (Modality.modality_type == 'Изображение')
            ).outerjoin(
                GeodataValue, (ResourceValue.value_id == GeodataValue.id) & (Modality.modality_type == 'Геоданные')
            )

            if object_ids:
                query = query.filter(Resource.objects.any(Object.id.in_(object_ids)))
            if criteria.title:
                query = query.filter(Resource.title.ilike(f"%{criteria.title}%"))
            if criteria.uri:
                query = query.filter(Resource.uri == criteria.uri)
            if criteria.author:
                query = query.filter(Author.name.ilike(f"%{criteria.author}%"))
            if criteria.source:
                query = query.filter(Source.name.ilike(f"%{criteria.source}%"))
            if criteria.modality_type:
                query = query.filter(Modality.modality_type == criteria.modality_type)
            if criteria.features:
                for key, val in criteria.features.items():
                    query = query.filter(Resource.features[key].as_string() == str(val))

            if criteria.modality_type == "Текст" or criteria.modality_type is None:
                from sqlalchemy import text as sql_text
                query = query.order_by(
                    sql_text("""
                        length(
                            COALESCE(text_value.structured_data::text, '')
                        ) DESC NULLS LAST
                    """)
                )
            else:
                query = query.order_by(Resource.id)

            resources = query.limit(limit).offset(offset).all()

            result = []
            for r in resources:
                matching_rv = None
                if criteria.modality_type:
                    for rv in r.resource_values:
                        if rv.modality and rv.modality.modality_type == criteria.modality_type:
                            matching_rv = rv
                            break
                if not matching_rv and r.resource_values:
                    matching_rv = r.resource_values[0]

                content = None
                if matching_rv and matching_rv.modality:
                    mt = matching_rv.modality.modality_type
                    if mt == 'Текст' and matching_rv.value_id:
                        tv = session.query(TextValue).get(matching_rv.value_id)
                        content = {'structured_data': tv.structured_data} if tv else None
                    elif mt == 'Изображение' and matching_rv.value_id:
                        iv = session.query(ImageValue).get(matching_rv.value_id)
                        content = {'url': iv.url, 'file_path': iv.file_path, 'format': iv.format} if iv else None
                    elif mt == 'Геоданные' and matching_rv.value_id:
                        gv = session.query(GeodataValue).get(matching_rv.value_id)
                        if gv:
                            from geoalchemy2.shape import to_shape
                            geom = to_shape(gv.geometry)
                            content = {'geojson': geom.__geo_interface__, 'type': gv.geometry_type}

                author_name = None
                source_name = None
                if r.resource_static and r.resource_static.bibliographic:
                    if r.resource_static.bibliographic.author:
                        author_name = r.resource_static.bibliographic.author.name
                    if r.resource_static.bibliographic.source:
                        source_name = r.resource_static.bibliographic.source.name

                modality_type = None
                if matching_rv and matching_rv.modality:
                    modality_type = matching_rv.modality.modality_type

                external_id = None
                if r.support_metadata and r.support_metadata.parameters:
                    external_id = r.support_metadata.parameters.get('external_id')

                result.append(ResourceResult(
                    id=r.id, title=r.title, uri=r.uri,
                    author=author_name,
                    source=source_name,
                    modality_type=modality_type,
                    content=content,
                    features=r.features,
                    external_id=external_id
                ))

            if criteria.modality_type:
                result = [r for r in result if r.modality_type == criteria.modality_type]

            return result

    def find_objects_with_geometry_by_subtypes(
    self, geometry_geojson: Dict[str, Any], subtypes: List[str],
    buffer_radius_km: float, limit: int, offset: int,
    search_type: str = "near"
) -> Tuple[List[Any], List[int]]:
        session = self._session_factory()
        with session:
            geom_json_str = json.dumps(geometry_geojson)
            buffer_meters = buffer_radius_km * 1000.0

            if search_type == "inside":
                spatial_cond = "ST_Within(gv.geometry, ig.geom)"
            elif search_type == "near":
                spatial_cond = "ST_DWithin(gv.geometry, ig.geom, :buffer)"
            else:
                spatial_cond = "(ST_Within(gv.geometry, ig.geom) OR ST_DWithin(gv.geometry, ig.geom, :buffer))"

            sql = text(f"""
                WITH input_geom AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326) AS geom)
                SELECT o.id, o.db_id, o.object_properties, ot.name as object_type,
                    ST_AsGeoJSON(gv.geometry)::json as geometry_geojson,
                    ST_GeometryType(gv.geometry) as geom_type,
                    ST_Distance(gv.geometry, ig.geom) as distance,
                    array_agg(DISTINCT ons.synonym) FILTER (WHERE ons.synonym IS NOT NULL) as synonyms
                FROM eco_assistant.object o
                JOIN eco_assistant.object_type ot ON o.object_type_id = ot.id
                JOIN eco_assistant.resource_object ro ON o.id = ro.object_id
                JOIN eco_assistant.resource_value rv ON ro.resource_id = rv.resource_id
                JOIN eco_assistant.modality m ON rv.modality_id = m.id
                JOIN eco_assistant.geodata_value gv ON rv.value_id = gv.id
                LEFT JOIN eco_assistant.object_name_synonym_link osl ON o.id = osl.object_id
                LEFT JOIN eco_assistant.object_name_synonym ons ON osl.synonym_id = ons.id
                CROSS JOIN input_geom ig
                WHERE m.modality_type = 'Геоданные'
                AND {spatial_cond}
                AND (o.object_properties->>'Подтип объекта' = ANY(:subtypes)
                OR o.object_properties->'Подтип объекта' ?| :subtypes)
                GROUP BY o.id, ot.name, gv.geometry, ig.geom
                ORDER BY distance
                LIMIT :limit OFFSET :offset
            """)
            
            rows = session.execute(sql, {
                'geom': geom_json_str,
                'buffer': buffer_meters,
                'subtypes': subtypes,
                'limit': limit,
                'offset': offset
            }).fetchall()
            
            from ..infrastructure.orm.object_models import Object, ObjectType, ObjectNameSynonym
            
            objects = []
            object_ids = []
            for r in rows:
                object_ids.append(r.id)
                obj = Object()
                obj.id = r.id
                obj.db_id = r.db_id
                obj.object_properties = r.object_properties
                obj.object_type = ObjectType(name=r.object_type)
                obj._geometry_geojson = r.geometry_geojson
                obj.synonyms = [ObjectNameSynonym(synonym=s) for s in (r.synonyms or [])]
                objects.append(obj)
            
            return objects, object_ids

    def find_objects_with_geometry_by_criteria(
        self, geometry_geojson: Dict[str, Any], criteria: ObjectCriteria,
        buffer_radius_km: float, limit: int, offset: int,
        search_type: str = "near"
    ) -> Tuple[List[Any], List[int]]:
        session = self._session_factory()
        with session:
            geom_json_str = json.dumps(geometry_geojson)
            buffer_meters = buffer_radius_km * 1000.0

            base_conditions = []
            params = {
                'geom': geom_json_str,
                'buffer': buffer_meters,
                'limit': limit,
                'offset': offset
            }
            
            if criteria.object_type:
                base_conditions.append("ot.name = :object_type")
                params['object_type'] = criteria.object_type
            if criteria.db_id:
                base_conditions.append("o.db_id = :db_id")
                params['db_id'] = criteria.db_id
            if criteria.name_synonyms:
                names = []
                for lang, name_list in criteria.name_synonyms.items():
                    names.extend(name_list)
                if names:
                    placeholders = ','.join([f"'{n}'" for n in names])
                    base_conditions.append(f"ons.synonym IN ({placeholders})")
            if criteria.properties:
                for key, val in criteria.properties.items():
                    param_name = f"prop_{key.replace(' ', '_').replace('-', '_')}"
                    if isinstance(val, str):
                        base_conditions.append(
                            f"o.object_properties->>'{key}' = :{param_name}"
                        )
                        params[param_name] = val
                    elif isinstance(val, list):
                        items = "', '".join(val)
                        base_conditions.append(
                            f"o.object_properties->'{key}' ?| ARRAY['{items}']"
                        )
            
            base_where = " AND ".join(base_conditions) if base_conditions else "TRUE"

            if search_type == "inside":
                spatial_cond = "ST_Within(gv.geometry, ig.geom)"
            elif search_type == "near":
                spatial_cond = "ST_DWithin(gv.geometry, ig.geom, :buffer)"
            else:
                spatial_cond = "(ST_Within(gv.geometry, ig.geom) OR ST_DWithin(gv.geometry, ig.geom, :buffer))"

            sql = text(f"""
                WITH input_geom AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326) AS geom)
                SELECT o.id, o.db_id, o.object_properties, ot.name as object_type,
                    ST_AsGeoJSON(gv.geometry)::json as geometry_geojson,
                    ST_GeometryType(gv.geometry) as geom_type,
                    ST_Distance(gv.geometry, ig.geom) as distance,
                    array_agg(DISTINCT ons.synonym) FILTER (WHERE ons.synonym IS NOT NULL) as synonyms
                FROM eco_assistant.object o
                JOIN eco_assistant.object_type ot ON o.object_type_id = ot.id
                JOIN eco_assistant.resource_object ro ON o.id = ro.object_id
                JOIN eco_assistant.resource_value rv ON ro.resource_id = rv.resource_id
                JOIN eco_assistant.modality m ON rv.modality_id = m.id
                JOIN eco_assistant.geodata_value gv ON rv.value_id = gv.id
                LEFT JOIN eco_assistant.object_name_synonym_link osl ON o.id = osl.object_id
                LEFT JOIN eco_assistant.object_name_synonym ons ON osl.synonym_id = ons.id
                CROSS JOIN input_geom ig
                WHERE m.modality_type = 'Геоданные'
                AND {spatial_cond}
                AND {base_where}
                GROUP BY o.id, ot.name, gv.geometry, ig.geom
                ORDER BY distance
                LIMIT :limit OFFSET :offset
            """)
            
            rows = session.execute(sql, params).fetchall()

            from ..infrastructure.orm.object_models import Object, ObjectType, ObjectNameSynonym
            objects = []
            object_ids = []
            for r in rows:
                object_ids.append(r.id)
                obj = Object()
                obj.id = r.id
                obj.db_id = r.db_id
                obj.object_properties = r.object_properties
                obj.object_type = ObjectType(name=r.object_type)
                obj._geometry_geojson = r.geometry_geojson
                obj.synonyms = [ObjectNameSynonym(synonym=s) for s in (r.synonyms or [])]
                objects.append(obj)
            return objects, object_ids
          
    def find_place_geometry(self, place_name: str) -> Optional[Dict[str, Any]]:
        session = self._session_factory()
        with session:
            sql = text("""
                WITH matched_geometries AS (
                    SELECT DISTINCT
                        gv.geometry,
                        CASE 
                            WHEN ST_GeometryType(gv.geometry) IN ('ST_Polygon', 'ST_MultiPolygon') THEN 1
                            WHEN ST_GeometryType(gv.geometry) = 'ST_Point' THEN 3
                            ELSE 2
                        END as priority
                    FROM eco_assistant.object o
                    LEFT JOIN eco_assistant.object_name_synonym_link osl ON o.id = osl.object_id
                    LEFT JOIN eco_assistant.object_name_synonym ons ON osl.synonym_id = ons.id
                    JOIN eco_assistant.resource_object ro ON o.id = ro.object_id
                    JOIN eco_assistant.resource_value rv ON ro.resource_id = rv.resource_id
                    JOIN eco_assistant.modality m ON rv.modality_id = m.id
                    JOIN eco_assistant.geodata_value gv ON rv.value_id = gv.id
                    WHERE m.modality_type = 'Геоданные'
                    AND (LOWER(ons.synonym) = LOWER(:name) OR LOWER(o.db_id) = LOWER(:name))
                )
                SELECT ST_AsGeoJSON(geometry)::json as geojson
                FROM matched_geometries
                ORDER BY priority ASC
                LIMIT 1
            """)
            result = session.execute(sql, {'name': place_name}).first()
            return result.geojson if result else None

    def find_related_objects(self, object_ids: List[int], relation_type: str) -> List[dict]:
        if not object_ids:
            return []
        session = self._session_factory()
        with session:
            # Ищем в обоих направлениях: нерпа может быть как object_id, так и related_object_id
            sql = text("""
                SELECT DISTINCT o.id, o.db_id, o.object_properties, ot.name as object_type,
                    array_agg(DISTINCT ons.synonym) FILTER (WHERE ons.synonym IS NOT NULL) as synonyms
                FROM eco_assistant.object_object_link ool
                JOIN eco_assistant.object o
                    ON (ool.object_id = ANY(:object_ids) AND o.id = ool.related_object_id)
                    OR (ool.related_object_id = ANY(:object_ids) AND o.id = ool.object_id)
                JOIN eco_assistant.object_type ot ON o.object_type_id = ot.id
                LEFT JOIN eco_assistant.object_name_synonym_link osl ON o.id = osl.object_id
                LEFT JOIN eco_assistant.object_name_synonym ons ON osl.synonym_id = ons.id
                WHERE ool.relation_type = :relation_type
                AND o.id != ALL(:object_ids)
                GROUP BY o.id, o.db_id, o.object_properties, ot.name
            """)
            rows = session.execute(sql, {
                'object_ids': object_ids,
                'relation_type': relation_type,
            }).fetchall()
            return [
                {
                    'id': r.id,
                    'db_id': r.db_id,
                    'object_type': r.object_type,
                    'name': r.synonyms[0] if r.synonyms else r.db_id,
                    'synonyms': list(r.synonyms or []),
                }
                for r in rows
            ]

    def get_geometry_type_for_place(self, place_name: str) -> Optional[str]:
        session = self._session_factory()
        with session:
            sql = text("""
                SELECT gv.geometry_type
                FROM eco_assistant.object o
                LEFT JOIN eco_assistant.object_name_synonym_link osl ON o.id = osl.object_id
                LEFT JOIN eco_assistant.object_name_synonym ons ON osl.synonym_id = ons.id
                JOIN eco_assistant.resource_object ro ON o.id = ro.object_id
                JOIN eco_assistant.resource_value rv ON ro.resource_id = rv.resource_id
                JOIN eco_assistant.modality m ON rv.modality_id = m.id
                JOIN eco_assistant.geodata_value gv ON rv.value_id = gv.id
                WHERE m.modality_type = 'Геоданные'
                AND (LOWER(ons.synonym) = LOWER(:name) OR LOWER(o.db_id) = LOWER(:name))
                LIMIT 1
            """)
            result = session.execute(sql, {'name': place_name}).first()
            return result.geometry_type if result else None
