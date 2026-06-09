"""Jira pusher — creates issues and routes them to sprint or backlog."""

from __future__ import annotations

import base64
import os
from typing import List, Optional

import httpx
import structlog

import memgraph_client
from models import ActionItem, ExtractedMeeting
from utils import with_retry

log = structlog.get_logger()

PRIORITY_MAP = {"high": "High", "medium": "Medium", "low": "Low"}


def _auth_header() -> str:
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
    return f"Basic {encoded}"


def _jira_base() -> str:
    domain = os.environ["JIRA_DOMAIN"]
    return f"https://{domain}/rest/api/3"


def _agile_base() -> str:
    domain = os.environ["JIRA_DOMAIN"]
    return f"https://{domain}/rest/agile/1.0"


def _build_adf_description(meeting: ExtractedMeeting) -> dict:
    """Build an Atlassian Document Format description body."""
    content = [
        {
            "type": "heading",
            "attrs": {"level": 2},
            "content": [{"type": "text", "text": f"{meeting.title} — {meeting.date}"}],
        },
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": meeting.summary or "No summary available."}],
        },
    ]

    if meeting.attendees:
        attendee_items = [
            {
                "type": "listItem",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"{a.name} ({a.email})"}]}],
            }
            for a in meeting.attendees
        ]
        content.append({"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "Attendees"}]})
        content.append({"type": "bulletList", "content": attendee_items})

    if meeting.decisions:
        decision_items = [
            {
                "type": "listItem",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": d}]}],
            }
            for d in meeting.decisions
        ]
        content.append({"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "Decisions"}]})
        content.append({"type": "bulletList", "content": decision_items})

    return {"version": 1, "type": "doc", "content": content}


async def push_action_items(
    action_items: List[ActionItem],
    meeting: ExtractedMeeting,
    meeting_node_id: str,
) -> List[str]:
    """Push action items to Jira. Returns list of created issue keys."""
    if os.environ.get("JIRA_ENABLED", "false").lower() != "true":
        log.info("jira_pusher.disabled")
        return []

    project_key = os.environ["JIRA_PROJECT_KEY"]
    board_id = os.environ.get("JIRA_BOARD_ID", "1")
    issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Task")
    headers = {
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    created_keys: List[str] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for item in action_items:
            issue_key = await _create_issue(client, item, meeting, project_key, issue_type, headers)
            if not issue_key:
                continue
            created_keys.append(issue_key)
            log.info("jira_pusher.issue_created", key=issue_key, task=item.task)

            if item.priority == "high":
                await _add_to_sprint(client, issue_key, board_id, headers)

            from utils import uuid5_id
            action_id = uuid5_id(meeting_node_id, item.task)
            try:
                memgraph_client.update_action_jira_key(action_id, issue_key)
            except Exception as exc:
                log.warning("jira_pusher.graph_update_failed", action_id=action_id, error=str(exc))

    return created_keys


@with_retry(max_attempts=3, base_delay=2.0)
async def _create_issue(
    client: httpx.AsyncClient,
    item: ActionItem,
    meeting: ExtractedMeeting,
    project_key: str,
    issue_type: str,
    headers: dict,
) -> Optional[str]:
    labels = ["meeting-generated"] + [t.replace(" ", "-") for t in meeting.topics[:3]]
    body = {
        "fields": {
            "project": {"key": project_key},
            "summary": item.task,
            "issuetype": {"name": issue_type},
            "priority": {"name": PRIORITY_MAP.get(item.priority, "Medium")},
            "labels": labels,
            "description": _build_adf_description(meeting),
        }
    }
    if item.due:
        body["fields"]["duedate"] = item.due.isoformat()

    resp = await client.post(f"{_jira_base()}/issue", headers=headers, json=body)
    if resp.status_code in (200, 201):
        return resp.json().get("key")
    log.warning("jira_pusher.create_failed", status=resp.status_code, body=resp.text[:200])
    return None


@with_retry(max_attempts=3, base_delay=2.0)
async def _add_to_sprint(
    client: httpx.AsyncClient,
    issue_key: str,
    board_id: str,
    headers: dict,
) -> None:
    sprint_resp = await client.get(
        f"{_agile_base()}/board/{board_id}/sprint",
        headers=headers,
        params={"state": "active"},
    )
    if sprint_resp.status_code != 200:
        log.warning("jira_pusher.sprint_fetch_failed", status=sprint_resp.status_code)
        return
    sprints = sprint_resp.json().get("values", [])
    if not sprints:
        log.info("jira_pusher.no_active_sprint", board_id=board_id)
        return
    sprint_id = sprints[0]["id"]
    await client.post(
        f"{_agile_base()}/sprint/{sprint_id}/issue",
        headers=headers,
        json={"issues": [issue_key]},
    )
    log.info("jira_pusher.added_to_sprint", issue_key=issue_key, sprint_id=sprint_id)
