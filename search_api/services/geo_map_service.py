# search_api/services/geo_map_service.py
import os
import time
import logging
from typing import Dict, Any, List, Tuple

import folium
from staticmap import StaticMap, CircleMarker, Polygon as StaticMapPolygon, Line
from shapely.geometry import shape, GeometryCollection, mapping
from shapely.geometry.base import BaseGeometry
from ..domain.value_objects import GeoContent, MapLinks

logger = logging.getLogger(__name__)


class GeoMapService:
    def __init__(self, maps_dir: str, domain: str):
        self.maps_dir = maps_dir
        self.domain = domain
        os.makedirs(maps_dir, exist_ok=True)

    def generate_static_map(self, geojson: Dict[str, Any], name: str) -> str:
        start = time.time()
        geom = shape(geojson)
        static_url, _ = self._draw_geometry(geom, name)
        elapsed = time.time() - start
        logger.info(f"generate_static_map '{name}' took {elapsed:.4f}s")
        return static_url

    def enrich_geo_content(self, geojson: Dict[str, Any], name: str) -> GeoContent:
        start = time.time()
        static_url = self.generate_static_map(geojson, name)
        interactive_url = self.generate_interactive_map(geojson, name)
        geom = shape(geojson)
        elapsed = time.time() - start
        logger.info(f"enrich_geo_content '{name}' took {elapsed:.4f}s")
        return GeoContent(
            geojson=geojson,
            geometry_type=geom.geom_type,
            map_links=MapLinks(static=static_url, interactive=interactive_url)
        )

    def _add_geom_to_static(self, m: StaticMap, geom: BaseGeometry) -> None:
        gt = geom.geom_type
        if gt == 'Point':
            m.add_marker(CircleMarker((geom.x, geom.y), '#E53935', 14))
        elif gt == 'LineString':
            m.add_line(Line(list(geom.coords), '#1565C0', 3))
        elif gt == 'Polygon':
            m.add_polygon(StaticMapPolygon(
                list(geom.exterior.coords),
                (26, 115, 232, 70),
                '#1565C0',
                2
            ))
        elif gt in ('MultiPolygon', 'MultiLineString', 'MultiPoint', 'GeometryCollection'):
            for sub in geom.geoms:
                self._add_geom_to_static(m, sub)

    def _draw_geometry(self, geometry: BaseGeometry, name: str) -> Tuple[str, str]:
        draw_start = time.time()
        if isinstance(geometry, GeometryCollection):
            geometries = list(geometry.geoms)
        else:
            geometries = [geometry]

        m_static = StaticMap(
            1024, 768,
            url_template='https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
            tile_size=512
        )
        for geom in geometries:
            self._add_geom_to_static(m_static, geom)

        last_err = None
        for attempt in range(3):
            try:
                image = m_static.render()
                break
            except Exception as e:
                last_err = e
                logger.warning(f"_draw_geometry '{name}': tile download failed (attempt {attempt + 1}/3): {e}")
                time.sleep(0.5)
        else:
            raise last_err

        filename = f"{name}.png"
        filepath = os.path.join(self.maps_dir, filename)
        image.save(filepath, format='PNG', optimize=True)

        elapsed = time.time() - draw_start
        logger.info(f"_draw_geometry (static) '{name}' took {elapsed:.4f}s")
        return f"{self.domain}/maps/{filename}", None

    def generate_interactive_map(self, geojson: Dict[str, Any], name: str) -> str:
        start = time.time()
        geom = shape(geojson)
        centroid = geom.centroid

        m = folium.Map(
            location=[centroid.y, centroid.x],
            zoom_start=9,
            tiles='CartoDB Voyager',
            attributionControl=False,
        )
        folium.GeoJson(
            mapping(geom),
            tooltip=name,
            name=name,
            style_function=lambda x: {
                'fillColor': '#1a73e8',
                'color': '#0d47a1',
                'weight': 2,
                'fillOpacity': 0.3,
            }
        ).add_to(m)

        filename = f"webapp_{name}.html"
        filepath = os.path.join(self.maps_dir, filename)
        m.save(filepath)

        elapsed = time.time() - start
        logger.info(f"generate_interactive_map '{name}' took {elapsed:.4f}s")
        return f"{self.domain}/maps/{filename}"

    def draw_custom_geometries(self, objects: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
        start = time.time()

        if not objects:
            logger.warning("draw_custom_geometries: no objects")
            return {"status": "error", "message": "Нет объектов для отрисовки"}

        geometries = []
        tooltips = []
        popups = []

        for obj in objects:
            geojson = obj.get("geojson")
            if not geojson:
                continue
            geom = shape(geojson)
            geometries.append(geom)
            tooltips.append(obj.get("tooltip", obj.get("name", "Без имени")))
            popups.append(obj.get("popup", obj.get("name", "Без имени")))

        if not geometries:
            logger.warning("draw_custom_geometries: no valid geometries")
            return {"status": "error", "message": "Нет валидных геометрий"}

        combined = GeometryCollection(geometries)
        static_map, _ = self._draw_geometry(combined, name)

        centroid = combined.centroid
        m = folium.Map(
            location=[centroid.y, centroid.x],
            zoom_start=9,
            tiles='CartoDB Voyager',
            attributionControl=False
        )

        for geom, tooltip_text, popup_html in zip(geometries, tooltips, popups):
            folium.GeoJson(
                mapping(geom),
                tooltip=tooltip_text,
                popup=folium.Popup(popup_html, max_width=400),
                style_function=lambda x: {
                    'fillColor': '#1a73e8',
                    'color': '#0d47a1',
                    'weight': 2,
                    'fillOpacity': 0.3,
                }
            ).add_to(m)

        filename_html = f"webapp_{name}.html"
        filepath_html = os.path.join(self.maps_dir, filename_html)
        m.save(filepath_html)
        interactive_map_url = f"{self.domain}/maps/{filename_html}"

        elapsed = time.time() - start
        logger.info(f"draw_custom_geometries '{name}' took {elapsed:.4f}s")
        return {"static_map": static_map, "interactive_map": interactive_map_url}