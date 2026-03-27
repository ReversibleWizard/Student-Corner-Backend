"""Review action routes — optimize and chat-refine.

  POST /review/optimize   — run Claude ↔ GPT optimization loop
  POST /review/chat       — apply a user refinement and validate
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..exceptions import MiddlewareError
from ..logger import get_logger
from ..models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatRole,
    OptimizeRequest,
    OptimizeResponse,
)
from ..reviewer_agent import run_chat_refinement, run_optimizer
from ..services.middleware_client import middleware_client
from ..session_store import session_store

router = APIRouter(prefix="/review", tags=["coder-reviewer-actions"])
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# POST /review/optimize
# ---------------------------------------------------------------------------


@router.post("/optimize", response_model=OptimizeResponse)
async def optimize_code(body: OptimizeRequest, background_tasks: BackgroundTasks):
    """Run the Claude → GPT optimization loop and update the session."""
    session = session_store.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{body.session_id}' not found.")

    code_to_optimize = session.current_code or session.original_code
    try:
        optimized = await run_optimizer(code_to_optimize, session)
    except Exception as exc:
        logger.exception("[%s] Optimize failed: %s", body.session_id, exc)
        raise HTTPException(status_code=500, detail=f"Optimization error: {exc}") from exc

    session.optimized_code = optimized
    session.current_code = optimized
    session_store.update(session)

    background_tasks.add_task(_push_to_middleware, body.session_id)

    return OptimizeResponse(session_id=body.session_id, optimized_code=optimized)


# ---------------------------------------------------------------------------
# POST /review/chat
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat_refine(body: ChatRequest, background_tasks: BackgroundTasks):
    """Apply a user-requested refinement, validate with GPT, persist to middleware."""
    session = session_store.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{body.session_id}' not found.")

    # Append user message to chat history
    session.chat_history.append(
        ChatMessage(role=ChatRole.USER, content=body.message, timestamp=datetime.utcnow())
    )

    code = session.current_code or session.original_code
    try:
        result = await run_chat_refinement(code, session, body.message)
    except Exception as exc:
        logger.exception("[%s] Chat refinement failed: %s", body.session_id, exc)
        raise HTTPException(status_code=500, detail=f"Chat refinement error: {exc}") from exc

    # Append assistant response to chat history
    session.chat_history.append(
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content=result.explanation,
            timestamp=datetime.utcnow(),
        )
    )
    session.current_code = result.updated_code
    session_store.update(session)

    background_tasks.add_task(_push_to_middleware, body.session_id)

    return ChatResponse(
        session_id=body.session_id,
        updated_code=result.updated_code,
        changes_made=result.changes_made,
        explanation=result.explanation,
    )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


async def _push_to_middleware(session_id: str) -> None:
    session = session_store.get(session_id)
    if not session:
        return
    try:
        await middleware_client.store_session(session)
    except MiddlewareError as exc:
        logger.error("[%s] Background middleware push failed: %s", session_id, exc)
