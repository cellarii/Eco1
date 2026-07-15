import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List

from search_api.adapters.sqlalchemy_repository import SQLAlchemySearchRepository
from search_api.infrastructure import get_session, init_db
from search_api.config import SearchConfig

logger = logging.getLogger(__name__)
router = APIRouter()


class RelatedRequest(BaseModel):
    object_ids: Optional[List[int]] = None
    relation_type: Optional[str] = "promo"
    user_query: Optional[str] = ""


def _build_promo_text(promo_name: str) -> str:
    name = promo_name[0].upper() + promo_name[1:] if promo_name else promo_name
    return f"Узнать больше можно здесь: {name}."


@router.post("/objects/related")
async def get_related_objects(request_data: RelatedRequest):
    try:
        # ← ИНИЦИАЛИЗИРУЕМ БД ПЕРЕД ИСПОЛЬЗОВАНИЕМ
        config = SearchConfig.from_env()
        init_db(config)
        
        data = request_data.dict()
        
        object_ids = data.get('object_ids', [])
        relation_type = data.get('relation_type', 'promo')
        user_query = data.get('user_query', '')

        if not object_ids or not isinstance(object_ids, list):
            return {'related': []}

        repository = SQLAlchemySearchRepository(get_session)
        related = repository.find_related_objects(object_ids, relation_type)

        for item in related:
            item['promo_text'] = _build_promo_text(item.get('name', ''))

        return {'related': related}

    except Exception as e:
        logger.error(f"Error in /objects/related: {e}", exc_info=True)
        return {'error': str(e)}