"""Rules-based meeting classifier ported from meeting-memory v1."""

from __future__ import annotations

import re
from typing import List

from models import ClassificationResult, RawEmail

MEETING_KEYWORDS = [
    "recap", "minutes", "sync", "standup", "all-hands", "call log",
    "meeting notes", "discussed", "action items", "follow-up",
    "agenda", "meeting summary", "post-mortem",
]

ATTENDEE_LANGUAGE = [
    "attendees:", "participants:", "present:", "joined by", "on the call",
]

DECISION_LANGUAGE = [
    "we decided", "agreed to", "resolved", "approved", "rejected", "signed off",
]

ACTION_LANGUAGE = [
    "action:", "action item", "to-do", "owner:", "due:", "assigned to", "next steps",
]

INVITE_SIGNALS = [
    "you've been invited", "accept/decline", "calendar invite", "added to your calendar",
    "you have been invited",
]

DATETIME_PATTERN = re.compile(
    r"\b(\d{1,2}(:\d{2})?\s*(am|pm|AM|PM)|[01]\d:[0-5]\d|2[0-3]:[0-5]\d)\b"
)

SIGNAL_WEIGHTS = {
    "meeting_keywords": 0.25,
    "attendee_language": 0.15,
    "datetime_patterns": 0.10,
    "decision_language": 0.20,
    "action_language": 0.20,
}

INVITE_PENALTY = -0.40
MEETING_THRESHOLD = 0.6


def _check_signals(text: str) -> tuple[float, bool, List[str]]:
    lower = text.lower()
    matched: List[str] = []
    score = 0.0
    is_invite = False

    if any(kw in lower for kw in MEETING_KEYWORDS):
        matched.append("meeting_keywords")
        score += SIGNAL_WEIGHTS["meeting_keywords"]

    if any(kw in lower for kw in ATTENDEE_LANGUAGE):
        matched.append("attendee_language")
        score += SIGNAL_WEIGHTS["attendee_language"]

    if DATETIME_PATTERN.search(text):
        matched.append("datetime_patterns")
        score += SIGNAL_WEIGHTS["datetime_patterns"]

    if any(kw in lower for kw in DECISION_LANGUAGE):
        matched.append("decision_language")
        score += SIGNAL_WEIGHTS["decision_language"]

    if any(kw in lower for kw in ACTION_LANGUAGE):
        matched.append("action_language")
        score += SIGNAL_WEIGHTS["action_language"]

    if any(kw in lower for kw in INVITE_SIGNALS):
        matched.append("invite_penalty")
        score += INVITE_PENALTY
        is_invite = True

    score = max(0.0, min(1.0, score))
    return score, is_invite, matched


def classify(email: RawEmail) -> ClassificationResult:
    """Classify a RawEmail as a meeting recap or not."""
    combined = f"{email.subject}\n{email.body_text}"
    score, is_invite, matched = _check_signals(combined)
    return ClassificationResult(
        score=score,
        is_meeting=score >= MEETING_THRESHOLD,
        is_invite=is_invite,
        matched_signals=matched,
    )


def classify_text(subject: str, body: str) -> ClassificationResult:
    """Classify raw text strings — used for testing and calendar event classification."""
    combined = f"{subject}\n{body}"
    score, is_invite, matched = _check_signals(combined)
    return ClassificationResult(
        score=score,
        is_meeting=score >= MEETING_THRESHOLD,
        is_invite=is_invite,
        matched_signals=matched,
    )


if __name__ == "__main__":
    from datetime import datetime

    def _test(label: str, result: ClassificationResult, expect_meeting: bool, expect_invite: bool) -> None:
        ok_meeting = result.is_meeting == expect_meeting
        ok_invite = result.is_invite == expect_invite
        status = "PASS" if (ok_meeting and ok_invite) else "FAIL"
        print(f"[{status}] {label} | score={result.score:.2f} is_meeting={result.is_meeting} is_invite={result.is_invite} signals={result.matched_signals}")

    _test(
        "Clear meeting recap",
        classify_text(
            "Meeting Recap: Q2 Planning Sync",
            "Attendees: Alice, Bob\nWe decided to proceed with the Airbyte integration.\nAction item: Alice to set up connectors by Friday. Owner: alice@co.com. Due: 2026-06-13.",
        ),
        expect_meeting=True,
        expect_invite=False,
    )

    _test(
        "Calendar invite",
        classify_text(
            "You've been invited: Team Standup",
            "You've been invited to a recurring standup. Accept/Decline via calendar invite.",
        ),
        expect_meeting=False,
        expect_invite=True,
    )

    _test(
        "Promotional email",
        classify_text(
            "50% off this weekend only!",
            "Don't miss our biggest sale. Shop now and save.",
        ),
        expect_meeting=False,
        expect_invite=False,
    )

    _test(
        "Slack meeting export",
        classify_text(
            "Slack export: #eng-sync",
            "On the call: Alice, Bob, Charlie.\nWe discussed the new API design.\nAgreed to ship by EOD Friday.\nNext steps: Bob to open PR.",
        ),
        expect_meeting=True,
        expect_invite=False,
    )

    _test(
        "Email with only action items",
        classify_text(
            "Follow-up from today's sync",
            "Action items from today:\n- Action: Deploy to staging. Owner: alice. Due: 2026-06-15.\n- Action: Review PR. Owner: bob.",
        ),
        expect_meeting=True,
        expect_invite=False,
    )

    _test(
        "Empty body",
        classify_text("", ""),
        expect_meeting=False,
        expect_invite=False,
    )
