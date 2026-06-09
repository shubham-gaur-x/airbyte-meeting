"""Pydantic v2 data models for the meeting-memory-graph transform service."""

from __future__ import annotations

from datetime import date as DateType
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from utils import safe_date

__all__ = [
    "Attendee",
    "ActionItem",
    "ExtractedMeeting",
    "RawEmail",
    "ClassificationResult",
    "ProcessResult",
    "BuildResult",
    "AirbyteWebhookPayload",
]


class Attendee(BaseModel):
    """A person who attended or was invited to a meeting."""

    model_config = ConfigDict(extra="ignore")

    name: str
    email: str = ""
    role: Literal["organizer", "attendee", "optional"] = "attendee"


class ActionItem(BaseModel):
    """A task or follow-up produced during a meeting."""

    model_config = ConfigDict(extra="ignore")

    owner: str = ""
    task: str
    due: Optional[DateType] = None
    done: bool = False
    priority: Literal["high", "medium", "low"] = "medium"

    @field_validator("priority", mode="before")
    @classmethod
    def derive_priority(cls, v: str, info: any) -> str:
        if v and v in ("high", "medium", "low"):
            return v
        due = info.data.get("due") if hasattr(info, "data") else None
        if due is None:
            return "low"
        if isinstance(due, str):
            due = safe_date(due)
        if due is None:
            return "low"
        delta = (due - DateType.today()).days
        if delta <= 14:
            return "high"
        if delta <= 60:
            return "medium"
        return "low"


class ExtractedMeeting(BaseModel):
    """Structured extraction output from the LLM for a single meeting."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    kind: str = "meeting"
    platform: str = "unknown"
    date: Optional[DateType] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_minutes: Optional[int] = None
    location: str = ""
    attendees: List[Attendee] = []
    summary: str = ""
    topics: List[str] = []
    decisions: List[str] = []
    action_items: List[ActionItem] = []
    key_quotes: List[str] = []
    links: List[str] = []
    sentiment: str = "neutral"
    follow_up_needed: bool = False
    confidence: float = 0.0


class RawEmail(BaseModel):
    """A raw email row from the Postgres staging table."""

    model_config = ConfigDict(extra="ignore")

    id: int
    message_id: str
    subject: str = ""
    sender: str = ""
    recipients: List[str] = []
    body_text: str = ""
    received_at: datetime
    processed: bool = False


class ClassificationResult(BaseModel):
    """Output of the rules-based meeting classifier."""

    model_config = ConfigDict(extra="ignore")

    score: float
    is_meeting: bool
    is_invite: bool
    matched_signals: List[str]


class ProcessResult(BaseModel):
    """Result of processing a single email or calendar event."""

    model_config = ConfigDict(extra="ignore")

    message_id: str
    status: Literal["processed", "skipped", "error"]
    reason: Optional[str] = None
    meeting_title: Optional[str] = None
    nodes_created: int = 0
    edges_created: int = 0


class BuildResult(BaseModel):
    """Result of writing one meeting to the Memgraph graph."""

    model_config = ConfigDict(extra="ignore")

    meeting_id: str
    nodes_created: int
    edges_created: int
    persons: List[str] = []
    topics: List[str] = []
    decisions_count: int = 0
    action_items_count: int = 0


class AirbyteWebhookPayload(BaseModel):
    """Payload received from Airbyte sync-complete webhook."""

    model_config = ConfigDict(extra="ignore")

    connection_id: str
    status: str
    streams: List[str] = []
