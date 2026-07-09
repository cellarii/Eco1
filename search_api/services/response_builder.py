import hashlib
import logging
from typing import Dict, Any, List, Optional

from ..domain.entities import ObjectResult, ResourceResult, SearchResponse
from ..domain.value_objects import ModalityType, GeoContent
from .geo_map_service import GeoMapService
from .llm_answer_generator import LLMAnswerGenerator

logger = logging.getLogger(__name__)


class ResponseBuilder:
    def __init__(self, geo_service: GeoMapService, llm_generator: LLMAnswerGenerator):
        self._geo_service = geo_service
        self._llm_generator = llm_generator

    def build(self, search_response: SearchResponse, user_query: Optional[str] = None,
              use_llm: bool = False, vector_search=None) -> Dict[str, Any]:
        if search_response.modality_filter == ModalityType.GEODATA.value:
            geo_resources = [r for r in search_response.resources
                             if r.modality_type == ModalityType.GEODATA.value]
            if geo_resources:
                combined_resource = self._build_combined_map_resource(geo_resources)
                if combined_resource is not None:
                    search_response.resources = [combined_resource]

        result = {
            'object_criteria': self._serialize_object_criteria(search_response.object_criteria),
            'resource_criteria': self._serialize_resource_criteria(search_response.resource_criteria),
            'modality_filter': search_response.modality_filter,
            'objects': self._serialize_objects(search_response.objects),
            'resources': self._serialize_resources(search_response.resources, search_response.objects),
        }
        if search_response.debug_info:
            result['debug'] = search_response.debug_info
        if use_llm and user_query:
            llm_answer = self._llm_generator.generate(
                user_query, search_response.objects, search_response.resources
            )
            result['llm_answer'] = llm_answer
        return result

    def _serialize_resources(self, resources: List[ResourceResult], objects: List[ObjectResult]) -> List[Dict[str, Any]]:
        serialized = []
        for r in resources:
            if r is None:
                continue
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
                elif isinstance(r.content, GeoContent):
                    item['content'] = {
                        'geojson': r.content.geojson,
                        'geometry_type': r.content.geometry_type,
                        'map_links': {
                            'static': r.content.map_links.static,
                            'interactive': r.content.map_links.interactive,
                        }
                    }
                else:
                    item['content'] = r.content
            else:
                item['content'] = r.content
            serialized.append(item)
        return serialized

    def _build_combined_map_resource(self, geo_resources: List[ResourceResult]) -> Optional[ResourceResult]:
        import time
        start = time.time()

        geojson_with_titles = []
        for r in geo_resources:
            content = r.content
            geojson = None
            title = r.title or "Геообъект"
            if isinstance(content, dict) and 'geojson' in content:
                geojson = content['geojson']
            elif isinstance(content, GeoContent) and content.geojson:
                geojson = content.geojson
            if geojson:
                geojson_with_titles.append((geojson, title))

        if not geojson_with_titles:
            return None

        short_names = [title.replace(' ', '_')[:20] for _, title in geojson_with_titles[:3]]
        base_name = "_".join(short_names) if short_names else "combined"
        if len(short_names) > 1:
            base_name += "_и_др"
        if len(base_name) > 80:
            base_name = hashlib.md5(base_name.encode()).hexdigest()[:16]
        map_name = f"combined_{base_name}"

        map_objects = [
            {"geojson": gj, "tooltip": title, "popup": title, "name": title}
            for gj, title in geojson_with_titles
        ]
        map_result = self._geo_service.draw_custom_geometries(map_objects, map_name)

        elapsed = time.time() - start
        logger.info(f"_build_combined_map_resource took {elapsed:.4f}s for {len(geo_resources)} resources")

        return ResourceResult(
            id=-1,
            title=f"Общая карта ({len(geo_resources)-1} ресурсов)",
            uri=None,
            author=None,
            source=None,
            modality_type=ModalityType.GEODATA.value,
            content={
                "map_links": {
                    "static": map_result.get("static_map"),
                    "interactive": map_result.get("interactive_map")
                }
            },
            features=None,
            resource_type="Динамически вычисляемый",
            external_id=None
        )

    def _serialize_object_criteria(self, criteria) -> Optional[Dict[str, Any]]:
        if not criteria:
            return None
        return {
            'db_id': criteria.db_id,
            'name_synonyms': criteria.name_synonyms,
            'properties': criteria.properties,
            'object_type': criteria.object_type,
        }

    def _serialize_resource_criteria(self, criteria) -> Optional[Dict[str, Any]]:
        if not criteria:
            return None
        return {
            'title': criteria.title,
            'author': criteria.author,
            'source': criteria.source,
            'modality_type': criteria.modality_type,
            'features': criteria.features,
        }

    def _serialize_objects(self, objects: List[ObjectResult]) -> List[Dict[str, Any]]:
        return [
            {
                'id': o.id,
                'db_id': o.db_id,
                'type': o.object_type,
                'properties': o.properties,
                'synonyms': o.synonyms,
            }
            for o in objects
        ]

    