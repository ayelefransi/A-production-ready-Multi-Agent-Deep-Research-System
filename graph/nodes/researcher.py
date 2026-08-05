"""
Researcher node - per-sub-question research with tool choice.

This node is spawned via LangGraph's ``Send`` API - one instance per
sub-question, running concurrently.  Each instance has its own scoped
state and tool budget.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from tenacity import retry, stop_after_attempt, wait_exponential

from schemas.researcher import SourceItem, SubQuestionResult
from utils.llm_router import get_llm
from utils.logger import logger


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=4, max=60))
async def researcher_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Research a single sub-question using multiple tools.

    This node receives a scoped state containing:
    - ``sub_question``: the question text
    - ``research_goal``: what to discover
    - ``query``: the original user query (for context)

    It performs search (Tavily → DuckDuckGo fallback), optionally
    web-fetches full article text, and returns a ``SubQuestionResult``.
    """
    start = time.time()
    sub_question = state.get("sub_question", "")
    research_goal = state.get("research_goal", "")
    original_query = state.get("query", "")

    logger.info(
        "researcher_started",
        sub_question=sub_question[:80],
        research_goal=research_goal[:80],
    )

    # ── Step 1: Search ────────────────────────────────────────────────────
    from tools.search_tool import execute_search_async

    tools_used = []
    search_queries = [sub_question]
    all_search_results = []

    # Primary search
    try:
        raw = await execute_search_async(sub_question, max_results=5)
        results = raw.get("results", [])
        all_search_results.extend(results)
        tools_used.append(raw.get("provider", "search"))
    except Exception as e:
        logger.warning("researcher_search_failed", sub_question=sub_question[:80], error=str(e))

    # If too few results, try a variant query
    if len(all_search_results) < 3 and research_goal:
        variant_query = f"{sub_question} {research_goal}"
        search_queries.append(variant_query)
        try:
            raw2 = await execute_search_async(variant_query, max_results=3)
            all_search_results.extend(raw2.get("results", []))
        except Exception:
            pass

    # ── Step 2: Optional web fetch for full text ──────────────────────────
    try:
        from tools.web_fetch import web_fetch_async

        for result in all_search_results[:3]:  # Fetch top 3 only (budget)
            url = result.get("url", "")
            if url:
                try:
                    full_text = await web_fetch_async(url)
                    if full_text:
                        result["full_text_snippet"] = full_text[:2000]
                        if "web_fetch" not in tools_used:
                            tools_used.append("web_fetch")
                except Exception:
                    pass  # Non-critical
    except ImportError:
        pass  # web_fetch not available

    # ── Step 3: LLM analysis ──────────────────────────────────────────────
    llm = get_llm("researcher", structured_output=SubQuestionResult)

    context = ""
    for r in all_search_results:
        context += (
            f"URL: {r.get('url', '')}\n"
            f"Title: {r.get('title', '')}\n"
            f"Content: {r.get('content', '')}\n"
        )
        if r.get("full_text_snippet"):
            context += f"Extended Text: {r['full_text_snippet'][:1000]}\n"
        context += "\n"

    prompt = f"""
    You are an expert Research Agent. Analyze the search results below for the
    given sub-question and extract key points from the highest-quality sources.

    RULES:
    - NEVER hallucinate URLs. Only use the exact sources provided below.
    - Each source must have at least 2 key points.
    - Assign a credibility score (0.0-1.0) based on the source domain and content quality.
    - Categorize each source's domain (e.g., 'academic', 'news', 'government', 'industry', 'blog').

    Original Query: {original_query}
    Sub-Question: {sub_question}
    Research Goal: {research_goal}

    Search Results:
    {context}
    """

    try:
        result: SubQuestionResult = await llm.ainvoke(prompt)
        result.sub_question = sub_question
        result.research_goal = research_goal
        result.tools_used = tools_used
        result.search_queries_tried = search_queries

        duration = time.time() - start
        logger.info(
            "researcher_success",
            sub_question=sub_question[:80],
            sources_found=len(result.sources),
            duration_seconds=round(duration, 2),
        )

        return {
            "sub_question_results": [result.model_dump()],
            "messages": [
                f"Researcher found {len(result.sources)} sources for: {sub_question[:60]}…"
            ],
        }
    except Exception as e:
        logger.error("researcher_failed", sub_question=sub_question[:80], error=str(e))
        # Return empty result rather than crashing the whole fan-out
        fallback = SubQuestionResult(
            sub_question=sub_question,
            research_goal=research_goal,
            sources=[],
            tools_used=tools_used,
            search_queries_tried=search_queries,
        )
        return {
            "sub_question_results": [fallback.model_dump()],
            "messages": [f"Researcher failed for: {sub_question[:60]}... - {e}"],
        }
