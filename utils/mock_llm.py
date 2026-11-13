"""
Mock LLM for testing and CI.

Returns deterministic, schema-valid output for each agent role
so tests never burn API quota.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
from typing import Any, Dict, List, Optional, Type

from config.settings import settings

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
        "executive_summary": (
            "X is an established technology whose adoption is accelerating, and the "
            "available evidence supports continued growth [1]. The strongest support comes "
            "from peer-reviewed analysis, corroborated by industry reporting [2]. The main "
            "caveat is that long-term outcomes are not yet documented."
        ),
        "methodology": (
            "A multi-agent pipeline (planner, parallel researchers, analyst, critic, writer, "
            "editor) decomposed the question into three sub-questions, ran six searches and "
            "reviewed two unique sources, weighted by credibility. Claims were verified "
            "against source text before drafting."
        ),
        "summary": (
            "### Current state\n"
            "X is a well-established technology with a stable technical foundation [1]. "
            "Academic analysis describes its core mechanism as mature, with incremental "
            "rather than fundamental change expected over the near term [1]. Industry "
            "reporting independently reaches the same conclusion, which strengthens "
            "confidence in the assessment [2].\n\n"
            "### Adoption and drivers\n"
            "Adoption is increasing across the sectors examined [2]. The evidence suggests "
            "that falling costs and better tooling are the main drivers, although only one "
            "source quantifies the effect [2]. **Organisational readiness**, not technical "
            "capability, appears to be the binding constraint on further uptake [1].\n\n"
            "### Implications\n"
            "The relationship between X and Y is described as complementary rather than "
            "competitive [1]. Where both are deployed together, sources report compounding "
            "benefits, but this finding rests on a single study and should be treated as "
            "provisional [2]. Decision makers should plan for gradual integration instead "
            "of abrupt replacement."
        ),
        "key_findings": [
            "X rests on a mature technical foundation and is evolving incrementally [1].",
            "Adoption is rising across the sectors examined, driven mainly by falling costs [2].",
            "Organisational readiness, not technology, is the main constraint on uptake [1].",
            "X and Y are complementary; combined deployment may compound benefits [2].",
        ],
        "risks": [
            "Adoption challenges remain where organisational readiness is low [1].",
            "Evidence for combined X and Y benefits depends on a single study [2].",
        ],
        "contradictions": [],
        "knowledge_gaps": ["Long-term outcomes of X adoption are not yet documented."],
        "recommendations": [
            "Assess organisational readiness before committing to large-scale adoption.",
            "Pilot combined X and Y deployment on a limited scope and measure the outcome.",
            "Revisit the evidence as longer-term studies are published.",
        ],
        "confidence_level": "moderate",
        "confidence_rationale": (
            "The two sources are credible and agree, but the evidence base is small and "
            "one finding rests on a single study."
        ),
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
            "risks": True,
            "sources": True,
        },
        "schema_valid": True,
        "passed": True,
        "quality_warnings": [],
        "improvement_suggestions": [],
    },
}


# Per-run call counters. A caller (the API stream, a test) sets this to a
# fresh dict before running the graph; LangGraph tasks inherit the context,
# so every node in that run shares it. Unset means "no scripted scenario".
mock_run_calls: contextvars.ContextVar[Optional[Dict[str, int]]] = contextvars.ContextVar(
    "mock_run_calls", default=None
)

# Overrides applied on the FIRST call of a role when mock_scenario is
# "self_heal": the Critic demands a replan and the Editor fails QA once.
_SELF_HEAL_FIRST_CALL: dict[str, dict[str, Any]] = {
    "critic": {
        "overall_source_adequacy": 0.45,
        "quality_score": 0.5,
        "gaps": ["No coverage of long-term implications", "Only two independent sources"],
        "flags": ["low source diversity"],
        "should_replan": True,
        "replan_guidance": "Add sub-questions on long-term implications and seek more sources.",
        "feedback": "Coverage is too thin to support the analysis.",
    },
    "editor": {
        "citation_coverage": 0.4,
        "word_count": 60,
        "passed": False,
        "quality_warnings": ["Low citation coverage in key_findings", "Summary is too short"],
        "improvement_suggestions": ["Cite every key finding", "Expand the summary"],
    },
}


def _response_for(role: str) -> dict[str, Any]:
    """Pick the canned response for *role*, honouring the scripted scenario."""
    data = dict(_MOCK_RESPONSES.get(role, {}))
    calls = mock_run_calls.get()
    if calls is not None:
        seen = calls.get(role, 0)
        calls[role] = seen + 1
        if settings.mock_scenario == "self_heal" and seen == 0:
            data.update(_SELF_HEAL_FIRST_CALL.get(role, {}))
    return data


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
        return self._schema(**_response_for(self._mock.role))

    async def ainvoke(self, input: Any, **kwargs: Any) -> BaseModel:
        if settings.mock_latency_ms:
            await asyncio.sleep(settings.mock_latency_ms / 1000)
        return self.invoke(input, **kwargs)

