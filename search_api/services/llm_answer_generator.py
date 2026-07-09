import logging
from typing import List, Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from ..domain.entities import ObjectResult, ResourceResult
from ..domain.ports import VectorSearchPort
from .llm_integration import get_llm

logger = logging.getLogger(__name__)

class LLMAnswerGenerator:
    def __init__(self, vector_search: VectorSearchPort = None):
        self._vector_search = vector_search
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    def generate(self, question: str, objects: List[ObjectResult],
                 resources: List[ResourceResult]) -> Dict[str, Any]:
        context = self._build_context(objects, resources)
        use_vector = (self._vector_search is not None and
                      not resources and
                      question and
                      any(r.modality_type == 'Текст' for r in resources) is False)

        if use_vector:
            vector_docs = self._vector_search.search(question, 'biological_entity', 10)
            if vector_docs:
                context = self._build_vector_context(vector_docs)

        llm = self._get_llm()
        prompt = ChatPromptTemplate.from_messages([
            ("system", self._system_prompt()),
        ])
        full_prompt = self._system_prompt().format(question=question, context=context)
        logger.info(f"COMPLETE PROMPT:\n{full_prompt}")
        try:
            chain = prompt | llm
            response = chain.invoke({"question": question, "context": context})
            content = response.content.strip() if hasattr(response, 'content') else str(response)
            finish_reason = None
            if hasattr(response, 'response_metadata'):
                finish_reason = response.response_metadata.get('finish_reason')
            elif hasattr(response, 'additional_kwargs'):
                finish_reason = response.additional_kwargs.get('finish_reason')
            return {
                "content": content,
                "finish_reason": finish_reason,
                "success": bool(content)
            }
        except Exception as e:
            logger.error(f"LLM generation error: {e}")
            return {
                "content": "Извините, не удалось сгенерировать ответ.",
                "finish_reason": "error",
                "success": False
            }

    def _system_prompt(self) -> str:
        return (
            "Ты эксперт по Байкальской природной территории. "
            "Используй базу знаний для точных ответов.\n\n"
            "### ВАЖНО ###\n"
            "- На вопросы 'сколько' подсчитай количество объектов или ресурсов выданных из базы и расскажи вкратце о нескольких\n"
            "- Начинай ответ с прямого ответа на запрос\n"
            "- Даже при неполной информации предоставь доступные детали\n"
            "- Будь информативным"
            "- Даже при неполной информации предоставь доступные детали"
            "- Не давай ссылки на localhost и карты в целом\n\n"
            "База знаний:\n{context}\n\n"
            "Вопрос: {question}\n\nОтвет:"
        )

    def _build_context(self, objects: List[ObjectResult],
                       resources: List[ResourceResult]) -> str:
        parts = []
        if objects:
            parts.append(f"Найдено объектов: {len(objects)}")
            for o in objects[:5]:
                parts.append(f"- {o.object_type} '{o.db_id}': {str(o.properties)[:200]}")
        if resources:
            parts.append(f"Найдено ресурсов: {len(resources)}")
            for r in resources[:5]:
                content_preview = str(r.content)[:2000] if r.content else "Нет данных"
                parts.append(f"- {r.title} ({r.modality_type}): {content_preview}")
        return "\n".join(parts) if parts else "Нет релевантной информации."

    def _build_vector_context(self, docs: List[Dict[str, Any]]) -> str:
        parts = [f"Найдено по векторному поиску: {len(docs)}"]
        for i, d in enumerate(docs[:10], 1):
            name = d.get('object_name', 'Документ')
            content = d.get('content', '')[:500]
            parts.append(f"{i}. {name}:\n{content}")
        return "\n\n".join(parts)