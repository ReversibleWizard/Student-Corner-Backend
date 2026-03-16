from fastapi import APIRouter, HTTPException

from ai_interviewer.models import (
    StartSessionRequest, StartSessionResponse,
    SessionSummaryResponse, InterviewContext,
)
from ai_interviewer.interviewer_agent import InterviewerAgent
from ai_interviewer.session_store import session_store
from ai_interviewer.services.middleware_client import middleware_client
from ai_interviewer.exceptions import (
    InterviewerBaseError, MiddlewareDispatchError,
)
from ai_interviewer.logger import get_logger

log    = get_logger(__name__)
router = APIRouter(prefix="/interview", tags=["AI Interviewer"])

# NOTE: ResumeLoader is intentionally NOT imported here.
# Resume parsing is the middleware's responsibility.
# The middleware sends pre-parsed resume text in req.resume_text.


@router.post("/start", response_model=StartSessionResponse)
async def start_session(req: StartSessionRequest):
    """
    Create a new interview session.

    The middleware is responsible for parsing the candidate's PDF resume
    and passing the extracted plain text in `resume_text`.
    The backend uses that text directly — no file I/O or PDF parsing here.
    """
    try:
        if not req.resume_text.strip():
            log.warning(
                "Session start for '%s' — resume_text is empty. "
                "Agents will work without resume context.",
                req.name,
            )

        context    = InterviewContext()
        agent      = InterviewerAgent(context=context, req=req, resume=req.resume_text)
        session_id = session_store.create(agent)

        opening = (
            f"👋 Welcome, **{req.name}**! I'll be your interviewer today.\n\n"
            f"We'll cover {req.max_questions} questions in about {req.duration_minutes} minutes "
            f"for the role of **{req.target_role}**.\n\n"
            f"Let's begin — please introduce yourself."
        )
        log.info("Session started: %s for '%s' (resume_len=%d)", session_id, req.name, len(req.resume_text))
        return StartSessionResponse(session_id=session_id, opening_message=opening)

    except InterviewerBaseError:
        raise
    except Exception as exc:
        log.error("Unexpected error starting session: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to start interview session.")


@router.post("/{session_id}/end", response_model=SessionSummaryResponse)
async def end_session(session_id: str):
    """
    Manually end the interview, generate summary, and dispatch session
    data to the Spring Boot middleware for storage.
    """
    try:
        agent   = session_store.get(session_id)
        summary = await agent.end_interview()

        if agent.last_summary:
            try:
                await middleware_client.dispatch(
                    session_id = session_id,
                    context    = agent.ctx,
                    req        = agent.req,
                    summary    = agent.last_summary,
                )
            except MiddlewareDispatchError as exc:
                log.warning(
                    "Middleware dispatch failed for session '%s' (data may not be stored): %s",
                    session_id, exc.detail,
                )

        session_store.delete(session_id)
        return SessionSummaryResponse(summary=summary)

    except InterviewerBaseError:
        raise
    except Exception as exc:
        log.error("Unexpected error ending session '%s': %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Failed to end interview session.")


@router.get("/{session_id}/status")
async def session_status(session_id: str):
    """Return current session progress."""
    try:
        agent = session_store.get(session_id)
        ctx   = agent.ctx
        return {
            "session_id":         session_id,
            "question_count":     ctx.question_count,
            "max_questions":      agent.req.max_questions,
            "topics_covered":     ctx.topics_covered,
            "current_topic":      ctx.current_topic,
            "current_difficulty": ctx.current_difficulty,
            "is_completed":       ctx.is_completed,
        }
    except InterviewerBaseError:
        raise
    except Exception as exc:
        log.error("Error fetching status for '%s': %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch session status.")