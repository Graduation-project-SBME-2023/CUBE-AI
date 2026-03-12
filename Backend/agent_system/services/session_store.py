from models.session import Session


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(session_id)
        return self._sessions[session_id]

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def require(self, session_id: str) -> Session:
        """Get a session or raise KeyError if not found."""
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        return session

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def all(self) -> dict[str, Session]:
        return dict(self._sessions)
