import logging # модуль для логирования
from fastapi import APIRouter, Depends, Request # APIRouter для создания роутера
# Depends для внедрения зависимостей
# Request для доступа к объекту запроса
from pydantic import BaseModel # базовый класс для создания схем данных
from typing import Optional, Dict, Any, List

# импорты из search_api
from search_api.config import SearchConfig # класс с настройками (БД, Redis, пути)
from search_api.use_cases import SearchUseCase, SearchAndBuildUseCase # SearchUseCase - основная логика поиска
# SearchAndBuildUseCase - логика поиска, построение ответа, кэширование
from search_api.domain.entities import SearchRequest, ObjectCriteria, ResourceCriteria # SearchRequest - класс-контейнер для параметров запроса
# ObjectCriteria - класс с критериями поиска объектов (имя, тип, свойства)
# ResourceCriteria - класс с критериями поиска ресурсов (автор, источник, модальность)
from search_api.services import GeoMapService, LLMAnswerGenerator, ResponseBuilder
# GeoMapService - сервис для генерации карт
# LLMAnswerGenerator - сервис для генерации ответов через llm
# ResponseBuilder - сервис для построения конечного json-ответа
from search_api.infrastructure import RedisCache, init_db, get_session
# RedisCache - класс для работы с Redis (кэширование)
# init_db - функция инициализации подключения к БД
# get_session - функция для подключения сессии БД
from search_api.adapters.sqlalchemy_repository import SQLAlchemySearchRepository # репозиторий для работы с БД через SQLAlchemy
from search_api.adapters.vector_search_adapter import VectorSearchAdapter # адаптер для векторго поиска (faiss)

logger = logging.getLogger(__name__)
router = APIRouter() # экземпляр роутера (в него буду добавляться жндпоинты)

# Pydеntic-схемы
class ObjectIdentificator(BaseModel):
    db_id: Optional[str] = None
 
class ObjectSchema(BaseModel):
    identificator: Optional[ObjectIdentificator] = None
    name_synonyms: Optional[Dict[str, List[str]]] = None
    properties: Optional[Dict[str, Any]] = None
    object_type: Optional[str] = None

class ResourceIdentificator(BaseModel):
    uri: Optional[str] = None

class ResourceBibliographic(BaseModel):
    author: Optional[str] = None
    source: Optional[str] = None

class ResourceModalityValue(BaseModel):
    structured_data: Optional[Dict[str, Any]] = None

class ResourceModality(BaseModel):
    type: Optional[str] = None
    value: Optional[ResourceModalityValue] = None

class ResourceSchema(BaseModel):
    title: Optional[str] = None
    identificator: Optional[ResourceIdentificator] = None
    bibliographic: Optional[ResourceBibliographic] = None
    modality: Optional[ResourceModality] = None
    features: Optional[Dict[str, Any]] = None

class SearchParameters(BaseModel):
    modality_type: Optional[str] = "Текст"
    object: Optional[ObjectSchema] = None
    resource: Optional[ResourceSchema] = None

class SystemParameters(BaseModel):
    user_query: Optional[str] = None
    clean_user_query: Optional[str] = None
    limit: int = 20
    offset: int = 0
    debug: bool = False
    use_llm_answer: bool = False

class SearchRequestSchema(BaseModel):
    system_parameters: SystemParameters
    search_parameters: SearchParameters


@router.post("/search") # регистрации функции как post эндпоинта по адресу /search
async def search(request_data: SearchRequestSchema):
    logger.info("Search endpoint called")
    
    try:
        data = request_data.dict()
        
        if not data:
            data = {}

        # параметры системы
        sys_params = data.get('system_parameters', {})
        limit = sys_params.get('limit', data.get('limit', 20))
        offset = sys_params.get('offset', data.get('offset', 0))
        debug = sys_params.get('debug', data.get('debug', False))
        use_llm = sys_params.get('use_llm_answer', data.get('use_llm_answer', False))
        user_query = sys_params.get('user_query', data.get('user_query'))
        clean_user_query = sys_params.get('clean_user_query', data.get('clean_user_query'))

        # 
        search_params = data.get('search_parameters', data)
        # если модальность не указана, то текст по умолчанию
        if search_params.get('modality_type') is None:
            search_params['modality_type'] = "Текст"
        
        logger.info(f"Modality type after default: {search_params.get('modality_type')}")
        
        object_criteria = None
        if search_params.get('object'):
            obj = search_params['object']
            object_criteria = ObjectCriteria(
                db_id=obj.get('identificator', {}).get('db_id') if obj.get('identificator') else None,
                name_synonyms=obj.get('name_synonyms'),
                properties=obj.get('properties'),
                object_type=obj.get('object_type')
            )
        
        logger.info(f"Object criteria properties: {object_criteria.properties if object_criteria else None}")
        
        # проверяет, есть ли в запросе раздел resource
        resource_criteria = None
        # Если есть, достаем его в переменную res
        if search_params.get('resource'):
            res = search_params['resource']
            features = None
            if res.get('features'):
                fd = res['features']
                if isinstance(fd, dict):
                    features = fd
                elif isinstance(fd, list):
                    features = {f['name']: f['value'] for f in fd if isinstance(f, dict)}
            # создание ResourceCriteria со всеми полями
            resource_criteria = ResourceCriteria(
                title=res.get('title'),
                uri=res.get('identificator', {}).get('uri') if res.get('identificator') else None,
                author=res.get('bibliographic', {}).get('author') if res.get('bibliographic') else None,
                source=res.get('bibliographic', {}).get('source') if res.get('bibliographic') else None,
                modality_type=search_params.get('modality_type') or res.get('modality', {}).get('type'),
                features=features,
                structured_data=res.get('modality', {}).get('value', {}).get('structured_data') if res.get('modality') else None,
                taxonomy=res.get('modality', {}).get('value', {}).get('structured_data', {}).get('taxonomy') if res.get('modality') else None
            )
        # Если resource нет, создается пустой ResourceCriteria только с modality_type
        else:
            resource_criteria = ResourceCriteria(
                modality_type=search_params.get('modality_type')
            )

        # контейнер со всеми параметрами для use_case (передаются критерии объекта, критерии ресурса, модальность, лимит, сдвиг, отладка, флаг LLM, запросы)
        request_obj = SearchRequest(
            object=object_criteria,
            resource=resource_criteria,
            modality_type=search_params.get('modality_type'),
            limit=limit,
            offset=offset,
            debug=debug,
            use_llm_answer=use_llm,
            user_query=user_query,
            clean_user_query=clean_user_query
        )

        logger.info("Creating use case...")
        use_case = _get_use_case() # вызов функции, которая создает все зависимости (репозиторий кэш, сервисы)
        logger.info("Use case created, executing...")
        result = use_case.execute(request_obj) # запуск основной логики поиска
        logger.info("Search completed successfully")
        return result # fastapi автоматом превращает словарь в json и отдает клиенту
        
    except Exception as e:
        logger.error(f"Error in search endpoint: {e}", exc_info=True)
        return {'error': str(e)}

# создает репозиторий (БД), кэш (Redis), векторный поиск (faiss), сервисы (карты, llm, ответы)
def _get_use_case():
    config = SearchConfig.from_env() # загрузка настроек из переменных окружения (БД, Redis, пути)
    cache = RedisCache(config.redis_host, config.redis_port, config.redis_db)
    init_db(config) # инициализация подключения к БД
    session_factory = get_session
    repository = SQLAlchemySearchRepository(session_factory) # создание репозитория для работы с БД через SQLAlchemy
    vector_search = VectorSearchAdapter(config.embedding_model_path, config.faiss_index_path) # создание адаптера для векторного поиска (загружает эмбеддинг-модель и индекс)
    search_use_case = SearchUseCase(repository)
    search_use_case.set_vector_search(vector_search)
    geo_service = GeoMapService(config.maps_dir, config.domain)
    llm_generator = LLMAnswerGenerator(vector_search)
    response_builder = ResponseBuilder(geo_service, llm_generator)
    return SearchAndBuildUseCase(search_use_case, response_builder, cache, vector_search)