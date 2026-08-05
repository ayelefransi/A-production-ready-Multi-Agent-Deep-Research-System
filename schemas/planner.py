"""Pydantic schemas for the Planner agent."""

from pydantic import BaseModel, Field
from typing import List, Optional


class SubQuestion(BaseModel):
    """A single research sub-question with explicit research goal."""

    question: str = Field(
        description="The specific sub-question to research"
    )
    research_goal: str = Field(
        description="What this sub-question aims to discover or validate"
    )
    priority: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Priority level (1=highest, 5=lowest)"
    )


class ResearchPlan(BaseModel):
    """
    Output of the Planner agent. Decomposes a user query into
    3-7 targeted sub-questions with research goals.
    """

    original_query: str = Field(
        description="The original user query"
    )
    sub_questions: List[SubQuestion] = Field(
        description="3-7 decomposed sub-questions that together fully answer the original query",
        min_length=3,
        max_length=7,
    )
    research_strategy: str = Field(
        description="A brief description of the research strategy and approach"
    )
    expected_domains: List[str] = Field(
        description="Expected domain areas to cover (e.g., 'technology', 'economics', 'health')"
    )
    gap_description: Optional[str] = Field(
        default=None,
        description="If replanning, describes what gaps the previous iteration found"
    )
