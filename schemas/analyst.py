"""Pydantic schemas for the Analyst agent."""

from pydantic import BaseModel, Field
from typing import List
from enum import Enum


class EvidenceStrength(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class Insight(BaseModel):
    """A key insight from cross-source analysis."""

    text: str = Field(description="The insight text")
    evidence_strength: str = Field(
        description="Strength of evidence: 'strong', 'moderate', or 'weak'"
    )
    supporting_sources: List[str] = Field(
        default_factory=list,
        description="URLs of sources supporting this insight"
    )


class AnalystOutput(BaseModel):
    """Output of the Analyst agent - synthesis and cross-source reasoning."""

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
        ge=0.0,
        le=1.0,
        description="A score from 0.0 to 1.0 indicating confidence in the analysis"
    )
