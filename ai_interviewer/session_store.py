from __future__ import annotations

import uuid
import threading
from typing import TYPE_CHECKING

from ai_interviewer.exceptions import SessionNotFoundError, SessionCreationError
from ai_interviewer.logger import get_logger

if TYPE_CHECKING:
    from ai_interviewer.interviewer_agent import InterviewerAgent

log = get_logger(__name__)


class SessionStore:
    """Thread-safe in-memory registry of active interview sessions."""

    def __init__(self):
        self._sessions: dict[str, "InterviewerAgent"] = {}
        self._lock = threading.RLock()

    def create(self, agent: "InterviewerAgent") -> str:
        try:
            session_id = str(uuid.uuid4())
            with self._lock:
                self._sessions[session_id] = agent
            log.info("Session created: %s (active=%d)", session_id, len(self))
            return session_id
        except Exception as exc:
            raise SessionCreationError(
                message="Could not create interview session.", detail=str(exc)
            ) from exc

    def get(self, session_id: str) -> "InterviewerAgent":
        """Return agent or raise SessionNotFoundError."""
        with self._lock:
            agent = self._sessions.get(session_id)
        if agent is None:
            log.warning("Session not found: %s", session_id)
            raise SessionNotFoundError(session_id)
        return agent

    def get_or_none(self, session_id: str) -> "InterviewerAgent | None":
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> None:
        with self._lock:
            removed = self._sessions.pop(session_id, None)
        if removed:
            log.info("Session deleted: %s (active=%d)", session_id, len(self))

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


# ── Singleton shared across all routers ───────────────────────────────────────
session_store = SessionStore()