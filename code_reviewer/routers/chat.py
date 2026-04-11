"""
code_reviewer/routers/chat.py

MIGRATION NOTE
--------------
REMOVED:
  - import of middleware_client object
  - middleware_client.dispatch(session) call

CHANGED:
  - After a successful refinement, the chat_refine step is persisted to
    MongoDB via session_store.persist_step() and the session is marked
    complete via session_store.complete().
  - session_store.update(session) is kept — it updates in-memory state.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter

from code_reviewer.models import (
    ChatRequest,
    ChatResponse,
)
from code_reviewer.session_store import session_store
from code_reviewer.reviewer_agent import refine_code_with_chat
from code_reviewer.exceptions import InvalidInputError
from code_reviewer.logger import get_logger

log    = get_logger("routers.chat")
router = APIRouter(prefix="/review", tags=["Chat"])


@router.post("/chat/{session_id}", response_model=ChatResponse)
async def chat_refine(session_id: uuid.UUID, request: ChatRequest):
    """
    Refine code based on a user instruction.

    Examples:
        "Convert to Java"
        "Make it O(1)"
        "Add error handling"
        "Use async/await"

    Pipeline:
    - Claude applies the requested changes
    - GPT validates correctness
    - Repeats up to 3 times until valid

    Updates both the in-memory session and MongoDB on success.
    """
    session        = session_store.get(session_id)
    session_id_str = str(session_id)

    if not request.instruction or not request.instruction.strip():
        raise InvalidInputError("Instruction cannot be empty")

    log.info("Chat refinement for session %s: %s", session_id, request.instruction)

    try:
        current_code = session.optimized_code or session.original_code

        refinement_result = await refine_code_with_chat(
            code             = current_code,
            understanding    = session.understanding,
            technical_review = session.technical_review,
            user_instruction = request.instruction,
        )

        # ── Update in-memory session ──────────────────────────────────────────
        chat_entry = {
            "instruction":  request.instruction,
            "result":       refinement_result.explanation,
            "code":         refinement_result.updated_code,
            "changes_made": refinement_result.changes_made,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        }
        session.chat_history.append(chat_entry)
        session.optimized_code = refinement_result.updated_code

        if not session.is_completed:
            session.is_completed = True
            session.completed_at = datetime.now(timezone.utc)

        session_store.update(session)
        log.info("✓ Chat refinement complete for session %s", session_id)

        # ── Persist to MongoDB ────────────────────────────────────────────────
        try:
            await session_store.persist_step(
                session_id_str,
                "optional_steps.chat_refine",
                {
                    "enabled":    True,
                    "iterations": session.chat_history,   # full history so far
                },
            )
            await session_store.complete(
                session_id_str,
                final_response={
                    "summary":    refinement_result.explanation,
                    "final_code": refinement_result.updated_code,
                    "highlights": refinement_result.changes_made,
                },
            )
        except Exception as db_exc:
            # MongoDB failure is non-fatal — in-memory state is already updated
            log.warning(
                "MongoDB persistence failed for chat_refine session %s: %s",
                session_id, db_exc,
            )

        return ChatResponse(
            updated_code = refinement_result.updated_code,
            changes_made = refinement_result.changes_made,
            explanation  = refinement_result.explanation,
        )

    except InvalidInputError:
        raise
    except Exception:
        log.exception("Failed to refine code for session %s", session_id)
        try:
            await session_store.mark_failed(session_id_str, reason="chat_refine error")
        except Exception:
            pass
        raise