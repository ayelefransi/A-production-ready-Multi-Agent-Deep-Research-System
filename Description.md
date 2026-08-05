# Prompt: Upgrade Multi-Agent Deep Research System to a Production-Grade Agentic Architecture


---

## Context for the agent doing the work

The current system is a LangGraph + FastAPI + Pydantic app using Gemini 1.5 Pro as the sole LLM, with three sequential agents (Researcher → Analyst → Writer), Tavily search, one human-in-the-loop checkpoint, structlog logging, and a 20-case eval harness. It works but is a linear pipeline, not a true agentic system: no planning/replanning, no parallel sub-agents, no self-critique, no tool diversity, single-provider/single-model risk, and thin observability.

Your job: refactor this into a **supervisor-orchestrated, multi-agent research system** with planning, parallel execution, reflection, verification, and multi-provider free-tier LLM routing — while keeping it deployable on free infrastructure.

---

## 1. Target agentic architecture

Replace the linear `Researcher → Analyst → Writer` chain with a **supervisor/orchestrator graph** in LangGraph:

```
                     ┌─────────────────┐
                     │   Orchestrator   │  (plans, delegates, replans)
                     └───────┬─────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                     ▼
 ┌─────────────┐     ┌──────────────┐      ┌──────────────┐
 │ Sub-question │     │ Sub-question │ ...  │ Sub-question │   (parallel fan-out,
 │ Researcher 1 │     │ Researcher 2 │      │ Researcher N │    N = dynamic per plan)
 └──────┬───────┘     └──────┬───────┘      └──────┬───────┘
        └────────────────────┼─────────────────────┘
                             ▼
                    ┌──────────────────┐
                    │  Critic / Verifier│  (fact-checks claims against
                    │      Agent        │   sources, flags contradictions)
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │   Analyst Agent   │  (synthesis, cross-source
                    │                   │   reasoning, contradiction
                    │                   │   resolution)
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │   Writer Agent    │  (structured report w/
                    │                   │   inline citations)
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │  Editor / QA Gate │  (checks citation coverage,
                    │                   │   length, tone, schema)
                    └────────┬─────────┘
                             ▼
                        Final Report
```

Required behaviors:

1. **Planner node**: decomposes the user query into 3–7 sub-questions with explicit research goals, using structured output (Pydantic `ResearchPlan` schema). Must support **replanning**: if the Critic or Analyst flags gaps, loop back to the Planner with the gap description rather than failing.
2. **Dynamic parallel fan-out**: use LangGraph's `Send` API to spawn one Researcher subgraph per sub-question concurrently (not sequential `for` loops). Each subgraph gets its own scoped state and tool budget.
3. **Tool-using Researcher agents**: each sub-question researcher should be able to choose between multiple tools (web search, web fetch/scrape, and optionally an academic/arXiv tool or a calculator/code tool for quantitative claims) rather than being hardcoded to one search call.
4. **Critic/Verifier agent**: cross-checks Analyst claims against retrieved source text, flags unsupported claims and contradicting sources, and produces a structured `VerificationReport` (claim → supporting source → confidence). This is the key upgrade that turns "search-and-summarize" into "agentic research."
5. **Self-reflection / retry loop**: after the Critic runs, if source adequacy or confidence is below a threshold, route back to the Planner (bounded to e.g. 2 extra iterations) instead of the current single-shot HIL checkpoint.
6. **Editor/QA gate**: final automated check before returning the report — validates the Pydantic output schema, checks that every major claim has an inline citation, checks word count/section completeness. Reject-and-retry once if it fails, else return with a `quality_warnings` field.
7. **Persistent HIL**: keep and extend the existing human-in-the-loop resume endpoint, but allow HIL checkpoints at more than one node (after planning, and optionally after critique) via LangGraph's `interrupt()`.
8. **Memory**: add a lightweight long-term memory (SQLite or Redis-backed) that stores prior research runs by topic hash, so repeated/related queries can reuse prior findings instead of re-searching from scratch. Add a short-term scratchpad per run for the orchestrator to track what's already been tried.

Deliverable: a `graph/` module with one file per node/subgraph, a top-level `graph/build_graph.py` that wires them with conditional edges for replanning/HIL, and a Pydantic schema per node input/output in `schemas/`.

---

## 2. Multi-provider, free-tier LLM routing (no single point of failure, no paid keys required)

Replace the hardcoded single Gemini call with a **provider-agnostic LLM router** with automatic fallback. Use LangChain's `ChatModel` abstraction so providers are swappable via config, not code changes.

Recommended free-tier stack (as of writing — verify current limits before shipping, since free-tier terms change):

| Role | Primary (free tier) | Fallback (free tier) | Why |
|---|---|---|---|
| Planner / Analyst (reasoning-heavy) | Google **Gemini 2.0/2.5 Flash** (free tier via AI Studio API key) | **Groq** (Llama 3.3 70B or similar, free tier, very fast) | Gemini free tier is generous for structured output; Groq is a fast, no-cost fallback if Gemini is rate-limited |
| Sub-question Researchers (many parallel calls, latency-sensitive) | **Groq** free tier (fast inference is important when fanning out N parallel calls) | Gemini Flash | Parallel fan-out needs low latency/high throughput more than max reasoning quality |
| Critic/Verifier (needs careful reasoning) | Gemini Flash/Pro free tier | OpenRouter free-tier models (e.g. free Llama/Mistral variants) | Verification benefits from a distinct model from the one that generated the claims, to reduce correlated errors |
| Writer (long-form generation) | Gemini Flash (large context window is useful here) | Groq | — |
| Embeddings (for memory / dedup / similarity) | Gemini `text-embedding-004` free tier, or local `sentence-transformers` (fully free, no API) | — | Local embeddings avoid burning API quota on every run |

Implementation requirements:
- `utils/llm_router.py`: a factory that returns a configured LangChain chat model based on `config/*.yaml` or env vars, with automatic retry-with-fallback on rate-limit/5xx errors (extend the existing Tenacity usage).
- Never hardcode a model name inline in agent code — all agents pull their model from the router by **role** (`planner`, `researcher`, `critic`, `writer`), so swapping providers is a config change.
- Log which provider/model actually served each call (for debugging quota exhaustion) via the existing structlog setup.
- Add a `--dry-run`/mock LLM mode for tests so the eval suite doesn't burn API quota on every CI run.

Search/tool free tiers to add alongside Tavily:
- **DuckDuckGo HTML search** (no API key, no rate limit issues) as a zero-cost fallback tool when Tavily's free quota (1,000 searches/month) is exhausted.
- Optionally **Exa.ai** free tier or **SerpAPI** free tier as a second real search provider for the Researcher agents to pick between, improving source diversity.
- A `web_fetch`/scraping tool (e.g. `httpx` + `trafilatura` or `readability-lxml`, both free/local) so Researcher agents can pull full article text instead of relying only on search snippets — this materially improves the Critic's ability to verify claims.

---

## 3. Production-hardening tasks

- **Config**: consolidate all model names, rate limits, retry counts, and feature flags (HIL on/off, replanning max iterations, provider order) into `config/settings.yaml`, loaded via Pydantic `BaseSettings`. Remove the committed `.env` file from the repo (it should never be committed — add it to `.gitignore` and ship `.env.example` only); rotate any keys that were exposed if this repo has been public with real keys.
- **Testing**: add `pytest` unit tests per node (planner produces valid schema, critic flags a known-bad claim, router falls back correctly on a simulated 429) and at least one end-to-end test using the mock LLM mode.
- **Observability**: add LangSmith or LangFuse tracing (both have free tiers) alongside structlog so multi-agent traces (which sub-agent ran, which tool, which model, token counts) are inspectable — this is essential once you have parallel fan-out and replanning loops, since flat logs won't show the graph shape.
- **Rate limiting & caching**: cache search results and LLM calls for identical sub-questions within a run (and optionally across runs, keyed by normalized query hash) to stay inside free-tier quotas — use `diskcache` or Redis, both free/local.
- **Docker**: add a `Dockerfile` + `docker-compose.yml` (API + optional Redis) so the system is portable beyond Vercel.
- **CI**: GitHub Actions workflow running lint (`ruff`), type-check (`mypy` or `pyright`), and the pytest suite with mock LLMs on every PR.
- **API surface**: extend FastAPI with a `/research/stream` SSE or WebSocket endpoint so clients can see live agent progress (which node is active) — this is the kind of visible "agentic" behavior that differentiates a demo from a real product.
- **Evals**: extend `evals/evaluator.py` beyond the current 20 cases to score the *new* capabilities specifically — source-verification accuracy (does the Critic catch injected false claims in a red-team test set?), replanning effectiveness, and latency/cost per run under the free-tier routing.

---

## 4. Suggested execution order (so the agent doing the work can checkpoint progress)

1. Introduce Pydantic schemas for `ResearchPlan`, `SubQuestionResult`, `VerificationReport`, `FinalReport`.
2. Build `utils/llm_router.py` with role-based provider fallback (Gemini ⇄ Groq ⇄ OpenRouter) and a mock mode.
3. Rebuild the LangGraph graph: Planner → parallel Researcher fan-out (`Send` API) → Critic → Analyst → Writer → Editor, with conditional replanning edges.
4. Add DuckDuckGo fallback search + a scraping tool; give Researcher agents a tool-choice step instead of a hardcoded single tool call.
5. Add memory (SQLite/Redis) for run reuse and a scratchpad for in-run dedup.
6. Wire LangSmith/LangFuse tracing and expand structlog fields.
7. Add caching, Docker, CI, and expand HIL to multiple interrupt points.
8. Extend the eval suite with verification-accuracy and replanning test cases; re-run against the original 20 cases plus new red-team cases to confirm no regression.

Keep every change backward-compatible with the existing `/research` and `/research/resume` endpoints where possible, and document new endpoints/config flags in the README as you go.