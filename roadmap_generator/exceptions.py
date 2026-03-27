class RoadmapGenerationError(Exception):
    """Raised when the primary generator fails."""
    pass

class RoadmapValidationError(Exception):
    """Raised when the validator rejects the generated roadmap."""
    pass

class SessionNotFoundError(Exception):
    """Raised when an invalid session ID is provided."""
    pass