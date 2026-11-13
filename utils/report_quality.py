"""
Deterministic report-quality guardrails.

LLMs are asked to cite and to self-assess, but neither is trusted blindly:

* the evidence digest gives every source the same share of the prompt budget,
  so the Writer sees key points for all sources instead of a truncated blob;
* citations that point at a source that does not exist are removed, and the
  report's source list is rebuilt from the sources the Researchers actually
  retrieved (the model cannot invent a reference);
* the Editor's verdict is combined with measured facts (word count, citation
  coverage, section completeness) rather than the model's estimate of them.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Set

MAX_REPORT_SOURCES = 25
MIN_SUMMARY_WORDS = 100
MIN_CITATION_COVERAGE = 0.6
REQUIRED_SECTIONS = (
    "executive_summary", "methodology", "summary", "key_findings", "risks", "sources",
)

_CITATION = re.compile(r"\[(\d{1,3})\]")
_TEXT_FIELDS = ("executive_summary", "summary", "methodology")
_LIST_FIELDS = ("key_findings", "risks", "contradictions", "knowledge_gaps", "recommendations")


def rank_sources(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Most credible sources first, capped; position + 1 is the citation number."""
    ranked = sorted(sources, key=lambda s: s.get("credibility") or 0, reverse=True)
    return ranked[:MAX_REPORT_SOURCES]


def evidence_digest(sources: List[Dict[str, Any]], max_chars: int = 12_000) -> str:
    """Numbered evidence block with an equal character budget per source."""
    if not sources:
        return "(no sources were retrieved)"
    budget = max(300, max_chars // len(sources))
    blocks = []
    for index, source in enumerate(sources, 1):
        points = "\n".join(f"  - {point}" for point in source.get("key_points", []))
        block = (
            f"[{index}] {source.get('title', 'Untitled')}\n"
            f"  URL: {source.get('url', '')}\n"
            f"  Type: {source.get('domain', 'general')} | "
            f"Credibility: {source.get('credibility') or 0:.2f}\n"
            f"  Key points:\n{points}"
        )
        blocks.append(block[:budget])
    return "\n\n".join(blocks)


def cited_indices(texts: Iterable[str]) -> Set[int]:
    return {int(n) for text in texts for n in _CITATION.findall(text or "")}


def strip_invalid_citations(text: str, valid: Set[int]) -> str:
    """Drop [n] markers that do not correspond to a real source."""
    cleaned = _CITATION.sub(lambda m: m.group(0) if int(m.group(1)) in valid else "", text or "")
    return re.sub(r"[ \t]+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", cleaned)).strip()


def ground_report(report: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Make the report's citations and source list consistent with real evidence.

    *sources* must be the same ranked list that was numbered in the Writer's
    prompt. Returns a new report dict.
    """
    grounded = dict(report)
    valid = set(range(1, len(sources) + 1))

    for field in _TEXT_FIELDS:
        grounded[field] = strip_invalid_citations(grounded.get(field, ""), valid)
    for field in _LIST_FIELDS:
        grounded[field] = [
            strip_invalid_citations(item, valid) for item in grounded.get(field) or []
        ]

    used = cited_indices(
        [grounded.get(f, "") for f in _TEXT_FIELDS]
        + [item for f in _LIST_FIELDS for item in grounded[f]]
    )
    # List what was cited; if nothing was cited, list everything consulted.
    listed = sorted(used) if used else sorted(valid)
    grounded["sources"] = [
        {
            "index": index,
            "title": sources[index - 1].get("title") or "Untitled source",
            "url": sources[index - 1].get("url", ""),
            "credibility": sources[index - 1].get("credibility") or 0.0,
        }
        for index in listed
    ]
    return grounded


def _words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def measure_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Measured (not estimated) quality facts about a report."""
    findings = report.get("key_findings") or []
    cited = sum(1 for finding in findings if _CITATION.search(finding or ""))
    coverage = cited / len(findings) if findings else 0.0
    return {
        "word_count": _words(report.get("summary", "")),
        "citation_coverage": round(coverage, 2),
        "section_completeness": {
            section: bool(report.get(section)) for section in REQUIRED_SECTIONS
        },
    }


def deterministic_warnings(measured: Dict[str, Any]) -> List[str]:
    """Hard failures that override an optimistic LLM verdict."""
    warnings = []
    missing = [name for name, ok in measured["section_completeness"].items() if not ok]
    if missing:
        warnings.append(f"Missing or empty sections: {', '.join(missing)}")
    if measured["word_count"] < MIN_SUMMARY_WORDS:
        warnings.append(
            f"Summary is too short ({measured['word_count']} words; "
            f"minimum {MIN_SUMMARY_WORDS})"
        )
    if measured["citation_coverage"] < MIN_CITATION_COVERAGE:
        warnings.append(
            f"Only {measured['citation_coverage']:.0%} of key findings carry a citation "
            f"(minimum {MIN_CITATION_COVERAGE:.0%})"
        )
    return warnings
