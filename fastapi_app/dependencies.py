from core.coordinates_finder import GeoProcessor
from core.search_service import SearchService
from core.relational_service import RelationalService
from core.geo_service import GeoService
from infrastructure.db_utils_for_search import Slot_validator

from .config import MAPS_DIR, DOMAIN, EMBEDDING_MODEL_PATH, FAISS_INDEX_PATH

# --- Инициализация сервисов ---
geo = GeoProcessor(maps_dir=MAPS_DIR, domain=DOMAIN)
slot_val = Slot_validator()

relational_service = RelationalService()

search_service = SearchService(
    embedding_model_path=EMBEDDING_MODEL_PATH,
    faiss_index_path=FAISS_INDEX_PATH
)
search_service.relational_service = relational_service

# --- Функции для Depends ---
def get_geo_service():
    return geo

def get_search_service():
    return search_service

def get_relational_service():
    return relational_service

def get_slot_validator():
    return slot_val