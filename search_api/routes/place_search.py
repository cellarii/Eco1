# search_api/routes/place_search.py
import logging
from flask import Blueprint, request, jsonify, current_app
from ..use_cases.place_search_use_case import PlaceSearchUseCase
from ..adapters.sqlalchemy_repository import SQLAlchemySearchRepository
from ..infrastructure.database import get_session
from ..services.geo_map_service import GeoMapService
from ..domain.value_objects import ModalityType
from ..domain.entities import ObjectCriteria

place_search_bp = Blueprint('place_search', __name__, url_prefix='/search')
logger = logging.getLogger(__name__)

def _get_repository():
    return SQLAlchemySearchRepository(get_session)

@place_search_bp.route('/place/objects', methods=['POST'])
def search_objects_near_place():
    import time
    request_start = time.time()
    data = request.get_json() or {}
    place_name = data.get('place_name')
    if not place_name:
        return jsonify({'error': 'place_name is required'}), 400

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

    config = current_app.config.get('SEARCH_CONFIG')
    if not config:
        from ..config import SearchConfig
        config = SearchConfig.from_env()

    geo_service = GeoMapService(config.maps_dir, config.domain)
    use_case = PlaceSearchUseCase(_get_repository(), geo_service)
    result = use_case.execute(
        place_name=place_name, subtypes=subtypes,
        modality_type=modality_type, buffer_radius_km=buffer_radius_km,
        limit=limit, offset=offset, search_type=search_type,
        object_criteria=object_criteria
    )

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
            'resource_type': r.resource_type,
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

    request_elapsed = time.time() - request_start
    logger.info(f"POST /place/objects total request time: {request_elapsed:.4f}s")
    return jsonify(response_data), 200