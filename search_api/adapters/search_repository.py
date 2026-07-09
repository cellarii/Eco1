# search_api/adapters/search_repository.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

class SearchRepository(ABC):
    @abstractmethod
    def find_objects_by_criteria(self, criteria, limit=20, offset=0): pass
    @abstractmethod
    def find_resources_by_criteria(self, criteria, object_ids=None, limit=50, offset=0): pass
    @abstractmethod
    def find_place_geometry(self, place_name: str) -> Optional[Dict[str, Any]]: pass
    @abstractmethod
    def get_geometry_type_for_place(self, place_name: str) -> Optional[str]: pass
    @abstractmethod
    def find_objects_with_geometry_by_subtypes(
        self, geometry_geojson: Dict[str, Any], subtypes: List[str],
        buffer_radius_km: float, limit: int, offset: int,
        search_type: str = "near"
    ) -> Tuple[List[Any], List[int]]: pass
    @abstractmethod
    def find_objects_with_geometry_by_criteria(
        self, geometry_geojson: Dict[str, Any], criteria: Any,
        buffer_radius_km: float, limit: int, offset: int,
        search_type: str = "near"
    ) -> Tuple[List[Any], List[int]]: pass