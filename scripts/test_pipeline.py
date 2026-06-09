#!/usr/bin/env python3
"""End-to-end smoke test using sample_data/sample_email.json."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime

sys.path.insert(0, "transform_service")

import db
import classifier
import extractor
import graph_builder
import memgraph_client
from models import RawEmail

SAMPLE_PATH = "sample_data/sample_email.json"


def _pass(label: str, detail: str = "") -> None:
    print(f"[PASS] {label}" + (f" — {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" — {detail}" if detail else ""))


def load_sample() -> RawEmail:
    with open(SAMPLE_PATH) as f:
        data = json.load(f)
    return RawEmail(
        id=9999,
        message_id=data["message_id"],
        subject=data["subject"],
        sender=data["sender"],
        recipients=data.get("recipients", []),
        body_text=data["body_text"],
        received_at=datetime.fromisoformat(data["received_at"].replace("Z", "+00:00")),
    )


async def main() -> None:
    email = load_sample()

    # 1. Insert into Postgres
    try:
        pool = db.get_pool()
        conn = pool.getconn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw_emails (message_id, subject, sender, recipients, body_text, received_at)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (message_id) DO NOTHING
                """,
                (
                    email.message_id,
                    email.subject,
                    email.sender,
                    json.dumps(email.recipients),
                    email.body_text,
                    email.received_at,
                ),
            )
        conn.commit()
        pool.putconn(conn)
        _pass("Insert sample email into Postgres")
    except Exception as exc:
        _fail("Insert sample email into Postgres", str(exc))
        return

    # 2. Classify
    try:
        clf = classifier.classify(email)
        assert clf.is_meeting, f"Expected is_meeting=True, got score={clf.score}"
        assert not clf.is_invite, "Expected is_invite=False"
        _pass("Classify as meeting", f"score={clf.score:.2f} signals={clf.matched_signals}")
    except AssertionError as exc:
        _fail("Classify as meeting", str(exc))
        return

    # 3. Extract
    try:
        meeting = await extractor.extract(email)
        assert meeting.confidence > 0.3, f"confidence={meeting.confidence}"
        assert len(meeting.attendees) > 0, "No attendees extracted"
        _pass("LLM extraction", f"title='{meeting.title}' confidence={meeting.confidence:.2f} attendees={len(meeting.attendees)}")
    except Exception as exc:
        _fail("LLM extraction", str(exc))
        return

    # 4. Build graph (real writes)
    try:
        build = await graph_builder.build_graph(meeting, email.message_id, dry_run=False)
        assert build.nodes_created > 0, "No nodes created"
        _pass("Graph write", f"nodes={build.nodes_created} edges={build.edges_created}")
    except Exception as exc:
        _fail("Graph write", str(exc))
        return

    # 5. Verify Meeting node in Memgraph
    try:
        driver = memgraph_client.get_driver()
        with driver.session() as session:
            result = session.run(
                "MATCH (m:Meeting {id: $id}) RETURN m", id=email.message_id
            ).single()
        assert result is not None, "Meeting node not found in Memgraph"
        _pass("Meeting node exists in Memgraph", f"id={email.message_id}")
    except Exception as exc:
        _fail("Meeting node exists in Memgraph", str(exc))


if __name__ == "__main__":
    asyncio.run(main())
