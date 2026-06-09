"""Memgraph Cloud client — all Cypher queries live in this file."""

from __future__ import annotations

import os
from typing import List, Optional

import neo4j
import structlog

from models import ActionItem, ExtractedMeeting
from utils import extract_domain, slugify, uuid5_id

log = structlog.get_logger()

_driver: Optional[neo4j.Driver] = None


def get_driver() -> neo4j.Driver:
    global _driver
    if _driver is None:
        host = os.environ["MEMGRAPH_HOST"]
        port = os.environ.get("MEMGRAPH_PORT", "7687")
        user = os.environ.get("MEMGRAPH_USER", "memgraph")
        password = os.environ["MEMGRAPH_PASSWORD"]
        _driver = neo4j.GraphDatabase.driver(
            f"bolt+ssc://{host}:{port}",
            auth=(user, password),
        )
        log.info("memgraph.driver_created", host=host, port=port)
    return _driver


def create_indexes() -> None:
    driver = get_driver()
    constraints = [
        ("Meeting", "CREATE CONSTRAINT ON (n:Meeting) ASSERT n.id IS UNIQUE"),
        ("Person", "CREATE CONSTRAINT ON (n:Person) ASSERT n.id IS UNIQUE"),
        ("Topic", "CREATE CONSTRAINT ON (n:Topic) ASSERT n.id IS UNIQUE"),
        ("Decision", "CREATE CONSTRAINT ON (n:Decision) ASSERT n.id IS UNIQUE"),
        ("ActionItem", "CREATE CONSTRAINT ON (n:ActionItem) ASSERT n.id IS UNIQUE"),
        ("Organization", "CREATE CONSTRAINT ON (n:Organization) ASSERT n.id IS UNIQUE"),
    ]
    with driver.session() as session:
        for label, cypher in constraints:
            try:
                session.run(cypher)
                log.info("memgraph.constraint_created", label=label)
            except Exception as exc:
                log.info("memgraph.constraint_exists", label=label, detail=str(exc))


# ── Write functions ────────────────────────────────────────────────────────────

def upsert_meeting(meeting: ExtractedMeeting, source_id: str) -> str:
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MERGE (m:Meeting {id: $id})
                ON CREATE SET
                    m.title=$title, m.date=$date, m.platform=$platform,
                    m.duration_minutes=$duration, m.summary=$summary,
                    m.sentiment=$sentiment, m.confidence=$confidence,
                    m.source=$source, m.created_at=datetime()
                ON MATCH SET
                    m.title=$title, m.summary=$summary, m.confidence=$confidence
                """,
                id=source_id,
                title=meeting.title,
                date=str(meeting.date) if meeting.date else None,
                platform=meeting.platform,
                duration=meeting.duration_minutes,
                summary=meeting.summary,
                sentiment=meeting.sentiment,
                confidence=meeting.confidence,
                source=getattr(meeting, "source", "unknown"),
            )
        )
    log.info("memgraph.upsert_meeting", meeting_id=source_id)
    return source_id


def upsert_person(name: str, email: str) -> str:
    person_id = email.lower()
    org = extract_domain(email)
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MERGE (p:Person {id: $id})
                ON CREATE SET p.name=$name, p.email=$id, p.organization=$org
                ON MATCH SET p.name=CASE WHEN p.name = '' THEN $name ELSE p.name END
                """,
                id=person_id, name=name, org=org,
            )
        )
    log.info("memgraph.upsert_person", person_id=person_id)
    return person_id


def upsert_organization(domain: str) -> str:
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MERGE (o:Organization {id: $domain})
                ON CREATE SET o.domain=$domain, o.name=$domain
                """,
                domain=domain,
            )
        )
    log.info("memgraph.upsert_organization", domain=domain)
    return domain


def upsert_topic(name: str) -> str:
    topic_id = slugify(name)
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MERGE (t:Topic {id: $id})
                ON CREATE SET t.name=$name, t.frequency=1
                ON MATCH SET t.frequency = t.frequency + 1
                """,
                id=topic_id, name=name,
            )
        )
    log.info("memgraph.upsert_topic", topic_id=topic_id)
    return topic_id


def upsert_decision(text: str, meeting_id: str) -> str:
    decision_id = uuid5_id(meeting_id, text)
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MERGE (d:Decision {id: $id})
                ON CREATE SET d.text=$text, d.status='open', d.date=date()
                """,
                id=decision_id, text=text,
            )
        )
    log.info("memgraph.upsert_decision", decision_id=decision_id)
    return decision_id


def upsert_action_item(item: ActionItem, meeting_id: str) -> str:
    action_id = uuid5_id(meeting_id, item.task)
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MERGE (a:ActionItem {id: $id})
                ON CREATE SET
                    a.task=$task, a.due=$due, a.priority=$priority,
                    a.done=$done, a.owner=$owner, a.jira_key=null
                """,
                id=action_id,
                task=item.task,
                due=str(item.due) if item.due else None,
                priority=item.priority,
                done=item.done,
                owner=item.owner,
            )
        )
    log.info("memgraph.upsert_action_item", action_id=action_id)
    return action_id


def update_action_jira_key(action_id: str, jira_key: str) -> None:
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                "MATCH (a:ActionItem {id: $id}) SET a.jira_key=$jira_key",
                id=action_id, jira_key=jira_key,
            )
        )
    log.info("memgraph.update_action_jira_key", action_id=action_id, jira_key=jira_key)


# ── Edge functions ─────────────────────────────────────────────────────────────

def create_attended(person_id: str, meeting_id: str, role: str) -> None:
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MATCH (p:Person {id:$pid}), (m:Meeting {id:$mid})
                MERGE (p)-[:ATTENDED {role:$role}]->(m)
                """,
                pid=person_id, mid=meeting_id, role=role,
            )
        )


def create_discussed(meeting_id: str, topic_id: str) -> None:
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MATCH (m:Meeting {id:$mid}), (t:Topic {id:$tid})
                MERGE (m)-[:DISCUSSED]->(t)
                """,
                mid=meeting_id, tid=topic_id,
            )
        )


def create_produced_decision(meeting_id: str, decision_id: str) -> None:
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MATCH (m:Meeting {id:$mid}), (d:Decision {id:$did})
                MERGE (m)-[:PRODUCED]->(d)
                """,
                mid=meeting_id, did=decision_id,
            )
        )


def create_produced_action(meeting_id: str, action_id: str) -> None:
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MATCH (m:Meeting {id:$mid}), (a:ActionItem {id:$aid})
                MERGE (m)-[:PRODUCED]->(a)
                """,
                mid=meeting_id, aid=action_id,
            )
        )


def create_assigned_to(action_id: str, person_id: str) -> None:
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MATCH (a:ActionItem {id:$aid}), (p:Person {id:$pid})
                MERGE (a)-[:ASSIGNED_TO]->(p)
                """,
                aid=action_id, pid=person_id,
            )
        )


def create_works_at(person_id: str, org_id: str) -> None:
    driver = get_driver()
    with driver.session() as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MATCH (p:Person {id:$pid}), (o:Organization {id:$oid})
                MERGE (p)-[:WORKS_AT]->(o)
                """,
                pid=person_id, oid=org_id,
            )
        )


# ── Read functions ─────────────────────────────────────────────────────────────

def get_recent_meetings(limit: int = 10) -> List[dict]:
    driver = get_driver()
    with driver.session() as session:
        result = session.execute_read(
            lambda tx: tx.run(
                """
                MATCH (m:Meeting)
                OPTIONAL MATCH (p:Person)-[:ATTENDED]->(m)
                RETURN m, collect(p.name) as attendees
                ORDER BY m.date DESC LIMIT $limit
                """,
                limit=limit,
            ).data()
        )
    return result


def get_person_meetings(email: str) -> List[dict]:
    driver = get_driver()
    with driver.session() as session:
        result = session.execute_read(
            lambda tx: tx.run(
                """
                MATCH (p:Person {id:$email})-[:ATTENDED]->(m:Meeting)
                OPTIONAL MATCH (m)-[:DISCUSSED]->(t:Topic)
                RETURN m, collect(t.name) as topics ORDER BY m.date DESC
                """,
                email=email.lower(),
            ).data()
        )
    return result


def get_topic_meetings(topic_name: str) -> List[dict]:
    driver = get_driver()
    with driver.session() as session:
        result = session.execute_read(
            lambda tx: tx.run(
                """
                MATCH (m:Meeting)-[:DISCUSSED]->(t:Topic)
                WHERE toLower(t.name) CONTAINS toLower($name)
                RETURN m, t ORDER BY m.date DESC
                """,
                name=topic_name,
            ).data()
        )
    return result


def get_open_action_items(assignee_email: Optional[str] = None) -> List[dict]:
    driver = get_driver()
    with driver.session() as session:
        if assignee_email:
            result = session.execute_read(
                lambda tx: tx.run(
                    """
                    MATCH (a:ActionItem {done:false})-[:ASSIGNED_TO]->(p:Person {id:$email})
                    RETURN a, p.name as assignee ORDER BY a.due ASC
                    """,
                    email=assignee_email.lower(),
                ).data()
            )
        else:
            result = session.execute_read(
                lambda tx: tx.run(
                    """
                    MATCH (a:ActionItem {done:false})
                    OPTIONAL MATCH (a)-[:ASSIGNED_TO]->(p:Person)
                    RETURN a, p.name as assignee ORDER BY a.due ASC
                    """
                ).data()
            )
    return result


def get_weekly_digest_data() -> dict:
    driver = get_driver()
    with driver.session() as session:
        meetings = session.execute_read(
            lambda tx: tx.run(
                "MATCH (m:Meeting) WHERE m.date >= date() - duration({days:7}) RETURN m"
            ).data()
        )
        decisions = session.execute_read(
            lambda tx: tx.run(
                """
                MATCH (m:Meeting)-[:PRODUCED]->(d:Decision)
                WHERE m.date >= date() - duration({days:7})
                RETURN d
                """
            ).data()
        )
        actions_created = session.execute_read(
            lambda tx: tx.run(
                """
                MATCH (m:Meeting)-[:PRODUCED]->(a:ActionItem)
                WHERE m.date >= date() - duration({days:7})
                RETURN a
                """
            ).data()
        )
        actions_closed = session.execute_read(
            lambda tx: tx.run(
                """
                MATCH (a:ActionItem {done:true})
                RETURN a
                """
            ).data()
        )
        top_topics = session.execute_read(
            lambda tx: tx.run(
                """
                MATCH (m:Meeting)-[:DISCUSSED]->(t:Topic)
                WHERE m.date >= date() - duration({days:7})
                RETURN t.name as name, count(m) as freq
                ORDER BY freq DESC LIMIT 5
                """
            ).data()
        )
    return {
        "meetings": meetings,
        "decisions": decisions,
        "actions_created": actions_created,
        "actions_closed": actions_closed,
        "top_topics": top_topics,
    }


def get_graph_counts() -> dict:
    driver = get_driver()
    with driver.session() as session:
        def _count(tx: neo4j.ManagedTransaction, label: str) -> int:
            return tx.run(f"MATCH (n:{label}) RETURN count(n) as c").single()["c"]

        return {
            "meetings": session.execute_read(lambda tx: _count(tx, "Meeting")),
            "persons": session.execute_read(lambda tx: _count(tx, "Person")),
            "topics": session.execute_read(lambda tx: _count(tx, "Topic")),
            "decisions": session.execute_read(lambda tx: _count(tx, "Decision")),
            "action_items": session.execute_read(lambda tx: _count(tx, "ActionItem")),
        }
