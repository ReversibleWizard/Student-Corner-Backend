"""
Typed exception hierarchy for the code review system.
"""


class CodeReviewBaseError(Exception):
    """Base exception for all code review errors."""
    
    def __init__(self, message: str, error_code: str, http_status: int = 500):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.http_status = http_status


class SessionNotFoundError(CodeReviewBaseError):
    """Session ID not found in the session store."""
    
    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session '{session_id}' not found.",
            error_code="SESSION_NOT_FOUND",
            http_status=404,
        )


class SessionAlreadyCompletedError(CodeReviewBaseError):
    """Session has already been completed."""
    
    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session '{session_id}' is already completed.",
            error_code="SESSION_ALREADY_COMPLETED",
            http_status=409,
        )


class SessionCreationFailedError(CodeReviewBaseError):
    """Failed to create a new review session."""
    
    def __init__(self, reason: str):
        super().__init__(
            message=f"Failed to create review session: {reason}",
            error_code="SESSION_CREATION_FAILED",
            http_status=500,
        )


class AgentError(CodeReviewBaseError):
    """An LLM agent failed to process the request."""
    
    def __init__(self, agent_name: str, details: str):
        super().__init__(
            message=f"{agent_name} failed: {details}",
            error_code="AGENT_ERROR",
            http_status=502,
        )


class AgentTimeoutError(CodeReviewBaseError):
    """An LLM agent timed out."""
    
    def __init__(self, agent_name: str):
        super().__init__(
            message=f"{agent_name} did not respond within the timeout period.",
            error_code="AGENT_TIMEOUT",
            http_status=504,
        )


class InvalidInputError(CodeReviewBaseError):
    """Invalid input provided by the user."""
    
    def __init__(self, details: str):
        super().__init__(
            message=f"Invalid input: {details}",
            error_code="INVALID_INPUT",
            http_status=422,
        )


class MiddlewareDispatchFailedError(CodeReviewBaseError):
    """Failed to dispatch session data to middleware."""
    
    def __init__(self, details: str):
        super().__init__(
            message=f"Failed to dispatch session to middleware: {details}",
            error_code="MIDDLEWARE_DISPATCH_FAILED",
            http_status=502,
        )