"""
code_reviewer/routers/review.py

MIGRATION NOTE
--------------
REMOVED:
  - import of middleware_client object
  - middleware_client.dispatch() calls

CHANGED:
  - session_store.create(session) now also calls await session_store.init_db_session()
  - middleware_client.dispatch() replaced with:
      await session_store.persist_step(...)  — after each pipeline step
      await session_store.complete(...)      — after optimize completes
  - Best-effort db_fail() call added in except blocks
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from code_reviewer.models import (
    StartReviewRequest,
    StartReviewResponse,
    OptimizeResponse,
    SessionStatusResponse,
    ReviewSession,
)
from code_reviewer.session_store import session_store
from code_reviewer.reviewer_agent import (
    run_code_understander,
    run_technical_reviewer,
    run_quality_reviewer,
    optimize_code_with_validation,
)
from code_reviewer.exceptions import (
    SessionNotFoundError,
    SessionAlreadyCompletedError,
    InvalidInputError,
)
from code_reviewer.logger import get_logger

log    = get_logger("routers.review")
router = APIRouter(prefix="/review", tags=["Review"])


@router.post("/start", response_model=StartReviewResponse)
async def start_review(request: StartReviewRequest):
    """
    Start a new code review session.

    Pipeline:
    1. Code Understanding  (Claude Sonnet)
    2. Technical Review    (Claude Opus)
    3. Quality Review      (GPT)

    Each step is persisted to MongoDB as it completes.
    Returns the session ID and all review results.
    """
    if not request.code or not request.code.strip():
        raise InvalidInputError("Code cannot be empty")

    log.info("Starting new code review session")

    session_id: str | None = None

    try:
        # ── Resume check ──────────────────────────────────────────────────────
        if request.user_id:
            existing = await session_store.get_or_resume(request.user_id)
            if existing:
                log.info(
                    "Resuming existing code-review session  session_id=%s",
                    existing["session_id"],
                )
                # Return a minimal response so the client can continue
                return StartReviewResponse(
                    session_id       = uuid.UUID(existing["session_id"]),
                    understanding    = existing.get("pipeline", {})
                                               .get("code_understanding", {})
                                               .get("output"),
                    technical_review = existing.get("pipeline", {})
                                               .get("technical_review", {})
                                               .get("output"),
                    quality_review   = existing.get("pipeline", {})
                                               .get("quality_review", {})
                                               .get("output"),
                )

        # ── Pipeline ──────────────────────────────────────────────────────────
        log.info("Running Code Understanding Agent...")
        understanding = run_code_understander(request.code)
        log.info("✓ Understanding complete")

        log.info("Running Technical Review Agent...")
        technical_review = run_technical_reviewer(request.code, understanding)
        log.info("✓ Technical Review complete")

        log.info("Running Code Quality Agent...")
        quality_review = await run_quality_reviewer(
            request.code, understanding, technical_review,
        )
        log.info("✓ Quality Review complete")

        # ── Create session ────────────────────────────────────────────────────
        session = ReviewSession(
            session_id       = uuid.uuid4(),
            original_code    = request.code,
            user_id          = request.user_id,
            metadata         = request.metadata or {},
            understanding    = understanding,
            technical_review = technical_review,
            quality_review   = quality_review,
            started_at       = datetime.now(timezone.utc),
        )
        session_id = str(session.session_id)

        # In-memory registration
        session_store.create(session)

        # MongoDB: initial document
        await session_store.init_db_session(
            session_id = session_id,
            user_id    = request.user_id or "anonymous",
            input_data = {
                "type":    "code",
                "content": request.code,
                "meta":    request.metadata or {},
            },
        )

        now_iso = datetime.now(timezone.utc).isoformat()

        # MongoDB: persist each pipeline step
        await session_store.persist_step(session_id, "pipeline.code_understanding", {
            "output":    understanding,
            "timestamp": now_iso,
        })
        await session_store.persist_step(session_id, "pipeline.technical_review", {
            "output":    technical_review,
            "timestamp": now_iso,
        })
        await session_store.persist_step(session_id, "pipeline.quality_review", {
            "output":    quality_review,
            "timestamp": now_iso,
        })

        log.info("Code review session created  session_id=%s", session_id)

        return StartReviewResponse(
            session_id       = session.session_id,
            understanding    = understanding,
            technical_review = technical_review,
            quality_review   = quality_review,
        )

    except InvalidInputError:
        raise
    except Exception as exc:
        log.exception("Failed to start review")
        if session_id:
            try:
                await session_store.mark_failed(session_id, reason=str(exc))
            except Exception:
                pass
        raise


@router.post("/optimize/{session_id}", response_model=OptimizeResponse)
async def optimize_code(session_id: uuid.UUID):
    """
    Optimize the code from a review session.

    Pipeline:
    - Claude generates optimized code from all reviews
    - GPT validates the optimization
    - Repeats up to 3 times until valid

    On success the complete session is persisted to MongoDB.
    """
    session    = session_store.get(session_id)
    session_id_str = str(session_id)

    if session.is_optimized:
        log.info("Session %s already optimized — returning cached result", session_id)
        return OptimizeResponse(
            optimized_code       = session.optimized_code,
            changes_made         = session.optimization_details.changes_made
                                   if session.optimization_details else [],
            optimization_summary = session.optimization_details.optimization_summary
                                   if session.optimization_details else "",
        )

    log.info("Optimizing code for session %s", session_id)

    try:
        optimization_result = await optimize_code_with_validation(
            code             = session.original_code,
            understanding    = session.understanding,
            technical_review = session.technical_review,
            quality_review   = session.quality_review,
        )

        session.optimized_code       = optimization_result.optimized_code
        session.optimization_details = optimization_result
        session.is_optimized         = True
        session.is_completed         = True
        session.completed_at         = datetime.now(timezone.utc)
        session_store.update(session)

        log.info("✓ Optimization complete for session %s", session_id)

        # Persist optional step to MongoDB
        await session_store.persist_step(session_id_str, "optional_steps.optimize", {
            "enabled":      True,
            "final_output": optimization_result.optimized_code,
            "changes_made": optimization_result.changes_made,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        })

        # Mark session as complete in MongoDB
        await session_store.complete(
            session_id_str,
            final_response={
                "summary":      optimization_result.optimization_summary,
                "final_code":   optimization_result.optimized_code,
                "highlights":   optimization_result.changes_made,
            },
        )

        return OptimizeResponse(
            optimized_code       = optimization_result.optimized_code,
            changes_made         = optimization_result.changes_made,
            optimization_summary = optimization_result.optimization_summary,
        )

    except Exception as exc:
        log.exception("Failed to optimize code for session %s", session_id)
        try:
            await session_store.mark_failed(session_id_str, reason=str(exc))
        except Exception:
            pass
        raise


@router.get("/status/{session_id}", response_model=SessionStatusResponse)
async def get_session_status(session_id: uuid.UUID):
    """Get the current status of a review session."""
    session = session_store.get(session_id)
    return SessionStatusResponse(
        session_id        = session.session_id,
        is_completed      = session.is_completed,
        is_optimized      = session.is_optimized,
        has_understanding = session.understanding is not None,
        has_technical_review = session.technical_review is not None,
        has_quality_review   = session.quality_review is not None,
        chat_turns        = len(session.chat_history),
        started_at        = session.started_at,
        completed_at      = session.completed_at,
    )