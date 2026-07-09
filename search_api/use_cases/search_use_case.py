# search_api/use_cases/search_use_case.py
import time
import logging
from dataclasses import dataclass
from typing import List, Optional

from ..domain.entities import SearchRequest, SearchResponse, ResourceCriteria, ObjectResult, ResourceResult
from ..adapters.search_repository import SearchRepository

logger = logging.getLogger(__name__)

@dataclass
class SearchUseCase:
    _repository: SearchRepository
    _vector_search = None

    def set_vector_search(self, vector_search):
        self._vector_search = vector_search

    def execute(self, request: SearchRequest) -> SearchResponse:
        total_start = time.time()
        debug = {}

        logger.info("=== SearchUseCase.execute START ===")
        logger.info(f"object criteria: {request.object}")
        logger.info(f"resource criteria: {request.resource}")
        logger.info(f"modality_type: {request.modality_type}")

        objects: List[ObjectResult] = []
        object_ids: Optional[List[int]] = None

        if request.object:
            obj_start = time.time()
            objects = self._repository.find_objects_by_criteria(
                request.object, limit=request.limit, offset=request.offset
            )
            obj_time = time.time() - obj_start
            debug['objects_query_time'] = obj_time
            logger.info(f"Objects query took {obj_time:.4f}s, found {len(objects)} objects")
            object_ids = [obj.id for obj in objects] if objects else None
        else:
            logger.info("No object criteria provided")

        resources: List[ResourceResult] = []
        if request.object and not objects:
            debug['resources_query_time'] = 0.0
            debug['resources_skipped'] = True
            logger.info("Skipping resource search because no objects found")
        else:
            resource_criteria = request.resource if request.resource else ResourceCriteria()
            res_start = time.time()
            resources = self._repository.find_resources_by_criteria(
                resource_criteria, object_ids, limit=request.limit * 2, offset=request.offset
            )
            res_raw_time = time.time() - res_start
            debug['resources_query_time_raw'] = res_raw_time
            logger.info(f"Raw resources query took {res_raw_time:.4f}s, found {len(resources)} resources")

            for r in resources:
                r.resource_type = "Статический"

            if request.modality_type:
                filter_start = time.time()
                resources = [r for r in resources if r.modality_type == request.modality_type]
                filter_time = time.time() - filter_start
                debug['resources_filter_time'] = filter_time
                logger.info(f"Modality filter took {filter_time:.4f}s, kept {len(resources)} resources")
            else:
                logger.info("No modality filter applied")

            resources = resources[:request.limit]
            debug['resources_query_time'] = res_raw_time

        vector_time = 0.0
        if (request.modality_type == "Текст" and not resources and
            self._vector_search and request.user_query):
            logger.info("No text resources, activating vector search fallback")
            vec_start = time.time()
            vector_docs = self._vector_search.search(
                query=request.user_query,
                object_type=request.object.object_type if request.object else "all",
                limit=request.limit * 2
            )
            vector_time = time.time() - vec_start
            logger.info(f"Vector search took {vector_time:.4f}s, returned {len(vector_docs)} documents")
            vector_resources = []
            for idx, doc in enumerate(vector_docs):
                content_obj = {
                    'structured_data': {
                        'content': doc.get('content', ''),
                        'source': doc.get('source', 'vector_search'),
                        'similarity': doc.get('similarity', 0)
                    }
                }
                vector_resources.append(ResourceResult(
                    id=-idx - 1,
                    title=doc.get('object_name', 'Результат векторного поиска'),
                    uri=None,
                    author=None,
                    source='векторный поиск',
                    modality_type='Текст',
                    content=content_obj,
                    features={'similarity': doc.get('similarity', 0), 'search_type': 'vector'},
                    resource_type="Статический",
                    external_id=None
                ))
            resources = vector_resources[:request.limit]
            debug['vector_search_used'] = True
            debug['vector_results_count'] = len(vector_docs)
            debug['vector_search_time'] = vector_time

        total_time = time.time() - total_start
        debug['total_time'] = total_time
        logger.info(f"SearchUseCase total execution time: {total_time:.4f}s (objects: {debug.get('objects_query_time', 0):.4f}, resources_raw: {debug.get('resources_query_time_raw', 0):.4f}, filter: {debug.get('resources_filter_time', 0):.4f}, vector: {vector_time:.4f})")

        response = SearchResponse(
            object_criteria=request.object,
            resource_criteria=request.resource,
            modality_filter=request.modality_type,
            objects=objects,
            resources=resources,
        )

        if request.debug:
            response.debug_info = debug

        return response