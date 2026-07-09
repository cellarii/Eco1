# services/geodata_provider.py

import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List


class GeodataProvider:
    
    _GEOMETRY_TYPE_MAP = {
        'точка': 'Point',
        'Point': 'Point',
        'линия': 'LineString',
        'LineString': 'LineString',
        'полигон': 'Polygon',
        'Polygon': 'Polygon',
        'мультиполигон': 'MultiPolygon',
        'MultiPolygon': 'MultiPolygon',
        'мультиточка': 'MultiPoint',
        'MultiPoint': 'MultiPoint',
        'мультилиния': 'MultiLineString',
        'MultiLineString': 'MultiLineString',
    }
    
    def __init__(self, geodb_path: Path):
        self._geodb: Dict[str, Any] = self._load_geodb(geodb_path)
    
    def _load_geodb(self, path: Path) -> Dict[str, Any]:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load geodb.json: {e}")
            return {}
    
    def _normalize_geometry_type(self, geom_type: str) -> str:
        if not geom_type:
            return 'Point'
        normalized = self._GEOMETRY_TYPE_MAP.get(geom_type, geom_type)
        return normalized
    
    def get_geometry(self, geodb_id: str) -> Optional[Tuple[Dict[str, Any], str]]:
        if geodb_id in self._geodb:
            data = self._geodb[geodb_id]
            geometry = data.get('geometry')
            if geometry:
                geom_type = geometry.get('type', 'Point')
                normalized_type = self._normalize_geometry_type(geom_type)
                return (geometry, normalized_type)
        
        for key, data in self._geodb.items():
            if key.lower() == geodb_id.lower():
                geometry = data.get('geometry')
                if geometry:
                    geom_type = geometry.get('type', 'Point')
                    normalized_type = self._normalize_geometry_type(geom_type)
                    return (geometry, normalized_type)
        
        return None
    
    def get_all_geometries(self) -> List[Tuple[str, Dict[str, Any], str]]:
        result = []
        for key, data in self._geodb.items():
            geometry = data.get('geometry')
            if geometry:
                geom_type = geometry.get('type', 'Point')
                normalized_type = self._normalize_geometry_type(geom_type)
                result.append((key, geometry, normalized_type))
        return result
    
    def get_geometry_by_name(self, name: str) -> Optional[Tuple[Dict[str, Any], str]]:
        name_lower = name.lower().strip()
        
        for key, data in self._geodb.items():
            data_name = data.get('name', '').lower()
            if data_name == name_lower or key.lower() == name_lower:
                geometry = data.get('geometry')
                if geometry:
                    geom_type = geometry.get('type', 'Point')
                    normalized_type = self._normalize_geometry_type(geom_type)
                    return (geometry, normalized_type)
        
        return None
    
    def get_geo_object_names(self) -> List[str]:
        names = set()
        for key, data in self._geodb.items():
            name = data.get('name')
            if name:
                names.add(name)
            names.add(key)
        return sorted(list(names))
    
    def has_geometry(self, geodb_id: str) -> bool:
        return self.get_geometry(geodb_id) is not None
    
    def get_geodb_keys(self) -> List[str]:
        return list(self._geodb.keys())
    
    def get_geodb_size(self) -> int:
        return len(self._geodb)