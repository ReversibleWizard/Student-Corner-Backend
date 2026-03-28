"""
Review session endpoints:
- POST /review/start         — Start a new code review
- POST /review/{id}/optimize — Optimize the reviewed code
- GET  /review/{id}/status   — Get session status
"""
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

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
from code_reviewer.services.middleware_client import middleware_client
from code_reviewer.exceptions import (
    SessionNotFoundError,
    SessionAlreadyCompletedError,
    InvalidInputError,
)
from code_reviewer.logger import get_logger

log = get_logger("routers.review")

router = APIRouter(prefix="/review", tags=["Review"])


@router.post("/start", response_model=StartReviewResponse)
async def start_review(request: StartReviewRequest):
    """
    Start a new code review session.
    
    Runs the initial pipeline:
    1. Code Understanding (Claude Sonnet)
    2. Technical Review (Claude Opus)
    3. Quality Review (GPT)
    
    Returns the session ID and all review results.
    """
    if not request.code or not request.code.strip():
        raise InvalidInputError("Code cannot be empty")
    
    log.info("Starting new code review session")
    
    try:
        # Run initial pipeline
        log.info("Running Code Understanding Agent...")
        understanding = run_code_understander(request.code)
        log.info("✓ Understanding complete")
        
        log.info("Running Technical Review Agent...")
        technical_review = run_technical_reviewer(request.code, understanding)
        log.info("✓ Technical Review complete")
        
        log.info("Running Code Quality Agent...")
        quality_review = await run_quality_reviewer(
            request.code,
            understanding,
            technical_review
        )
        log.info("✓ Quality Review complete")
        
        # Create session
        session = ReviewSession(
            session_id=uuid.uuid4(),
            original_code=request.code,
            user_id=request.user_id,
            metadata=request.metadata or {},
            understanding=understanding,
            technical_review=technical_review,
            quality_review=quality_review,
            started_at=datetime.utcnow(),
        )
        
        session_store.create(session)
        log.info(f"Created session {session.session_id}")
        
        return StartReviewResponse(
            session_id=session.session_id,
            understanding=understanding,
            technical_review=technical_review,
            quality_review=quality_review,
        )
    
    except Exception as e:
        log.exception("Failed to start review")
        raise


@router.post("/optimize/{session_id}", response_model=OptimizeResponse)
async def optimize_code(session_id: uuid.UUID):
    """
    Optimize the code from a review session.
    
    Runs the optimization pipeline:
    - Claude generates optimized code based on all reviews
    - GPT validates the optimization
    - Repeats up to 3 times until valid
    
    On success, dispatches the complete session to middleware.
    """
    session = session_store.get(session_id)
    
    if session.is_optimized:
        # Already optimized — return cached result
        log.info(f"Session {session_id} already optimized, returning cached result")
        return OptimizeResponse(
            optimized_code=session.optimized_code,
            changes_made=session.optimization_details.changes_made if session.optimization_details else [],
            optimization_summary=session.optimization_details.optimization_summary if session.optimization_details else "",
        )
    
    log.info(f"Optimizing code for session {session_id}")
    
    try:
        # Run optimization with validation
        optimization_result = await optimize_code_with_validation(
            code=session.original_code,
            understanding=session.understanding,
            technical_review=session.technical_review,
            quality_review=session.quality_review,
        )
        
        # Update session
        session.optimized_code = optimization_result.optimized_code
        session.optimization_details = optimization_result
        session.is_optimized = True
        session.is_completed = True
        session.completed_at = datetime.utcnow()
        session_store.update(session)
        
        log.info(f"✓ Optimization complete for session {session_id}")
        
        # Dispatch to middleware (non-blocking, errors logged but not raised)
        try:
            await middleware_client.dispatch(session)
        except Exception as e:
            log.error(f"Middleware dispatch failed: {e}", exc_info=True)
        
        return OptimizeResponse(
            optimized_code=optimization_result.optimized_code,
            changes_made=optimization_result.changes_made,
            optimization_summary=optimization_result.optimization_summary,
        )
    
    except Exception as e:
        log.exception(f"Failed to optimize code for session {session_id}")
        raise


@router.get("/status/{session_id}", response_model=SessionStatusResponse)
async def get_session_status(session_id: uuid.UUID):
    """Get the current status of a review session."""
    session = session_store.get(session_id)
    
    return SessionStatusResponse(
        session_id=session.session_id,
        is_completed=session.is_completed,
        is_optimized=session.is_optimized,
        has_understanding=session.understanding is not None,
        has_technical_review=session.technical_review is not None,
        has_quality_review=session.quality_review is not None,
        chat_turns=len(session.chat_history),
        started_at=session.started_at,
        completed_at=session.completed_at,
    )