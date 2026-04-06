from typing import TypedDict, Optional, List
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from agents.researcher import researcher_node
from agents.analyst import analyst_node
from agents.writer import writer_node
from config.settings import settings
import operator
from typing import Annotated

class ResearchState(TypedDict):
    query: str
    researcher_output: Optional[dict]
    analyst_output: Optional[dict]
    final_report: Optional[dict]
    messages: Annotated[List[str], operator.add]

def build_graph():
    builder = StateGraph(ResearchState)
    
    # Add nodes
    builder.add_node("researcher", researcher_node)
    builder.add_node("analyst", analyst_node)
    builder.add_node("writer", writer_node)
    
    # Define edges
    builder.add_edge(START, "researcher")
    builder.add_edge("researcher", "analyst")
    builder.add_edge("analyst", "writer")
    builder.add_edge("writer", END)
    
    # Setup optional Human in the loop
    memory = MemorySaver()
    interrupt_before = ["analyst"] if settings.require_human_approval else []
    
    return builder.compile(checkpointer=memory, interrupt_before=interrupt_before)

app_graph = build_graph()
