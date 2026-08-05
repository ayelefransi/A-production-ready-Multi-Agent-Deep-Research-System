"""Pydantic schemas for the Editor / QA Gate agent."""

from pydantic import BaseModel, Field
from typing import List, Dict


class QACheckResult(BaseModel):
    """
    Output of the Editor / QA Gate.
    Validates the final report for completeness, citations, and quality.
    """

    citation_coverage: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of major claims that have inline citations (0.0 to 1.0)"
    )
    word_count: int = Field(
        ge=0,
        description="Approximate word count of the report summary"
    )
    section_completeness: Dict[str, bool] = Field(
        default_factory=dict,
        description="Per-section completeness check (e.g., {'executive_summary': True, 'methodology': True})"
    )
    schema_valid: bool = Field(
        default=True,
        description="Whether the report passes Pydantic schema validation"
    )
    passed: bool = Field(
        description="Whether the report passes all QA checks"
    )
    quality_warnings: List[str] = Field(
        default_factory=list,
        description="List of quality warnings (e.g., 'Low citation coverage in key_findings')"
    )
    improvement_suggestions: List[str] = Field(
        default_factory=list,
        description="Specific suggestions for improving the report if QA failed"
    )
