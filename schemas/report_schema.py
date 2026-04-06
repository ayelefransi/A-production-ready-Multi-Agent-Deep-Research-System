from pydantic import BaseModel, Field
from typing import List

class SourceItem(BaseModel):
    title: str = Field(description="The title of the source")
    url: str = Field(description="The URL of the source")
    key_points: List[str] = Field(description="A list of key points extracted from this source, minimum 2 points")
    credibility: float = Field(description="A score from 0.0 to 1.0 indicating the credibility or reliability of the source")

class ResearcherOutput(BaseModel):
    query: str = Field(description="The original user query")
    sources: List[SourceItem] = Field(description="The retrieved and analyzed sources, typically 3 to 7 high-quality sources")

class AnalystOutput(BaseModel):
    summary: str = Field(description="An overarching summary of the research findings based on the sources")
    key_insights: List[str] = Field(description="A list of key insights extracted from analyzing all sources")
    risks: List[str] = Field(description="A list of potential risks or drawbacks identified in the research")
    contradictions: List[str] = Field(description="A list of any contradictions or conflicting information found between sources")
    confidence_score: float = Field(description="A score from 0.0 to 1.0 indicating confidence in the analysis")

class ResearchReport(BaseModel):
    title: str = Field(description="The final title for the research report")
    summary: str = Field(description="A comprehensive summary of the research topic")
    key_findings: List[str] = Field(description="The main findings and insights from the research")
    risks: List[str] = Field(description="Identified risks or concerns regarding the topic")
    sources: List[str] = Field(description="A list of source URLs or citations used to build the report")
