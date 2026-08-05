# Production-Ready Multi-Agent Deep Research System

A production-ready Multi-Agent Deep Research System built with LangGraph, FastAPI, and Pydantic. It leverages Gemini 1.5 Pro to coordinate specialized agents (Researcher, Analyst, and Writer) to perform comprehensive, automated research and analysis on complex topics.

(You can try the full deployed app here: https://deepagentlab.vercel.app/)

A highly resilient, production-grade Multi-Agent Deep Research System built with LangGraph, FastAPI, and Pydantic. It coordinates specialized agents to decompose complex queries, perform parallel web research, cross-examine evidence, and synthesize publication-quality reports.

## Key Upgrades in this Architecture

1. **Supervisor Orchestration & Parallel Fan-Out**
   - Uses LangGraph's new `Send` API to dynamically spawn one **Researcher Agent** per sub-question, executing searches concurrently rather than sequentially.
2. **Iterative Self-Healing (Critic Node)**
   - The **Critic Agent** cross-checks claims against retrieved source texts. If evidence is lacking or quality is below threshold, it triggers a deterministic **replan loop** back to the Planner to adjust sub-questions.
3. **Automated QA Gate (Editor Node)**
   - The **Editor Agent** checks citation coverage, section completeness, and word count. If it fails, the **Writer Agent** is retried before returning the final report.
4. **Provider-Agnostic LLM Router**
   - Seamlessly mix-and-match LLM providers per agent role (e.g., Opus for Planner, Gemini for Researcher, Groq for Critic) with automatic fallbacks on failure.
5. **SSE Streaming Interface**
   - A new `/research/stream` endpoint streams agent transitions, sub-question previews, and iteration loops in real-time.
6. **Mock Mode for CI/CD**
   - A `MockChatModel` generates schema-valid, deterministic responses for tests and CI pipelines, ensuring your test suite never burns API quota.
7. **Long-Term Research Memory**
   - SQLite-backed memory stores past research runs, optionally using `sentence-transformers` for semantic similarity to deduplicate redundant queries.

## Architecture Diagram

<img width="1536" height="1024" alt="Multi Agent For Reasearch  Architecture" src="https://github.com/user-attachments/assets/439337b7-655e-4f78-89fb-bb9f20a9764f" />



## Setup Instructions

### 1. Environment & Dependencies

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configuration

Copy the example environment file:
```bash
cp .env.example .env
```
Fill in your keys in `.env`. By default, you'll want:
- `GEMINI_API_KEY` or `OPENROUTER_API_KEY`
- `TAVILY_API_KEY` (for search)

All behavior settings (max replans, model selection, enabled gates) are configured in `config/settings.yaml`.

### 3. Running the Server

**Locally:**
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

**With Docker:**
```bash
docker-compose up --build
```

## API Usage

### Standard Request (Blocking)
```bash
curl -X POST "http://localhost:8000/research" \
     -H "Content-Type: application/json" \
     -d '{"query": "What are the latest advancements in solid-state batteries?"}'
```

### Streaming Request (SSE)
Streams agent progress in real-time.
```bash
curl -X POST "http://localhost:8000/research/stream" \
     -H "Content-Type: application/json" \
     -d '{"query": "What are the latest advancements in solid-state batteries?"}'
```

### History and Markdown Export
```bash
# List past research sessions
curl "http://localhost:8000/research/history"

# Export a completed report to Markdown
curl -OJ "http://localhost:8000/research/{thread_id}/export/markdown"
```

## Testing & Evaluation

Run the evaluation suite in **Mock Mode** to test the graph logic without hitting actual LLM APIs:
```bash
python evals/evaluator.py --mock
```

Run standard tests:
```bash
pytest tests/ -v
```
