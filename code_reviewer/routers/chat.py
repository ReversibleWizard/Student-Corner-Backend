"""
Chat refinement endpoint:
- POST /review/{id}/chat — Refine code based on user instructions
"""
import uuid
from datetime import datetime
from fastapi import APIRouter

from code_reviewer.models import (
    ChatRequest,
    ChatResponse,
)
from code_reviewer.session_store import session_store
from code_reviewer.reviewer_agent import refine_code_with_chat
from code_reviewer.services.middleware_client import middleware_client
from code_reviewer.exceptions import InvalidInputError
from code_reviewer.logger import get_logger

log = get_logger("routers.chat")

router = APIRouter(prefix="/review", tags=["Chat"])


@router.post("/chat/{session_id}", response_model=ChatResponse)
async def chat_refine(session_id: uuid.UUID, request: ChatRequest):
    """
    Refine code based on user instruction.
    
    Examples:
    - "Convert to Java"
    - "Make it O(1)"
    - "Add error handling"
    - "Use async/await"
    
    Runs the chat pipeline:
    - Claude applies the requested changes
    - GPT validates correctness
    - Repeats up to 3 times until valid
    
    Updates the session with the new code and chat history.
    If this completes the session, dispatches to middleware.
    """
    session = session_store.get(session_id)
    
    if not request.instruction or not request.instruction.strip():
        raise InvalidInputError("Instruction cannot be empty")
    
    log.info(f"Chat refinement for session {session_id}: {request.instruction}")
    
    try:
        # Determine current code (use optimized if available, else original)
        current_code = session.optimized_code or session.original_code
        
        # Run chat refinement with validation
        refinement_result = await refine_code_with_chat(
            code=current_code,
            understanding=session.understanding,
            technical_review=session.technical_review,
            user_instruction=request.instruction,
        )
        
        # Update session with chat history
        chat_entry = {
            "instruction": request.instruction,
            "result": refinement_result.explanation,
            "code": refinement_result.updated_code,
            "changes_made": refinement_result.changes_made,
        }
        session.chat_history.append(chat_entry)
        
        # Update optimized code with the latest refinement
        session.optimized_code = refinement_result.updated_code
        
        # Mark as completed if not already
        if not session.is_completed:
            session.is_completed = True
            session.completed_at = datetime.utcnow()
        
        session_store.update(session)
        
        log.info(f"✓ Chat refinement complete for session {session_id}")
        
        # Dispatch to middleware (non-blocking)
        try:
            await middleware_client.dispatch(session)
        except Exception as e:
            log.error(f"Middleware dispatch failed: {e}", exc_info=True)
        
        return ChatResponse(
            updated_code=refinement_result.updated_code,
            changes_made=refinement_result.changes_made,
            explanation=refinement_result.explanation,
        )
    
    except Exception as e:
        log.exception(f"Failed to refine code for session {session_id}")
        raise