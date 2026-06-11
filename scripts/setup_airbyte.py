#!/usr/bin/env python3
"""
Airbyte Cloud setup script — creates destination, sources, and connections via API.

Usage:
    python scripts/setup_airbyte.py

Required env vars (in .env):
    AIRBYTE_API_KEY        — from Airbyte Cloud → Settings → API keys
    AIRBYTE_WORKSPACE_ID   — from the URL: cloud.airbyte.com/workspaces/<ID>/...
    DATABASE_URL           — Neon Postgres connection string
    WEBHOOK_BASE_URL       — public URL of the transform service (e.g. https://xyz.onrender.com)

    # Source credentials:
    GOOGLE_CLIENT_ID       — OAuth2 client ID from Google Cloud Console
    GOOGLE_CLIENT_SECRET   — OAuth2 client secret from Google Cloud Console
    GOOGLE_REFRESH_TOKEN   — OAuth2 refresh token (run scripts/get_google_token.py)
    JIRA_DOMAIN            — e.g. shubhamgaur1.atlassian.net
    JIRA_EMAIL
    JIRA_API_TOKEN
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

import httpx

BASE_URL = "https://api.airbyte.com/v1"
SYNC_FREQUENCY_MINUTES = 60  # free tier limit — use 60 minimum


def _headers() -> dict:
    key = os.environ.get("AIRBYTE_API_KEY", "")
    if not key:
        print("ERROR: AIRBYTE_API_KEY not set in .env")
        sys.exit(1)
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _workspace() -> str:
    ws = os.environ.get("AIRBYTE_WORKSPACE_ID", "")
    if not ws:
        print("ERROR: AIRBYTE_WORKSPACE_ID not set in .env")
        sys.exit(1)
    return ws


def _parse_neon(database_url: str) -> dict:
    p = urlparse(database_url)
    return {
        "host": p.hostname,
        "port": p.port or 5432,
        "database": p.path.lstrip("/"),
        "username": p.username,
        "password": p.password,
        "ssl_mode": {"mode": "require"},
        "schema": "public",
        "tunnel_method": {"tunnel_method": "NO_TUNNEL"},
    }


def api(method: str, path: str, **kwargs) -> dict:
    url = f"{BASE_URL}{path}"
    r = httpx.request(method, url, headers=_headers(), timeout=30, **kwargs)
    if r.status_code >= 400:
        print(f"  ERROR {r.status_code}: {r.text[:300]}")
        return {}
    return r.json()


# ── Destination ────────────────────────────────────────────────────────────────

def create_destination() -> str:
    print("\n[1/5] Creating Neon Postgres destination...")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("  SKIP: DATABASE_URL not set")
        return ""

    existing = api("GET", f"/destinations?workspaceId={_workspace()}")
    for d in existing.get("data", []):
        if d.get("name") == "meeting-memory-neon":
            print(f"  EXISTS: {d['destinationId']}")
            return d["destinationId"]

    result = api("POST", "/destinations", json={
        "workspaceId": _workspace(),
        "name": "meeting-memory-neon",
        "definitionId": "25c5221d-dce2-4163-ade9-739ef790f503",  # Postgres
        "configuration": {
            "destinationType": "postgres",
            **_parse_neon(database_url),
        },
    })
    dest_id = result.get("destinationId", "")
    print(f"  OK: {dest_id}")
    return dest_id


# ── Sources ────────────────────────────────────────────────────────────────────

def create_source_gmail() -> str:
    print("\n[2/5] Creating Gmail source...")
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
    if not all([client_id, client_secret, refresh_token]):
        print("  SKIP: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN not set")
        return ""

    existing = api("GET", f"/sources?workspaceId={_workspace()}")
    for s in existing.get("data", []):
        if s.get("name") == "gmail-source":
            print(f"  EXISTS: {s['sourceId']}")
            return s["sourceId"]

    result = api("POST", "/sources", json={
        "workspaceId": _workspace(),
        "name": "gmail-source",
        "definitionId": "2de7a064-af05-4b6d-8bf3-f9f35e7d34da",  # Gmail
        "configuration": {
            "sourceType": "gmail",
            "credentials": {
                "auth_type": "Client",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        },
    })
    source_id = result.get("sourceId", "")
    print(f"  OK: {source_id}")
    return source_id


def create_source_google_calendar() -> str:
    print("\n[3/5] Creating Google Calendar source...")
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
    if not all([client_id, client_secret, refresh_token]):
        print("  SKIP: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN not set")
        return ""

    existing = api("GET", f"/sources?workspaceId={_workspace()}")
    for s in existing.get("data", []):
        if s.get("name") == "gcal-source":
            print(f"  EXISTS: {s['sourceId']}")
            return s["sourceId"]

    result = api("POST", "/sources", json={
        "workspaceId": _workspace(),
        "name": "gcal-source",
        "definitionId": "71607ba1-c0ac-4799-8049-7f4b90dd50f7",  # Google Calendar
        "configuration": {
            "sourceType": "google-calendar",
            "credentials": {
                "auth_type": "Client",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        },
    })
    source_id = result.get("sourceId", "")
    print(f"  OK: {source_id}")
    return source_id


def create_source_jira() -> str:
    print("\n[4/5] Creating Jira source...")
    domain = os.environ.get("JIRA_DOMAIN", "")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    if not all([domain, email, token]):
        print("  SKIP: JIRA_DOMAIN / JIRA_EMAIL / JIRA_API_TOKEN not set")
        return ""

    existing = api("GET", f"/sources?workspaceId={_workspace()}")
    for s in existing.get("data", []):
        if s.get("name") == "jira-source":
            print(f"  EXISTS: {s['sourceId']}")
            return s["sourceId"]

    result = api("POST", "/sources", json={
        "workspaceId": _workspace(),
        "name": "jira-source",
        "definitionId": "68e63de3-c7f4-4324-9aad-4e9f07b0a81a",  # Jira
        "configuration": {
            "sourceType": "jira",
            "domain": domain,
            "email": email,
            "api_token": token,
            "start_date": "2024-01-01",
            "projects": [os.environ.get("JIRA_PROJECT_KEY", "SCRUM")],
        },
    })
    source_id = result.get("sourceId", "")
    print(f"  OK: {source_id}")
    return source_id


# ── Connections ────────────────────────────────────────────────────────────────

def create_connection(name: str, source_id: str, dest_id: str, prefix: str, streams: list[dict]) -> str:
    if not source_id or not dest_id:
        print(f"  SKIP {name}: missing source or destination ID")
        return ""

    existing = api("GET", f"/connections?workspaceId={_workspace()}")
    for c in existing.get("data", []):
        if c.get("name") == name:
            print(f"  EXISTS: {c['connectionId']}")
            return c["connectionId"]

    webhook_url = os.environ.get("WEBHOOK_BASE_URL", "").rstrip("/")
    notify_config = []
    if webhook_url:
        notify_config = [{
            "notificationType": "webhook",
            "webhookUrl": f"{webhook_url}/webhook/airbyte",
            "sendOnSuccess": True,
            "sendOnFailure": False,
        }]

    result = api("POST", "/connections", json={
        "workspaceId": _workspace(),
        "name": name,
        "sourceId": source_id,
        "destinationId": dest_id,
        "namespaceDefinition": "destination",
        "namespaceFormat": "${SOURCE_NAMESPACE}",
        "prefix": prefix,
        "nonBreakingSchemaUpdatesBehavior": "propagate_columns",
        "schedule": {
            "scheduleType": "cron",
            "cronExpression": "0 */1 * * *",  # every hour — free tier minimum
        },
        "configurations": {"streams": streams},
        "notifySchemaChanges": True,
        "notifications": notify_config,
    })
    conn_id = result.get("connectionId", "")
    print(f"  OK: {conn_id}")
    return conn_id


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 55)
    print("  Airbyte Cloud — meeting-memory-graph setup")
    print("=" * 55)

    dest_id = create_destination()
    if not dest_id:
        print("\nDestination creation failed — check DATABASE_URL and AIRBYTE_API_KEY")
        sys.exit(1)

    gmail_id = create_source_gmail()
    gcal_id  = create_source_google_calendar()
    jira_id  = create_source_jira()

    print("\n[5/5] Creating connections...")

    if gmail_id:
        create_connection("gmail-to-neon", gmail_id, dest_id, "raw_gmail_", [
            {"name": "messages", "syncMode": "incremental_append_dedup", "primaryKey": [["id"]], "cursorField": ["internalDate"]},
        ])

    if gcal_id:
        create_connection("gcal-to-neon", gcal_id, dest_id, "raw_gcal_", [
            {"name": "events", "syncMode": "incremental_append_dedup", "primaryKey": [["id"]], "cursorField": ["updated"]},
        ])

    if jira_id:
        create_connection("jira-to-neon", jira_id, dest_id, "raw_jira_", [
            {"name": "issues", "syncMode": "incremental_append_dedup", "primaryKey": [["id"]], "cursorField": ["updated"]},
            {"name": "sprints", "syncMode": "full_refresh_overwrite", "primaryKey": [["id"]]},
        ])

    print("\n" + "=" * 55)
    print("  Done! Summary:")
    print(f"  Destination : {dest_id}")
    print(f"  Gmail       : {gmail_id or 'skipped'}")
    print(f"  G. Calendar : {gcal_id or 'skipped'}")
    print(f"  Jira        : {jira_id or 'skipped'}")
    print("=" * 55)
    if not all([gmail_id, gcal_id, jira_id]):
        print("\nNOTE: Some sources were skipped — add credentials to .env and re-run.")
        print("      This script is idempotent — safe to run multiple times.")


if __name__ == "__main__":
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
    main()
