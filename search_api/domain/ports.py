from abc import ABC, abstractmethod
from typing import List, Dict, Any

class VectorSearchPort(ABC):
    @abstractmethod
    def search(self, query: str, object_type: str, limit: int) -> List[Dict[str, Any]]:
        pass