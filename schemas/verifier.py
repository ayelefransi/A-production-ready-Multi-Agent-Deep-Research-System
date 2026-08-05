"""Pydantic schemas for the Critic/Verifier agent."""

from pydantic import BaseModel, Field
from typing import List, Literal


class ClaimVerification(BaseModel):
    """Verification result for a single claim against source evidence."""

    claim: str = Field(
        description="The claim being verified"
    )
    supporting_sources: List[str] = Field(
        default_factory=list,
        description="URLs of sources that support this claim"
    )
    contradicting_sources: List[str] = Field(
        default_factory=list,
        description="URLs of sources that contradict this claim"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence level in the claim's accuracy (0.0 to 1.0)"
    )
    verdict: Literal[
        "supported", "unsupported", "contradicted", "partially_supported"
    ] = Field(
        description="Verification verdict for this claim"
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of why this verdict was reached"
    )


class VerificationReport(BaseModel):
    """
    Output of the Critic/Verifier agent.
    Cross-checks claims against retrieved source text.
    """

    claims: List[ClaimVerification] = Field(
        description="Per-claim verification results"
    )
    overall_source_adequacy: float = Field(
        ge=0.0,
        le=1.0,
        description="Overall adequacy of sources to support the analysis (0.0 to 1.0)"
    )
    quality_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Overall quality score of the research (0.0 to 1.0)"
    )
    gaps: List[str] = Field(
        default_factory=list,
        description="Identified gaps in the research that need to be filled"
    )
    flags: List[str] = Field(
        default_factory=list,
        description="Warning flags (e.g., 'low source diversity', 'contradictions unresolved')"
    )
    should_replan: bool = Field(
        default=False,
        description="Whether the system should loop back to the Planner to address gaps"
    )
    replan_guidance: str = Field(
        default="",
        description="Guidance for the Planner if replanning is needed"
    )
    feedback: str = Field(
        default="",
        description="Detailed feedback on the research quality"
    )
    refined_queries: List[str] = Field(
        default_factory=list,
        description="Refined search queries to use if replanning"
    )
