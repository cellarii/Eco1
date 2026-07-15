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

class AttractionsRequest(BaseModel):
    area_name: Optional[str] = None
    area_polygon: Optional[Dict[str, Any]] = None
    attraction_types: Optional[List[str]] = None
    attraction_subtypes: Optional[List[str]] = []
    off_types: Optional[List[str]] = ["biological_entity"]
    buffer_radius_km: Optional[float] = 1.0
    limit: Optional[int] = 50


# ============================================================
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ
# ============================================================

def generate_cache_key(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.md5(canonical).hexdigest()


# ============================================================
# ЭНДПОИНТ: /find_off_near_attractions
# ============================================================

@router.post("/find_off_near_attractions")
async def find_off_near_attractions(
    request: AttractionsRequest,
    debug_mode: bool = Query(False, description="Режим отладки"),
    in_stoplist: int = Query(1, description="Уровень фильтрации безопасности"),
    relational_service=Depends(get_relational_service),
    search_service=Depends(get_search_service),
    geo=Depends(get_geo_service)
):
    logger.info(f"🔍 /find_off_near_attractions - запрос: {request.dict()}")

    data = request.dict()

    # ===== ПАРАМЕТРЫ =====
    area_name = data.get("area_name")
    area_polygon = data.get("area_polygon")
    attraction_types = data.get("attraction_types", [])
    attraction_subtypes = data.get("attraction_subtypes", [])
    off_types = data.get("off_types", ["biological_entity"])
    buffer_radius_km = data.get("buffer_radius_km", 1.0)
    limit = data.get("limit", 50)

    # ===== ВАЛИДАЦИЯ =====
    if not attraction_types:
        return {
            "error": "Необходимо указать attraction_types (типы достопримечательностей)",
            "used_objects": [],
            "not_used_objects": []
        }

    # ===== КЕШ =====
    cache_params = {
        "area_name": area_name,
        "area_polygon": area_polygon,
        "attraction_types": attraction_types,
        "attraction_subtypes": attraction_subtypes,
        "off_types": off_types,
        "buffer_radius_km": buffer_radius_km,
        "limit": limit,
        "in_stoplist": in_stoplist,
        "version": "v1"
    }
    redis_key = f"cache:off_near_attractions:{generate_cache_key(cache_params)}"
    debug_info = {
        "timestamp": time.time(),
        "cache_key": redis_key,
        "steps": []
    }

    debug_info["parameters"] = {
        "area_name": area_name,
        "has_area_polygon": bool(area_polygon),
        "attraction_types": attraction_types,
        "attraction_subtypes": attraction_subtypes,
        "off_types": off_types,
        "buffer_radius_km": buffer_radius_km,
        "limit": limit,
        "in_stoplist": in_stoplist
    }

    try:
        # ===== 1. ПОЛУЧАЕМ ПОЛИГОН ОБЛАСТИ =====
        area_geometry = None
        area_info = None

        if area_name:
            area_result = relational_service.find_area_geometry(area_name)
            if area_result:
                area_geometry = area_result.get("geometry")
                area_info = area_result.get("area_info", {})
                debug_info["steps"].append({
                    "step": "area_search",
                    "found": True,
                    "area_title": area_info.get('title', area_name)
                })
            else:
                debug_info["steps"].append({
                    "step": "area_search",
                    "found": False,
                    "error": f"Area '{area_name}' not found"
                })
        elif area_polygon:
            area_geometry = area_polygon
            debug_info["steps"].append({
                "step": "area_polygon_provided",
                "found": True
            })

        # ===== 2. ИЩЕМ ДОСТОПРИМЕЧАТЕЛЬНОСТИ =====
        attractions = []
        all_attractions = []

        for attraction_type in attraction_types:
            search_object_type = "geographical_entity"
            try:
                if area_geometry:
                    results = relational_service.get_objects_in_area_by_type(
                        area_geometry=area_geometry,
                        object_type=search_object_type,
                        object_subtype=attraction_type if attraction_subtypes else None,
                        limit=limit * 2,
                        search_around=False
                    )
                else:
                    results = relational_service.search_objects_by_name(
                        object_name="",
                        object_type=search_object_type,
                        object_subtype=attraction_type if attraction_subtypes else None,
                        limit=limit * 2
                    )

                if results:
                    for obj in results:
                        obj["attraction_type"] = attraction_type
                    all_attractions.extend(results)

            except Exception as e:
                logger.error(f"Ошибка поиска достопримечательностей типа {attraction_type}: {str(e)}")
                debug_info["steps"].append({
                    "step": f"attraction_search_{attraction_type}",
                    "error": str(e)
                })

        # Фильтруем по подтипам
        if attraction_subtypes:
            filtered_attractions = []
            for obj in all_attractions:
                features = obj.get("features", {})
                geo_type = features.get("geo_type", {})
                specific_types = geo_type.get("specific_types", [])
                if any(subtype in specific_types for subtype in attraction_subtypes):
                    filtered_attractions.append(obj)
            attractions = filtered_attractions[:limit]
        else:
            attractions = all_attractions[:limit]

        debug_info["attraction_search"] = {
            "total_found": len(all_attractions),
            "after_filtering": len(attractions),
            "attraction_types_used": list(set([a.get("attraction_type", "unknown") for a in attractions]))
        }

        if not attractions:
            response = {
                "status": "no_attractions",
                "message": f"В указанной области не найдено достопримечательностей типов: {attraction_types}",
                "used_objects": [],
                "not_used_objects": []
            }
            if debug_mode:
                response["debug"] = debug_info
            return response

        # ===== 3. ИЩЕМ ОФФ ВОКРУГ ДОСТОПРИМЕЧАТЕЛЬНОСТЕЙ =====
        all_off_results = []
        attraction_off_map = {}

        for attraction in attractions:
            attraction_id = attraction.get("id")
            attraction_name = attraction.get("name", "Неизвестная достопримечательность")
            attraction_geojson = attraction.get("geojson")

            if not attraction_geojson:
                continue

            try:
                buffer_geometry = search_service.geo_service.create_buffer_geometry(
                    attraction_geojson,
                    buffer_radius_km
                )

                if not buffer_geometry:
                    continue

                for off_type in off_types:
                    off_results = search_service.geo_service.get_objects_in_polygon(
                        polygon_geojson=buffer_geometry,
                        object_type=off_type,
                        limit=20
                    )

                    if off_results:
                        clipped_results = []
                        for off_obj in off_results:
                            original_geojson = off_obj.get("geojson")
                            if original_geojson and original_geojson.get("type") in ["Polygon", "MultiPolygon"]:
                                try:
                                    from shapely.geometry import shape, mapping
                                    buffer_shape = shape(buffer_geometry)
                                    off_shape = shape(original_geojson)
                                    if buffer_shape.intersects(off_shape):
                                        intersection = buffer_shape.intersection(off_shape)
                                        if not intersection.is_empty:
                                            clipped_geojson = mapping(intersection)
                                            off_obj["geojson"] = clipped_geojson
                                        else:
                                            continue
                                    else:
                                        continue
                                except Exception as e:
                                    logger.warning(f"Ошибка обрезки геометрии ОФФ: {str(e)}")

                            off_obj["near_attraction"] = {
                                "id": attraction_id,
                                "name": attraction_name,
                                "type": attraction.get("attraction_type", "unknown"),
                                "distance_km": buffer_radius_km
                            }
                            clipped_results.append(off_obj)

                        if clipped_results:
                            unique_off = {}
                            for off_obj in clipped_results:
                                key = off_obj.get("id") or f"{off_obj.get('name')}_{off_obj.get('geojson', {}).get('coordinates', [])}"
                                if key not in unique_off:
                                    unique_off[key] = off_obj
                            attraction_off_map.setdefault(attraction_id, []).extend(list(unique_off.values()))
                            all_off_results.extend(list(unique_off.values()))

            except Exception as e:
                logger.error(f"Ошибка поиска ОФФ около {attraction_name}: {str(e)}")
                debug_info["steps"].append({
                    "step": f"off_search_near_{attraction_id}",
                    "error": str(e)
                })

        # ===== 4. ПОДГОТОВКА ДАННЫХ ДЛЯ КАРТЫ =====
        map_objects = []
        used_objects = []
        not_used_objects = []

        if area_geometry:
            map_objects.append({
                'tooltip': f"Область поиска: {area_info.get('title', area_name) if area_name else 'Пользовательский полигон'}",
                'popup': f"<h6>Область поиска</h6><p>Поиск достопримечательностей в этой области</p>",
                'geojson': area_geometry,
                'style': {'color': 'blue', 'fillOpacity': 0.1, 'weight': 2}
            })

        for attraction in attractions:
            attraction_id = attraction.get("id")
            attraction_name = attraction.get("name")
            attraction_type = attraction.get("attraction_type", "unknown")
            geojson = attraction.get("geojson")

            used_objects.append({
                "name": attraction_name,
                "type": "attraction",
                "attraction_type": attraction_type,
                "id": attraction_id
            })

            off_list = attraction_off_map.get(attraction_id, [])
            off_count = len(off_list)

            popup_html = f"<h6>{attraction_name}</h6>"
            popup_html += f"<p>Тип: {attraction_type}</p>"
            if off_count > 0:
                popup_html += f"<p>Найдено ОФФ поблизости: {off_count}</p>"
                popup_html += "<ul>"
                for off in off_list[:5]:
                    popup_html += f"<li>{off.get('name', 'Неизвестный ОФФ')}</li>"
                if off_count > 5:
                    popup_html += f"<li>... и еще {off_count - 5}</li>"
                popup_html += "</ul>"
            else:
                popup_html += "<p>ОФФ поблизости не найдены</p>"

            map_objects.append({
                'tooltip': f"{attraction_name} ({attraction_type})",
                'popup': popup_html,
                'geojson': geojson,
                'style': {'color': 'red', 'fillOpacity': 0.3, 'weight': 3}
            })

            try:
                buffer_geometry = search_service.geo_service.create_buffer_geometry(
                    geojson,
                    buffer_radius_km
                )
                if buffer_geometry:
                    map_objects.append({
                        'tooltip': f"Зона поиска ОФФ вокруг {attraction_name}",
                        'popup': f"<h6>Буферная зона</h6><p>Зона поиска ОФФ в радиусе {buffer_radius_km} км вокруг {attraction_name}</p>",
                        'geojson': buffer_geometry,
                        'style': {'color': 'orange', 'fillOpacity': 0.1, 'weight': 2}
                    })
            except Exception as e:
                logger.warning(f"Не удалось создать буфер для {attraction_name}: {str(e)}")

        off_names = set()
        for off in all_off_results:
            off_name = off.get("name", "Неизвестный ОФФ")
            off_type = off.get("type", "unknown")
            geojson = off.get("geojson")
            near_attraction = off.get("near_attraction", {})

            if off_name not in off_names:
                off_names.add(off_name)
                used_objects.append({
                    "name": off_name,
                    "type": off_type,
                    "near_attraction": near_attraction.get("name")
                })

            popup_html = f"<h6>{off_name}</h6>"
            popup_html += f"<p>Тип: {off_type}</p>"
            if near_attraction:
                popup_html += f"<p>Найден около: {near_attraction.get('name')} ({near_attraction.get('type')})</p>"
                popup_html += f"<p>Расстояние: до {near_attraction.get('distance_km')} км</p>"

            map_objects.append({
                'tooltip': off_name,
                'popup': popup_html,
                'geojson': geojson,
                'style': {'color': 'green', 'fillOpacity': 0.5, 'weight': 2}
            })

        if not map_objects:
            response = {
                "status": "no_results",
                "message": "Не найдено объектов для отображения на карте",
                "used_objects": used_objects,
                "not_used_objects": not_used_objects
            }
            if debug_mode:
                response["debug"] = debug_info
            return response

        # ===== 5. ГЕНЕРАЦИЯ КАРТЫ =====
        map_name = redis_key.replace("cache:", "map_").replace(":", "_")
        map_result = geo.draw_custom_geometries(map_objects, map_name)

        response_data = {
            "map": map_result,
            "statistics": {
                "attractions_found": len(attractions),
                "off_found": len(all_off_results),
                "unique_off_names": len(off_names),
                "attraction_types": list(set([a.get("attraction_type", "unknown") for a in attractions])),
                "buffer_radius_km": buffer_radius_km
            },
            "used_objects": used_objects,
            "not_used_objects": not_used_objects,
            "in_stoplist_filter_applied": True,
            "in_stoplist_level": str(in_stoplist)
        }

        if area_name:
            response_data["answer"] = f"В области '{area_name}' найдено {len(attractions)} достопримечательностей. Около них обнаружено {len(all_off_results)} ОФФ ({len(off_names)} уникальных видов)."
        else:
            response_data["answer"] = f"Найдено {len(attractions)} достопримечательностей. Около них обнаружено {len(all_off_results)} ОФФ ({len(off_names)} уникальных видов)."

        response_data["attractions"] = [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "type": a.get("attraction_type"),
                "off_count": len(attraction_off_map.get(a.get("id"), []))
            }
            for a in attractions[:10]
        ]

        response_data["off_objects"] = [
            {
                "name": off.get("name"),
                "type": off.get("type"),
                "near_attraction": off.get("near_attraction", {}).get("name")
            }
            for off in all_off_results[:20]
        ]

        if debug_mode:
            debug_info["results_summary"] = {
                "total_map_objects": len(map_objects),
                "attraction_off_distribution": {aid: len(offs) for aid, offs in attraction_off_map.items()}
            }
            response_data["debug"] = debug_info

        return response_data

    except Exception as e:
        logger.error(f"Ошибка в /find_off_near_attractions: {str(e)}", exc_info=True)
        return {
            "error": f"Внутренняя ошибка сервера: {str(e)}",
            "used_objects": [],
            "not_used_objects": []
        }