"""Pydantic schemas for the Researcher agent."""

from pydantic import BaseModel, Field
from typing import List, Optional


class SourceItem(BaseModel):
    """A single research source with extracted information."""

    title: str = Field(description="The title of the source")
    url: str = Field(description="The URL of the source")
    key_points: List[str] = Field(
        description="A list of key points extracted from this source, minimum 2 points"
    )
    credibility: float = Field(
        ge=0.0,
        le=1.0,
        description="A score from 0.0 to 1.0 indicating the credibility or reliability of the source"
    )
    domain: str = Field(
        default="general",
        description="The domain/category this source belongs to (e.g., 'academic', 'news', 'government')"
    )
    sub_question: str = Field(
        default="",
        description="The sub-question this source was found for"
    )
    full_text_snippet: Optional[str] = Field(
        default=None,
        description="Extended text extracted via web scraping, used for claim verification"
    )


class SubQuestionResult(BaseModel):
    """Research output for a single sub-question (produced by one parallel researcher)."""

    sub_question: str = Field(
        description="The sub-question that was researched"
    )
    research_goal: str = Field(
        default="",
        description="The research goal for this sub-question"
    )
    sources: List[SourceItem] = Field(
        default_factory=list,
        description="Sources found for this sub-question"
    )
    tools_used: List[str] = Field(
        default_factory=list,
        description="List of tools used (e.g., 'tavily_search', 'duckduckgo', 'web_fetch')"
    )
    search_queries_tried: List[str] = Field(
        default_factory=list,
        description="All search queries attempted for this sub-question"
    )


class ResearcherOutput(BaseModel):
    """Aggregated research output across all sub-questions."""

    query: str = Field(description="The original user query")
    sources: List[SourceItem] = Field(
        description="All retrieved and analyzed sources, deduplicated"
    )
    sub_question_results: List[SubQuestionResult] = Field(
        default_factory=list,
        description="Per-sub-question breakdown of research results"
    )
    total_searches_performed: int = Field(
        default=1,
        description="Total number of search queries executed across all sub-questions"
    )
    total_raw_results: int = Field(
        default=0,
        description="Total raw results before deduplication"
    )
