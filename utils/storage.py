"""
In-memory research history store with thread-safe access.
Stores completed research sessions for retrieval, listing, and export.
"""
import threading
from datetime import datetime, timezone
from typing import Optional


class ResearchStore:
    """Thread-safe in-memory store for research sessions."""

    def __init__(self, max_sessions: int = 200):
        self._store: dict = {}
        self._max_sessions = max_sessions
        self._lock = threading.Lock()

    def save(self, thread_id: str, query: str, status: str, report: Optional[dict] = None,
             metadata: Optional[dict] = None, preview: Optional[dict] = None) -> dict:
        """Save or update a research session."""
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            if thread_id in self._store:
                # Update existing
                entry = self._store[thread_id]
                entry["status"] = status
                entry["updated_at"] = now
                if report is not None:
                    entry["report"] = report
                if metadata is not None:
                    entry["metadata"] = metadata
                if preview is not None:
                    entry["preview"] = preview
            else:
                # Create new
                entry = {
                    "thread_id": thread_id,
                    "query": query,
                    "status": status,
                    "report": report,
                    "preview": preview,
                    "metadata": metadata,
                    "created_at": now,
                    "updated_at": now,
                }
                self._store[thread_id] = entry
                # Bound memory: evict the oldest sessions beyond the cap.
                while len(self._store) > self._max_sessions:
                    oldest = min(self._store.values(), key=lambda x: x["created_at"])
                    del self._store[oldest["thread_id"]]
            return entry

    def get(self, thread_id: str) -> Optional[dict]:
        """Get a single research session by thread_id."""
        with self._lock:
            return self._store.get(thread_id)

    def list_all(self, limit: int = 50, offset: int = 0) -> list:
        """List all research sessions, newest first."""
        with self._lock:
            sessions = sorted(
                self._store.values(),
                key=lambda x: x.get("created_at", ""),
                reverse=True
            )
            return sessions[offset:offset + limit]

    def delete(self, thread_id: str) -> bool:
        """Delete a research session. Returns True if found and deleted."""
        with self._lock:
            if thread_id in self._store:
                del self._store[thread_id]
                return True
            return False

    def count(self) -> int:
        """Return total number of stored sessions."""
        with self._lock:
            return len(self._store)


# Global singleton
from config.settings import settings  # noqa: E402

research_store = ResearchStore(max_sessions=settings.max_stored_sessions)
