from dataclasses import dataclass
from typing import Optional, List, Any, Dict
from .entities import ObjectCriteria

@dataclass
class PlaceGeometryRequest:
    place_name: str
    buffer_radius_km: float = 10.0

@dataclass
class PlaceGeometryResult:
    geometry: Optional[Dict[str, Any]]
    is_polygon: bool
    geometry_type: str
    place_name: str

@dataclass
class PlaceObjectsQuery:
    geometry: Dict[str, Any]
    subtypes: List[str]
    modality_type: Optional[str]
    buffer_radius_km: float
    limit: int
    offset: int
    search_type: str = "near"
    object_criteria: Optional[ObjectCriteria] = None

@dataclass
class PlaceSearchResponse:
    objects: List[Any]
    resources: List[Any]
    used_geometry: Dict[str, Any]
    total_objects: int