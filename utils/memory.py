"""
SQLite-backed long-term memory for research runs.

Stores prior research runs by topic hash, so repeated or highly related
queries can reuse prior findings instead of re-searching from scratch.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from utils.logger import logger

# Simple local embeddings fallback if needed (using sentence-transformers)
_embedder = None


def _get_embedder() -> Any:
    """Lazy load sentence_transformers to save memory if not used."""
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer

            _embedder = SentenceTransformer(settings.embeddings_model)
        except ImportError:
            logger.warning("sentence_transformers_not_installed")
            _embedder = False
    return _embedder


def _normalize_query(query: str) -> str:
    """Basic normalization for exact hashing."""
    return " ".join(query.lower().split())


def _hash_query(query: str) -> str:
    """SHA-256 hash of normalized query."""
    return hashlib.sha256(_normalize_query(query).encode("utf-8")).hexdigest()


class SQLiteResearchMemory:
    """SQLite-backed storage for completed research runs."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    query_hash TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    embedding BLOB,
                    plan JSON,
                    final_report JSON,
                    created_at TIMESTAMP
                )
                """
            )
            conn.commit()

    def store_run(
        self, query: str, plan: Dict[str, Any], final_report: Dict[str, Any]
    ) -> None:
        """Store a completed research run."""
        query_hash = _hash_query(query)
        embedding_blob = None

        embedder = _get_embedder()
        if embedder:
            try:
                import numpy as np

                emb = embedder.encode(query)
                embedding_blob = np.array(emb, dtype=np.float32).tobytes()
            except Exception as e:
                logger.warning("embedding_failed", error=str(e))

        now = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs 
                (query_hash, query, embedding, plan, final_report, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    query_hash,
                    query,
                    embedding_blob,
                    json.dumps(plan),
                    json.dumps(final_report),
                    now,
                ),
            )
            conn.commit()
        logger.info("memory_run_stored", query_hash=query_hash)

    def get_exact_match(self, query: str) -> Optional[Dict[str, Any]]:
        """Find an exact match by query hash."""
        query_hash = _hash_query(query)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT plan, final_report FROM runs WHERE query_hash = ?",
                (query_hash,),
            ).fetchone()

            if row:
                logger.info("memory_exact_match_found", query_hash=query_hash)
                return {
                    "plan": json.loads(row["plan"]) if row["plan"] else None,
                    "final_report": json.loads(row["final_report"])
                    if row["final_report"]
                    else None,
                }
        return None

    def find_similar(
        self, query: str, similarity_threshold: float = 0.85
    ) -> List[Dict[str, Any]]:
        """Find similar past runs using cosine similarity (if embeddings enabled)."""
        embedder = _get_embedder()
        if not embedder:
            return []

        try:
            import numpy as np

            query_emb = embedder.encode(query)
        except Exception:
            return []

        results = []
        with self._get_connection() as conn:
            rows = conn.execute("SELECT query, embedding, final_report FROM runs").fetchall()
            for row in rows:
                if row["embedding"]:
                    db_emb = np.frombuffer(row["embedding"], dtype=np.float32)
                    # Cosine similarity
                    sim = np.dot(query_emb, db_emb) / (
                        np.linalg.norm(query_emb) * np.linalg.norm(db_emb)
                    )
                    if sim >= similarity_threshold:
                        results.append(
                            {
                                "query": row["query"],
                                "similarity": float(sim),
                                "final_report": json.loads(row["final_report"]),
                            }
                        )

        # Sort by similarity descending
        results.sort(key=lambda x: x["similarity"], reverse=True)
        if results:
            logger.info("memory_similar_runs_found", count=len(results))
        return results


# Global singleton if enabled
memory_store = None
if settings.memory_enabled and settings.memory_backend == "sqlite":
    memory_store = SQLiteResearchMemory(settings.memory_db_path)
