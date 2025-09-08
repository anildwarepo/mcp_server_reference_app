from __future__ import annotations
from typing import List, Optional, Union, Literal
from pydantic import BaseModel, field_validator
import json


# ---------- Message payloads ----------

class ProgressPayload(BaseModel):
    progress: float
    progressToken: Optional[str] = None

    @field_validator("progress")
    @classmethod
    def progress_between_0_and_1(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("progress must be between 0.0 and 1.0")
        return float(v)


class MessageDataText(BaseModel):
    type: Literal["text"]
    text: str


class MessagePayload(BaseModel):
    data: List[MessageDataText]
    level: Optional[str] = None


# ---------- Notifications (envelopes) ----------

class BaseNotification(BaseModel):
    user_id: Optional[str] = None


class ProgressNotification(BaseNotification):
    method: Literal["notifications/progress"]
    params: ProgressPayload


class MessageNotification(BaseNotification):
    method: Literal["notifications/message"]
    params: MessagePayload


Notification = Union[ProgressNotification, MessageNotification]


# ---------- Helpers ----------

def parse_notification_json(data: str) -> Notification:
    """
    Parse a JSON string into a typed Notification.
    Tries ProgressNotification first, then MessageNotification.
    """
    obj = json.loads(data)

    # Try discriminating by method fast-path
    method = obj.get("method")
    if method == "notifications/progress":
        return ProgressNotification.model_validate(obj)
    if method == "notifications/message":
        return MessageNotification.model_validate(obj)

    # Fallback: attempt both
    try:
        return ProgressNotification.model_validate(obj)
    except Exception:
        pass
    return MessageNotification.model_validate(obj)


def dumps_notification(notification: Notification) -> str:
    """Serialize a typed Notification to compact JSON."""
    return json.dumps(notification.model_dump(), separators=(",", ":"))
