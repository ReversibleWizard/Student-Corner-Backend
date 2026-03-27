"""MiddlewareClient — sends the complete session payload to the middleware store."""

from __future__ import annotations

import os

import httpx

from ..exceptions import MiddlewareError
from ..logger import get_logger
from ..models import MiddlewareStorePayload, ReviewSession

logger = get_logger(__name__)

# The middleware base URL is read from the environment so it can differ across
# dev / staging / prod without touching code.
_MIDDLEWARE_BASE_URL = os.getenv("MIDDLEWARE_BASE_URL", "http://localhost:8001")
_STORE_PATH = "/store/coder-reviewer"          # POST endpoint on the middleware
_REQUEST_TIMEOUT = 30.0                        # seconds


class MiddlewareClient:
    """HTTP client that serialises a ReviewSession and POSTs it to the middleware."""

    def __init__(
        self,
        base_url: str = _MIDDLEWARE_BASE_URL,
        timeout: float = _REQUEST_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def store_session(self, session: ReviewSession) -> dict:
        """Build the store payload from *session* and POST it to the middleware.

        Returns the JSON body from the middleware on success.
        Raises MiddlewareError on non-2xx responses.
        """
        payload = self._build_payload(session)
        url = f"{self._base_url}{_STORE_PATH}"

        logger.info(
            "[%s] Sending session to middleware: %s",
            session.session_id,
            url,
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url,
                json=payload.model_dump(),
                headers={"Content-Type": "application/json"},
            )

        if response.status_code >= 400:
            logger.error(
                "[%s] Middleware returned %d: %s",
                session.session_id,
                response.status_code,
                response.text[:500],
            )
            raise MiddlewareError(response.status_code, response.text)

        logger.info(
            "[%s] Session stored successfully (status %d)",
            session.session_id,
            response.status_code,
        )
        return response.json()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(session: ReviewSession) -> MiddlewareStorePayload:
        """Serialise a ReviewSession into the MiddlewareStorePayload format."""
        return MiddlewareStorePayload(
            session_id=session.session_id,
            original_code=session.original_code,
            understanding=(
                session.understanding.model_dump() if session.understanding else None
            ),
            technical_review=(
                session.technical_review.model_dump() if session.technical_review else None
            ),
            quality_review=(
                session.quality_review.model_dump() if session.quality_review else None
            ),
            optimized_code=session.optimized_code,
            final_code=session.current_code,
            chat_history=[
                {
                    "role": msg.role.value,
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat(),
                }
                for msg in session.chat_history
            ],
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
        )


# Module-level singleton
middleware_client = MiddlewareClient()
