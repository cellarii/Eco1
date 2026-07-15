import logging
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

# Импорты из search_api (те же, что были во Flask-версии)
from search_api.config import SearchConfig
from search_api.use_cases.place_search_use_case import PlaceSearchUseCase
from search_api.adapters.sqlalchemy_repository import SQLAlchemySearchRepository
from search_api.infrastructure.database import get_session, init_db
from search_api.services.geo_map_service import GeoMapService
from search_api.domain.entities import ObjectCriteria
from search_api.domain.value_objects import ModalityType

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Pydantic-схема запроса (полностью повторяет Flask-структуру)
# ============================================================

class PlaceSearchRequest(BaseModel):
    place_name: str
    subtypes: Optional[List[str]] = ["Достопримечательности"]
    modality_type: Optional[str] = None
    buffer_radius_km: float = 10.0
    limit: int = 20
    offset: int = 0
    search_type: str = "near"
    object_criteria: Optional[Dict[str, Any]] = None
    object_type: Optional[str] = None
    name_synonyms: Optional[Dict[str, List[str]]] = None
    properties: Optional[Dict[str, Any]] = None
    db_id: Optional[str] = None


# ============================================================
# ЭНДПОИНТ: /search/place/objects
# ============================================================

@router.post("/search/place/objects")
async def search_objects_near_place(request_data: PlaceSearchRequest):
    logger.info("=== /search/place/objects called ===")
    
    try:
        data = request_data.dict()
        
        place_name = data.get('place_name')
        if not place_name:
            return {'error': 'place_name is required'}, 400

        subtypes = data.get('subtypes') or data.get('Подтип объекта', ['Достопримечательности'])
        modality_type = data.get('modality_type')
        buffer_radius_km = data.get('buffer_radius_km', 10.0)
        limit = data.get('limit', 20)
        offset = data.get('offset', 0)
        search_type = data.get('search_type', 'near')

        object_criteria = None
        if data.get('object_criteria'):
            oc = data['object_criteria']
            object_criteria = ObjectCriteria(
                db_id=oc.get('db_id'),
                name_synonyms=oc.get('name_synonyms'),
                properties=oc.get('properties'),
                object_type=oc.get('object_type')
            )
        else:
            if data.get('object_type') or data.get('name_synonyms') or data.get('properties'):
                object_criteria = ObjectCriteria(
                    db_id=data.get('db_id'),
                    name_synonyms=data.get('name_synonyms'),
                    properties=data.get('properties'),
                    object_type=data.get('object_type')
                )

        # --- Инициализация use_case (как во Flask-версии) ---
        config = SearchConfig.from_env()
        init_db(config)
        session_factory = get_session
        repository = SQLAlchemySearchRepository(session_factory)
        geo_service = GeoMapService(config.maps_dir, config.domain)
        use_case = PlaceSearchUseCase(repository, geo_service)

        # --- Выполнение ---
        result = use_case.execute(
            place_name=place_name,
            subtypes=subtypes,
            modality_type=modality_type,
            buffer_radius_km=buffer_radius_km,
            limit=limit,
            offset=offset,
            search_type=search_type,
            object_criteria=object_criteria
        )

        # --- Сериализация объектов (как во Flask-версии) ---
        objects_serialized = [{
            'id': o.id,
            'db_id': o.db_id,
            'type': o.object_type,
            'properties': o.properties,
            'synonyms': o.synonyms
        } for o in result.objects]

        resources_serialized = []
        for r in result.resources:
            item = {
                'id': r.id,
                'title': r.title,
                'uri': r.uri,
                'author': r.author,
                'source': r.source,
                'modality_type': r.modality_type,
                'features': r.features,
                'resource_type': getattr(r, 'resource_type', 'Статический'),
                'external_id': getattr(r, 'external_id', None),
            }
            if r.modality_type == ModalityType.GEODATA.value:
                if isinstance(r.content, dict) and 'map_links' in r.content:
                    item['content'] = r.content
                else:
                    item['content'] = r.content
            else:
                item['content'] = r.content
            resources_serialized.append(item)

        response_data = {
            'place_name': place_name,
            'used_geometry': result.used_geometry,
            'total_objects': result.total_objects,
            'objects': objects_serialized,
            'resources': resources_serialized
        }

        if hasattr(result, 'total_resources'):
            response_data['total_resources'] = result.total_resources

        logger.info(f"POST /place/objects completed")
        return response_data

    except Exception as e:
        logger.error(f"Error in /place/objects: {e}", exc_info=True)
        return {'error': str(e)}, 500