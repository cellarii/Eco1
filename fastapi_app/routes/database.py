import os
import logging
from fastapi import APIRouter, Depends, Form
from pydantic import BaseModel
from typing import Optional

from fastapi_app.dependencies import get_relational_service
from core.resource_update_service import ResourceUpdateService
from fastapi_app.config import RESOURCES_DIST_PATH, IMAGES_DIR

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Pydantic-схема запроса (Form-data)
# ============================================================

class ReloadDatabaseRequest(BaseModel):
    reload_database: bool = True
    incremental: bool = True


# ============================================================
# ЭНДПОИНТ: /reload_database
# ============================================================

@router.post("/reload_database")
async def reload_database(
    reload_database: bool = Form(True),
    incremental: bool = Form(True)
):
    try:
        logger.info(f"📤 /reload_database - получен запрос")
        logger.info(f"  - reload_database: {reload_database}")
        logger.info(f"  - incremental: {incremental}")

        if not reload_database:
            return {
                "status": "error",
                "message": "Параметр reload_database должен быть true для перезагрузки БД",
                "used_objects": [],
                "not_used_objects": []
            }

        if not os.path.exists(RESOURCES_DIST_PATH):
            logger.error(f"Файл resources_dist.json не найден: {RESOURCES_DIST_PATH}")
            return {
                "status": "error",
                "message": "Файл resources_dist.json не найден",
                "used_objects": [],
                "not_used_objects": []
            }

        service = ResourceUpdateService(RESOURCES_DIST_PATH, IMAGES_DIR)
        results = service.reload_database_only(
            reload_database=reload_database,
            incremental=incremental
        )

        response_data = {
            "status": "success",
            "message": "База данных успешно перезагружена",
            "results": results,
            "used_objects": [
                {
                    "name": "resources_dist.json",
                    "type": "configuration",
                    "operation": "read"
                }
            ],
            "not_used_objects": []
        }

        if not results.get("database_reloaded", False):
            response_data["status"] = "error"
            response_data["message"] = "Ошибка при перезагрузке базы данных"
            if results.get("errors"):
                response_data["message"] += f": {', '.join(results['errors'])}"

        logger.info(f"✅ Перезагрузка БД завершена: {results}")
        return response_data

    except Exception as e:
        logger.error(f"❌ Ошибка в /reload_database: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "message": f"Внутренняя ошибка сервера: {str(e)}",
            "used_objects": [],
            "not_used_objects": []
        }