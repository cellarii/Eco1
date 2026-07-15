import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from infrastructure.db_utils_for_search import Slot_validator

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Pydantic-схема запроса
# ============================================================

class SpeciesRequest(BaseModel):
    name: str
    limit: Optional[int] = 1500
    offset: Optional[int] = 0


# ============================================================
# ЭНДПОИНТ: /find_species_with_description
# ============================================================

@router.post("/find_species_with_description")
async def find_species_with_description(
    request: SpeciesRequest
):
    name = request.name
    limit = request.limit
    offset = request.offset

    logger.info(f"POST /find_species_with_description - name: {name}, limit: {limit}, offset: {offset}")

    if not name:
        return {
            "status": "error",
            "message": "Параметр 'name' обязателен",
            "used_objects": [],
            "not_used_objects": []
        }

    slot_val = Slot_validator()
    result = slot_val.find_species_with_description(name, limit, offset)

    used_objects = []
    not_used_objects = []

    if result.get("status") == "success" and result.get("results"):
        for species in result["results"]:
            used_objects.append({
                "name": species.get("name", name),
                "type": "biological_entity"
            })
    else:
        not_used_objects.append({
            "name": name,
            "type": "biological_entity"
        })

    result["used_objects"] = used_objects
    result["not_used_objects"] = not_used_objects

    return result