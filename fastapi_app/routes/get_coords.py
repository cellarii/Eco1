import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from fastapi_app.dependencies import get_geo_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Pydantic-схема запроса
# ============================================================

class GetCoordsRequest(BaseModel):
    name: str


# ============================================================
# ЭНДПОИНТ: /get_coords
# ============================================================

@router.post("/get_coords")
async def get_coords(
    request: GetCoordsRequest,
    geo=Depends(get_geo_service)
):
    name = request.name

    logger.info(f"🔍 /get_coords - получен запрос:")
    logger.info(f"   - name: {name}")

    if not name:
        return {
            "status": "error",
            "message": "Параметр 'name' обязателен.",
            "used_objects": [],
            "not_used_objects": []
        }

    result = geo.get_point_coords_from_geodb(name)

    used_objects = []
    not_used_objects = []

    if result.get("status") == "ok":
        used_objects.append({
            "name": name,
            "type": "geographical_entity"
        })
    else:
        not_used_objects.append({
            "name": name,
            "type": "geographical_entity"
        })

    result["used_objects"] = used_objects
    result["not_used_objects"] = not_used_objects

    return result