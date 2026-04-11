"""
roadmap_generator/session_store.py

In-memory registry for *active* roadmap-generation sessions, backed by
MongoDB for durable persistence.

Lifecycle
---------
1. session_store.start()       — create in-memory entry + MongoDB document
2. session_store.update_step() — persist each generation step to MongoDB
3. session_store.end()         — write final roadmap; keep in-memory entry
4. session_store.remove()      — evict from in-memory dict after response sent

Pipeline step field paths (examples)
--------------------------------------
    "pipeline.skill_analysis"
    "pipeline.gap_detection"
    "pipeline.roadmap_generation"

In-memory dict schema (per session_id)
---------------------------------------
{
    "session_id":  str,
    "user_id":     str,
    "status":      "active" | "ended" | "failed",
    "roadmap":     {},   # latest roadmap snapshot
    "meta":        {},
}
"""

import logging
from typing import Any

from db.session_repository import session_repository

log = logging.getLogger("roadmap_generator.session_store")

# ── In-memory registry ────────────────────────────────────────────────────────
session_store: dict[str, dict] = {}


# ── Public API ────────────────────────────────────────────────────────────────

async def start(
    session_id: str,
    user_id: str,
    input_data: dict,
    parent_session_id: str | None = None,
) -> dict:
    """
    Register a new roadmap-generation session.

    ``input_data`` should include the parsed resume / skill profile sent by
    the Spring Boot middleware:
        { "skills": [...], "target_role": "...", "experience_level": "..." }

    Returns the in-memory session dict.
    """
    await session_repository.create_session({
        "session_id":        session_id,
        "parent_session_id": parent_session_id,
        "user_id":           user_id,
        "app_id":            "roadmap_generator",
        "input":             input_data,
        "status":            "in_progress",
    })

    entry = {
        "session_id": session_id,
        "user_id":    user_id,
        "status":     "active",
        "roadmap":    {},
        "meta":       {},
    }
    session_store[session_id] = entry
    log.info(
        "Roadmap session started  session_id=%s  user_id=%s", session_id, user_id
    )
    return entry


async def update_step(
    session_id: str,
    field_path: str,
    value: Any,
) -> None:
    """
    Persist a pipeline step result to MongoDB.

    Examples
    --------
        await update_step(sid, "pipeline.skill_analysis", {
            "model":     "claude-sonnet",
            "output":    { "existing_skills": [...], "skill_gaps": [...] },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await update_step(sid, "pipeline.roadmap_generation", {
            "model":     "claude-opus",
            "output":    { "phases": [...] },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    """
    await session_repository.update_session_step(session_id, field_path, value)

    # Mirror roadmap output into memory for fast access by follow-up requests
    if "roadmap_generation" in field_path and session_id in session_store:
        roadmap_output = value.get("output")
        if roadmap_output:
            session_store[session_id]["roadmap"] = roadmap_output


async def end(
    session_id: str,
    final_response: dict,
    status: str = "completed",
) -> None:
    """
    Mark the session as finished in MongoDB.

    ``final_response`` example:
        {
            "roadmap":          { "phases": [...] },
            "summary":          "12-week path to become a backend engineer",
            "recommended_next": ["...", "..."],
        }
    """
    await session_repository.complete_session(
        session_id,
        final_response=final_response,
        status=status,
    )
    if session_id in session_store:
        session_store[session_id]["status"] = "ended"

    log.info(
        "Roadmap session ended  session_id=%s  status=%s", session_id, status
    )


def remove(session_id: str) -> None:
    """Evict the session from the in-memory registry."""
    session_store.pop(session_id, None)
    log.debug("Roadmap session removed from memory  session_id=%s", session_id)


def get(session_id: str) -> dict | None:
    """Return the in-memory session entry, or None if not present."""
    return session_store.get(session_id)


async def get_or_resume(
    user_id: str,
    session_id: str | None = None,
) -> dict | None:
    """
    Resume logic — check in-memory first, then MongoDB.

    Returns the MongoDB document of an existing in-progress session,
    or None if no such session exists.
    """
    if session_id and session_id in session_store:
        return session_store[session_id]

    existing = await session_repository.get_active_session(
        user_id=user_id,
        app_id="roadmap_generator",
    )
    if existing:
        log.info(
            "Resuming existing roadmap session  session_id=%s  user_id=%s",
            existing["session_id"], user_id,
        )
    return existing


async def fail(session_id: str, reason: str = "unexpected error") -> None:
    """Mark a session as failed (call inside except blocks)."""
    await session_repository.fail_session(session_id, reason=reason)
    if session_id in session_store:
        session_store[session_id]["status"] = "failed"
    log.warning(
        "Roadmap session marked failed  session_id=%s  reason=%s",
        session_id, reason,
    )