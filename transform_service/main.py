"""FastAPI transform service — webhook receiver, graph query layer, background workers."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import httpx
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

import classifier
import db
import extractor
import graph_builder
import jira_pusher
import memgraph_client
from extractor import LowConfidenceError
from models import AirbyteWebhookPayload

log = structlog.get_logger()

JIRA_ENABLED = os.environ.get("JIRA_ENABLED", "false").lower() == "true"


POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "15"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.create_staging_tables()
    memgraph_client.create_indexes()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(process_new_emails, "interval", minutes=POLL_INTERVAL_MINUTES, id="poll_emails")
    scheduler.add_job(process_new_events, "interval", minutes=POLL_INTERVAL_MINUTES, id="poll_events")
    # Run once immediately on startup to pick up any backlog
    scheduler.add_job(process_new_events, "date", id="poll_events_startup")
    scheduler.start()

    log.info(
        "service.ready",
        memgraph_host=os.environ.get("MEMGRAPH_HOST", "not-set"),
        jira_enabled=JIRA_ENABLED,
        poll_interval_minutes=POLL_INTERVAL_MINUTES,
    )
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="meeting-memory-graph", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    t0 = time.monotonic()
    response = await call_next(request)
    duration_ms = int((time.monotonic() - t0) * 1000)
    log.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/webhook/airbyte")
async def airbyte_webhook(request: Request, background_tasks: BackgroundTasks):
    secret = os.environ.get("AIRBYTE_WEBHOOK_SECRET", "").strip()
    if secret:
        sig = request.headers.get("X-Airbyte-Signature", "")
        body = await request.body()
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            log.warning("webhook.invalid_signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        body = await request.body()

    import json
    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    payload = AirbyteWebhookPayload.model_validate(data)
    if payload.status != "succeeded":
        log.info("webhook.ignored", status=payload.status)
        return {"status": "ignored", "reason": payload.status}
    background_tasks.add_task(process_new_emails)
    background_tasks.add_task(process_new_events)
    log.info("webhook.queued", connection_id=payload.connection_id, source=payload.source if hasattr(payload, "source") else "?")
    return {"status": "queued", "connection_id": payload.connection_id}


@app.get("/health")
async def health():
    postgres_ok = False
    memgraph_ok = False
    llm_ok = False
    counts = {}

    try:
        db.get_stats()
        postgres_ok = True
    except Exception:
        pass

    try:
        counts = memgraph_client.get_graph_counts()
        memgraph_ok = True
    except Exception:
        pass

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {groq_key}"},
                )
                llm_ok = resp.status_code == 200
        except Exception:
            pass
        llm_backend = "groq"
    else:
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{ollama_url}/api/tags")
                llm_ok = resp.status_code == 200
        except Exception:
            pass
        llm_backend = "ollama"

    return {
        "status": "ok",
        "postgres": postgres_ok,
        "memgraph": memgraph_ok,
        "llm": llm_ok,
        "llm_backend": llm_backend,
        "counts": counts,
    }


@app.get("/graph/meetings/recent")
async def recent_meetings(limit: int = 10):
    rows = memgraph_client.get_recent_meetings(limit)
    return [{"meeting": _node_props("m", [r])[0], "attendees": r.get("attendees", [])} for r in rows]


@app.get("/graph/person/{email}")
async def person_meetings(email: str):
    rows = memgraph_client.get_person_meetings(email)
    return [{"meeting": _node_props("m", [r])[0], "topics": r.get("topics", [])} for r in rows]


@app.get("/graph/topic/{name}")
async def topic_meetings(name: str):
    rows = memgraph_client.get_topic_meetings(name)
    return [{"meeting": _node_props("m", [r])[0], "topic": _node_props("t", [r])[0]} for r in rows]


@app.get("/graph/actions/open")
async def open_actions(assignee: Optional[str] = None):
    return memgraph_client.get_open_action_items(assignee)


@app.post("/process")
async def trigger_process():
    """Manually trigger processing of unprocessed events (useful for demos)."""
    await process_new_events()
    await process_new_emails()
    counts = memgraph_client.get_graph_counts()
    return {"status": "done", "graph_counts": counts}


def _node_props(record_key: str, rows: list) -> list:
    """Extract node properties from neo4j query rows, serializing datetime objects."""
    out = []
    for row in rows:
        node = row.get(record_key, row)
        props = dict(node) if hasattr(node, "items") else node
        cleaned = {}
        for k, v in props.items():
            if hasattr(v, "isoformat"):
                cleaned[k] = v.isoformat()
            elif hasattr(v, "_DateTime__date"):
                cleaned[k] = str(v)
            else:
                cleaned[k] = v
        out.append(cleaned)
    return out


@app.get("/graph/digest/weekly")
async def weekly_digest(days: int = 30):
    data = memgraph_client.get_weekly_digest_data(days=days)
    meetings = data.get("meetings", [])
    decisions = data.get("decisions", [])
    actions_created = data.get("actions_created", [])
    actions_closed = data.get("actions_closed", [])
    top_topics = data.get("top_topics", [])
    return {
        "period": f"last {days} days",
        "meetings_count": len(meetings),
        "meetings": _node_props("m", meetings),
        "decisions_made": _node_props("d", decisions),
        "action_items_created": len(actions_created),
        "action_items_completed": len(actions_closed),
        "top_topics": [{"topic": t.get("name"), "mentions": t.get("freq")} for t in top_topics],
        "generated_at": datetime.utcnow().isoformat(),
    }


# ── Background tasks ───────────────────────────────────────────────────────────

async def process_new_emails() -> None:
    emails = db.get_unprocessed_emails(limit=50)
    processed = skipped = errors = 0

    for email in emails:
        try:
            clf = classifier.classify(email)
            if not clf.is_meeting or clf.is_invite:
                db.mark_email_processed(email.message_id, success=True)
                skipped += 1
                continue

            meeting = await extractor.extract(email)
            build = await graph_builder.build_graph(meeting, email.message_id)

            if JIRA_ENABLED:
                await jira_pusher.push_action_items(
                    meeting.action_items, meeting, email.message_id
                )

            db.mark_email_processed(email.message_id, success=True)
            processed += 1

        except LowConfidenceError as exc:
            db.mark_email_processed(email.message_id, success=False, error_msg=str(exc))
            skipped += 1
        except Exception as exc:
            log.error("process_emails.error", message_id=email.message_id, error=str(exc))
            db.mark_email_processed(email.message_id, success=False, error_msg=str(exc))
            errors += 1

    log.info("process_emails.done", processed=processed, skipped=skipped, errors=errors)


_SKIP_TITLES = ("out of office", "ooo", "birthday", "holiday", "away", "vacation", "pto")


def _is_calendar_meeting(event: dict) -> bool:
    """Calendar-aware meeting check: Meet link or 2+ attendees = meeting."""
    title = (event.get("title") or "").lower()
    if any(kw in title for kw in _SKIP_TITLES):
        return False
    location = (event.get("location") or "").lower()
    if any(kw in location for kw in ("meet.google.com", "zoom.us", "teams.microsoft", "microsoft teams", "webex", "gotomeeting")):
        return True
    attendees = event.get("attendees") or []
    if isinstance(attendees, list) and len(attendees) >= 2:
        return True
    return False


async def process_new_events() -> None:
    imported = db.sync_airbyte_calendar_events()
    if imported:
        log.info("process_events.imported_from_airbyte", count=imported)
    events = db.get_unprocessed_events(limit=50)
    processed = skipped = errors = 0

    for event in events:
        event_id = event.get("event_id", "")
        try:
            if not _is_calendar_meeting(event):
                db.mark_event_processed(event_id, success=True)
                skipped += 1
                continue

            from datetime import datetime as dt
            from models import RawEmail
            subject = event.get("title") or ""
            body = event.get("description") or ""
            synthetic = RawEmail(
                id=event.get("id", 0),
                message_id=event_id,
                subject=subject,
                sender=event.get("organizer", ""),
                body_text=f"{body}\n\nAttendees: {event.get('attendees', [])}",
                received_at=event.get("start_time") or dt.utcnow(),
            )
            meeting = await extractor.extract(synthetic)
            await graph_builder.build_graph(meeting, event_id)

            if JIRA_ENABLED:
                await jira_pusher.push_action_items(meeting.action_items, meeting, event_id)

            db.mark_event_processed(event_id, success=True)
            processed += 1

        except LowConfidenceError as exc:
            db.mark_event_processed(event_id, success=False, error_msg=str(exc))
            skipped += 1
        except Exception as exc:
            log.error("process_events.error", event_id=event_id, error=str(exc))
            db.mark_event_processed(event_id, success=False, error_msg=str(exc))
            errors += 1

    log.info("process_events.done", processed=processed, skipped=skipped, errors=errors)
