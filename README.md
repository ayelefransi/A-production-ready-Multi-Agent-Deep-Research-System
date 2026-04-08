# Multi-Agent Deep Research System
(you can get the full deployed app here https://deepagentlab.vercel.app/"

A production-ready Multi-Agent Deep Research System built with LangGraph, FastAPI, and Pydantic. It leverages Gemini 1.5 Pro to coordinate specialized agents (Researcher, Analyst, and Writer) to perform comprehensive, automated research and analysis on complex topics.

## Architecture

<img width="8192" height="905" alt="mermaid-ai-diagram-2026-04-06-133312" src="https://github.com/user-attachments/assets/8db35e71-60f9-4f3d-ac4d-31752153a125" />


## Features
- **Multi-Agent Orchestration**: Handled by `LangGraph` defining explicit state transitions.
- **Strict Data Contracts**: Uses `Pydantic` with LangChain's structured output to ensure agents never fail schemas.
- **Human-in-the-loop (HIL)**: Configurable ability to pause the workflow after the initial research phase.
- **Custom Search Tool**: Integrated `Tavily` search with robust Tenacity retry logic.
- **FastAPI Layer**: Exposes asynchronous endpoints to trigger and resume workflows.
- **Observability**: Structured JSON logging using `structlog`.

## Setup Instructions

1. **Clone & Environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. **Configuration**
   Copy the example environment file and fill in your keys:
   ```bash
   cp .env.example .env
   ```
   **Required Keys:**
   - `GEMINI_API_KEY`: Your Google Gemini API Key.
   - `TAVILY_API_KEY`: Your Tavily Search API Key.

3. **Running the Server**
   ```bash
   uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
   ```

## Example API Request

**Start Research:**

*PowerShell:*
```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/research" -Headers @{"Content-Type"="application/json"} -Body '{"query": "What are the latest advancements in solid-state batteries?"}'
```

*Bash (Linux/Mac):*
```bash
curl -X POST "http://localhost:8000/research" \
     -H "Content-Type: application/json" \
     -d '{"query": "What are the latest advancements in solid-state batteries?"}'
```

**Resume from Human Approval Checkpoint (if configured):**

*PowerShell:*
```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/research/resume" -Headers @{"Content-Type"="application/json"} -Body '{"thread_id": "uuid-from-start-response", "action": "approve"}'
```

*Bash (Linux/Mac):*
```bash
curl -X POST "http://localhost:8000/research/resume" \
     -H "Content-Type: application/json" \
     -d '{"thread_id": "uuid-from-start-response", "action": "approve"}'
```

## Evaluation Guide

To run the evaluation system over the 20 pre-configured test cases:
```bash
export PYTHONPATH=$(pwd)
python evals/evaluator.py
```
This tests for completeness, schema validity, and source adequacy, outputting an accuracy score.
