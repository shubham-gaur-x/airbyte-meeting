"""Neon Postgres staging layer — connection pool and CRUD operations."""

from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

import structlog

from models import RawEmail
from utils import with_retry

log = structlog.get_logger()

_pool: Optional[SimpleConnectionPool] = None


def get_pool() -> SimpleConnectionPool:
    global _pool
    if _pool is None:
        database_url = os.environ["DATABASE_URL"]
        _pool = SimpleConnectionPool(minconn=1, maxconn=10, dsn=database_url)
        log.info("db.pool_created")
    return _pool


@with_retry(max_attempts=3, base_delay=2.0)
def create_staging_tables() -> None:
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS raw_emails (
                    id               SERIAL PRIMARY KEY,
                    message_id       TEXT UNIQUE NOT NULL,
                    subject          TEXT,
                    sender           TEXT,
                    recipients       JSONB DEFAULT '[]',
                    body_text        TEXT,
                    received_at      TIMESTAMPTZ,
                    processed        BOOLEAN DEFAULT FALSE,
                    processed_at     TIMESTAMPTZ,
                    processing_error TEXT,
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS raw_calendar_events (
                    id               SERIAL PRIMARY KEY,
                    event_id         TEXT UNIQUE NOT NULL,
                    title            TEXT,
                    description      TEXT,
                    organizer        TEXT,
                    attendees        JSONB DEFAULT '[]',
                    start_time       TIMESTAMPTZ,
                    end_time         TIMESTAMPTZ,
                    location         TEXT,
                    processed        BOOLEAN DEFAULT FALSE,
                    processed_at     TIMESTAMPTZ,
                    processing_error TEXT,
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS raw_slack_messages (
                    id               SERIAL PRIMARY KEY,
                    ts               TEXT UNIQUE NOT NULL,
                    channel_id       TEXT,
                    channel_name     TEXT,
                    user_id          TEXT,
                    text             TEXT,
                    thread_ts        TEXT,
                    message_at       TIMESTAMPTZ,
                    processed        BOOLEAN DEFAULT FALSE,
                    processed_at     TIMESTAMPTZ,
                    processing_error TEXT,
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS raw_jira_issues (
                    id               SERIAL PRIMARY KEY,
                    issue_key        TEXT UNIQUE NOT NULL,
                    summary          TEXT,
                    description      TEXT,
                    status           TEXT,
                    assignee         TEXT,
                    reporter         TEXT,
                    labels           JSONB DEFAULT '[]',
                    created_jira     TIMESTAMPTZ,
                    updated_jira     TIMESTAMPTZ,
                    processed        BOOLEAN DEFAULT FALSE,
                    processed_at     TIMESTAMPTZ,
                    processing_error TEXT,
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        conn.commit()
        log.info("db.staging_tables_ready")
    finally:
        pool.putconn(conn)


@with_retry(max_attempts=3, base_delay=2.0)
def get_unprocessed_emails(limit: int = 50) -> List[RawEmail]:
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM raw_emails WHERE processed = FALSE "
                "ORDER BY received_at ASC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
        log.info("db.get_unprocessed_emails", count=len(rows), table="raw_emails")
        return [RawEmail(**dict(row)) for row in rows]
    finally:
        pool.putconn(conn)


@with_retry(max_attempts=3, base_delay=2.0)
def mark_email_processed(
    message_id: str, success: bool, error_msg: Optional[str] = None
) -> None:
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE raw_emails SET processed=TRUE, processed_at=NOW(), "
                "processing_error=%s WHERE message_id=%s",
                (None if success else error_msg, message_id),
            )
        conn.commit()
        log.info("db.mark_email_processed", message_id=message_id, success=success)
    finally:
        pool.putconn(conn)


@with_retry(max_attempts=3, base_delay=2.0)
def get_unprocessed_events(limit: int = 50) -> List[dict]:
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM raw_calendar_events WHERE processed = FALSE "
                "ORDER BY start_time ASC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
        log.info("db.get_unprocessed_events", count=len(rows), table="raw_calendar_events")
        return [dict(row) for row in rows]
    finally:
        pool.putconn(conn)


@with_retry(max_attempts=3, base_delay=2.0)
def mark_event_processed(
    event_id: str, success: bool, error_msg: Optional[str] = None
) -> None:
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE raw_calendar_events SET processed=TRUE, processed_at=NOW(), "
                "processing_error=%s WHERE event_id=%s",
                (None if success else error_msg, event_id),
            )
        conn.commit()
        log.info("db.mark_event_processed", event_id=event_id, success=success)
    finally:
        pool.putconn(conn)


@with_retry(max_attempts=3, base_delay=2.0)
def get_stats() -> dict:
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FILTER (WHERE TRUE) AS total, "
                "COUNT(*) FILTER (WHERE processed) AS processed "
                "FROM raw_emails"
            )
            email_row = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*) FILTER (WHERE TRUE) AS total, "
                "COUNT(*) FILTER (WHERE processed) AS processed "
                "FROM raw_calendar_events"
            )
            event_row = cur.fetchone()
        return {
            "total_emails": email_row[0],
            "processed_emails": email_row[1],
            "pending_emails": email_row[0] - email_row[1],
            "total_events": event_row[0],
            "processed_events": event_row[1],
            "pending_events": event_row[0] - event_row[1],
        }
    finally:
        pool.putconn(conn)
