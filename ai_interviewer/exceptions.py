"""
Custom exception hierarchy for the AI Interviewer module.
Raised internally; global handlers in main.py convert them
to consistent JSON HTTP responses.
"""


class InterviewerBaseError(Exception):
    """Root exception — all AI Interviewer errors inherit from this."""
    http_status: int = 500
    error_code:  str = "INTERVIEWER_ERROR"

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail  = detail

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r})"


# ── Session ────────────────────────────────────────────────────────────────────

class SessionNotFoundError(InterviewerBaseError):
    http_status = 404
    error_code  = "SESSION_NOT_FOUND"

    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session '{session_id}' not found.",
            detail="Session may have expired or never existed.",
        )
        self.session_id = session_id


class SessionAlreadyCompletedError(InterviewerBaseError):
    http_status = 409
    error_code  = "SESSION_ALREADY_COMPLETED"

    def __init__(self, session_id: str = ""):
        super().__init__(
            message="This interview session has already ended.",
            detail=f"session_id={session_id}",
        )


class SessionCreationError(InterviewerBaseError):
    http_status = 500
    error_code  = "SESSION_CREATION_FAILED"


# ── Agent ──────────────────────────────────────────────────────────────────────

class AgentError(InterviewerBaseError):
    http_status = 502
    error_code  = "AGENT_ERROR"


class AgentTimeoutError(AgentError):
    http_status = 504
    error_code  = "AGENT_TIMEOUT"

    def __init__(self, agent_name: str, timeout_seconds: int):
        super().__init__(
            message=f"Agent '{agent_name}' timed out after {timeout_seconds}s.",
            detail="LLM did not respond within the allowed window.",
        )
        self.agent_name      = agent_name
        self.timeout_seconds = timeout_seconds


class AgentOutputError(AgentError):
    http_status = 502
    error_code  = "AGENT_BAD_OUTPUT"

    def __init__(self, agent_name: str, reason: str):
        super().__init__(
            message=f"Agent '{agent_name}' returned unexpected output.",
            detail=reason,
        )


# ── Voice / Audio ──────────────────────────────────────────────────────────────

class AudioProcessingError(InterviewerBaseError):
    http_status = 422
    error_code  = "AUDIO_PROCESSING_FAILED"


class TranscriptionError(AudioProcessingError):
    error_code = "TRANSCRIPTION_FAILED"

    def __init__(self, reason: str):
        super().__init__(
            message="Could not transcribe the audio recording.",
            detail=reason,
        )


class VoiceAgentError(AudioProcessingError):
    http_status = 502
    error_code  = "VOICE_AGENT_FAILED"

    def __init__(self, reason: str):
        super().__init__(
            message="Voice delivery analysis failed.",
            detail=reason,
        )


class VoiceAgentNotConfiguredError(VoiceAgentError):
    http_status = 503
    error_code  = "VOICE_AGENT_NOT_CONFIGURED"

    def __init__(self):
        super().__init__(reason="ELEVENLABS_AGENT_ID is not set in .env")


# ── TTS ────────────────────────────────────────────────────────────────────────

class TTSError(InterviewerBaseError):
    http_status = 502
    error_code  = "TTS_FAILED"

    def __init__(self, reason: str):
        super().__init__(
            message="Text-to-speech conversion failed.",
            detail=reason,
        )


# ── Resume ─────────────────────────────────────────────────────────────────────

class ResumeLoadError(InterviewerBaseError):
    http_status = 500
    error_code  = "RESUME_LOAD_FAILED"

    def __init__(self, path: str, reason: str):
        super().__init__(
            message=f"Failed to load resume from '{path}'.",
            detail=reason,
        )


# ── Middleware ─────────────────────────────────────────────────────────────────

class MiddlewareDispatchError(InterviewerBaseError):
    """
    Raised when the session payload could not be delivered to the
    Spring Boot middleware after all retries are exhausted.
    This is treated as a WARNING — the interview itself already succeeded.
    """
    http_status = 502
    error_code  = "MIDDLEWARE_DISPATCH_FAILED"

    def __init__(self, reason: str):
        super().__init__(
            message="Failed to deliver session data to storage middleware.",
            detail=reason,
        )


# ── Validation ─────────────────────────────────────────────────────────────────

class InvalidInputError(InterviewerBaseError):
    http_status = 422
    error_code  = "INVALID_INPUT"