import logging
from flask import Blueprint, request, jsonify

from ..adapters.sqlalchemy_repository import SQLAlchemySearchRepository
from ..infrastructure import get_session

logger = logging.getLogger(__name__)
related_bp = Blueprint('related_api', __name__)


def _build_promo_text(promo_name: str) -> str:
    # Название хранится строчными — capitalize только первой буквы всей строки
    name = promo_name[0].upper() + promo_name[1:] if promo_name else promo_name
    return f"Узнать больше можно здесь: {name}."


@related_bp.route('/objects/related', methods=['POST'])
def get_related_objects():
    try:
        data = request.get_json(silent=True) or {}
        object_ids = data.get('object_ids', [])
        relation_type = data.get('relation_type', 'promo')
        user_query = data.get('user_query', '')

        if not object_ids or not isinstance(object_ids, list):
            return jsonify({'related': []}), 200

        repository = SQLAlchemySearchRepository(get_session)
        related = repository.find_related_objects(object_ids, relation_type)

        for item in related:
            item['promo_text'] = _build_promo_text(item.get('name', ''))

        return jsonify({'related': related}), 200

    except Exception as e:
        logger.error(f"Error in /objects/related: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
