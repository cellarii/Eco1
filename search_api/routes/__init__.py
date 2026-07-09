from .search import search_bp
from .place_search import place_search_bp
from .related import related_bp

all_blueprints = [
    search_bp,
    place_search_bp,
    related_bp,
]

__all__ = ['all_blueprints', 'search_bp', 'place_search_bp', 'related_bp']
