"""
ai_interviewer/routers/session.py

MIGRATION NOTE
--------------
REMOVED:
  - import of middleware_client object
  - MiddlewareDispatchError import
  - local _agents dict (moved to session_store as the shared agent registry)

CHANGED:
  - agent storage  → register_agent() / get_agent() / remove_agent()
  - middleware dispatch → db_end() / db_fail() (MongoDB persistence)
"""

import uuid

from fastapi import APIRouter, HTTPException

from ai_interviewer.models import (
    StartSessionRequest, StartSessionResponse,
    SessionSummaryResponse, InterviewContext,
)
from ai_interviewer.interviewer_agent import InterviewerAgent
from ai_interviewer.session_store import (
    register_agent,
    get_agent,
    remove_agent,
    start        as db_start,
    end          as db_end,
    fail         as db_fail,
    get_or_resume,
    session_store,          # alias for _agents dict — used by /health for len()
)
from ai_interviewer.exceptions import InterviewerBaseError
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

    Resume logic: if an in-progress session already exists for this user in
    MongoDB, that session is resumed instead of creating a new one.
    """
    try:
        if not req.resume_text.strip():
            log.warning(
                "Session start for '%s' — resume_text is empty. "
                "Agents will work without resume context.",
                req.name,
            )

        user_id = getattr(req, "user_id", None) or req.name

        # ── Resume check ──────────────────────────────────────────────────────
        existing = await get_or_resume(user_id)

        if existing:
            session_id = existing["session_id"]
            log.info(
                "Resuming existing session  session_id=%s  user_id=%s",
                session_id, user_id,
            )
            # Re-create agent in memory if the server was restarted
            try:
                get_agent(session_id)
            except Exception:
                context = InterviewContext()
                agent   = InterviewerAgent(context=context, req=req, resume=req.resume_text)
                register_agent(session_id, agent)

            opening = (
                f"👋 Welcome back, **{req.name}**! Resuming your interview.\n\n"
                "Let's continue from where we left off."
            )
            return StartSessionResponse(session_id=session_id, opening_message=opening)

        # ── New session ───────────────────────────────────────────────────────
        session_id = str(uuid.uuid4())
        context    = InterviewContext()
        agent      = InterviewerAgent(context=context, req=req, resume=req.resume_text)

        await db_start(
            session_id = session_id,
            user_id    = user_id,
            input_data = {
                "name":             req.name,
                "target_role":      req.target_role,
                "max_questions":    req.max_questions,
                "duration_minutes": req.duration_minutes,
                "resume_length":    len(req.resume_text),
            },
        )
        register_agent(session_id, agent)

        opening = (
            f"👋 Welcome, **{req.name}**! I'll be your interviewer today.\n\n"
            f"We'll cover {req.max_questions} questions in about "
            f"{req.duration_minutes} minutes for the role of **{req.target_role}**.\n\n"
            f"Let's begin — please introduce yourself."
        )
        log.info(
            "Session started  session_id=%s  user='%s'  resume_len=%d",
            session_id, req.name, len(req.resume_text),
        )
        return StartSessionResponse(session_id=session_id, opening_message=opening)

    except InterviewerBaseError:
        raise
    except Exception as exc:
        log.error("Unexpected error starting session: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to start interview session.")


@router.post("/{session_id}/end", response_model=SessionSummaryResponse)
async def end_session(session_id: str):
    """
    Manually end the interview, generate summary, and persist results to MongoDB.
    """
    try:
        agent   = get_agent(session_id)
        summary = await agent.end_interview()

        if agent.last_summary:
            await db_end(
                session_id=session_id,
                final_response={
                    "candidate":       agent.req.name,
                    "target_role":     agent.req.target_role,
                    "questions_asked": agent.ctx.question_count,
                    "topics_covered":  agent.ctx.topics_covered,
                    "summary":         summary,
                },
                status="completed",
            )
        else:
            await db_fail(session_id, reason="end_interview produced no summary")

        remove_agent(session_id)
        return SessionSummaryResponse(summary=summary)

    except InterviewerBaseError:
        raise
    except Exception as exc:
        log.error("Unexpected error ending session '%s': %s", session_id, exc)
        try:
            await db_fail(session_id, reason=str(exc))
        except Exception:
            pass
        remove_agent(session_id)
        raise HTTPException(status_code=500, detail="Failed to end interview session.")


@router.get("/{session_id}/status")
async def session_status(session_id: str):
    """Return current session progress."""
    try:
        agent = get_agent(session_id)
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