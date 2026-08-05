"""
Mock LLM for testing and CI.

Returns deterministic, schema-valid output for each agent role
so tests never burn API quota.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Type

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Canned responses per role (valid JSON matching the Pydantic schemas)
# ---------------------------------------------------------------------------

_MOCK_RESPONSES: dict[str, dict[str, Any]] = {
    "planner": {
        "original_query": "mock query",
        "sub_questions": [
            {"question": "What is X?", "research_goal": "Understand X", "priority": 1},
            {"question": "How does X relate to Y?", "research_goal": "Find connections", "priority": 2},
            {"question": "What are the implications of X?", "research_goal": "Assess impact", "priority": 3},
        ],
        "research_strategy": "Multi-angle analysis covering technical, economic, and social dimensions.",
        "expected_domains": ["technology", "economics"],
    },
    "researcher": {
        "sub_question": "What is X?",
        "research_goal": "Understand X",
        "sources": [
            {
                "title": "Mock Source 1",
                "url": "https://example.com/source1",
                "key_points": ["Key point A", "Key point B"],
                "credibility": 0.9,
                "domain": "academic",
                "sub_question": "What is X?",
            },
            {
                "title": "Mock Source 2",
                "url": "https://example.com/source2",
                "key_points": ["Key point C", "Key point D"],
                "credibility": 0.8,
                "domain": "news",
                "sub_question": "What is X?",
            },
        ],
        "tools_used": ["tavily_search"],
        "search_queries_tried": ["What is X?"],
    },
    "critic": {
        "claims": [
            {
                "claim": "X is a well-established technology",
                "supporting_sources": ["https://example.com/source1"],
                "contradicting_sources": [],
                "confidence": 0.85,
                "verdict": "supported",
                "reasoning": "Multiple credible sources confirm this claim.",
            }
        ],
        "overall_source_adequacy": 0.8,
        "quality_score": 0.8,
        "gaps": [],
        "flags": [],
        "should_replan": False,
        "replan_guidance": "",
        "feedback": "Research quality is adequate.",
        "refined_queries": [],
    },
    "analyst": {
        "summary": "Mock analysis summary covering the topic comprehensively.",
        "key_insights": [
            {
                "text": "X is rapidly evolving with significant implications.",
                "evidence_strength": "strong",
                "supporting_sources": ["https://example.com/source1"],
            }
        ],
        "risks": ["Adoption challenges remain."],
        "contradictions": [],
        "knowledge_gaps": [],
        "confidence_score": 0.85,
    },
    "writer": {
        "title": "Mock Research Report: Understanding X",
        "executive_summary": "This report examines X and its implications. Key findings indicate rapid evolution.",
        "methodology": "Multi-agent pipeline with 3 sub-questions, 2 sources, 1 iteration.",
        "summary": "X is a well-established technology [1] with growing adoption [2].",
        "key_findings": [
            "X is rapidly evolving [1]",
            "Adoption is increasing [2]",
        ],
        "risks": ["Adoption challenges remain"],
        "contradictions": [],
        "knowledge_gaps": [],
        "sources": [
            {"index": 1, "title": "Mock Source 1", "url": "https://example.com/source1", "credibility": 0.9},
            {"index": 2, "title": "Mock Source 2", "url": "https://example.com/source2", "credibility": 0.8},
        ],
        "quality_warnings": [],
    },
    "editor": {
        "citation_coverage": 1.0,
        "word_count": 250,
        "section_completeness": {
            "executive_summary": True,
            "methodology": True,
            "summary": True,
            "key_findings": True,
            "sources": True,
        },
        "schema_valid": True,
        "passed": True,
        "quality_warnings": [],
        "improvement_suggestions": [],
    },
}


class MockChatModel(BaseChatModel):
    """
    A fake LangChain ChatModel that returns canned responses.

    Use ``with_structured_output(Schema)`` to get parsed Pydantic objects.
    """

    role: str = "planner"

    class Config:
        arbitrary_types_allowed = True

    @property
    def _llm_type(self) -> str:
        return "mock"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        content = json.dumps(_MOCK_RESPONSES.get(self.role, {"text": "mock response"}))
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content))]
        )

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop, **kwargs)

    def with_structured_output(self, schema: Type[BaseModel], **kwargs: Any) -> "MockStructuredModel":
        """Return a wrapper that parses canned JSON through the Pydantic schema."""
        return MockStructuredModel(mock=self, schema=schema)


class MockStructuredModel:
    """
    Wraps a MockChatModel so that ``.invoke()`` / ``.ainvoke()`` return
    a validated Pydantic model instance instead of an AIMessage.
    """

    def __init__(self, mock: MockChatModel, schema: Type[BaseModel]):
        self._mock = mock
        self._schema = schema

    def invoke(self, input: Any, **kwargs: Any) -> BaseModel:
        data = _MOCK_RESPONSES.get(self._mock.role, {})
        return self._schema(**data)

    async def ainvoke(self, input: Any, **kwargs: Any) -> BaseModel:
        return self.invoke(input, **kwargs)

