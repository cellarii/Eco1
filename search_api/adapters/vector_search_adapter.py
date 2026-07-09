import sys
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.search_service import SearchService

from ..domain.ports import VectorSearchPort

class VectorSearchAdapter(VectorSearchPort):
    def __init__(self, embedding_model_path: str, faiss_index_path: str):
        self._embedding_model_path = embedding_model_path
        self._faiss_index_path = faiss_index_path
        self._search_service = None

    def _get_service(self) -> SearchService:
        if self._search_service is None:
            self._search_service = SearchService(
                embedding_model_path=self._embedding_model_path,
                faiss_index_path=self._faiss_index_path
            )
        return self._search_service

    def search(self, query: str, object_type: str, limit: int) -> List[Dict[str, Any]]:
        service = self._get_service()
        service.faiss_index_path = self._faiss_index_path
        service.load_faiss_index()
        
        return service.vector_search_fallback(
            query=query,
            object_type=object_type,
            similarity_threshold=0.03,
            limit=limit,
            rerank_top_k=20
        )