import uuid
from typing import Dict, Any
from .exceptions import SessionNotFoundError
from .logger import logger

class SessionStore:
    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def create_session(self) -> str:
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = {
            "version": 0,
            "roadmap": None,
            "chat_history": []
        }
        logger.info(f"Created new session: {session_id}")
        return session_id

    def get_session(self, session_id: str) -> Dict[str, Any]:
        if session_id not in self._sessions:
            raise SessionNotFoundError(f"Session {session_id} not found.")
        return self._sessions[session_id]

    def update_roadmap(self, session_id: str, new_roadmap: dict, user_message: str = None):
        session = self.get_session(session_id)
        session["roadmap"] = new_roadmap
        session["version"] += 1
        
        if user_message:
            session["chat_history"].append({"role": "user", "content": user_message})
            session["chat_history"].append({
                "role": "assistant", 
                "content": f"Updated roadmap to version {session['version']}"
            })
        logger.info(f"Updated session {session_id} to version {session['version']}")

    def delete_session(self, session_id: str):
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"Deleted session: {session_id}")
    
    def restore_session(self, session_id: str, roadmap: dict, chat_history: list, version: int):
        """Rehydrates a previously terminated session from the database."""
        self._sessions[session_id] = {
            "version": version,
            "roadmap": roadmap,
            "chat_history": chat_history
        }
        logger.info(f"Restored session {session_id} to active memory at version {version}")

store = SessionStore()