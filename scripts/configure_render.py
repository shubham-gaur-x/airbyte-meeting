#!/usr/bin/env python3
"""
Push all environment variables to the Render service via Render API,
then trigger a deploy.

Usage:
    python scripts/configure_render.py

Required env vars (in .env):
    RENDER_API_KEY   — Render Dashboard → Account → API Keys → Create API Key
    RENDER_SERVICE   — service name in render.yaml (default: meeting-memory-graph)
"""

from __future__ import annotations

import os
import sys
import time

import httpx

BASE_URL = "https://api.render.com/v1"

# These are the vars render.yaml expects (from .env → Render).
# LOG_LEVEL is hardcoded in render.yaml so we skip it here.
RENDER_ENV_KEYS = [
    "DATABASE_URL",
    "GROQ_API_KEY",
    "MEMGRAPH_HOST",
    "MEMGRAPH_PORT",
    "MEMGRAPH_USER",
    "MEMGRAPH_PASSWORD",
    "JIRA_ENABLED",
    "JIRA_DOMAIN",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_PROJECT_KEY",
    "JIRA_BOARD_ID",
    "JIRA_ISSUE_TYPE",
    "AIRBYTE_WEBHOOK_SECRET",
    "POLL_INTERVAL_MINUTES",
]


def _headers() -> dict:
    key = os.environ.get("RENDER_API_KEY", "")
    if not key:
        print("ERROR: RENDER_API_KEY not set in .env")
        print("  Get it at: Render Dashboard → Account Settings → API Keys → Create API Key")
        sys.exit(1)
    return {"Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json"}


def get_service_id(name: str) -> str:
    r = httpx.get(f"{BASE_URL}/services", headers=_headers(), timeout=15,
                  params={"limit": 100})
    r.raise_for_status()
    for svc in r.json():
        s = svc.get("service", svc)
        if s.get("name") == name:
            sid = s.get("id", "")
            print(f"  Found service '{name}': {sid}")
            return sid
    print(f"ERROR: Service '{name}' not found on Render.")
    print("  Make sure you pushed render.yaml and Render created the service.")
    sys.exit(1)


def get_existing_env_vars(service_id: str) -> dict:
    r = httpx.get(f"{BASE_URL}/services/{service_id}/env-vars",
                  headers=_headers(), timeout=15)
    r.raise_for_status()
    # API returns [{"envVar": {"key": ..., "value": ...}, "cursor": ...}, ...]
    return {item["envVar"]["key"]: item["envVar"]["value"] for item in r.json()}


def push_env_vars(service_id: str) -> None:
    print("\n[2/3] Reading env vars from .env...")
    existing_map = get_existing_env_vars(service_id)

    updates: list[dict] = []
    skipped: list[str] = []

    for key in RENDER_ENV_KEYS:
        val = os.environ.get(key, "").strip()
        if not val:
            skipped.append(key)
            continue
        updates.append({"key": key, "value": val})

    # Preserve any Render-managed vars not in our list
    for key, val in existing_map.items():
        if key not in RENDER_ENV_KEYS:
            updates.append({"key": key, "value": val})

    # LOG_LEVEL is hardcoded in render.yaml but we keep it
    if "LOG_LEVEL" not in [u["key"] for u in updates]:
        updates.append({"key": "LOG_LEVEL", "value": os.environ.get("LOG_LEVEL", "INFO")})

    r = httpx.put(
        f"{BASE_URL}/services/{service_id}/env-vars",
        headers=_headers(),
        json=updates,
        timeout=15,
    )
    r.raise_for_status()
    pushed = [u["key"] for u in updates if u["key"] in RENDER_ENV_KEYS]
    print(f"  Pushed {len(pushed)} vars: {', '.join(pushed)}")
    if skipped:
        print(f"  Skipped (not set in .env): {', '.join(skipped)}")


def trigger_deploy(service_id: str) -> str:
    print("\n[3/3] Triggering Render deploy...")
    r = httpx.post(
        f"{BASE_URL}/services/{service_id}/deploys",
        headers=_headers(),
        json={"clearCache": "do_not_clear"},
        timeout=15,
    )
    r.raise_for_status()
    deploy_id = r.json().get("id", "") if r.content else ""
    print(f"  Deploy triggered: {deploy_id or '(queued)'}")
    return deploy_id


def wait_for_deploy(service_id: str, deploy_id: str) -> None:
    print("  Waiting for deploy to complete", end="", flush=True)
    for _ in range(60):
        time.sleep(5)
        r = httpx.get(f"{BASE_URL}/services/{service_id}/deploys/{deploy_id}",
                      headers=_headers(), timeout=10)
        if r.status_code != 200:
            print(".", end="", flush=True)
            continue
        status = r.json().get("status", "")
        if status == "live":
            print(" live!")
            return
        if status in ("deactivated", "build_failed"):
            print(f"\n  ERROR: deploy status={status}")
            sys.exit(1)
        print(".", end="", flush=True)
    print("\n  Timed out waiting for deploy — check Render dashboard.")


def main() -> None:
    service_name = os.environ.get("RENDER_SERVICE", "meeting-memory-graph")
    print("=" * 55)
    print("  Render — configure env vars + deploy")
    print("=" * 55)

    print(f"\n[1/3] Finding service '{service_name}'...")
    service_id = get_service_id(service_name)

    push_env_vars(service_id)
    deploy_id = trigger_deploy(service_id)
    if deploy_id:
        wait_for_deploy(service_id, deploy_id)
    else:
        print("  Deploy queued — check https://dashboard.render.com for status.")
        url = os.environ.get("WEBHOOK_BASE_URL", "")
        if url:
            print(f"  Service URL: {url}/health")

    service_url = os.environ.get("WEBHOOK_BASE_URL", "https://meeting-memory-graph.onrender.com")
    print(f"\n  Service live at: {service_url}/health")
    print("=" * 55)


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
