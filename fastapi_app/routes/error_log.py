import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any

from fastapi_app.dependencies import get_relational_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Pydantic-схема запроса
# ============================================================

class LogErrorRequest(BaseModel):
    user_query: Optional[str] = ""
    error_message: str
    context: Optional[Dict[str, Any]] = {}
    additional_info: Optional[Dict[str, Any]] = {}


# ============================================================
# ЭНДПОИНТ: /log_error
# ============================================================

@router.post("/log_error")
async def log_error(
    request: LogErrorRequest,
    relational_service=Depends(get_relational_service)
):
    try:
        user_query = request.user_query
        error_message = request.error_message
        context = request.context
        additional_info = request.additional_info

        if not error_message:
            return {
                "status": "error",
                "message": "Обязательное поле 'error_message' отсутствует",
                "used_objects": [],
                "not_used_objects": []
            }

        success, error_id, message = relational_service.log_error_to_db(
            user_query=user_query,
            error_message=error_message,
            context=context,
            additional_info=additional_info
        )

        if success:
            return {
                "status": "success",
                "message": message,
                "error_id": error_id,
                "used_objects": [],
                "not_used_objects": []
            }
        else:
            return {
                "status": "error",
                "message": message,
                "used_objects": [],
                "not_used_objects": []
            }

    except Exception as e:
        logger.error(f"❌ Ошибка обработки запроса /log_error: {str(e)}")
        return {
            "status": "error",
            "message": f"Ошибка обработки запроса: {str(e)}",
            "used_objects": [],
            "not_used_objects": []
        }