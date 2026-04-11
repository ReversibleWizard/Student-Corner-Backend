"""
ai_interviewer/routers/answer.py

MIGRATION NOTE
--------------
REMOVED:
  - import of middleware_client object
  - MiddlewareDispatchError import
  - _maybe_dispatch_to_middleware() helper
  - session_store.delete() calls (replaced with remove_agent())

ADDED:
  - get_agent()     from session_store — retrieves the InterviewerAgent
  - remove_agent()  from session_store — evicts agent from memory
  - db_end()        from session_store — persists final response to MongoDB
  - db_fail()       from session_store — marks session failed on error
"""
import threading
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import Response

from ai_interviewer.models import (
    TextAnswerRequest, InterviewerReply, VoiceAnswerResponse,
)
from ai_interviewer.session_store import (
    get_agent,
    remove_agent,
    end   as db_end,
    fail  as db_fail,
)
from ai_interviewer.services.tts import TTSService
from ai_interviewer.services.voice_agent import VoiceAgentService
from ai_interviewer.exceptions import (
    InterviewerBaseError, InvalidInputError,
    VoiceAgentNotConfiguredError, VoiceAgentError, TranscriptionError,
    TTSError,
)
from ai_interviewer.logger import get_logger

log    = get_logger(__name__)
router = APIRouter(prefix="/interview", tags=["AI Interviewer"])

MAX_TEXT_LEN   = 5_000
MAX_AUDIO_SIZE = 25 * 1024 * 1024

_tts         = TTSService()
_voice_agent = VoiceAgentService()


async def _persist_completed_session(session_id: str, agent) -> None:
    """
    Persist the finished session to MongoDB and evict the agent from memory.
    Non-fatal: logs warning on failure, never raises to the caller.
    """
    if not agent.last_summary:
        log.warning(
            "Session '%s' completed but last_summary is None — "
            "marking as failed in DB.",
            session_id,
        )
        try:
            await db_fail(session_id, reason="session completed with no summary")
        except Exception as exc:
            log.error("db_fail error for session '%s': %s", session_id, exc)
        remove_agent(session_id)
        return

    try:
        await db_end(
            session_id=session_id,
            final_response={
                "candidate":       agent.req.name,
                "target_role":     agent.req.target_role,
                "questions_asked": agent.ctx.question_count,
                "topics_covered":  agent.ctx.topics_covered,
                "summary":         agent.last_summary,
            },
            status="completed",
        )
    except Exception as exc:
        log.warning(
            "MongoDB persistence failed for session '%s' (data may be lost): %s",
            session_id, exc,
        )
    finally:
        remove_agent(session_id)


# ── Text answer ───────────────────────────────────────────────────────────────

@router.post("/answer/text", response_model=InterviewerReply)
async def text_answer(req: TextAnswerRequest):
    """
    Submit a typed answer.
    If this answer completes the session, results are persisted to MongoDB.
    """
    try:
        if not req.answer or not req.answer.strip():
            raise InvalidInputError("Answer cannot be empty.")
        if len(req.answer) > MAX_TEXT_LEN:
            raise InvalidInputError(f"Answer exceeds {MAX_TEXT_LEN} character limit.")

        agent      = get_agent(req.session_id)
        ai_message = await agent.handle_response(req.answer)

        if agent.ctx.is_completed:
            await _persist_completed_session(req.session_id, agent)

        log.info("Text answer processed for session '%s'", req.session_id)
        return ai_message

    except InterviewerBaseError:
        raise
    except Exception as exc:
        log.error("Unexpected error in text_answer for '%s': %s", req.session_id, exc)
        try:
            await db_fail(req.session_id, reason=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to process your answer.")


# ── Voice answer ──────────────────────────────────────────────────────────────

@router.post("/answer/voice", response_model=VoiceAnswerResponse)
async def voice_answer(
    session_id: str        = Form(...),
    audio:      UploadFile = File(..., description="Recorded audio (webm/wav/mp3)"),
):
    """
    Submit a voice answer.

    ElevenLabs agent returns transcript + delivery review in one call.
    The voice review is stored in InterviewContext.voice_reviews so it
    is included in the MongoDB payload at session end.

    If the voice agent fails, the interview degrades gracefully — a fallback
    review is stored and the session continues.
    """
    try:
        agent = get_agent(session_id)

        audio_bytes = await audio.read()
        if not audio_bytes:
            raise InvalidInputError("Audio file is empty.")
        if len(audio_bytes) > MAX_AUDIO_SIZE:
            raise InvalidInputError(
                f"Audio too large ({len(audio_bytes)/1024/1024:.1f} MB). "
                f"Max: {MAX_AUDIO_SIZE//1024//1024} MB."
            )

        suffix = "." + (audio.filename or "audio.webm").rsplit(".", 1)[-1]

        # ── ElevenLabs agent: transcript + delivery review ────────────────────
        transcript_holder: list[str]       = []
        review_holder:     list[str]       = []
        exc_holder:        list[Exception] = []

        def _voice_thread():
            try:
                t, r = _voice_agent.analyze(audio_bytes, suffix=suffix)
                transcript_holder.append(t)
                review_holder.append(r)
            except TranscriptionError as exc:
                exc_holder.append(exc)
            except (VoiceAgentNotConfiguredError, VoiceAgentError) as exc:
                log.warning("Voice agent degraded: %s", exc)
                review_holder.append(_voice_agent.FALLBACK_REVIEW)
            except Exception as exc:
                log.error("Unexpected voice agent error: %s", exc)
                review_holder.append(_voice_agent.FALLBACK_REVIEW)

        vt = threading.Thread(target=_voice_thread, daemon=True)
        vt.start()
        vt.join(timeout=38)

        if exc_holder and isinstance(exc_holder[0], TranscriptionError):
            raise exc_holder[0]

        if not transcript_holder:
            raise TranscriptionError(
                reason="Voice agent produced no transcript. "
                       "Audio may be silent, too short, or unsupported."
            )

        transcript = transcript_holder[0]
        raw_review = review_holder[0] if review_holder else _voice_agent.FALLBACK_REVIEW

        # Track which history index this answer will occupy
        answer_index = len(agent.ctx.history)

        # ── Process answer ────────────────────────────────────────────────────
        ai_message = await agent.handle_response(transcript)

        # Insert voice review at the correct history index
        while len(agent.ctx.voice_reviews) < answer_index:
            agent.ctx.voice_reviews.append("")
        if len(agent.ctx.voice_reviews) == answer_index:
            agent.ctx.voice_reviews.append(raw_review)

        if agent.ctx.is_completed:
            await _persist_completed_session(session_id, agent)

        log.info(
            "Voice answer processed for session '%s'  transcript_len=%d",
            session_id, len(transcript),
        )
        return VoiceAnswerResponse(
            is_completed  = ai_message.is_completed,
            transcript    = transcript,
            voice_review  = _voice_agent.format_review(raw_review),
            review        = ai_message.review,
            next_question = ai_message.next_question,
            summary       = ai_message.summary,
        )

    except InterviewerBaseError:
        raise
    except Exception as exc:
        log.error("Unexpected error in voice_answer for '%s': %s", session_id, exc)
        try:
            await db_fail(session_id, reason=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to process your voice answer.")


# ── TTS ───────────────────────────────────────────────────────────────────────

@router.post("/answer/tts")
async def get_tts_audio(req: TextAnswerRequest):
    """Convert text to MP3 bytes for the frontend. Pass text in req.answer."""
    try:
        if not req.answer or not req.answer.strip():
            raise InvalidInputError("Text for TTS cannot be empty.")
        mp3_bytes = await _tts.to_bytes(req.answer)
        return Response(content=mp3_bytes, media_type="audio/mpeg")
    except TTSError as exc:
        log.error("TTS failed: %s", exc.detail)
        raise HTTPException(status_code=502, detail=exc.message)
    except InterviewerBaseError:
        raise
    except Exception as exc:
        log.error("Unexpected TTS error: %s", exc)
        raise HTTPException(status_code=500, detail="TTS conversion failed.")