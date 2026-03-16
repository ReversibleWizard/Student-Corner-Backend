"""
MiddlewareClient — async HTTP client that POSTs completed session data
to the Spring Boot middleware for persistent storage.

Uses httpx for async requests and tenacity for retry logic.
If dispatch fails after all retries, a MiddlewareDispatchError is raised
but the interview result is still returned to the user (non-fatal).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from ai_interviewer.models import (
    SessionPayload, QuestionRecord, MiddlewareResponse,
    InterviewContext, StartSessionRequest, InterviewSummary,
)
from ai_interviewer.exceptions import MiddlewareDispatchError
from ai_interviewer.logger import get_logger

log = get_logger(__name__)

MAX_RETRIES      = 4
TIMEOUT_S        = 10.0   # per request
BACKOFF_MIN_S    = 1
BACKOFF_MAX_S    = 8


class MiddlewareClient:
    """
    Dispatches completed session payloads to the Spring Boot middleware.

    Usage:
        client = MiddlewareClient()
        await client.dispatch(session_id, context, req, summary)
    """

    def __init__(self):
        self.url        = os.getenv("MIDDLEWARE_URL", "")
        self.auth_token = os.getenv("MIDDLEWARE_AUTH_TOKEN", "")

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _is_configured(self) -> bool:
        return bool(self.url and self.url.startswith("http"))

    def _build_payload(
        self,
        session_id: str,
        context:    InterviewContext,
        req:        StartSessionRequest,
        summary:    InterviewSummary,
    ) -> SessionPayload:
        """
        Assemble the full SessionPayload from session context and summary.
        Zips AnswerReview history with voice_reviews (parallel lists).
        """
        # Pad voice_reviews to match history length (text-only answers have no entry)
        voice_reviews = context.voice_reviews + [""] * (
            len(context.history) - len(context.voice_reviews)
        )

        questions = [
            QuestionRecord(
                question_number = i + 1,
                question        = review.question,
                topic           = review.topic_covered,
                difficulty      = review.difficulty,
                user_answer     = review.user_answer,
                score           = review.score,
                strengths       = review.strengths,
                weaknesses      = review.weaknesses,
                feedback        = review.user_answer_review,
                voice_review    = voice_reviews[i] if voice_reviews[i] else None,
            )
            for i, review in enumerate(context.history)
        ]

        return SessionPayload(
            session_id            = session_id,
            candidate_name        = req.name,
            target_role           = req.target_role,
            experience_level      = req.experience_level,
            work_experience       = req.work_experience,
            confidence_level      = req.confidence_level,
            completed_at          = datetime.now(timezone.utc).isoformat(),
            duration_minutes      = req.duration_minutes,
            questions             = questions,
            overall_score         = summary.overall_score,
            total_questions       = summary.total_questions,
            strong_topics         = summary.strong_topics,
            weak_topics           = summary.weak_topics,
            hiring_recommendation = summary.hiring_recommendation,
            summary               = summary.detailed_summary,
        )

    @retry(
        retry       = retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop        = stop_after_attempt(MAX_RETRIES),
        wait        = wait_exponential(multiplier=1, min=BACKOFF_MIN_S, max=BACKOFF_MAX_S),
        before_sleep= before_sleep_log(log, 20),  # 20 = logging.INFO equivalent in tenacity
        reraise     = False,
    )
    async def _post(self, payload_json: str) -> MiddlewareResponse:
        """
        Inner POST with retry decoration.
        Retries only on network-level errors (timeout, connect failure).
        4xx responses are NOT retried — they indicate a contract mismatch.
        """
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.post(
                self.url,
                content  = payload_json,
                headers  = self._headers,
            )

        if response.status_code == 200 or response.status_code == 201:
            try:
                return MiddlewareResponse(**response.json())
            except Exception:
                return MiddlewareResponse(status="ok", message="Stored (response unparseable)")

        # 4xx — don't retry, raise immediately
        if 400 <= response.status_code < 500:
            raise MiddlewareDispatchError(
                reason=f"Middleware rejected payload: HTTP {response.status_code} — {response.text[:200]}"
            )

        # 5xx — raise so tenacity can retry
        raise httpx.HTTPStatusError(
            message=f"Middleware server error: {response.status_code}",
            request=response.request,
            response=response,
        )

    async def dispatch(
        self,
        session_id: str,
        context:    InterviewContext,
        req:        StartSessionRequest,
        summary:    InterviewSummary,
    ) -> None:
        """
        Build the SessionPayload and POST it to the Spring Boot middleware.

        Non-fatal contract:
          - If MIDDLEWARE_URL is not configured, logs a warning and skips.
          - If all retries fail, raises MiddlewareDispatchError.
            The caller (router) logs this as a warning but still returns
            the interview result to the user.
        """
        if not self._is_configured():
            log.warning(
                "MIDDLEWARE_URL not configured — skipping dispatch for session '%s'.",
                session_id,
            )
            return

        payload = self._build_payload(session_id, context, req, summary)
        payload_json = payload.model_dump_json()

        log.info(
            "Dispatching session '%s' to middleware (%s questions, score=%.1f)",
            session_id,
            len(context.history),
            summary.overall_score,
        )

        try:
            result = await self._post(payload_json)
            log.info(
                "Middleware dispatch successful for session '%s' — record_id=%s",
                session_id,
                result.record_id or "N/A",
            )
        except MiddlewareDispatchError:
            raise
        except Exception as exc:
            log.error(
                "Middleware dispatch failed for session '%s' after retries: %s",
                session_id, exc,
            )
            raise MiddlewareDispatchError(reason=str(exc)) from exc


# ── Singleton shared across routers ──────────────────────────────────────────
middleware_client = MiddlewareClient()