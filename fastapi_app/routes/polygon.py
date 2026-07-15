import json
import logging
import time
import hashlib
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from fastapi_app.dependencies import get_search_service, get_relational_service, get_geo_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Pydantic-схема запроса
# ============================================================

class PolygonRequest(BaseModel):
    name: str
    buffer_radius_km: Optional[float] = 0
    object_type: Optional[str] = None
    object_subtype: Optional[str] = None
    limit: Optional[int] = 1500


# ============================================================
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ (как в Flask)
# ============================================================

def generate_cache_key(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.md5(canonical).hexdigest()


# ============================================================
# ЭНДПОИНТ: /objects_in_polygon_simply
# ============================================================

@router.post("/objects_in_polygon_simply")
async def objects_in_polygon_simply(
    request: PolygonRequest,
    debug_mode: bool = Query(False, description="Режим отладки"),
    in_stoplist: int = Query(1, description="Уровень фильтрации безопасности"),
    relational_service=Depends(get_relational_service),
    search_service=Depends(get_search_service),
    geo=Depends(get_geo_service)
):
    logger.info(f"📦 /objects_in_polygon_simply - POST data: {request.dict()}")

    data = request.dict()
    name = data.get("name")
    buffer_radius_km = data.get("buffer_radius_km", 0)
    object_type = data.get("object_type")
    object_subtype = data.get("object_subtype")
    limit = data.get("limit", 1500)

    # ===== КЕШИРОВАНИЕ =====
    cache_params = {
        "name": name,
        "buffer_radius_km": buffer_radius_km,
        "object_type": object_type,
        "object_subtype": object_subtype,
        "limit": limit,
        "in_stoplist": in_stoplist,
        "version": "v2"
    }
    redis_key = f"cache:polygon_simply:{generate_cache_key(cache_params)}"
    debug_info = {
        "timestamp": time.time(),
        "cache_key": redis_key,
        "steps": []
    }

    # TODO: cache check

    debug_info["parameters"] = {
        "name": name,
        "buffer_radius_km": buffer_radius_km,
        "object_type": object_type,
        "object_subtype": object_subtype,
        "limit": limit,
        "in_stoplist": in_stoplist
    }

    # ===== РАЗРЕШЕНИЕ СИНОНИМА =====
    try:
        resolved_info = search_service.resolve_object_synonym(name, "all")
        if resolved_info.get("resolved", False):
            canonical_name = resolved_info["main_form"]
            logger.debug(f"Найден синоним: '{name}' -> '{canonical_name}' (тип: {resolved_info['object_type']})")
            name = canonical_name
            debug_info["steps"].append({
                "step": "universal_synonym_resolution",
                "original_name": data.get("name"),
                "canonical_name": canonical_name,
                "object_type": resolved_info["object_type"],
                "resolved_info": resolved_info
            })
    except Exception as e:
        logger.warning(f"Ошибка при проверке синонимов для '{name}': {e}")
        debug_info["steps"].append({
            "step": "synonym_resolution",
            "error": str(e)
        })

    # ===== ПОИСК ГЕОМЕТРИИ =====
    entry = relational_service.find_geometry(name)
    if not entry or "geometry" not in entry:
        from infrastructure.geo_db_store import find_place_flexible
        flexible_result = find_place_flexible(name)
        if flexible_result and flexible_result.get("status") == "found":
            entry = flexible_result["record"]
            logger.debug(f"Найдено через гибкий поиск: '{name}' -> '{flexible_result['name']}'")
            debug_info["steps"].append({
                "step": "flexible_search",
                "found_name": flexible_result['name'],
                "original_name": name
            })
        else:
            logger.debug(f"Геометрия для '{name}' не найдена")
            response = {"error": f"Геометрия для '{name}' не найдена"}
            if debug_mode:
                response["debug"] = debug_info
            return response

    polygon = entry["geometry"]
    debug_info["geometry_source"] = {
        "source": "database" if entry else "flexible_search",
        "entry_id": entry.get("id", "unknown") if entry else "unknown"
    }

    if not polygon:
        response = {"error": "Polygon not specified"}
        if debug_mode:
            response["debug"] = debug_info
        return response

    # ===== ПОИСК ОБЪЕКТОВ В ПОЛИГОНЕ =====
    try:
        results = search_service.get_objects_in_polygon(
            polygon_geojson=polygon,
            buffer_radius_km=float(buffer_radius_km),
            object_type=object_type,
            object_subtype=object_subtype,
            limit=int(limit)
        )

        objects = results.get("objects", [])
        answer = results.get("answer", "")

        total_objects_before = len(objects)
        biological_objects_before = [obj for obj in objects if obj.get('type') in ['Объект флоры', 'Объект фауны']]
        biological_names_before = list(set(obj.get('name', 'Без имени') for obj in biological_objects_before))

        debug_info["before_filter"] = {
            "total_objects": total_objects_before,
            "biological_objects_count": len(biological_objects_before),
            "biological_names": biological_names_before
        }

        # ===== ФИЛЬТРАЦИЯ ПО in_stoplist =====
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
        total_objects_after = len(objects)
        biological_objects_after = [obj for obj in objects if obj.get('type') in ['Объект флоры', 'Объект фауны']]
        biological_names_after = list(set(obj.get('name', 'Без имени') for obj in biological_objects_after))
        all_biological_names = sorted(biological_names_after)

        if stoplisted_objects:
            answer = f"{answer} (исключено {len(stoplisted_objects)} объектов по уровню безопасности)"

        debug_info["stoplist_filter"] = {
            "total_before_filter": total_objects_before,
            "safe_after_filter": total_objects_after,
            "stoplisted_count": len(stoplisted_objects),
            "biological_before_filter": len(biological_objects_before),
            "biological_after_filter": len(biological_objects_after),
            "biological_names_before": biological_names_before,
            "biological_names_after": biological_names_after
        }

        if not objects:
            response = {
                "status": "no_objects",
                "message": answer,
                "used_objects": [],
                "not_used_objects": [],
                "all_biological_names": []
            }
            if debug_mode:
                response["debug"] = debug_info
                response["in_stoplist_filter_applied"] = True
                response["in_stoplist_level"] = in_stoplist
            return response

        # ===== ГРУППИРОВКА ПО ГЕОМЕТРИИ =====
        grouped_by_geojson = {}
        for obj in objects:
            if 'geojson' not in obj or not obj['geojson']:
                continue
            geojson_key = json.dumps(obj['geojson'], sort_keys=True)
            if geojson_key not in grouped_by_geojson:
                grouped_by_geojson[geojson_key] = {
                    'geojson': obj['geojson'],
                    'biological_names': []
                }
            name_obj = obj.get('name', 'Без имени')
            if name_obj not in grouped_by_geojson[geojson_key]['biological_names']:
                grouped_by_geojson[geojson_key]['biological_names'].append(name_obj)

        # ===== ПОДГОТОВКА КАРТЫ =====
        objects_for_map = []
        used_objects = []
        not_used_objects = []

        for group_data in grouped_by_geojson.values():
            biological_names = sorted(group_data['biological_names'])
            used_objects.append({
                "name": ", ".join(biological_names[:3]) + ("..." if len(biological_names) > 3 else ""),
                "type": "biological_entity"
            })
            if len(biological_names) > 3:
                tooltip_text = f"{len(biological_names)} вида"
            else:
                tooltip_text = ", ".join(biological_names)
            popup_html = f"<h6>Найдено видов: {len(biological_names)}</h6><ul>"
            for name in biological_names[:10]:
                popup_html += f"<li>{name}</li>"
            if len(biological_names) > 10:
                popup_html += f"<li>... и еще {len(biological_names) - 10}</li>"
            popup_html += "</ul>"
            objects_for_map.append({
                'tooltip': tooltip_text,
                'popup': popup_html,
                'geojson': group_data['geojson']
            })

        map_name = redis_key.replace("cache:", "map_").replace(":", "_")
        map_result = geo.draw_custom_geometries(objects_for_map, map_name)

        map_result["count"] = total_objects_after
        map_result["answer"] = answer
        map_result["grouped_names"] = [obj.get("tooltip", "") for obj in objects_for_map]
        map_result["all_biological_names"] = all_biological_names
        map_result["used_objects"] = used_objects
        map_result["not_used_objects"] = not_used_objects
        map_result["in_stoplist_filter_applied"] = True
        map_result["in_stoplist_level"] = in_stoplist
        map_result["stoplisted_count"] = len(stoplisted_objects)

        if debug_mode:
            debug_info["visualization"] = {
                "map_name": map_name,
                "objects_count": len(objects_for_map),
                "biological_names_count": len(all_biological_names)
            }
            map_result["debug"] = debug_info

        # TODO: set_cached_result(redis_key, map_result, expire_time=2700)
        return map_result

    except Exception as e:
        logger.error(f"Ошибка при поиске объектов в полигоне: {e}")
        debug_info["search_error"] = str(e)
        response = {"error": "Внутренняя ошибка сервера при поиске"}
        if debug_mode:
            response["debug"] = debug_info
        return response