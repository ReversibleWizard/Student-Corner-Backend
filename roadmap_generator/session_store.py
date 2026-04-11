"""
roadmap_generator/session_store.py

Dual-layer store for roadmap-generation sessions.

Layer 1 — in-memory dict (`store` object, original API preserved):
    store.create_session()
    store.update_roadmap(session_id, data, user_message)
    store.get_session(session_id)
    store.delete_session(session_id)
    store.restore_session(session_id, roadmap, chat_history, version)

Layer 2 — MongoDB persistence (async helpers):
    await store.db_create(session_id, user_id, input_data)
    await store.db_update_step(session_id, field_path, value)
    await store.db_complete(session_id, final_response)
    await store.db_get(session_id)        → MongoDB doc or None
    await store.db_get_or_resume(user_id) → MongoDB doc or None
    await store.db_fail(session_id, reason)

The router (roadmap.py) calls in-memory methods synchronously and then
calls db_* helpers with await for persistence.
"""

import logging
import uuid
from typing import Any

from db.session_repository import session_repository
from roadmap_generator.exceptions import SessionNotFoundError

log = logging.getLogger("roadmap_generator.session_store")


class RoadmapSessionStore:
    """
    Combined in-memory + MongoDB store for roadmap sessions.

    In-memory document shape:
        {
            "session_id":   str,
            "roadmap":      dict,
            "chat_history": list,
            "version":      int,
        }
    """

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}

    # ── Original sync API (preserved) ─────────────────────────────────────────

    def create_session(self) -> str:
        """Create a blank in-memory session and return the new session_id."""
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = {
            "session_id":   session_id,
            "roadmap":      {},
            "chat_history": [],
            "version":      0,
        }
        log.info("Roadmap session created (memory)  session_id=%s", session_id)
        return session_id

    def update_roadmap(
        self,
        session_id: str,
        roadmap_data: dict,
        user_message: str = "",
    ) -> None:
        """Update the in-memory roadmap and append a chat history entry."""
        session = self._get_or_raise(session_id)
        session["roadmap"] = roadmap_data
        session["version"] += 1
        if user_message:
            session["chat_history"].append({
                "version": session["version"],
                "message": user_message,
            })

    def get_session(self, session_id: str) -> dict:
        """Return the in-memory session dict.  Raises SessionNotFoundError."""
        return self._get_or_raise(session_id)

    def delete_session(self, session_id: str) -> None:
        """Remove the session from memory (MongoDB doc is kept for history)."""
        self._sessions.pop(session_id, None)
        log.debug("Roadmap session evicted from memory  session_id=%s", session_id)

    def restore_session(
        self,
        session_id: str,
        roadmap: dict,
        chat_history: list,
        version: int = 1,
    ) -> None:
        """Re-hydrate an in-memory entry from a retrieved document."""
        self._sessions[session_id] = {
            "session_id":   session_id,
            "roadmap":      roadmap,
            "chat_history": chat_history,
            "version":      version,
        }
        log.info("Roadmap session restored to memory  session_id=%s", session_id)

    def _get_or_raise(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Session '{session_id}' not found.")
        return session

    # ── Async MongoDB helpers ─────────────────────────────────────────────────

    async def db_create(
        self,
        session_id: str,
        user_id: str,
        input_data: dict,
    ) -> None:
        """
        Write the initial MongoDB document.
        Call once from the /generate router after create_session().

        Example:
            session_id = store.create_session()
            await store.db_create(session_id, user_id=req.user_id, input_data={...})
        """
        await session_repository.create_session({
            "session_id": session_id,
            "user_id":    user_id,
            "app_id":     "roadmap_generator",
            "input":      input_data,
            "status":     "in_progress",
        })

    async def db_update_step(
        self,
        session_id: str,
        field_path: str,
        value: Any,
    ) -> None:
        """
        Persist one pipeline step to MongoDB.

        field_path examples:
            "pipeline.roadmap_generation"
            "pipeline.chat_update"
        """
        await session_repository.update_session_step(session_id, field_path, value)

    async def db_complete(
        self,
        session_id: str,
        final_response: dict,
        status: str = "completed",
    ) -> None:
        """Mark the session as finished in MongoDB."""
        await session_repository.complete_session(
            session_id, final_response=final_response, status=status,
        )
        log.info("Roadmap session completed in DB  session_id=%s", session_id)

    async def db_get(self, session_id: str) -> dict | None:
        """Fetch a session document from MongoDB by session_id."""
        return await session_repository.get_session_by_id(session_id)

    async def db_get_or_resume(self, user_id: str) -> dict | None:
        """Return an in-progress MongoDB session for this user, or None."""
        return await session_repository.get_active_session(
            user_id=user_id, app_id="roadmap_generator",
        )

    async def db_fail(
        self,
        session_id: str,
        reason: str = "unexpected error",
    ) -> None:
        """Mark a session as failed in MongoDB."""
        await session_repository.fail_session(session_id, reason=reason)


# ── Module-level singleton ────────────────────────────────────────────────────
store = RoadmapSessionStore()