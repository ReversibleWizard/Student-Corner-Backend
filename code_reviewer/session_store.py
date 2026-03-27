"""Thread-safe in-memory session registry for coder_reviewer."""

from __future__ import annotations

import threading
import uuid

from .logger import get_logger
from .models import ReviewSession

logger = get_logger(__name__)


class SessionStore:
    """Thread-safe registry that maps session IDs to ReviewSession objects."""

    def __init__(self) -> None:
        self._store: dict[str, ReviewSession] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    def create(self, code: str) -> ReviewSession:
        """Create a new session and return it."""
        session_id = str(uuid.uuid4())
        session = ReviewSession(session_id=session_id, original_code=code, current_code=code)
        with self._lock:
            self._store[session_id] = session
        logger.info("Session created: %s", session_id)
        return session

    def get(self, session_id: str) -> ReviewSession | None:
        """Return the session or None if not found."""
        with self._lock:
            return self._store.get(session_id)

    def update(self, session: ReviewSession) -> None:
        """Persist an updated session object."""
        from datetime import datetime

        session.updated_at = datetime.utcnow()
        with self._lock:
            self._store[session.session_id] = session
        logger.debug("Session updated: %s", session.session_id)

    def delete(self, session_id: str) -> bool:
        """Remove a session. Returns True if it existed."""
        with self._lock:
            existed = session_id in self._store
            self._store.pop(session_id, None)
        if existed:
            logger.info("Session deleted: %s", session_id)
        return existed

    def all_ids(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# Module-level singleton — import this everywhere
session_store = SessionStore()
