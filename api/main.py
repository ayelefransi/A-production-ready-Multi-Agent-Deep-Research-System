from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from graph.workflow import app_graph
from config.settings import settings
from utils.logger import logger
import uuid
import os

app = FastAPI(title="Multi-Agent Deep Research API")

# Setup frontend static and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

class ResearchRequest(BaseModel):
    query: str
    
class ResumeRequest(BaseModel):
    thread_id: str
    action: str = "approve"

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/research")
async def start_research(request: ResearchRequest):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    
    initial_state = {"query": request.query, "messages": []}
    logger.info("starting_research", query=request.query, thread_id=thread_id)
    
    try:
        state = await app_graph.ainvoke(initial_state, config)
        
        # Check if we hit an interrupt
        current_state = app_graph.get_state(config)
        if current_state.next:
            return {
                "status": "paused_for_approval",
                "thread_id": thread_id,
                "preview": current_state.values.get("researcher_output", {}),
                "message": "Human approval required to proceed to analyst step. Call POST /research/resume."
            }
        
        return {
            "status": "completed",
            "thread_id": thread_id,
            "report": state.get("final_report")
        }
    except Exception as e:
        logger.error("research_workflow_failed", error=str(e), thread_id=thread_id)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/research/resume")
async def resume_research(request: ResumeRequest):
    config = {"configurable": {"thread_id": request.thread_id}}
    current_state = app_graph.get_state(config)
    
    if not current_state.next:
        raise HTTPException(status_code=400, detail="No pending action for this thread ID.")
        
    if request.action == "reject":
        return {"status": "rejected", "message": "Research workflow canceled by human."}
        
    try:
        state = await app_graph.ainvoke(None, config)
        return {
            "status": "completed",
            "thread_id": request.thread_id,
            "report": state.get("final_report")
        }
    except Exception as e:
        logger.error("resume_workflow_failed", error=str(e), thread_id=request.thread_id)
        raise HTTPException(status_code=500, detail=str(e))
        
@app.get("/health")
def health_check():
    return {"status": "healthy"}
