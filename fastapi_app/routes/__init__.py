from .search import router as search_router
from .place import router as place_router
from .related import router as related_router
from .description import router as description_router
from .coordinates import router as coordinates_router
from .area import router as area_router
from .images import router as images_router
from .get_coords import router as get_coords_router
from .species import router as species_router
from .faiss import router as faiss_router
from .test_faiss_search import router as test_faiss_router
from .error_log import router as error_log_router
from .polygon import router as polygon_router
from .attractions import router as attractions_router

all_routers = [
    search_router,
    place_router,
    related_router,
    description_router,
    coordinates_router,
    area_router,
    images_router,
    get_coords_router,
    species_router,
    faiss_router,
    test_faiss_router,
    error_log_router,
    polygon_router,
    attractions_router,
]