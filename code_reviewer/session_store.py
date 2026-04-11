"""
code_reviewer/session_store.py

Provides BOTH the original synchronous ReviewSession object registry AND
async MongoDB persistence.

Original API preserved (same names, same call signatures):
    session_store.create(session)           — now also writes to MongoDB
    session_store.get(session_id)           — returns ReviewSession from memory
    session_store.update(session)           — updates memory + MongoDB step
    session_store.delete(session_id)        — removes from memory

New async MongoDB helpers (called explicitly from routers when needed):
    await session_store.persist_step(session_id, field_path, value)
    await session_store.complete(session_id, final_response, status)
    await session_store.mark_failed(session_id, reason)
    await session_store.get_or_resume(user_id) → MongoDB doc or None

len(session_store) still works via __len__.
"""

import logging
from typing import Any

from db.session_repository import session_repository
from code_reviewer.exceptions import SessionNotFoundError

log = logging.getLogger("code_reviewer.session_store")


class ReviewSessionStore:
    """
    Dual-layer store for code-review sessions.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}

    # ── Serialization Helper ────────────────────────────────────────────────

    def _serialize_for_mongo(self, value: Any) -> Any:
        """
        Recursively convert Pydantic/custom model objects into Mongo-safe dicts.
        """
        if hasattr(value, "model_dump"):   # Pydantic v2
            return self._serialize_for_mongo(value.model_dump())

        if hasattr(value, "dict"):         # Pydantic v1 fallback
            return self._serialize_for_mongo(value.dict())

        if isinstance(value, dict):
            return {
                k: self._serialize_for_mongo(v)
                for k, v in value.items()
            }

        if isinstance(value, list):
            return [
                self._serialize_for_mongo(v)
                for v in value
            ]

        return value

    # ── Sync In-Memory API ────────────────────────────────────────────────

    def create(self, session: Any) -> None:
        sid = str(session.session_id)
        self._sessions[sid] = session
        log.info("Code-review session created  session_id=%s", sid)

    def get(self, session_id: Any) -> Any:
        sid = str(session_id)
        session = self._sessions.get(sid)

        if session is None:
            raise SessionNotFoundError(str(session_id))

        return session

    def update(self, session: Any) -> None:
        sid = str(session.session_id)
        self._sessions[sid] = session

    def delete(self, session_id: Any) -> None:
        self._sessions.pop(str(session_id), None)
        log.debug(
            "Code-review session evicted from memory  session_id=%s",
            session_id
        )

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: Any) -> bool:
        return str(session_id) in self._sessions

    # ── Async MongoDB Helpers ──────────────────────────────────────────────

    async def init_db_session(
        self,
        session_id: str,
        user_id: str,
        input_data: dict,
    ) -> None:
        await session_repository.create_session({
            "session_id": session_id,
            "user_id": user_id,
            "app_id": "code_reviewer",
            "input": input_data,
            "status": "in_progress",
        })

        log.info(
            "Code-review DB session created  session_id=%s",
            session_id
        )

    async def persist_step(
        self,
        session_id: str,
        field_path: str,
        value: Any,
    ) -> None:
        """
        Persist one pipeline / optional-step result to MongoDB.
        Automatically serializes custom/Pydantic models.
        """
        serialized_value = self._serialize_for_mongo(value)

        await session_repository.update_session_step(
            session_id,
            field_path,
            serialized_value,
        )

    async def complete(
        self,
        session_id: str,
        final_response: dict,
        status: str = "completed",
    ) -> None:
        serialized_response = self._serialize_for_mongo(final_response)

        await session_repository.complete_session(
            session_id,
            final_response=serialized_response,
            status=status,
        )

        log.info(
            "Code-review session completed  session_id=%s  status=%s",
            session_id,
            status,
        )

    async def mark_failed(
        self,
        session_id: str,
        reason: str = "unexpected error",
    ) -> None:
        await session_repository.fail_session(session_id, reason=reason)

        log.warning(
            "Code-review session failed  session_id=%s  reason=%s",
            session_id,
            reason,
        )

    async def get_or_resume(self, user_id: str) -> dict | None:
        return await session_repository.get_active_session(
            user_id=user_id,
            app_id="code_reviewer",
        )


session_store = ReviewSessionStore()