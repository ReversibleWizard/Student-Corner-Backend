"""Session management routes for the coder_reviewer agent.

  POST /review/start    — submit code, kick off full pipeline, store result
  GET  /review/status   — return current session state
  POST /review/end      — finalise & push full payload to middleware
  DELETE /review/session/{session_id} — clean up
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..exceptions import MiddlewareError, SessionNotFoundError
from ..logger import get_logger
from ..models import (
    ReviewResultResponse,
    StartReviewRequest,
    StartReviewResponse,
)
from ..reviewer_agent import run_full_review
from ..services.middleware_client import middleware_client
from ..session_store import session_store

router = APIRouter(prefix="/review", tags=["coder-reviewer-session"])
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# POST /review/start
# ---------------------------------------------------------------------------


@router.post("/start", response_model=StartReviewResponse, status_code=202)
async def start_review(body: StartReviewRequest, background_tasks: BackgroundTasks):
    """Create a session, run the full 3-agent pipeline, and store to middleware."""
    session = session_store.create(body.code)

    # Run the pipeline inline (awaited) so the caller gets a session_id they can
    # immediately poll. Switch to a background_task if you want to return instantly.
    try:
        updated = await run_full_review(body.code, session)
    except Exception as exc:
        logger.exception("[%s] Pipeline failed: %s", session.session_id, exc)
        raise HTTPException(status_code=500, detail=f"Review pipeline error: {exc}") from exc

    session_store.update(updated)

    # Persist to middleware in the background so the HTTP response is not held up.
    background_tasks.add_task(_store_to_middleware, updated.session_id)

    return StartReviewResponse(session_id=updated.session_id)


# ---------------------------------------------------------------------------
# GET /review/status/{session_id}
# ---------------------------------------------------------------------------


@router.get("/status/{session_id}", response_model=ReviewResultResponse)
async def get_review_status(session_id: str):
    """Return the current analysis results for a session."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    return ReviewResultResponse(
        session_id=session.session_id,
        understanding=session.understanding,
        technical_review=session.technical_review,
        quality_review=session.quality_review,
        optimized_code=session.optimized_code,
    )


# ---------------------------------------------------------------------------
# POST /review/end
# ---------------------------------------------------------------------------


@router.post("/end/{session_id}")
async def end_review(session_id: str):
    """Finalise the session — push complete payload (incl. chat history) to middleware."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    try:
        result = await middleware_client.store_session(session)
    except MiddlewareError as exc:
        logger.error("[%s] Middleware error on end: %s", session_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"session_id": session_id, "middleware_response": result}


# ---------------------------------------------------------------------------
# DELETE /review/session/{session_id}
# ---------------------------------------------------------------------------


@router.delete("/session/{session_id}", status_code=204)
async def delete_session(session_id: str):
    """Remove a session from the in-memory store."""
    deleted = session_store.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


async def _store_to_middleware(session_id: str) -> None:
    """Background task: push session to middleware (swallows errors to avoid crashing worker)."""
    session = session_store.get(session_id)
    if not session:
        logger.warning("Background store: session %s not found", session_id)
        return
    try:
        await middleware_client.store_session(session)
    except MiddlewareError as exc:
        logger.error("[%s] Background middleware store failed: %s", session_id, exc)
