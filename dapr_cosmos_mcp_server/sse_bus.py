# sse_bus.py
import asyncio, json
from typing import Annotated, Dict, Optional
import requests

JSONRPC = "2.0"

DAPR_HTTP_PORT = 3500


def sse_event(data: dict, event: str = "message") -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

class Session:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.q: asyncio.Queue[str] = asyncio.Queue()
        self.closed = False

    async def publish(self, msg: str) -> None:
        if not self.closed:
            await self.q.put(msg)

    def close(self) -> None:
        self.closed = True

class SessionManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> Session:
        async with self._lock:
            s = self._sessions.get(session_id)
            if s is None or s.closed:
                s = Session(session_id)
                self._sessions[session_id] = s
            return s

    async def publish(self, session_id: str, msg: str) -> None:
        s = await self.get_or_create(session_id)
        payload = {"session_id": session_id, "message": msg}
        print("Publishing:", payload)
        await s.publish(msg)

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            s = self._sessions.pop(session_id, None)
        if s:
            s.close()
            while not s.q.empty():
                try:
                    s.q.get_nowait()
                    s.q.task_done()
                except Exception:
                    break
            return True
        return False

    async def exists(self, session_id: str) -> bool:
        async with self._lock:
            return session_id in self._sessions

SESSIONS = SessionManager()

# Optional: map user_id -> session_id for actor lookups
_USER_SESSION: Dict[str, str] = {}

def associate_user_session(user_id: str, session_id: str) -> None:
    if user_id and session_id:
        _USER_SESSION[user_id] = session_id

def session_for_user(user_id: str) -> Optional[str]:
    return _USER_SESSION.get(user_id)


# Convenience publishers
async def publish_progress(session_id: str, token: str, progress: float) -> None:
    payload = {
        "jsonrpc": JSONRPC,
        "method": "notifications/progress",
        "params": {"progressToken": token, "progress": float(progress)},
    }
    print(f"Publishing progress: {progress} (token: {token})")
    await SESSIONS.publish(session_id, sse_event(payload))

async def publish_message(session_id: str, text: str, level: str = "info", extra: dict | None = None) -> None:
    
    payload = {
        "jsonrpc": JSONRPC,
        "method": "notifications/message",
        "params": {
            "level": level,
            "data": [{"type": "text", "text": text}],
        },
    }
    if extra:
        payload["params"].update(extra)
    print(f"Publishing message: {text} (session: {session_id})")
    await SESSIONS.publish(session_id, sse_event(payload))
