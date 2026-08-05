"""Pydantic schemas for the final research report."""

from pydantic import BaseModel, Field
from typing import List, Optional


class CitedSource(BaseModel):
    """A cited source in the final report."""

    index: int = Field(description="Citation index number, e.g., 1, 2, 3")
    title: str = Field(description="Title of the source")
    url: str = Field(description="URL of the source")
    credibility: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Credibility score of the source"
    )


class ReportMetadata(BaseModel):
    """Metadata about the research process."""

    total_sources_scanned: int = Field(default=0)
    total_searches: int = Field(default=0)
    iterations_taken: int = Field(default=1)
    replan_count: int = Field(default=0)
    agent_timings: dict = Field(default_factory=dict)
    total_duration_seconds: float = Field(default=0.0)
    providers_used: List[str] = Field(
        default_factory=list,
        description="LLM providers that served calls during this run"
    )


class ResearchReport(BaseModel):
    """The final structured research report."""

    title: str = Field(
        description="The final title for the research report"
    )
    executive_summary: str = Field(
        description="A concise 2-3 sentence executive summary of the entire report"
    )
    methodology: str = Field(
        description="Description of how the research was conducted (agents used, sources searched, iterations)"
    )
    summary: str = Field(
        description="A comprehensive summary of the research topic with inline citations like [1], [2]"
    )
    key_findings: List[str] = Field(
        description="The main findings and insights from the research, with inline citations"
    )
    risks: List[str] = Field(
        description="Identified risks or concerns regarding the topic"
    )
    contradictions: List[str] = Field(
        default_factory=list,
        description="Contradictions or conflicting information found across sources"
    )
    knowledge_gaps: List[str] = Field(
        default_factory=list,
        description="Areas requiring further research"
    )
    sources: List[CitedSource] = Field(
        description="Numbered list of cited sources with full metadata"
    )
    metadata: Optional[ReportMetadata] = Field(
        default=None,
        description="Research process metadata (timings, iterations, search counts)"
    )
    quality_warnings: List[str] = Field(
        default_factory=list,
        description="Quality warnings from the Editor/QA gate, if any"
    )
