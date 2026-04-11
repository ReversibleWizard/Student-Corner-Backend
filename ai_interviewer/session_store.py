"""
ai_interviewer/session_store.py

Two responsibilities, cleanly separated:

1. IN-MEMORY AGENT REGISTRY  (_agents dict)
   InterviewerAgent objects are Python objects — not serialisable to MongoDB.
   They live here for the lifetime of the server process.
   Both routers/session.py and routers/answer.py share this registry.

2. MONGODB PERSISTENCE  (async helpers wrapping session_repository)
   Every create / step-update / completion is durably stored so that
   sessions survive server restarts and support resume logic.

Public surface
--------------
Agent registry (sync):
    register_agent(session_id, agent)
    get_agent(session_id)            → Agent  (raises SessionNotFoundError)
    remove_agent(session_id)

MongoDB helpers (async):
    start(session_id, user_id, input_data, parent_session_id=None)
    update_step(session_id, field_path, value)
    end(session_id, final_response, status="completed")
    fail(session_id, reason)
    get_or_resume(user_id, session_id=None) → MongoDB doc or None

Misc (sync):
    remove(session_id)      → evict from _meta only
    get(session_id)         → _meta entry or None
    session_store           → alias for _agents (used by /health for len())
"""

import logging
from typing import Any

from db.session_repository import session_repository
from ai_interviewer.exceptions import SessionNotFoundError

log = logging.getLogger("ai_interviewer.session_store")

# ── In-memory stores ──────────────────────────────────────────────────────────

# Full agent objects:  session_id → InterviewerAgent
_agents: dict[str, Any] = {}

# Light metadata:  session_id → {"session_id", "user_id", "status"}
_meta: dict[str, dict] = {}

# Legacy alias — used by main.py /health:  len(session_store)
session_store = _agents


# ── Agent registry (sync) ─────────────────────────────────────────────────────

def register_agent(session_id: str, agent: Any) -> None:
    """Store an InterviewerAgent in memory under session_id."""
    _agents[session_id] = agent


def get_agent(session_id: str) -> Any:
    """
    Return the agent for session_id.

    Raises SessionNotFoundError (→ HTTP 404) if the session is not in memory
    (e.g. the server was restarted after the session was created).
    """
    agent = _agents.get(session_id)
    if agent is None:
        raise SessionNotFoundError(session_id)
    return agent


def remove_agent(session_id: str) -> None:
    """Evict agent + metadata from memory after the response has been sent."""
    _agents.pop(session_id, None)
    _meta.pop(session_id, None)


# ── MongoDB helpers (async) ───────────────────────────────────────────────────

async def start(
    session_id: str,
    user_id: str,
    input_data: dict,
    parent_session_id: str | None = None,
) -> dict:
    """
    Persist a new session document to MongoDB.
    Also records light metadata in _meta for fast in-process look-ups.
    Returns the metadata entry.
    """
    await session_repository.create_session({
        "session_id":        session_id,
        "parent_session_id": parent_session_id,
        "user_id":           user_id,
        "app_id":            "ai_interviewer",
        "input":             input_data,
        "status":            "in_progress",
    })
    entry = {"session_id": session_id, "user_id": user_id, "status": "active"}
    _meta[session_id] = entry
    log.info("Interview session started  session_id=%s  user_id=%s", session_id, user_id)
    return entry


async def update_step(session_id: str, field_path: str, value: Any) -> None:
    """
    Persist one pipeline / optional-step result to MongoDB.

    Examples:
        await update_step(sid, "pipeline.question_generation", {...})
        await update_step(sid, "pipeline.answer_evaluation", {...})
        await update_step(sid, "optional_steps.follow_up", {...})
    """
    await session_repository.update_session_step(session_id, field_path, value)


async def end(
    session_id: str,
    final_response: dict,
    status: str = "completed",
) -> None:
    """Mark the session as finished and persist the final payload."""
    await session_repository.complete_session(
        session_id, final_response=final_response, status=status,
    )
    if session_id in _meta:
        _meta[session_id]["status"] = "ended"
    log.info("Interview session ended  session_id=%s  status=%s", session_id, status)


async def fail(session_id: str, reason: str = "unexpected error") -> None:
    """Mark a session as failed — call from except blocks."""
    await session_repository.fail_session(session_id, reason=reason)
    if session_id in _meta:
        _meta[session_id]["status"] = "failed"
    log.warning("Interview session failed  session_id=%s  reason=%s", session_id, reason)


def remove(session_id: str) -> None:
    """Remove from _meta only (MongoDB document is kept for history)."""
    _meta.pop(session_id, None)


def get(session_id: str) -> dict | None:
    """Return in-memory metadata entry, or None."""
    return _meta.get(session_id)


async def get_or_resume(
    user_id: str,
    session_id: str | None = None,
) -> dict | None:
    """
    Check MongoDB for an existing in-progress ai_interviewer session for user.
    Returns the MongoDB doc if found, else None.
    """
    if session_id and session_id in _meta:
        return _meta[session_id]

    existing = await session_repository.get_active_session(
        user_id=user_id, app_id="ai_interviewer",
    )
    if existing:
        log.info(
            "Resuming interview session  session_id=%s  user_id=%s",
            existing["session_id"], user_id,
        )
    return existing