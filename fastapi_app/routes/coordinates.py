import logging
import hashlib
import json
from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
from typing import Optional

from fastapi_app.dependencies import get_search_service, get_geo_service

logger = logging.getLogger(__name__)
router = APIRouter()


class CoordsRequest(BaseModel):
    latitude: float
    longitude: float
    radius_km: Optional[float] = 30.0
    object_type: Optional[str] = None
    species_name: Optional[str] = None


@router.post("/coords_to_map")
async def coords_to_map(
    request: CoordsRequest,
    debug_mode: bool = Query(False, description="Режим отладки"),
    in_stoplist: int = Query(1, description="Уровень фильтрации безопасности"),
    search_service=Depends(get_search_service),
    geo=Depends(get_geo_service)
):
    try:
        latitude = request.latitude
        longitude = request.longitude
        radius_km = request.radius_km
        object_type = request.object_type
        species_name = request.species_name

        if latitude is None or longitude is None:
            return {"error": "latitude and longitude are required"}

        # ===== ГЕНЕРАЦИЯ REDIS_KEY (КАК В FLASK) =====
        cache_params = {
            "latitude": latitude,
            "longitude": longitude,
            "radius_km": radius_km,
            "object_type": object_type,
            "species_name": species_name,
            "in_stoplist": in_stoplist,
            "version": "v2"
        }
        cache_key_raw = json.dumps(cache_params, sort_keys=True, ensure_ascii=False).encode('utf-8')
        cache_hash = hashlib.md5(cache_key_raw).hexdigest()
        redis_key = f"cache:coords_search:{cache_hash}"
        map_name = redis_key.replace("cache:", "map_").replace(":", "_")
        # ===============================================

        # ===== РАЗРЕШЕНИЕ СИНОНИМА (КАК В FLASK) =====
        resolved_species_info = None
        if species_name:
            resolved_species_info = search_service.resolve_object_synonym(species_name, "biological_entity")
            if resolved_species_info.get("resolved", False):
                species_name = resolved_species_info["main_form"]
        # ===============================================

        result = search_service.get_nearby_objects(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            object_type=object_type,
            species_name=species_name,
            in_stoplist=in_stoplist
        )

        objects = result.get("objects", [])
        answer = result.get("answer", "")

        if not objects:
            return {
                "status": "no_objects",
                "message": answer,
                "used_objects": [],
                "not_used_objects": []
            }

        # ===== ФИЛЬТРАЦИЯ ПО in_stoplist (КАК В FLASK) =====
        safe_objects = []
        stoplisted_objects = []

        for obj in objects:
            feature_data = obj.get("features", {})
            obj_in_stoplist = feature_data.get("in_stoplist")

            try:
                requested_level = int(in_stoplist)
                if obj_in_stoplist is None or int(obj_in_stoplist) <= requested_level:
                    safe_objects.append(obj)
                else:
                    stoplisted_objects.append(obj)
            except (ValueError, TypeError):
                if obj_in_stoplist is None or int(obj_in_stoplist) <= 1:
                    safe_objects.append(obj)
                else:
                    stoplisted_objects.append(obj)

        objects = safe_objects

        if not objects:
            return {
                "status": "no_objects",
                "message": answer,
                "used_objects": [],
                "not_used_objects": []
            }

        # ===== ФОРМИРОВАНИЕ used_objects (КАК В FLASK) =====
        used_objects = []
        not_used_objects = []

        for obj in objects:
            used_objects.append({
                "name": obj.get("name", "Без имени"),
                "type": obj.get("type", "unknown"),
                "distance_km": obj.get("distance", "0 км"),
                "geometry_type": "unknown"
            })

        # ===== ВЫЗОВ draw_custom_geometries С УНИКАЛЬНЫМ ИМЕНЕМ =====
        map_result = geo.draw_custom_geometries(objects, map_name)

        # ===== ДОБАВЛЕНИЕ ВСЕХ ПОЛЕЙ (КАК В FLASK) =====
        map_result["count"] = len(objects)
        map_result["answer"] = answer
        map_result["names"] = [obj.get("name", "Без имени") for obj in objects]
        map_result["used_objects"] = used_objects
        map_result["not_used_objects"] = not_used_objects
        map_result["in_stoplist_filter_applied"] = True
        map_result["in_stoplist_level"] = in_stoplist
        map_result["stoplisted_count"] = len(stoplisted_objects)

        if resolved_species_info and resolved_species_info.get("resolved", False):
            map_result["species_synonym_resolution"] = {
                "original_name": resolved_species_info["original_name"],
                "resolved_name": species_name,
                "resolved": True
            }

        if debug_mode:
            map_result["debug"] = {"message": "Debug mode enabled"}

        return map_result

    except Exception as e:
        logger.error(f"Error in /coords_to_map: {e}", exc_info=True)
        return {
            "error": str(e),
            "objects": [],
            "used_objects": [],
            "not_used_objects": []
        }