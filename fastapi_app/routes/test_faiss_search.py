import logging
from fastapi import APIRouter, Depends, Query
from typing import Optional

from fastapi_app.dependencies import get_search_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# ЭНДПОИНТ: /test_faiss_search
# ============================================================

@router.get("/test_faiss_search")
async def test_faiss_search(
    query: str = Query(..., description="Поисковый запрос"),
    k: int = Query(10, description="Количество результатов", ge=1, le=100),
    similarity_threshold: float = Query(0.8, description="Порог схожести", ge=0.0, le=1.0),
    include_full_docs: bool = Query(False, description="Возвращать полные документы"),
    debug: bool = Query(False, description="Детальная отладка"),
    search_service=Depends(get_search_service)
):
    """
    Тестовый эндпоинт для проверки FAISS векторного поиска
    """
    try:
        if not query:
            return {
                "status": "error",
                "message": "Параметр 'query' обязателен",
                "example": "/test_faiss_search?query=Байкал&k=5&similarity_threshold=0.7"
            }

        logger.info(f"🔍 Тестовый FAISS поиск: '{query}' (k={k}, threshold={similarity_threshold})")

        search_service.load_faiss_index()

        if not search_service.faiss_vectorstore:
            return {
                "status": "error",
                "message": "FAISS индекс не загружен",
                "details": f"Проверьте путь: {search_service.faiss_index_path}"
            }

        index_size = search_service.faiss_vectorstore.index.ntotal
        logger.info(f"📊 Размер FAISS индекса: {index_size} векторов")

        results = search_service.faiss_vectorstore.similarity_search_with_score(query, k=k*2)

        logger.info(f"📋 RAW FAISS результаты ({len(results)}):")
        for i, (doc, score) in enumerate(results[:5]):
            score_float = float(score)
            similarity_float = 1 - score_float
            logger.info(f"  {i+1}. Score: {score_float:.4f}, Similarity: {similarity_float:.4f}")
            logger.info(f"     Name: {doc.metadata.get('common_name', 'N/A')}")
            logger.info(f"     Type: {doc.metadata.get('resource_type', 'N/A')}")
            logger.info(f"     Resource ID: {doc.metadata.get('resource_id', 'N/A')}")

            if debug:
                logger.info(f"     Полный текст чанка: {doc.page_content}")

        def convert_floats(obj):
            if isinstance(obj, dict):
                return {k: convert_floats(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_floats(item) for item in obj]
            elif hasattr(obj, 'dtype') and 'float32' in str(obj.dtype):
                return float(obj)
            return obj

        filtered_results = []
        for doc, score in results:
            score_float = float(score)
            similarity = 1 - score_float

            if similarity >= similarity_threshold:
                if include_full_docs:
                    resource_id = doc.metadata.get('resource_id')
                    full_document = search_service._get_full_document(resource_id, doc.page_content)
                    content = full_document
                else:
                    content = doc.page_content

                result = {
                    'content': content[:500] + "..." if len(content) > 500 else content,
                    'similarity': similarity,
                    'score': score_float,
                    'metadata': {
                        'resource_id': doc.metadata.get('resource_id', 'unknown'),
                        'resource_type': doc.metadata.get('resource_type', 'unknown'),
                        'common_name': doc.metadata.get('common_name', ''),
                        'scientific_name': doc.metadata.get('scientific_name', ''),
                        'source': doc.metadata.get('source', ''),
                        'chunk_index': doc.metadata.get('chunk_index', 0),
                        'total_chunks': doc.metadata.get('total_chunks', 1)
                    }
                }

                if debug:
                    safe_metadata = {}
                    for key, value in doc.metadata.items():
                        if hasattr(value, 'dtype') and 'float32' in str(value.dtype):
                            safe_metadata[key] = float(value)
                        else:
                            safe_metadata[key] = value
                    result['full_metadata'] = safe_metadata

                result = convert_floats(result)
                filtered_results.append(result)

        filtered_results.sort(key=lambda x: x['similarity'], reverse=True)
        filtered_results = filtered_results[:k]

        stats = {
            "total_index_size": index_size,
            "query": query,
            "parameters": {
                "k_requested": k,
                "similarity_threshold": similarity_threshold,
                "include_full_docs": include_full_docs
            },
            "search_results": {
                "raw_results": len(results),
                "filtered_results": len(filtered_results),
                "threshold_passed": len(filtered_results)
            }
        }

        resource_types = {}
        for result in filtered_results:
            rtype = result['metadata']['resource_type']
            resource_types[rtype] = resource_types.get(rtype, 0) + 1

        stats["resource_types"] = resource_types

        response = {
            "status": "success",
            "message": f"Найдено {len(filtered_results)} документов (порог: {similarity_threshold})",
            "stats": stats,
            "results": filtered_results,
            "query": query
        }

        if not filtered_results:
            response["status"] = "no_results"
            response["message"] = f"Не найдено документов с порогом схожести {similarity_threshold}"
            response["suggestion"] = "Попробуйте снизить порог similarity_threshold или изменить запрос"
            response["debug_info"] = {
                "raw_results_count": len(results),
                "threshold_applied": similarity_threshold,
                "index_size": index_size
            }

        logger.info(f"✅ FAISS поиск завершен: {len(filtered_results)} результатов")
        return response

    except Exception as e:
        logger.error(f"❌ Ошибка в /test_faiss_search: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Ошибка FAISS поиска: {str(e)}",
            "error_details": str(e) if debug else None
        }