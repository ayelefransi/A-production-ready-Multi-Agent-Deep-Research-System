from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum


# --- Planner Agent Output ---

class PlannerOutput(BaseModel):
    original_query: str = Field(description="The original user query")
    sub_questions: List[str] = Field(
        description="3-5 decomposed sub-questions that together fully answer the original query"
    )
    research_strategy: str = Field(
        description="A brief description of the research strategy and approach"
    )
    expected_domains: List[str] = Field(
        description="Expected domain areas to cover (e.g., 'technology', 'economics', 'health')"
    )


# --- Researcher Agent Output ---

class SourceItem(BaseModel):
    title: str = Field(description="The title of the source")
    url: str = Field(description="The URL of the source")
    key_points: List[str] = Field(
        description="A list of key points extracted from this source, minimum 2 points"
    )
    credibility: float = Field(
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


class ResearcherOutput(BaseModel):
    query: str = Field(description="The original user query")
    sources: List[SourceItem] = Field(
        description="The retrieved and analyzed sources, typically 5 to 15 high-quality sources"
    )
    total_searches_performed: int = Field(
        default=1,
        description="Total number of search queries executed"
    )
    total_raw_results: int = Field(
        default=0,
        description="Total raw results before deduplication"
    )


# --- Analyst Agent Output ---

class EvidenceStrength(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class Insight(BaseModel):
    text: str = Field(description="The insight text")
    evidence_strength: str = Field(
        description="Strength of evidence: 'strong', 'moderate', or 'weak'"
    )
    supporting_sources: List[str] = Field(
        default_factory=list,
        description="URLs of sources supporting this insight"
    )


class AnalystOutput(BaseModel):
    summary: str = Field(
        description="An overarching summary of the research findings based on the sources"
    )
    key_insights: List[Insight] = Field(
        description="A list of key insights with evidence strength ratings"
    )
    risks: List[str] = Field(
        description="A list of potential risks or drawbacks identified in the research"
    )
    contradictions: List[str] = Field(
        description="A list of any contradictions or conflicting information found between sources"
    )
    knowledge_gaps: List[str] = Field(
        default_factory=list,
        description="Areas where the research is incomplete or more data is needed"
    )
    confidence_score: float = Field(
        description="A score from 0.0 to 1.0 indicating confidence in the analysis"
    )


# --- Critic Agent Output ---

class CriticOutput(BaseModel):
    quality_score: float = Field(
        description="Overall quality score from 0.0 to 1.0"
    )
    gaps: List[str] = Field(
        description="Identified gaps in the research that need to be filled"
    )
    should_iterate: bool = Field(
        description="Whether the research should loop back for another iteration to fill gaps"
    )
    feedback: str = Field(
        description="Detailed feedback for the researcher on how to improve the next iteration"
    )
    refined_queries: List[str] = Field(
        default_factory=list,
        description="Refined search queries to use if iterating"
    )


# --- Final Report ---

class CitedSource(BaseModel):
    index: int = Field(description="Citation index number, e.g., 1, 2, 3")
    title: str = Field(description="Title of the source")
    url: str = Field(description="URL of the source")
    credibility: float = Field(
        default=0.0,
        description="Credibility score of the source"
    )


class ReportMetadata(BaseModel):
    total_sources_scanned: int = Field(default=0)
    total_searches: int = Field(default=0)
    iterations_taken: int = Field(default=1)
    agent_timings: dict = Field(default_factory=dict)
    total_duration_seconds: float = Field(default=0.0)


class ResearchReport(BaseModel):
    title: str = Field(description="The final title for the research report")
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
