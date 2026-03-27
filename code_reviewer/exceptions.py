"""Typed exception hierarchy for the coder_reviewer agent."""


class CoderReviewerError(Exception):
    """Base exception for all coder_reviewer errors."""


class SessionNotFoundError(CoderReviewerError):
    """Raised when a requested session does not exist."""

    def __init__(self, session_id: str):
        super().__init__(f"Session '{session_id}' not found.")
        self.session_id = session_id


class AgentExecutionError(CoderReviewerError):
    """Raised when an agent fails during execution."""

    def __init__(self, agent_name: str, reason: str):
        super().__init__(f"Agent '{agent_name}' failed: {reason}")
        self.agent_name = agent_name
        self.reason = reason


class MiddlewareError(CoderReviewerError):
    """Raised when the middleware store call fails."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Middleware returned {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class JSONParseError(CoderReviewerError):
    """Raised when an LLM response cannot be parsed as JSON."""

    def __init__(self, agent_name: str, raw: str):
        super().__init__(f"Could not parse JSON from agent '{agent_name}'. Raw: {raw[:200]}")
        self.agent_name = agent_name
        self.raw = raw
