import threading
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import Response

from ai_interviewer.models import (
    TextAnswerRequest, InterviewerReply, VoiceAnswerResponse,
)
from ai_interviewer.session_store import session_store
from ai_interviewer.services.tts import TTSService
from ai_interviewer.services.voice_agent import VoiceAgentService
from ai_interviewer.services.middleware_client import middleware_client
from ai_interviewer.exceptions import (
    InterviewerBaseError, InvalidInputError,
    VoiceAgentNotConfiguredError, VoiceAgentError, TranscriptionError,
    TTSError, MiddlewareDispatchError,
)
from ai_interviewer.logger import get_logger

log    = get_logger(__name__)
router = APIRouter(prefix="/interview", tags=["AI Interviewer"])

MAX_TEXT_LEN   = 5_000
MAX_AUDIO_SIZE = 25 * 1024 * 1024

_tts         = TTSService()
_voice_agent = VoiceAgentService()


async def _maybe_dispatch_to_middleware(session_id: str, agent) -> None:
    """
    Called after any answer that completes the session.
    Dispatches full session data to Spring Boot middleware.
    Non-fatal: logs warning on failure, never raises to the user.
    """
    if not agent.last_summary:
        log.warning("Session '%s' completed but last_summary is None — skipping dispatch.", session_id)
        return
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
    except Exception as exc:
        log.error("Unexpected middleware dispatch error for session '%s': %s", session_id, exc)


# ── Text answer ───────────────────────────────────────────────────────────────

@router.post("/answer/text", response_model=InterviewerReply)
async def text_answer(req: TextAnswerRequest):
    """
    Submit a typed answer.
    If this answer completes the session, session data is dispatched
    to the Spring Boot middleware automatically.
    """
    try:
        if not req.answer or not req.answer.strip():
            raise InvalidInputError("Answer cannot be empty.")
        if len(req.answer) > MAX_TEXT_LEN:
            raise InvalidInputError(f"Answer exceeds {MAX_TEXT_LEN} character limit.")

        agent      = session_store.get(req.session_id)
        ai_message = await agent.handle_response(req.answer)

        # ── Middleware dispatch when session ends ─────────────────────────────
        if agent.ctx.is_completed:
            await _maybe_dispatch_to_middleware(req.session_id, agent)
            session_store.delete(req.session_id)

        log.info("Text answer processed for session '%s'", req.session_id)
        return ai_message   # already an InterviewerReply

    except InterviewerBaseError:
        raise
    except Exception as exc:
        log.error("Unexpected error in text_answer for '%s': %s", req.session_id, exc)
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
    gets included in the middleware payload at session end.

    If voice agent fails, the interview degrades gracefully — a fallback
    review is stored and the session continues.
    """
    try:
        agent = session_store.get(session_id)

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
        transcript_holder: list[str] = []
        review_holder:     list[str] = []
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

        # Hard failure — no transcript to proceed with
        if exc_holder and isinstance(exc_holder[0], TranscriptionError):
            raise exc_holder[0]

        if not transcript_holder:
            raise TranscriptionError(
                reason="Voice agent produced no transcript. "
                       "Audio may be silent, too short, or unsupported."
            )

        transcript = transcript_holder[0]
        raw_review = review_holder[0] if review_holder else _voice_agent.FALLBACK_REVIEW

        # ── Store voice review in context for middleware payload ───────────────
        # We pad to align with history (answer hasn't been appended yet,
        # so we append the review after handle_response updates history).
        # We record the pre-answer length to know which index to write to.
        answer_index = len(agent.ctx.history)   # index this answer will occupy

        # ── Process answer ────────────────────────────────────────────────────
        ai_message = await agent.handle_response(transcript)

        # Now insert the voice review at the correct index
        # (history was just appended in handle_response)
        while len(agent.ctx.voice_reviews) < answer_index:
            agent.ctx.voice_reviews.append("")   # pad any gap (e.g. text answers)
        if len(agent.ctx.voice_reviews) == answer_index:
            agent.ctx.voice_reviews.append(raw_review)

        # ── Middleware dispatch when session ends ─────────────────────────────
        if agent.ctx.is_completed:
            await _maybe_dispatch_to_middleware(session_id, agent)
            session_store.delete(session_id)

        log.info("Voice answer processed for session '%s' — transcript_len=%d", session_id, len(transcript))
        # Merge voice-specific fields into the structured reply
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