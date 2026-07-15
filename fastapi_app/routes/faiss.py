import logging
from pathlib import Path
from fastapi import APIRouter, Depends

from fastapi_app.dependencies import get_search_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# ЭНДПОИНТ: /faiss_status
# ============================================================

@router.get("/faiss_status")
async def faiss_status(
    search_service=Depends(get_search_service)
):
    """Статус FAISS индекса"""
    try:
        status = {
            "faiss_index_path": search_service.faiss_index_path,
            "faiss_vectorstore_loaded": search_service.faiss_vectorstore is not None,
            "resources_by_id_loaded": len(search_service.resources_by_id) > 0,
            "embedding_model_path": search_service.embedding_model_path
        }

        if search_service.faiss_vectorstore:
            status["index_size"] = search_service.faiss_vectorstore.index.ntotal
            status["resources_count"] = len(search_service.resources_by_id)

        if search_service.faiss_index_path:
            import os
            index_dir = Path(search_service.faiss_index_path)
            if index_dir.exists():
                files = []
                for file in index_dir.glob("*"):
                    size_mb = file.stat().st_size / (1024 * 1024)
                    files.append({
                        "name": file.name,
                        "size_mb": round(size_mb, 2)
                    })
                status["index_files"] = files
            else:
                status["index_files_error"] = f"Директория не найдена: {index_dir}"

        return {
            "status": "success",
            "data": status
        }

    except Exception as e:
        logger.error(f"❌ Ошибка получения статуса FAISS: {str(e)}")
        return {
            "status": "error",
            "message": f"Ошибка получения статуса: {str(e)}"
        }