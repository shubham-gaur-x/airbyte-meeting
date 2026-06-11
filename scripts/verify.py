#!/usr/bin/env python3
"""
Verify that every system in the stack is reachable and configured correctly.
Run this after setup to confirm the demo is ready.

Usage:
    python scripts/verify.py
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
from datetime import datetime

import httpx

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"


def _p(ok: bool, label: str, detail: str = "") -> None:
    icon = PASS if ok else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"{icon} {label}{suffix}")


async def check_render(url: str) -> bool:
    print("\n── Render (transform service) ──")
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{url}/health")
        d = r.json()
        _p(r.status_code == 200, "HTTP reachable", f"status={r.status_code}")
        _p(bool(d.get("postgres")), "Postgres connected")
        _p(bool(d.get("memgraph")), "Memgraph connected")
        # support both old field name ("ollama") and new ("llm")
        llm_ok = bool(d.get("llm", d.get("ollama", False)))
        llm_backend = d.get("llm_backend", "ollama" if d.get("ollama") else "?")
        _p(llm_ok, f"LLM reachable ({llm_backend})")
        _p(True, "Graph counts", str(d.get("counts", {})))
        return bool(d.get("postgres")) and bool(d.get("memgraph")) and llm_ok
    except Exception as e:
        _p(False, "Render health check", str(e))
        return False


async def check_groq() -> bool:
    print("\n── Groq LLM ──")
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        _p(False, "GROQ_API_KEY", "not set in .env")
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get("https://api.groq.com/openai/v1/models",
                            headers={"Authorization": f"Bearer {key}"})
        models = [m["id"] for m in r.json().get("data", []) if "qwen" in m["id"]]
        ok = bool(models)
        _p(ok, "API key valid", f"qwen models: {models}")
        return ok
    except Exception as e:
        _p(False, "Groq API", str(e))
        return False


async def check_jira() -> bool:
    print("\n── Jira ──")
    domain = os.environ.get("JIRA_DOMAIN", "")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    if not all([domain, email, token]):
        _p(False, "Jira credentials", "JIRA_DOMAIN/EMAIL/API_TOKEN not set")
        return False
    encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
    headers = {"Authorization": f"Basic {encoded}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"https://{domain}/rest/api/3/myself", headers=headers)
            ok = r.status_code == 200
            name = r.json().get("displayName", "?") if ok else r.text[:80]
            _p(ok, "Authenticated", name)
            if ok:
                r2 = await c.get(
                    f"https://{domain}/rest/api/3/project/{os.environ.get('JIRA_PROJECT_KEY','SCRUM')}",
                    headers=headers)
                _p(r2.status_code == 200, "Project accessible",
                   os.environ.get("JIRA_PROJECT_KEY", "SCRUM"))
        return ok
    except Exception as e:
        _p(False, "Jira API", str(e))
        return False


TOKEN_URL = "https://cloud.airbyte.com/auth/realms/_airbyte-application-clients/protocol/openid-connect/token"


async def _get_airbyte_token() -> str:
    client_id = os.environ.get("AIRBYTE_CLIENT_ID", "")
    client_secret = os.environ.get("AIRBYTE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return ""
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        })
        if r.status_code == 200:
            return r.json().get("access_token", "")
    return ""


async def check_airbyte() -> bool:
    print("\n── Airbyte Cloud ──")
    workspace = os.environ.get("AIRBYTE_WORKSPACE_ID", "")
    token = await _get_airbyte_token()
    if not token:
        _p(False, "Airbyte credentials", "AIRBYTE_CLIENT_ID / AIRBYTE_CLIENT_SECRET not set in .env")
        return False
    _p(True, "Token obtained via client credentials")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r_src = await c.get(f"https://api.airbyte.com/v1/sources?workspaceId={workspace}",
                                headers=headers)
            ok = r_src.status_code == 200
            if not ok:
                _p(False, "API key", f"status={r_src.status_code}")
                return False
            sources = r_src.json().get("data", [])
            source_names = [s.get("name") for s in sources]
            _p(True, "API key valid", f"sources: {source_names}")

            r_conn = await c.get(f"https://api.airbyte.com/v1/connections?workspaceId={workspace}",
                                 headers=headers)
            conns = r_conn.json().get("data", []) if r_conn.status_code == 200 else []
            conn_names = [c_.get("name") for c_ in conns]
            _p(bool(conns), "Connections exist", str(conn_names))
            return bool(conns)
    except Exception as e:
        _p(False, "Airbyte API", str(e))
        return False


async def check_postgres() -> bool:
    print("\n── Neon Postgres ──")
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        _p(False, "DATABASE_URL", "not set")
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(url)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM raw_emails")
        emails = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM raw_calendar_events")
        events = cur.fetchone()[0]
        conn.close()
        _p(True, "Connected", f"raw_emails={emails}, raw_calendar_events={events}")
        return True
    except Exception as e:
        _p(False, "Postgres", str(e))
        return False


async def check_memgraph() -> bool:
    print("\n── Memgraph Cloud ──")
    host = os.environ.get("MEMGRAPH_HOST", "")
    port = int(os.environ.get("MEMGRAPH_PORT", "7687"))
    user = os.environ.get("MEMGRAPH_USER", "")
    pw = os.environ.get("MEMGRAPH_PASSWORD", "")
    if not host:
        _p(False, "MEMGRAPH_HOST", "not set")
        return False
    try:
        import neo4j
        driver = neo4j.GraphDatabase.driver(f"bolt+ssc://{host}:{port}", auth=(user, pw))
        with driver.session() as s:
            counts = {label: s.run(f"MATCH (n:{label}) RETURN count(n) as c").single()["c"]
                      for label in ["Meeting", "Person", "Topic", "ActionItem"]}
        driver.close()
        _p(True, "Connected", str(counts))
        return True
    except Exception as e:
        _p(False, "Memgraph", str(e))
        return False


async def main() -> None:
    print("=" * 55)
    print("  meeting-memory-graph — stack verification")
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 55)

    service_url = os.environ.get("WEBHOOK_BASE_URL", "https://meeting-memory-graph.onrender.com")

    groq, postgres, memgraph, jira, airbyte, render = await asyncio.gather(
        check_groq(),
        check_postgres(),
        check_memgraph(),
        check_jira(),
        check_airbyte(),
        check_render(service_url),
    )

    results = [groq, postgres, memgraph, jira, airbyte, render]
    passing = sum(1 for r in results if r)
    total = len(results)

    print(f"\n{'=' * 55}")
    print(f"  {passing}/{total} systems OK")
    if passing == total:
        print("  Demo is ready.")
    else:
        print("  Fix the FAIL items above, then re-run: make verify")
    print("=" * 55)
    sys.exit(0 if passing == total else 1)


if __name__ == "__main__":
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
    asyncio.run(main())
