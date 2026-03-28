"""
Middleware client for dispatching completed review sessions.

Sends the complete session payload (code, reviews, optimizations, chat history)
to the Spring Boot middleware for persistent storage.
"""
import os
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from code_reviewer.models import ReviewSession, MiddlewarePayload
from code_reviewer.exceptions import MiddlewareDispatchFailedError
from code_reviewer.logger import get_logger

log = get_logger("middleware_client")


class MiddlewareClient:
    """Client for dispatching review sessions to the middleware."""
    
    def __init__(self):
        self.url = os.getenv("MIDDLEWARE_URL")
        self.auth_token = os.getenv("MIDDLEWARE_AUTH_TOKEN")
        self.enabled = bool(self.url)
        
        if not self.enabled:
            log.warning("MIDDLEWARE_URL not set — session dispatch is disabled")
    
    def _build_headers(self) -> dict:
        """Build HTTP headers for the middleware request."""
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers
    
    def _build_payload(self, session: ReviewSession) -> MiddlewarePayload:
        """Convert ReviewSession to MiddlewarePayload."""
        return MiddlewarePayload(
            session_id=str(session.session_id),
            user_id=session.user_id,
            metadata=session.metadata,
            original_code=session.original_code,
            optimized_code=session.optimized_code,
            understanding=session.understanding.model_dump() if session.understanding else None,
            technical_review=session.technical_review.model_dump() if session.technical_review else None,
            quality_review=session.quality_review.model_dump() if session.quality_review else None,
            optimization_details=session.optimization_details.model_dump() if session.optimization_details else None,
            chat_history=session.chat_history,
            started_at=session.started_at.isoformat(),
            completed_at=session.completed_at.isoformat() if session.completed_at else "",
        )
    
    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _send_request(self, payload: dict) -> dict:
        """
        Send POST request to middleware with retry logic.
        
        Retries on network errors and timeouts.
        Does NOT retry on 4xx responses.
        """
        print(payload)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.url,
                json=payload,
                headers=self._build_headers(),
            )
            
            # Don't retry 4xx errors (client errors)
            if 400 <= response.status_code < 500:
                log.error(
                    "Middleware rejected payload (HTTP %d): %s",
                    response.status_code,
                    response.text
                )
                raise MiddlewareDispatchFailedError(
                    f"HTTP {response.status_code}: {response.text}"
                )
            
            # Raise on 5xx to trigger retry
            response.raise_for_status()
            
            return response.json()
    
    async def dispatch(self, session: ReviewSession) -> bool:
        """
        Dispatch a completed session to the middleware.
        
        Args:
            session: ReviewSession to send
            
        Returns:
            True if successful, False otherwise
            
        Raises:
            MiddlewareDispatchFailedError: On unrecoverable failure (4xx)
        """
        if not self.enabled:
            log.info("Middleware dispatch is disabled (MIDDLEWARE_URL not set)")
            return False
        
        try:
            payload = self._build_payload(session)
            log.info(f"Dispatching session {session.session_id} to middleware")
            
            response_data = await self._send_request(payload.model_dump())
            
            log.info(
                f"Successfully dispatched session {session.session_id} to middleware: {response_data}"
            )
            return True
        
        except MiddlewareDispatchFailedError:
            # 4xx error — don't retry, re-raise
            raise
        
        except Exception as e:
            # All retries exhausted or unexpected error
            log.error(
                f"Failed to dispatch session {session.session_id} after all retries: {e}",
                exc_info=True
            )
            # Log warning but don't fail the review — session is still valid
            log.warning(
                f"Session {session.session_id} was NOT saved to middleware but remains accessible via API"
            )
            return False


# Global middleware client instance
middleware_client = MiddlewareClient()