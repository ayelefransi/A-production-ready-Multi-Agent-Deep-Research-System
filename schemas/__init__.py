"""
Pydantic schemas for the Multi-Agent Deep Research System.

Re-exports all schema types for backward compatibility.
"""

from schemas.planner import SubQuestion, ResearchPlan
from schemas.researcher import SourceItem, SubQuestionResult, ResearcherOutput
from schemas.verifier import ClaimVerification, VerificationReport
from schemas.analyst import AnalystOutput, Insight, EvidenceStrength
from schemas.editor import QACheckResult
from schemas.report import (
    CitedSource,
    ReportMetadata,
    ResearchReport,
)

__all__ = [
    # Planner
    "SubQuestion",
    "ResearchPlan",
    # Researcher
    "SourceItem",
    "SubQuestionResult",
    "ResearcherOutput",
    # Verifier
    "ClaimVerification",
    "VerificationReport",
    # Analyst
    "AnalystOutput",
    "Insight",
    "EvidenceStrength",
    # Editor
    "QACheckResult",
    # Report
    "CitedSource",
    "ReportMetadata",
    "ResearchReport",
]
