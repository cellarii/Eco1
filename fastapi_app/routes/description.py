import json
import logging
import math
import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from fastapi import APIRouter, Request, Query, Body, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from app.services import search_service
from app.utils import (
    extract_external_id, extract_all_external_ids, get_proper_title,
    convert_floats, generate_cache_key
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ==================== Pydantic Models ====================

class DescriptionRequest(BaseModel):
    """Модель для POST-запроса с фильтрами"""
    filters: Optional[Dict[str, Any]] = Field(None, description="Фильтры для поиска")


class ObjectDescriptionParams(BaseModel):
    """Параметры запроса /object/description"""
    object_name: Optional[str] = Field(None, description="Имя объекта")
    query: Optional[str] = Field(None, description="Поисковый запрос")
    clean_query: Optional[str] = Field(None, description="Очищенный запрос")
    limit: int = Field(1500, ge=1, description="Лимит результатов")
    similarity_threshold: float = Field(0.35, ge=0, le=1, description="Порог схожести")
    include_similarity: bool = Field(False, description="Включать ли схожесть в ответ")
    use_gigachat_filter: bool = Field(False, description="Использовать GigaChat для фильтрации")
    use_gigachat_answer: bool = Field(False, description="Использовать GigaChat для генерации ответа")
    debug_mode: bool = Field(False, description="Режим отладки")
    object_type: str = Field("all", description="Тип объекта")
    save_prompt: bool = Field(False, description="Сохранять промпт")
    in_stoplist: str = Field("1", description="Уровень стоп-листа")
    return_raw_documents: bool = Field(False, description="Возвращать сырые документы")
    force_vector_search: bool = Field(False, description="Принудительно использовать векторный поиск")
    vector_similarity_threshold: float = Field(0.03, ge=0, le=1, description="Порог схожести для векторного поиска")
    use_vector_fallback: bool = Field(True, description="Использовать векторный поиск как fallback")


class SpeciesDescriptionParams(BaseModel):
    """Параметры запроса /species/description"""
    species_name: str = Field(..., description="Название вида")
    query: Optional[str] = Field(None, description="Поисковый запрос")
    limit: int = Field(1500, ge=1, description="Лимит результатов")
    similarity_threshold: float = Field(0.1, ge=0, le=1, description="Порог схожести")
    include_similarity: bool = Field(False, description="Включать ли схожесть в ответ")
    use_gigachat_filter: bool = Field(False, description="Использовать GigaChat для фильтрации")
    debug_mode: bool = Field(False, description="Режим отладки")
    in_stoplist: str = Field("1", description="Уровень стоп-листа")
    force_vector_search: bool = Field(False, description="Принудительно использовать векторный поиск")
    vector_similarity_threshold: float = Field(0.03, ge=0, le=1, description="Порог схожести для векторного поиска")
    use_vector_fallback: bool = Field(True, description="Использовать векторный поиск как fallback")


# ==================== Helper Functions ====================

def build_debug_info(params: dict) -> dict:
    """Создает структуру для отладочной информации"""
    return {
        "parameters": params,
        "timestamp": time.time(),
        "steps": []
    }


def filter_by_stoplist(
    descriptions: List[Dict],
    in_stoplist: str,
    use_faiss_fallback: bool = False
) -> tuple[List[Dict], List[Dict], Dict]:
    """
    Фильтрует описания по уровню стоп-листа.
    Возвращает (safe_descriptions, stoplisted_descriptions, filter_info)
    """
    safe = []
    stoplisted = []
    
    logger.info(f"🔒 ФИЛЬТРАЦИЯ ПО STOPLIST (уровень {in_stoplist}):")
    logger.info(f"   - Всего описаний до фильтрации: {len(descriptions)}")
    
    for desc in descriptions:
        if not isinstance(desc, dict):
            safe.append(desc)
            logger.info(f"   ✓ БЕЗОПАСНО: простое описание")
            continue
        
        # Определяем значение in_stoplist
        if use_faiss_fallback:
            feature_data = desc.get('feature_data', {})
            if isinstance(feature_data, dict):
                desc_in_stoplist = feature_data.get('in_stoplist')
            else:
                desc_in_stoplist = desc.get('in_stoplist')
        else:
            feature_data = desc.get("feature_data", {})
            desc_in_stoplist = feature_data.get("in_stoplist") if feature_data else None
        
        try:
            requested_level = int(in_stoplist)
            if desc_in_stoplist is None or int(desc_in_stoplist) <= requested_level:
                safe.append(desc)
                logger.info(f"   ✓ БЕЗОПАСНО: in_stoplist={desc_in_stoplist}")
            else:
                stoplisted.append(desc)
                logger.info(f"   ✗ STOPLIST: in_stoplist={desc_in_stoplist} > запрошенного {requested_level}")
        except (ValueError, TypeError):
            if desc_in_stoplist is None or int(desc_in_stoplist) <= 1:
                safe.append(desc)
                logger.info(f"   ✓ БЕЗОПАСНО (по умолчанию): in_stoplist={desc_in_stoplist}")
            else:
                stoplisted.append(desc)
                logger.info(f"   ✗ STOPLIST (по умолчанию): in_stoplist={desc_in_stoplist}")
    
    filter_info = {
        "total_before_filter": len(descriptions),
        "safe_after_filter": len(safe),
        "stoplisted_count": len(stoplisted),
        "requested_level": in_stoplist
    }
    
    logger.info(f"📋 ИТОГИ ФИЛЬТРАЦИИ:")
    logger.info(f"   - Безопасные описания: {len(safe)}")
    logger.info(f"   - Исключено по stoplist: {len(stoplisted)}")
    
    return safe, stoplisted, filter_info


def format_description_for_response(
    desc: Union[Dict, str],
    index: int,
    object_name: Optional[str] = None,
    include_similarity: bool = False
) -> Dict:
    """Форматирует одно описание для ответа"""
    if isinstance(desc, dict):
        content = desc.get("content", "")
        similarity = desc.get("similarity")
        source = desc.get("source", "unknown")
        external_id = extract_external_id(desc)
        title = get_proper_title(desc, object_name, index)
        
        formatted = {
            "id": index,
            "title": title,
            "content": content,
            "source": source,
            "feature_data": desc.get("feature_data", {}),
            "structured_data": desc.get("structured_data", {})
        }
        
        if external_id:
            formatted["external_id"] = external_id
        
        if include_similarity and similarity is not None:
            formatted["similarity"] = round(similarity, 4)
        
        return formatted
    else:
        return {
            "id": index,
            "title": get_proper_title(None, object_name, index),
            "content": desc,
            "source": "content"
        }


def build_object_info(
    desc: Dict,
    object_name: Optional[str],
    object_type: str,
    use_faiss_fallback: bool
) -> Dict:
    """Создает информацию об объекте для used_objects"""
    return {
        "name": desc.get("object_name", object_name if object_name else "semantic_search"),
        "type": desc.get("object_type", object_type),
        "source": desc.get("source", "unknown"),
        "similarity": round(desc.get("similarity", 0), 4) if desc.get("similarity") else None,
        "search_source": "faiss_vector_store" if use_faiss_fallback else "relational_database"
    }


def handle_faiss_search(
    search_query: str,
    object_type: str,
    vector_similarity_threshold: float,
    context_limit: int,
    debug_info: dict,
    reason: str
) -> tuple[Optional[List[Dict]], bool, dict]:
    """
    Выполняет поиск в FAISS и возвращает результаты.
    Возвращает (descriptions, use_faiss_fallback, faiss_info)
    """
    logger.info(f"🚀 Активирован FAISS поиск для запроса: {search_query}")
    results = search_service.vector_search_fallback(
        query=search_query,
        object_type=object_type,
        similarity_threshold=vector_similarity_threshold,
        limit=context_limit
    )
    
    faiss_info = {
        "activated": True,
        "reason": reason,
        "query_used": search_query,
        "results_found": len(results) if results else 0,
        "similarity_threshold": vector_similarity_threshold,
        "search_source": "faiss_vector_store"
    }
    
    return results, bool(results), faiss_info


# ==================== Routes ====================

@router.get("/object/description")
@router.post("/object/description")
async def get_object_description(
    request: Request,
    # GET-параметры (также используются для POST)
    object_name: Optional[str] = Query(None, description="Имя объекта"),
    query: Optional[str] = Query(None, description="Поисковый запрос"),
    clean_query: Optional[str] = Query(None, description="Очищенный запрос"),
    limit: int = Query(1500, ge=1, description="Лимит результатов"),
    similarity_threshold: float = Query(0.35, ge=0, le=1, description="Порог схожести"),
    include_similarity: bool = Query(False, description="Включать ли схожесть в ответ"),
    use_gigachat_filter: bool = Query(False, description="Использовать GigaChat для фильтрации"),
    use_gigachat_answer: bool = Query(False, description="Использовать GigaChat для генерации ответа"),
    debug_mode: bool = Query(False, description="Режим отладки"),
    object_type: str = Query("all", description="Тип объекта"),
    save_prompt: bool = Query(False, description="Сохранять промпт"),
    in_stoplist: str = Query("1", description="Уровень стоп-листа"),
    return_raw_documents: bool = Query(False, description="Возвращать сырые документы"),
    force_vector_search: bool = Query(False, description="Принудительно использовать векторный поиск"),
    vector_similarity_threshold: float = Query(0.03, ge=0, le=1, description="Порог схожести для векторного поиска"),
    use_vector_fallback: bool = Query(True, description="Использовать векторный поиск как fallback"),
    # POST-тело (фильтры)
    filter_data: Optional[Dict[str, Any]] = Body(None, description="Фильтры для поиска")
):
    """
    Получение описаний объектов.
    Поддерживает GET и POST запросы.
    """
    # Логирование
    logger.info(f"📦 /object/description - GET params: {dict(request.query_params)}")
    if request.method == "POST":
        logger.info(f"📦 /object/description - POST data: {filter_data}")
    
    # Формируем параметры для отладки
    debug_params = {
        "object_name": object_name,
        "object_type": object_type,
        "query": query,
        "clean_query": clean_query,
        "limit": limit,
        "similarity_threshold": similarity_threshold,
        "include_similarity": include_similarity,
        "use_gigachat_filter": use_gigachat_filter,
        "use_gigachat_answer": use_gigachat_answer,
        "filter_data": filter_data,
        "save_prompt": save_prompt,
        "in_stoplist": in_stoplist,
        "force_vector_search": force_vector_search,
        "vector_similarity_threshold": vector_similarity_threshold,
        "use_vector_fallback": use_vector_fallback
    }
    debug_info = build_debug_info(debug_params)
    
    # Разрешение синонима объекта
    resolved_object_info = None
    if object_name:
        resolved_object_info = search_service.resolve_object_synonym(object_name, object_type)
        
        debug_info["synonym_resolution"] = {
            "original_name": object_name,
            "original_type": object_type,
            "resolved_info": resolved_object_info
        }
        
        if resolved_object_info.get("resolved", False):
            object_name = resolved_object_info["main_form"]
            if object_type != "all":
                object_type = resolved_object_info["object_type"]
            logger.info(f"✅ Разрешен синоним объекта: '{resolved_object_info['original_name']}' -> '{object_name}' (тип: {object_type})")
        else:
            logger.info(f"ℹ️ Синоним для объекта '{object_name}' не найден, используем оригинальное название")
    
    # Валидация обязательных параметров
    if use_gigachat_answer and not query:
        response = {"error": "Параметр 'query' обязателен при use_gigachat_answer=true"}
        if debug_mode:
            response["debug"] = debug_info
        return JSONResponse(content=response, status_code=400)
    
    if not object_name and not query and not filter_data:
        response = {"error": "Необходимо указать object_name, query или передать фильтры в body"}
        if debug_mode:
            response["debug"] = debug_info
        return JSONResponse(content=response, status_code=400)
    
    try:
        search_limit = limit if limit > 0 else 1500
        context_limit = 6
        
        # Определяем поисковый запрос
        search_query = None
        if clean_query:
            search_query = clean_query
        elif query:
            search_query = query
        elif object_name:
            search_query = object_name
        elif filter_data:
            search_query = json.dumps(filter_data, ensure_ascii=False)
        
        use_faiss_fallback = False
        faiss_results = []
        descriptions = []
        
        # Основная логика поиска
        if force_vector_search and search_query and use_gigachat_answer:
            faiss_results, use_faiss_fallback, faiss_info = handle_faiss_search(
                search_query=search_query,
                object_type=object_type,
                vector_similarity_threshold=vector_similarity_threshold,
                context_limit=context_limit,
                debug_info=debug_info,
                reason="force_vector_search"
            )
            debug_info["faiss_search"] = faiss_info
            descriptions = faiss_results if faiss_results else []
        else:
            if filter_data:
                descriptions = search_service.get_object_descriptions_by_filters(
                    filter_data=filter_data,
                    object_type=object_type,
                    limit=search_limit,
                    in_stoplist=in_stoplist,
                    object_name=object_name
                )
            elif query:
                descriptions = search_service.get_object_descriptions_by_filters(
                    filter_data={},
                    object_type=object_type,
                    limit=search_limit,
                    in_stoplist=in_stoplist,
                    object_name=object_name
                ) if object_name else []
            else:
                descriptions = search_service.get_object_descriptions(
                    object_name, object_type, in_stoplist=in_stoplist
                )
            
            # Fallback на FAISS
            if use_vector_fallback and not descriptions and search_query:
                logger.info(f"🔄 Активирован FAISS fallback (нет результатов в реляционной базе): {search_query}")
                faiss_results, use_faiss_fallback, faiss_info = handle_faiss_search(
                    search_query=search_query,
                    object_type=object_type,
                    vector_similarity_threshold=vector_similarity_threshold,
                    context_limit=context_limit,
                    debug_info=debug_info,
                    reason="no_relational_results"
                )
                debug_info["faiss_fallback"] = faiss_info
                descriptions = faiss_results if faiss_results else []
        
        # Фильтрация по стоп-листу
        safe_descriptions, stoplisted_descriptions, filter_info = filter_by_stoplist(
            descriptions=descriptions,
            in_stoplist=in_stoplist,
            use_faiss_fallback=use_faiss_fallback
        )
        
        if debug_mode:
            debug_info["in_stoplist_filter"] = filter_info
        
        if not safe_descriptions:
            response = {"error": "Я не готов про это разговаривать"}
            if debug_mode:
                response["debug"] = debug_info
            return JSONResponse(content=response, status_code=400)
        
        descriptions = safe_descriptions
        
        # Фильтрация через GigaChat
        if use_gigachat_filter:
            filter_query = query if query else object_name
            if debug_mode:
                debug_info["before_gigachat_filter"] = {
                    "count": len(descriptions),
                    "filter_query": filter_query
                }
            descriptions = search_service.filter_text_descriptions_with_gigachat(filter_query, descriptions)
            if debug_mode:
                debug_info["after_gigachat_filter"] = {
                    "count": len(descriptions),
                    "filtered_out": len(safe_descriptions) - len(descriptions)
                }
        
        used_objects = []
        not_used_objects = []
        
        # ============ GigaChat Answer ============
        if use_gigachat_answer:
            if not descriptions:
                response = {"error": "Не найдено описаний для генерации ответа"}
                if debug_mode:
                    response["debug"] = debug_info
                return JSONResponse(content=response, status_code=404)
            
            # Фильтрация blacklist
            safe_descriptions_for_gigachat = []
            blacklisted_descriptions = []
            for desc in descriptions:
                if isinstance(desc, dict):
                    feature_data = desc.get("feature_data", {})
                    if feature_data and feature_data.get("blacklist_risk") is True:
                        blacklisted_descriptions.append(desc)
                        continue
                    if desc.get("blacklist_risk") is True:
                        blacklisted_descriptions.append(desc)
                        continue
                safe_descriptions_for_gigachat.append(desc)
            
            if debug_mode:
                debug_info["blacklist_filter"] = {
                    "total_before_filter": len(descriptions),
                    "safe_after_filter": len(safe_descriptions_for_gigachat),
                    "blacklisted_count": len(blacklisted_descriptions)
                }
            
            if not safe_descriptions_for_gigachat:
                response = {
                    "error": "Все описания содержат риск blacklist и не могут быть использованы для генерации ответа GigaChat"
                }
                if debug_mode:
                    response["debug"] = debug_info
                return JSONResponse(content=response, status_code=400)
            
            descriptions_for_context = safe_descriptions_for_gigachat
            
            # Сортировка по схожести для контекста
            if all('similarity' in desc for desc in descriptions_for_context):
                context_descriptions = sorted(
                    descriptions_for_context,
                    key=lambda x: x.get('similarity', 0),
                    reverse=True
                )[:context_limit]
            else:
                context_descriptions = descriptions_for_context[:context_limit]
            
            # Формирование used_objects и not_used_objects
            for desc in context_descriptions:
                if isinstance(desc, dict):
                    used_objects.append(build_object_info(desc, object_name, object_type, use_faiss_fallback))
            
            remaining_descriptions = [d for d in descriptions_for_context if d not in context_descriptions]
            for desc in remaining_descriptions:
                if isinstance(desc, dict):
                    not_used_objects.append(build_object_info(desc, object_name, object_type, use_faiss_fallback))
            
            # ============ Return Raw Documents ============
            if return_raw_documents:
                logger.info("📄 Возвращаем сырые документы без вызова GigaChat")
                external_ids = extract_all_external_ids(descriptions_for_context)
                formatted_descriptions = []
                
                for i, desc in enumerate(descriptions_for_context, 1):
                    formatted = format_description_for_response(desc, i, object_name, True)
                    formatted_descriptions.append(formatted)
                
                if all('similarity' in desc for desc in formatted_descriptions):
                    formatted_descriptions.sort(key=lambda x: x.get('similarity', 0), reverse=True)
                
                response_data = {
                    "count": len(formatted_descriptions),
                    "descriptions": formatted_descriptions,
                    "external_id": external_ids,
                    "external_ids": external_ids,
                    "query_used": query if query else "simple_search",
                    "clean_query_used": clean_query if clean_query else None,
                    "similarity_threshold": similarity_threshold if query else None,
                    "use_gigachat_filter": use_gigachat_filter,
                    "use_gigachat_answer": True,
                    "raw_documents": True,
                    "message": "Возвращены исходные документы (GigaChat пропущен по запросу return_raw_documents)",
                    "formatted": True,
                    "in_stoplist_filter_applied": True,
                    "in_stoplist_level": in_stoplist,
                    "used_objects": used_objects,
                    "not_used_objects": not_used_objects
                }
                
                # Дополнительные поля
                if object_name:
                    response_data["object_name"] = object_name
                    response_data["object_type"] = object_type
                
                if filter_data:
                    response_data["filters_applied"] = filter_data
                
                if resolved_object_info and resolved_object_info.get("resolved", False):
                    response_data["synonym_resolution"] = {
                        "original_name": resolved_object_info["original_name"],
                        "resolved_name": object_name,
                        "original_type": resolved_object_info.get("original_type", object_type)
                    }
                
                if use_faiss_fallback:
                    response_data.update({
                        "search_source": "faiss_vector_store",
                        "vector_similarity_threshold": vector_similarity_threshold,
                        "faiss_fallback_used": True,
                        "faiss_search_query": search_query,
                        "clean_query_for_faiss": clean_query if clean_query else None
                    })
                
                if debug_mode:
                    response_data["debug"] = debug_info
                    response_data["debug"]["gigachat_generation"] = {
                        "skipped": True,
                        "reason": "return_raw_documents",
                        "prompt_saved": save_prompt,
                        "external_ids_found": len(external_ids)
                    }
                
                return JSONResponse(content=response_data)
            
            # ============ Формирование контекста для GigaChat ============
            context = "\n\n".join([
                desc["content"] if isinstance(desc, dict) else desc
                for desc in context_descriptions
            ])
            
            total_count = len(descriptions_for_context)
            count_info = f"\n\nВсего найдено безопасных записей: {total_count}"
            if len(blacklisted_descriptions) > 0:
                count_info += f" (исключено {len(blacklisted_descriptions)} записей с риском blacklist)"
            if total_count > context_limit:
                count_info += f" (в контекст включено топ-{context_limit} по релевантности)"
            if use_faiss_fallback:
                count_info += f"\nПоиск выполнен в FAISS векторной базе (порог схожести: {vector_similarity_threshold})"
                if clean_query:
                    count_info += f"\nИспользован очищенный запрос для поиска: '{clean_query}'"
            
            context += count_info
            
            # Формирование промпта
            full_prompt = f"""Ты эксперт по Байкальской природной территории. 
            Используй твою базу знаний для точных ответов на вопросы пользователя.

            Особые указания:
            - На вопросы 'сколько' - подсчитай количество соответствующих записей в базе знаний
            Например, на вопрос 'Сколько музеев?' при информации 'Всего найдено записей: 98 (в контекст включено топ-5 по релевантности)', нужно ответить около 98 музеев и затем описание каждого музея из топ записей
            - Будь информативным и лаконичным
            - Даже при неполной информации предоставь доступные детали

            Твоя база знаний:
            {context}

            Вопрос: {query}

            Ответ:"""
            
            # Сохранение промпта
            if save_prompt:
                current_dir = Path(__file__).parent.parent.parent
                timestamp = int(time.time())
                prompt_filename = current_dir / f"gigachat_prompt_{timestamp}.txt"
                try:
                    with open(prompt_filename, 'w', encoding='utf-8') as f:
                        f.write(full_prompt)
                    logger.info(f"✅ Полный промпт сохранен в: {prompt_filename}")
                except Exception as e:
                    logger.error(f"❌ Ошибка сохранения промпта: {e}")
            
            # ============ Генерация ответа через GigaChat ============
            try:
                llm_result = search_service._generate_llm_answer(query, context)
                is_blacklist = llm_result.get("finish_reason") == "blacklist" or not llm_result.get("success", True)
                
                # Обработка blacklist
                if is_blacklist:
                    logger.info("🚫 GigaChat вернул blacklist, возвращаем форматированные безопасные описания")
                    external_ids = extract_all_external_ids(descriptions_for_context)
                    formatted_descriptions = []
                    for i, desc in enumerate(descriptions_for_context, 1):
                        formatted = format_description_for_response(desc, i, object_name, True)
                        formatted_descriptions.append(formatted)
                    
                    if all('similarity' in desc for desc in formatted_descriptions):
                        formatted_descriptions.sort(key=lambda x: x.get('similarity', 0), reverse=True)
                    
                    response_data = {
                        "count": len(formatted_descriptions),
                        "descriptions": formatted_descriptions,
                        "external_id": external_ids,
                        "external_ids": external_ids,
                        "query_used": query if query else "simple_search",
                        "clean_query_used": clean_query if clean_query else None,
                        "similarity_threshold": similarity_threshold if query else None,
                        "use_gigachat_filter": use_gigachat_filter,
                        "use_gigachat_answer": True,
                        "gigachat_restricted": True,
                        "message": "GigaChat не смог сгенерировать ответ, поэтому возвращены исходные безопасные описания",
                        "formatted": True,
                        "in_stoplist_filter_applied": True,
                        "in_stoplist_level": in_stoplist,
                        "used_objects": used_objects,
                        "not_used_objects": not_used_objects
                    }
                    
                    if object_name:
                        response_data["object_name"] = object_name
                        response_data["object_type"] = object_type
                    
                    if filter_data:
                        response_data["filters_applied"] = filter_data
                    
                    if resolved_object_info and resolved_object_info.get("resolved", False):
                        response_data["synonym_resolution"] = {
                            "original_name": resolved_object_info["original_name"],
                            "resolved_name": object_name,
                            "original_type": resolved_object_info.get("original_type", object_type)
                        }
                    
                    if use_faiss_fallback:
                        response_data.update({
                            "search_source": "faiss_vector_store",
                            "vector_similarity_threshold": vector_similarity_threshold,
                            "faiss_fallback_used": True,
                            "faiss_search_query": search_query,
                            "clean_query_for_faiss": clean_query if clean_query else None
                        })
                    
                    if debug_mode:
                        response_data["debug"] = debug_info
                        response_data["debug"]["gigachat_generation"] = {
                            "finish_reason": llm_result.get("finish_reason"),
                            "blacklist_detected": True,
                            "fallback_to_descriptions": True,
                            "prompt_saved": save_prompt,
                            "external_ids_found": len(external_ids)
                        }
                    
                    return JSONResponse(content=response_data)
                
                # Успешный ответ GigaChat
                gigachat_response = llm_result.get("content", "")
                external_ids = extract_all_external_ids(context_descriptions)
                source_descriptions_summary = []
                
                for desc in context_descriptions:
                    if isinstance(desc, dict):
                        external_id = extract_external_id(desc)
                        title = get_proper_title(desc, object_name, len(source_descriptions_summary) + 1)
                        content = desc.get("content", "")
                        desc_summary = {
                            "title": title,
                            "content_preview": content[:200] + "..." if len(content) > 200 else content,
                            "source": desc.get("source", "unknown"),
                            "similarity": round(desc.get("similarity", 0), 4) if desc.get("similarity") else None
                        }
                        if external_id:
                            desc_summary["external_id"] = external_id
                        source_descriptions_summary.append(desc_summary)
                
                response_data = {
                    "gigachat_answer": gigachat_response,
                    "external_id": external_ids,
                    "external_ids": external_ids,
                    "source_descriptions": source_descriptions_summary,
                    "context_used": {
                        "descriptions_count": len(context_descriptions),
                        "total_descriptions": total_count,
                        "blacklisted_excluded": len(blacklisted_descriptions),
                        "external_ids_count": len(external_ids)
                    },
                    "query": query,
                    "clean_query": clean_query if clean_query else None,
                    "object_name": object_name if object_name else "semantic_search",
                    "object_type": object_type,
                    "in_stoplist_level": in_stoplist,
                    "used_objects": used_objects,
                    "not_used_objects": not_used_objects
                }
                
                if resolved_object_info and resolved_object_info.get("resolved", False):
                    response_data["synonym_resolution"] = {
                        "original_name": resolved_object_info["original_name"],
                        "resolved_name": object_name,
                        "original_type": resolved_object_info.get("original_type", object_type)
                    }
                
                if use_faiss_fallback:
                    response_data.update({
                        "search_source": "faiss_vector_store",
                        "vector_similarity_threshold": vector_similarity_threshold,
                        "faiss_fallback_used": True,
                        "faiss_search_query": search_query,
                        "clean_query_for_faiss": clean_query if clean_query else None
                    })
                
                if debug_mode:
                    response_data["debug"] = debug_info
                    response_data["debug"]["gigachat_generation"] = {
                        "response_length": len(gigachat_response),
                        "finish_reason": llm_result.get("finish_reason"),
                        "blacklist_detected": False,
                        "prompt_saved": save_prompt,
                        "external_ids_found": len(external_ids)
                    }
                
                response_data = convert_floats(response_data)
                return JSONResponse(content=response_data)
                
            except Exception as e:
                logger.error(f"Ошибка генерации ответа GigaChat: {str(e)}")
                error_response = {"error": "Ошибка генерации ответа GigaChat"}
                if debug_mode:
                    debug_info["gigachat_error"] = str(e)
                    error_response["debug"] = debug_info
                return JSONResponse(content=error_response, status_code=500)
        
        # ============ Без GigaChat Answer (просто возвращаем описания) ============
        for desc in descriptions:
            if isinstance(desc, dict):
                used_objects.append(build_object_info(desc, object_name, object_type, use_faiss_fallback))
        
        if not descriptions:
            response = {"error": "Я не готов про это разговаривать"}
            if debug_mode:
                response["debug"] = debug_info
            return JSONResponse(content=response, status_code=404)
        
        external_ids = extract_all_external_ids(descriptions)
        formatted_descriptions = []
        for i, desc in enumerate(descriptions, 1):
            formatted = format_description_for_response(desc, i, object_name, True)
            formatted_descriptions.append(formatted)
        
        if all('similarity' in desc for desc in formatted_descriptions):
            formatted_descriptions.sort(key=lambda x: x.get('similarity', 0), reverse=True)
        
        response_data = {
            "count": len(formatted_descriptions),
            "descriptions": formatted_descriptions,
            "external_id": external_ids,
            "external_ids": external_ids,
            "query_used": query if query else "simple_search",
            "clean_query_used": clean_query if clean_query else None,
            "similarity_threshold": similarity_threshold if query else None,
            "use_gigachat_filter": use_gigachat_filter,
            "in_stoplist_filter_applied": True,
            "in_stoplist_level": in_stoplist,
            "formatted": True,
            "used_objects": used_objects,
            "not_used_objects": []
        }
        
        if object_name:
            response_data["object_name"] = object_name
            response_data["object_type"] = object_type
        
        if filter_data:
            response_data["filters_applied"] = filter_data
        
        if resolved_object_info and resolved_object_info.get("resolved", False):
            response_data["synonym_resolution"] = {
                "original_name": resolved_object_info["original_name"],
                "resolved_name": object_name,
                "original_type": resolved_object_info.get("original_type", object_type)
            }
        
        if use_faiss_fallback:
            response_data.update({
                "search_source": "faiss_vector_store",
                "vector_similarity_threshold": vector_similarity_threshold,
                "faiss_fallback_used": True,
                "faiss_search_query": search_query,
                "clean_query_for_faiss": clean_query if clean_query else None
            })
        
        if debug_mode:
            response_data["debug"] = debug_info
            response_data["debug"]["external_ids_extracted"] = {
                "count": len(external_ids),
                "ids": external_ids
            }
        
        return JSONResponse(content=response_data)
        
    except Exception as e:
        logger.error(f"Ошибка получения описания: {str(e)}", exc_info=True)
        error_response = {"error": "Внутренняя ошибка сервера"}
        if debug_mode:
            debug_info["error"] = str(e)
            error_response["debug"] = debug_info
        return JSONResponse(content=error_response, status_code=500)


# ==================== Species Description ====================

@router.get("/species/description")
async def get_species_description(
    species_name: str = Query(..., description="Название вида"),
    query: Optional[str] = Query(None, description="Поисковый запрос"),
    limit: int = Query(1500, ge=1, description="Лимит результатов"),
    similarity_threshold: float = Query(0.1, ge=0, le=1, description="Порог схожести"),
    include_similarity: bool = Query(False, description="Включать ли схожесть в ответ"),
    use_gigachat_filter: bool = Query(False, description="Использовать GigaChat для фильтрации"),
    debug_mode: bool = Query(False, description="Режим отладки"),
    in_stoplist: str = Query("1", description="Уровень стоп-листа"),
    force_vector_search: bool = Query(False, description="Принудительно использовать векторный поиск"),
    vector_similarity_threshold: float = Query(0.03, ge=0, le=1, description="Порог схожести для векторного поиска"),
    use_vector_fallback: bool = Query(True, description="Использовать векторный поиск как fallback")
):
    """
    Получение описаний для биологических видов.
    """
    logger.info(f"📦 /species/description - GET params: species_name={species_name}, query={query}")
    
    debug_params = {
        "species_name": species_name,
        "query": query,
        "limit": limit,
        "similarity_threshold": similarity_threshold,
        "include_similarity": include_similarity,
        "use_gigachat_filter": use_gigachat_filter,
        "in_stoplist": in_stoplist,
        "force_vector_search": force_vector_search,
        "vector_similarity_threshold": vector_similarity_threshold,
        "use_vector_fallback": use_vector_fallback
    }
    debug_info = build_debug_info(debug_params)
    
    try:
        search_query = query if query else species_name
        
        use_faiss_fallback = False
        descriptions = []
        
        if force_vector_search and query:
            logger.info(f"🚀 Активирован принудительный FAISS поиск для вида: {species_name}, запрос: {query}")
            faiss_results = search_service.vector_search_fallback(
                query=query,
                object_type="biological_entity",
                similarity_threshold=vector_similarity_threshold,
                limit=limit
            )
            if faiss_results:
                use_faiss_fallback = True
                descriptions = faiss_results
                debug_info["faiss_search"] = {
                    "activated": True,
                    "reason": "force_vector_search",
                    "query_used": query,
                    "results_found": len(faiss_results),
                    "similarity_threshold": vector_similarity_threshold,
                    "search_source": "faiss_vector_store"
                }
        else:
            if query:
                descriptions = search_service.get_text_descriptions(species_name, in_stoplist=in_stoplist)
                
                if use_vector_fallback and not descriptions:
                    logger.info(f"🔄 Активирован FAISS fallback для вида: {species_name}, запрос: {query}")
                    faiss_results = search_service.vector_search_fallback(
                        query=query,
                        object_type="biological_entity",
                        similarity_threshold=vector_similarity_threshold,
                        limit=limit
                    )
                    if faiss_results:
                        use_faiss_fallback = True
                        descriptions = faiss_results
                        debug_info["faiss_fallback"] = {
                            "activated": True,
                            "reason": "no_relational_results",
                            "query_used": query,
                            "results_found": len(faiss_results),
                            "similarity_threshold": vector_similarity_threshold,
                            "search_source": "faiss_vector_store"
                        }
            else:
                descriptions = search_service.get_text_descriptions(species_name, in_stoplist=in_stoplist)
        
        # Фильтрация по стоп-листу
        safe_descriptions, stoplisted_descriptions, filter_info = filter_by_stoplist(
            descriptions=descriptions,
            in_stoplist=in_stoplist,
            use_faiss_fallback=use_faiss_fallback
        )
        
        if debug_mode:
            debug_info["in_stoplist_filter"] = filter_info
        
        if not safe_descriptions:
            logger.warning(f"🚫 НЕТ БЕЗОПАСНЫХ ОПИСАНИЙ для '{species_name}'")
            response = {
                "error": "Я не готов про это разговаривать",
                "used_objects": [],
                "not_used_objects": []
            }
            if debug_mode:
                response["debug"] = debug_info
            return JSONResponse(content=response, status_code=400)
        
        descriptions = safe_descriptions
        
        # Формирование used_objects
        used_objects = []
        not_used_objects = []
        
        for desc in descriptions:
            similarity = None
            if isinstance(desc, dict) and desc.get("similarity") is not None:
                try:
                    similarity_val = desc.get("similarity")
                    if similarity_val is not None and not math.isnan(float(similarity_val)):
                        similarity = round(float(similarity_val), 4)
                except (ValueError, TypeError):
                    similarity = None
            
            object_name_from_desc = desc.get("object_name", species_name) if isinstance(desc, dict) else species_name
            
            used_objects.append({
                "name": object_name_from_desc,
                "type": "biological_entity",
                "source": desc.get("source", "unknown") if isinstance(desc, dict) else "content",
                "similarity": similarity,
                "search_source": "faiss_vector_store" if use_faiss_fallback else "relational_database"
            })
        
        # Фильтрация через GigaChat
        if use_gigachat_filter:
            filter_query = query if query else species_name
            
            if debug_mode:
                debug_info["before_gigachat_filter"] = {
                    "count": len(descriptions),
                    "filter_query": filter_query
                }
            
            filtered_descriptions = search_service.filter_text_descriptions_with_gigachat(
                filter_query,
                descriptions
            )
            
            if debug_mode:
                debug_info["after_gigachat_filter"] = {
                    "count": len(filtered_descriptions),
                    "filtered_out": len(descriptions) - len(filtered_descriptions)
                }
            
            if filtered_descriptions:
                used_objects = []
                for desc in filtered_descriptions:
                    object_name_from_desc = desc.get("object_name", species_name) if isinstance(desc, dict) else species_name
                    used_objects.append({
                        "name": object_name_from_desc,
                        "type": "biological_entity",
                        "source": desc.get("source", "unknown") if isinstance(desc, dict) else "content",
                        "similarity": round(desc.get("similarity", 0), 4) if isinstance(desc, dict) and desc.get("similarity") else None,
                        "search_source": "faiss_vector_store" if use_faiss_fallback else "relational_database"
                    })
            
            descriptions = filtered_descriptions
        
        if not descriptions:
            logger.warning(f"🚫 ОПИСАНИЯ ОТФИЛЬТРОВАНЫ GigaChat для '{species_name}'")
            response = {
                "error": "Я не готов про это разговаривать",
                "used_objects": [],
                "not_used_objects": []
            }
            if debug_mode:
                response["debug"] = debug_info
            return JSONResponse(content=response, status_code=404)
        
        def format_content_with_title(desc: Union[Dict, str], index: int) -> str:
            if isinstance(desc, dict):
                content = desc.get("content", "")
                obj_name = desc.get("object_name", species_name)
                return f"** {obj_name} **\n\n{content}"
            else:
                return f"# {species_name}\n\n{desc}"
        
        formatted_descriptions = []
        for i, desc in enumerate(descriptions, 1):
            if isinstance(desc, dict):
                formatted_desc = {
                    "content": format_content_with_title(desc, i),
                    "source": desc.get("source", "unknown"),
                    "feature_data": desc.get("feature_data", {}),
                    "object_name": desc.get("object_name", species_name),
                    "object_type": "biological_entity"
                }
                
                if include_similarity and desc.get("similarity") is not None:
                    formatted_desc["similarity"] = round(desc.get("similarity", 0), 4)
                
                if desc.get("structured_data"):
                    formatted_desc["structured_data"] = desc.get("structured_data")
                
                if desc.get("species_features"):
                    formatted_desc["species_features"] = desc.get("species_features")
                
                formatted_descriptions.append(formatted_desc)
            else:
                formatted_desc = {
                    "content": format_content_with_title(desc, i),
                    "source": "content",
                    "object_name": species_name,
                    "object_type": "biological_entity"
                }
                formatted_descriptions.append(formatted_desc)
        
        response_data = {
            "count": len(formatted_descriptions),
            "descriptions": formatted_descriptions,
            "query_used": query if query else "simple_search",
            "similarity_threshold": similarity_threshold if query else None,
            "use_gigachat_filter": use_gigachat_filter,
            "in_stoplist_filter_applied": True,
            "in_stoplist_level": in_stoplist,
            "used_objects": used_objects,
            "not_used_objects": not_used_objects
        }
        
        if use_faiss_fallback:
            response_data.update({
                "search_source": "faiss_vector_store",
                "vector_similarity_threshold": vector_similarity_threshold,
                "faiss_fallback_used": True,
                "faiss_search_query": query
            })
        
        if debug_mode:
            response_data["debug"] = debug_info
        
        logger.info(f"✅ УСПЕШНЫЙ ОТВЕТ для '{species_name}': {len(formatted_descriptions)} описаний")
        return JSONResponse(content=response_data)
        
    except Exception as e:
        logger.error(f"Ошибка получения описания для '{species_name}': {str(e)}", exc_info=True)
        error_response = {
            "error": "Внутренняя ошибка сервера",
            "used_objects": [],
            "not_used_objects": []
        }
        if debug_mode:
            debug_info["error"] = str(e)
            error_response["debug"] = debug_info
        return JSONResponse(content=error_response, status_code=500)