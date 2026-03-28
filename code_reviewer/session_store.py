"""
Thread-safe in-memory session store for code review sessions.
"""
import threading
from typing import Dict, Optional
from uuid import UUID

from code_reviewer.models import ReviewSession
from code_reviewer.exceptions import SessionNotFoundError


class SessionStore:
    """Thread-safe registry for active review sessions."""
    
    def __init__(self):
        self._sessions: Dict[UUID, ReviewSession] = {}
        self._lock = threading.Lock()
    
    def create(self, session: ReviewSession) -> None:
        """Store a new session."""
        with self._lock:
            self._sessions[session.session_id] = session
    
    def get(self, session_id: UUID) -> ReviewSession:
        """
        Retrieve a session by ID.
        
        Raises:
            SessionNotFoundError: If session does not exist
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(str(session_id))
            return session
    
    def update(self, session: ReviewSession) -> None:
        """Update an existing session."""
        with self._lock:
            if session.session_id not in self._sessions:
                raise SessionNotFoundError(str(session.session_id))
            self._sessions[session.session_id] = session
    
    def delete(self, session_id: UUID) -> None:
        """Remove a session from the store."""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
    
    def exists(self, session_id: UUID) -> bool:
        """Check if a session exists."""
        with self._lock:
            return session_id in self._sessions
    
    def __len__(self) -> int:
        """Return the number of active sessions."""
        with self._lock:
            return len(self._sessions)


# Global session store instance
session_store = SessionStore()