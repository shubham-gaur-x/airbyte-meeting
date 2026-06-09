"""Graph builder — orchestrates Memgraph writes for one ExtractedMeeting."""

from __future__ import annotations

import time
from typing import List

import structlog

import memgraph_client
from models import ActionItem, BuildResult, ExtractedMeeting
from utils import extract_domain

log = structlog.get_logger()


async def build_graph(
    meeting: ExtractedMeeting,
    source_id: str,
    dry_run: bool = False,
) -> BuildResult:
    """Write one ExtractedMeeting to Memgraph. Returns counts of nodes/edges created."""
    t0 = time.monotonic()
    prefix = "[DRY RUN] " if dry_run else ""

    persons: List[str] = []
    orgs: List[str] = []
    topics: List[str] = []
    decisions: List[str] = []
    actions: List[str] = []
    edges = 0

    # 1. Upsert Meeting node
    if not dry_run:
        memgraph_client.upsert_meeting(meeting, source_id)
    else:
        log.info(f"{prefix}upsert_meeting", meeting_id=source_id, title=meeting.title)

    # 2. Persons + Organizations + ATTENDED + WORKS_AT edges
    for attendee in meeting.attendees:
        try:
            person_id = attendee.email.lower() if attendee.email else attendee.name.lower().replace(" ", ".")
            if not dry_run:
                memgraph_client.upsert_person(attendee.name, attendee.email or person_id)
            persons.append(person_id)

            domain = extract_domain(attendee.email) if attendee.email else ""
            if domain:
                if not dry_run:
                    memgraph_client.upsert_organization(domain)
                    memgraph_client.create_works_at(person_id, domain)
                orgs.append(domain)
                edges += 1  # WORKS_AT

            if not dry_run:
                memgraph_client.create_attended(person_id, source_id, attendee.role)
            edges += 1  # ATTENDED
        except Exception as exc:
            log.warning(f"{prefix}graph_builder.attendee_error", attendee=attendee.name, error=str(exc))

    # 3. Topics + DISCUSSED edges
    for topic_name in meeting.topics:
        try:
            if not dry_run:
                topic_id = memgraph_client.upsert_topic(topic_name)
                memgraph_client.create_discussed(source_id, topic_id)
            topics.append(topic_name)
            edges += 1  # DISCUSSED
        except Exception as exc:
            log.warning(f"{prefix}graph_builder.topic_error", topic=topic_name, error=str(exc))

    # 4. Decisions + PRODUCED edges
    for decision_text in meeting.decisions:
        try:
            if not dry_run:
                decision_id = memgraph_client.upsert_decision(decision_text, source_id)
                memgraph_client.create_produced_decision(source_id, decision_id)
            decisions.append(decision_text)
            edges += 1  # PRODUCED
        except Exception as exc:
            log.warning(f"{prefix}graph_builder.decision_error", error=str(exc))

    # 5. ActionItems + PRODUCED + ASSIGNED_TO edges
    person_lookup = {a.email.lower(): a.email.lower() for a in meeting.attendees if a.email}
    name_lookup = {a.name.lower(): a.email.lower() for a in meeting.attendees if a.email}

    for item in meeting.action_items:
        try:
            if not dry_run:
                action_id = memgraph_client.upsert_action_item(item, source_id)
                memgraph_client.create_produced_action(source_id, action_id)
            actions.append(item.task)
            edges += 1  # PRODUCED

            owner_lower = item.owner.lower()
            matched_person = person_lookup.get(owner_lower) or name_lookup.get(owner_lower)
            if matched_person and not dry_run:
                memgraph_client.create_assigned_to(action_id, matched_person)
                edges += 1  # ASSIGNED_TO
        except Exception as exc:
            log.warning(f"{prefix}graph_builder.action_error", task=item.task, error=str(exc))

    # 6. Count nodes
    nodes_created = 1 + len(persons) + len(set(orgs)) + len(topics) + len(decisions) + len(actions)
    duration_ms = int((time.monotonic() - t0) * 1000)

    log.info(
        f"{prefix}graph_builder.complete",
        meeting_id=source_id,
        title=meeting.title,
        nodes_created=nodes_created,
        edges_created=edges,
        duration_ms=duration_ms,
    )

    return BuildResult(
        meeting_id=source_id,
        nodes_created=nodes_created,
        edges_created=edges,
        persons=persons,
        topics=topics,
        decisions_count=len(decisions),
        action_items_count=len(actions),
    )
