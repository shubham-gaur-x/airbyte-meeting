# CLAUDE.md — meeting-memory-graph

This file is the authoritative context for Claude Code when working in this repository.
Read this entire file before writing any code. Re-read it at the start of every session.

---

## Current status

| Phase | Description | Status |
|---|---|---|
| 0 | Project scaffold | ✅ |
| 1 | utils.py + models.py | ✅ |
| 2 | db.py (Postgres staging) | ✅ |
| 3 | classifier.py | ✅ |
| 4 | extractor.py (Ollama/ngrok) | ✅ |
| 5 | memgraph_client.py | ✅ |
| 6 | graph_builder.py | ✅ |
| 7 | jira_pusher.py | ✅ |
| 8 | main.py (FastAPI) | ✅ |
| 9 | digest.py + scripts | ✅ |
| 10 | Docker + Railway | ✅ |
| 11 | Airbyte guide + demo doc | ✅ |

**Known issues / next steps:**
- Credentials needed in `.env` before first run (Neon, Memgraph, ngrok, Jira)
- Run `make setup` → fill in `.env` → `make dev` → `make pull-model`
- Run `make graph-setup` to create Memgraph constraints before first pipeline run
- Run `make test` to smoke-test with sample_data/sample_email.json

---

## Project summary

**meeting-memory-graph** is v3 of a meeting memory pipeline, evolved from:
- `meeting-memory` (Python + Obsidian vault) — v1
- `meeting-memory-n8n` (n8n + Confluence + Jira) — v2

v3 replaces flat-file/note output with a **property graph in Memgraph Cloud**,
replaces all custom ingestion code with **Airbyte Cloud** (4 source connectors),
and adds a **Postgres staging layer** between Airbyte and the transform service.
The project is designed to be demo-ready for the Airbyte team: cloud-accessible,
autonomous, and showcasing Airbyte's full connector + destination + webhook stack.

---

## Architecture

```
Gmail · Google Calendar · Slack · Jira
         │
         ▼  (Airbyte Cloud — 4 source connectors)
         │  incremental sync every 15 min
         │  Append+Dedup mode, schema evolution
         │  webhook on sync complete
         ▼
Neon Postgres (staging) — Airbyte destination
  raw_emails · raw_calendar_events
  raw_slack_messages · raw_jira_issues
  processed_flag BOOLEAN DEFAULT FALSE
         │
         ▼  (webhook triggers transform service)
Transform Service — Python 3.11, FastAPI, Docker → Railway
  1. classifier.py     rules-based meeting scorer (ported from v1)
  2. extractor.py      Ollama qwen2.5:14b via ngrok tunnel (Mac → cloud)
  3. graph_builder.py  MERGE operations into Memgraph Cloud
  4. jira_pusher.py    push ActionItems → Jira sprint or backlog
  5. digest.py         weekly graph traversal → Slack/email digest
         │
         ▼  (Bolt protocol)
Memgraph Cloud
  Nodes: Meeting · Person · Topic · Decision · ActionItem · Organization
  Edges: ATTENDED · DISCUSSED · PRODUCED · ASSIGNED_TO · FOLLOWS_UP
         WORKS_AT · MENTIONS
  Memgraph Lab: visual graph UI (shareable demo URL)
         │
         ▼
FastAPI query layer (same service as transform)
  /graph/meetings/recent
  /graph/person/{email}
  /graph/topic/{name}
  /graph/actions/open
  /graph/digest/weekly  ← Airbyte demo showstopper
  /webhook/airbyte      ← Airbyte sync-complete receiver
```

---

## Why Postgres staging stays in (important)

Airbyte has no native Memgraph destination. Postgres (Neon) is Airbyte's destination.
Removing Postgres would require either:
(a) A custom Airbyte destination connector (complex, defeats the showcase purpose), or
(b) Polling Airbyte's API for new data (fragile, no webhook).

Postgres staging gives us:
- The full Airbyte destination connector (well-supported, zero custom code)
- A durable buffer: Airbyte writes → transform service reads unprocessed rows → marks done
- Idempotent backfill: re-run without re-syncing from Airbyte
- Audit trail: raw data preserved even after graph is written

This IS the architecture. Do not attempt to remove or bypass the Postgres layer.

---

## Ollama + ngrok setup (Mac → Railway)

Ollama runs on the developer's Mac (localhost:11434). The transform service runs on
Railway (cloud). To connect them:

1. Install ngrok: `brew install ngrok`
2. Get a free static ngrok domain from dashboard.ngrok.com → Domains
3. Run: `OLLAMA_HOST=0.0.0.0 ollama serve` (allow external connections)
4. Run: `ngrok http 11434 --url https://{your-static-domain}.ngrok-free.app`
5. Set OLLAMA_BASE_URL=https://{your-static-domain}.ngrok-free.app in Railway env

Add basic auth to the ngrok tunnel to prevent public access:
```yaml
# ollama-policy.yaml
on_http_request:
  - actions:
    - type: basic-auth
      config:
        credentials:
          - "meeting-memory:your-secret-password"
```
Run: `ngrok http 11434 --url https://{domain}.ngrok-free.app --traffic-policy-file ollama-policy.yaml`
Set OLLAMA_NGROK_AUTH=meeting-memory:your-secret-password in Railway env.

The extractor.py must include the Authorization: Basic header when calling Ollama
if OLLAMA_NGROK_AUTH is set in the environment.

---

## Tech stack

| Layer | Tool | Why |
|---|---|---|
| Ingestion | Airbyte Cloud | 4 official connectors, showcase all Airbyte features |
| Staging DB | Neon Postgres | Serverless, free tier, cloud, Airbyte native destination |
| LLM | Ollama qwen2.5:14b | Best structured JSON accuracy for local 7-14B class |
| LLM tunnel | ngrok (static domain) | Exposes Mac Ollama to Railway transform service |
| Graph DB | Memgraph Cloud | Real-time graph, Cypher, Memgraph Lab UI, MAGE algorithms |
| Transform | Python 3.11, FastAPI | Docker, Railway/Fly.io deploy |
| Graph client | neo4j Python driver | Bolt-compatible with Memgraph |
| Task tracking | Jira Cloud | Push ActionItems, sprint routing (same as v1/v2) |

---

## Graph schema

### Node labels and properties

```cypher
(:Meeting {
  id: String,           -- message_id or calendar_event_id (unique key for MERGE)
  title: String,
  date: Date,
  platform: String,     -- "google-meet"|"zoom"|"teams"|"in-person"|"unknown"
  duration_minutes: Integer,
  summary: String,
  sentiment: String,    -- "positive"|"neutral"|"negative"
  confidence: Float,    -- LLM extraction confidence 0.0–1.0
  source: String,       -- "gmail"|"google-calendar"|"slack"
  created_at: DateTime
})

(:Person {
  id: String,           -- email.lower() (unique key for MERGE)
  name: String,
  email: String,
  organization: String  -- domain extracted from email
})

(:Topic {
  id: String,           -- slugify(name) (unique key for MERGE)
  name: String,
  frequency: Integer    -- incremented on every MERGE
})

(:Decision {
  id: String,           -- uuid5(meeting_id + text) (unique key for MERGE)
  text: String,
  date: Date,
  status: String        -- "open"|"implemented"|"reversed"
})

(:ActionItem {
  id: String,           -- uuid5(meeting_id + task) (unique key for MERGE)
  task: String,
  due: Date,            -- nullable
  priority: String,     -- "high"|"medium"|"low"
  done: Boolean,
  jira_key: String      -- nullable, set after Jira push
})

(:Organization {
  id: String,           -- domain e.g. "onixnet.com" (unique key for MERGE)
  name: String,
  domain: String
})
```

### Relationship types

```cypher
(:Person)-[:ATTENDED {role: "organizer"|"attendee"|"optional"}]->(:Meeting)
(:Meeting)-[:DISCUSSED]->(:Topic)
(:Meeting)-[:PRODUCED]->(:Decision)
(:Meeting)-[:PRODUCED]->(:ActionItem)
(:ActionItem)-[:ASSIGNED_TO]->(:Person)
(:Decision)-[:FOLLOWS_UP]->(:Decision)
(:Person)-[:WORKS_AT]->(:Organization)
(:Meeting)-[:MENTIONS]->(:Person)   -- mentioned in body but not in attendees list
```

---

## Repository structure

```
meeting-memory-graph/
├── CLAUDE.md                      ← this file
├── ARCHITECTURE.md
├── docker-compose.yml             ← local dev: Postgres + Ollama + service
├── .env.example
├── Makefile
├── README.md
│
├── airbyte/
│   ├── README.md                  ← step-by-step Airbyte Cloud setup guide
│   └── connections.yaml           ← connection config documentation
│
├── transform_service/
│   ├── main.py                    ← FastAPI app, webhook + query endpoints
│   ├── classifier.py              ← rules-based meeting scorer (ported from v1)
│   ├── extractor.py               ← Ollama extraction → Pydantic models
│   ├── graph_builder.py           ← orchestrates Memgraph writes
│   ├── jira_pusher.py             ← push ActionItems to Jira (ported from v1/v2)
│   ├── digest.py                  ← weekly graph traversal summary
│   ├── models.py                  ← Pydantic v2 data models
│   ├── db.py                      ← Neon Postgres connection + queries
│   ├── memgraph_client.py         ← all Cypher queries live here
│   ├── utils.py                   ← with_retry(), slugify(), uuid5_id()
│   ├── requirements.txt
│   └── Dockerfile
│
├── scripts/
│   ├── backfill.py                ← process all unprocessed Postgres rows
│   ├── setup_memgraph.py          ← create indexes + constraints
│   └── test_pipeline.py           ← smoke test with sample data
│
└── sample_data/
    └── sample_email.json          ← from v1, for local testing
```

---

## Environment variables (.env)

```bash
# ── Neon Postgres (Airbyte destination + staging)
DATABASE_URL=postgresql://user:pass@host/dbname?sslmode=require

# ── Ollama (via ngrok tunnel from Mac)
OLLAMA_BASE_URL=https://your-static-domain.ngrok-free.app
OLLAMA_MODEL=qwen2.5:14b
OLLAMA_NGROK_AUTH=meeting-memory:your-secret-password   # leave blank if no auth

# ── Memgraph Cloud
MEMGRAPH_HOST=your-instance.memgraph.cloud
MEMGRAPH_PORT=7687
MEMGRAPH_USER=memgraph
MEMGRAPH_PASSWORD=your_password

# ── Jira (same config as v1/v2)
JIRA_ENABLED=true
JIRA_DOMAIN=shubhamgaur1.atlassian.net
JIRA_EMAIL=shubham.gaur@onixnet.com
JIRA_API_TOKEN=
JIRA_PROJECT_KEY=SCRUM
JIRA_BOARD_ID=1
JIRA_ISSUE_TYPE=Task

# ── Airbyte webhook verification
AIRBYTE_WEBHOOK_SECRET=

# ── Service
PORT=8000
LOG_LEVEL=INFO
```

---

## Coding conventions

- Python 3.11+ with type hints on ALL function signatures
- Pydantic v2 for all data models — use `model_config = ConfigDict(extra="ignore")`
- `with_retry(max_attempts=3, base_delay=2.0)` decorator on all external calls
- Structured logging with `structlog` — every log includes `source`, `meeting_id`, `step`
- `httpx.AsyncClient` for ALL HTTP calls (Ollama, Jira, ngrok) — never requests or httpx.Client
- No Cypher outside `memgraph_client.py`
- No SQL outside `db.py`
- No hardcoded credentials — always `os.environ.get()`
- All Cypher node/edge writes use `MERGE` not `CREATE`
- Use `uuid5_id(namespace, value)` from utils.py for deterministic UUIDs

---

## Prior art to reuse from v1 and v2

From `meeting-memory` (v1) — Python:
- `lib/classifier.py` — port the scoring logic directly. Threshold: 0.6.
- `lib/extractor.py` — port the prompt structure and `Extracted` dataclass shape.
- `lib/utils.py` — port `with_retry()`.
- `lib/jira_client.py` + `lib/jira_pusher.py` — port Jira push + sprint routing.
  High priority → active sprint. Medium/low → backlog.

From `meeting-memory-n8n` (v2) — JavaScript (port to Python):
- Extracted data shape (IDENTICAL in v3):
  `{ title, kind, platform, date, start_time, end_time, duration_minutes,
     location, attendees, summary, topics, decisions, action_items,
     key_quotes, links, sentiment, follow_up_needed, confidence }`
- `action_items` entries: `{ owner, task, due, done, priority }`
- Jira priority routing: high → sprint, medium/low → backlog
- Fallback priority heuristic: due ≤14 days → high, ≤60 days → medium, else low

---

## Absolute rules — do NOT violate these

- Do NOT bypass or remove the Neon Postgres staging layer
- Do NOT use Composio (replaced by Airbyte)
- Do NOT write to Obsidian vault
- Do NOT use n8n
- Do NOT create Confluence pages
- Do NOT use `CREATE` in Cypher for unique nodes — always `MERGE`
- Do NOT use synchronous `requests` library — always `httpx.AsyncClient`
- Do NOT hardcode any secret, token, password, or API key in source code
- Do NOT put Cypher in files other than `memgraph_client.py`
- Do NOT put SQL in files other than `db.py`
