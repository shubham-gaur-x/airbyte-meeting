# Claude Code Prompts — meeting-memory-graph
# Run these in Claude Code, one phase at a time. Always have CLAUDE.md in the repo root.

---

## PHASE 0 — Project scaffold

```
Read CLAUDE.md in full before writing a single line.

Scaffold the complete meeting-memory-graph project directory exactly as described
in the "Repository structure" section of CLAUDE.md.

Deliverables:
1. All directories and empty __init__.py / placeholder files from the structure.

2. .env.example — every variable from "Environment variables" in CLAUDE.md,
   with placeholder values and a one-line comment explaining each.

3. Makefile with targets:
   setup       — create venv, pip install -r transform_service/requirements.txt, cp .env.example .env
   dev         — docker compose up (local Postgres + Ollama + service with hot reload)
   run         — cd transform_service && uvicorn main:app --reload --port 8000
   backfill    — python scripts/backfill.py
   test        — python scripts/test_pipeline.py
   graph-setup — python scripts/setup_memgraph.py
   pull-model  — docker compose exec ollama ollama pull qwen2.5:14b
   logs        — docker compose logs -f transform_service
   stop        — docker compose down

4. docker-compose.yml (LOCAL DEV ONLY):
   postgres service: postgres:15, port 5432, volume postgres_data,
     env POSTGRES_DB=meeting_memory POSTGRES_USER=postgres POSTGRES_PASSWORD=postgres
   ollama service: ollama/ollama:latest, port 11434, volume ollama_data
     (include a commented GPU passthrough section for NVIDIA users)
   transform_service: build ./transform_service, port 8000, env_file .env,
     depends_on postgres+ollama, volume ./transform_service:/app for hot reload

5. transform_service/requirements.txt:
   fastapi uvicorn[standard] httpx pydantic[email] psycopg2-binary
   neo4j structlog python-dotenv tqdm python-slugify

6. README.md with:
   - One-paragraph project summary
   - Quickstart (5 steps: clone → setup → add .env values → make dev → make pull-model)
   - Links to ARCHITECTURE.md and airbyte/README.md

No application logic yet. Scaffold only.
```

---

## PHASE 1 — Utilities + Pydantic models

```
Read CLAUDE.md in full.

Implement transform_service/utils.py and transform_service/models.py.

── utils.py ──────────────────────────────────────────────────────────────

1. with_retry(max_attempts: int = 3, base_delay: float = 2.0)
   Decorator. On exception, waits base_delay * (2 ** attempt) seconds.
   Logs each retry at WARNING level with attempt number and exception message.
   Raises the last exception if all attempts fail.
   Works on both sync and async functions (detect with asyncio.iscoroutinefunction).

2. slugify(text: str) -> str
   Lowercase, strip accents, replace spaces+special chars with hyphens.
   Use the python-slugify library. Max 64 chars.

3. uuid5_id(namespace: str, value: str) -> str
   Deterministic UUID using uuid.uuid5(uuid.NAMESPACE_DNS, namespace + ":" + value).
   Returns the UUID as a lowercase hex string (no hyphens).
   Used to generate stable IDs for Decision and ActionItem nodes.

4. extract_domain(email: str) -> str
   "alice@onixnet.com" → "onixnet.com". Returns "" on malformed input.

5. safe_date(value: Any) -> Optional[date]
   Tries to parse a date from a string, datetime, or date. Returns None on failure.
   Handles ISO 8601, "May 9 2026", "2026-05-09", datetime objects.

── models.py ──────────────────────────────────────────────────────────────

All models: Pydantic v2, model_config = ConfigDict(extra="ignore").

1. Attendee
   name: str, email: str = "",
   role: Literal["organizer", "attendee", "optional"] = "attendee"

2. ActionItem
   owner: str = "", task: str,
   due: Optional[date] = None,
   done: bool = False,
   priority: Literal["high", "medium", "low"] = "medium"

   Add a validator: if priority is not set, derive it from due using the
   heuristic from CLAUDE.md: due ≤14 days → high, ≤60 days → medium, else low.

3. ExtractedMeeting
   Exact shape from CLAUDE.md "Prior art to reuse" section:
   title, kind, platform, date (Optional[date]), start_time (Optional[str]),
   end_time (Optional[str]), duration_minutes (Optional[int]), location (str=""),
   attendees: List[Attendee] = [], summary: str = "",
   topics: List[str] = [], decisions: List[str] = [],
   action_items: List[ActionItem] = [], key_quotes: List[str] = [],
   links: List[str] = [], sentiment: str = "neutral",
   follow_up_needed: bool = False, confidence: float = 0.0

4. RawEmail
   id: int, message_id: str, subject: str = "", sender: str = "",
   recipients: List[str] = [], body_text: str = "",
   received_at: datetime, processed: bool = False

5. ClassificationResult
   score: float, is_meeting: bool, is_invite: bool,
   matched_signals: List[str]

6. ProcessResult
   message_id: str,
   status: Literal["processed", "skipped", "error"],
   reason: Optional[str] = None,
   meeting_title: Optional[str] = None,
   nodes_created: int = 0, edges_created: int = 0

7. BuildResult
   meeting_id: str, nodes_created: int, edges_created: int,
   persons: List[str] = [], topics: List[str] = [],
   decisions_count: int = 0, action_items_count: int = 0

8. AirbyteWebhookPayload
   connection_id: str, status: str,
   streams: List[str] = []

Full docstrings on every class. Include a __all__ list.
```

---

## PHASE 2 — Postgres staging layer (db.py)

```
Read CLAUDE.md in full.

Implement transform_service/db.py.

Uses psycopg2. Connection pool: SimpleConnectionPool(minconn=1, maxconn=10).
DATABASE_URL is read from os.environ. Pool is a module-level singleton
created lazily on first call to get_pool().

Implement these functions (all use with_retry from utils.py):

1. get_pool() -> SimpleConnectionPool

2. create_staging_tables()
   Creates these tables with IF NOT EXISTS:

   raw_emails (
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

   raw_calendar_events (
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

   raw_slack_messages (
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

   raw_jira_issues (
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

3. get_unprocessed_emails(limit: int = 50) -> List[RawEmail]
   SELECT * FROM raw_emails WHERE processed = FALSE
   ORDER BY received_at ASC LIMIT %s
   Map rows to RawEmail model.

4. mark_email_processed(message_id: str, success: bool, error_msg: str = None)
   UPDATE raw_emails SET processed=TRUE, processed_at=NOW(),
   processing_error=%s WHERE message_id=%s
   Set processing_error=NULL on success, error text on failure.

5. get_unprocessed_events(limit: int = 50) -> List[dict]
   Same pattern for raw_calendar_events.

6. mark_event_processed(event_id: str, success: bool, error_msg: str = None)

7. get_stats() -> dict
   Returns counts: total_emails, processed_emails, pending_emails,
   total_events, processed_events, pending_events.
   Used by the /health endpoint.

All functions log with structlog including table name and row count.
Wrap every DB operation in a try/finally that returns the connection to the pool.
```

---

## PHASE 3 — Classifier

```
Read CLAUDE.md in full.

Implement transform_service/classifier.py by porting the rules-based scorer
from meeting-memory v1 (lib/classifier.py). The logic is proven — port it
faithfully, adapt to use RawEmail from models.py.

The classifier scores on 0.0–1.0 based on signal groups. Each group contributes
a weight. Final score is the sum of group weights, capped at 1.0.

Signal groups and weights:
  meeting_keywords   +0.25  (recap, minutes, sync, standup, all-hands, call log,
                              meeting notes, discussed, action items, follow-up,
                              agenda, meeting summary, post-mortem)
  attendee_language  +0.15  (attendees:, participants:, present:, joined by,
                              on the call)
  datetime_patterns  +0.10  (regex: time patterns like 2pm, 14:00, AM/PM in body)
  decision_language  +0.20  (we decided, agreed to, resolved, approved, rejected,
                              signed off)
  action_language    +0.20  (action:, action item, to-do, owner:, due:, assigned to,
                              next steps)
  invite_penalty     -0.40  (you've been invited, accept/decline, calendar invite,
                              added to your calendar — these are pre-meeting invites
                              with no content)

classify(email: RawEmail) -> ClassificationResult:
  score = sum of triggered group weights, clamped [0.0, 1.0]
  is_meeting = score >= 0.6
  is_invite = invite_penalty signals fired (regardless of score)
  matched_signals = list of signal names that fired

Also implement:
classify_text(subject: str, body: str) -> ClassificationResult
  Same logic, takes raw strings instead of RawEmail.
  Used for testing and for calendar event classification.

At the bottom, under if __name__ == "__main__":
  Run 6 tests and print PASS/FAIL for each:
  1. Clear meeting recap email (expect is_meeting=True, is_invite=False)
  2. Calendar invite (expect is_invite=True)
  3. Promotional email (expect is_meeting=False)
  4. Slack export of a meeting discussion (expect is_meeting=True)
  5. Email with only action items (expect is_meeting=True)
  6. Empty body (expect is_meeting=False, score=0.0)
```

---

## PHASE 4 — LLM extractor (Ollama via ngrok)

```
Read CLAUDE.md in full. Pay close attention to the "Ollama + ngrok setup" section.

Implement transform_service/extractor.py.

Uses httpx.AsyncClient exclusively. OLLAMA_BASE_URL, OLLAMA_MODEL,
OLLAMA_NGROK_AUTH are read from env.

Custom exceptions (define at top of file):
  class OllamaUnavailableError(Exception): pass
  class LowConfidenceError(Exception):
    def __init__(self, confidence: float, title: str): ...

Helper: _get_headers() -> dict
  If OLLAMA_NGROK_AUTH is set (format "user:pass"), return
  {"Authorization": "Basic " + base64(user:pass), "Content-Type": "application/json"}
  Otherwise return {"Content-Type": "application/json"}

async def extract(email: RawEmail) -> ExtractedMeeting:

  1. Build the prompt:
     SYSTEM: You are a meeting intelligence assistant. Extract structured data from
     meeting email content. Be precise with names, emails, and dates. Return ONLY
     valid JSON. Do not add commentary, markdown, or explanation.

     USER:
     Subject: {email.subject}
     From: {email.sender}
     Date: {email.received_at.strftime("%Y-%m-%d")}
     Body:
     {email.body_text[:4000]}  ← truncate at 4000 chars to stay within context

  2. POST to {OLLAMA_BASE_URL}/api/generate:
     {
       "model": OLLAMA_MODEL,
       "prompt": full_prompt,
       "stream": false,
       "format": ExtractedMeeting.model_json_schema(),
       "options": {"temperature": 0.0, "num_predict": 2048}
     }
     Headers from _get_headers().
     Timeout: httpx.Timeout(120.0) — LLM can be slow.

  3. Parse response.json()["response"] → ExtractedMeeting.model_validate_json()

  4. If confidence < 0.3 → raise LowConfidenceError(confidence, title)

  5. Wrap entire call with @with_retry(max_attempts=3, base_delay=2.0).
     Catch httpx.ConnectError specifically → raise OllamaUnavailableError(
       f"Cannot reach Ollama at {OLLAMA_BASE_URL}. Is ngrok running?")

  6. If OLLAMA_MODEL is "qwen2.5:14b" and the call returns HTTP 404,
     retry once with "qwen2.5:7b" and log a WARNING.

  7. Log at INFO: model, duration_ms, confidence, extracted title.

def extract_sync(email: RawEmail) -> ExtractedMeeting:
  return asyncio.run(extract(email))
  Used by backfill scripts.
```

---

## PHASE 5 — Memgraph client

```
Read CLAUDE.md in full. All Cypher lives in this file — nowhere else.

Implement transform_service/memgraph_client.py.

Uses the neo4j Python driver (bolt-compatible with Memgraph).
Driver is a module-level singleton created lazily by get_driver().
URI: bolt://{MEMGRAPH_HOST}:{MEMGRAPH_PORT}

── Setup ─────────────────────────────────────────────────────────────────

def get_driver() -> neo4j.Driver

def create_indexes():
  Run at service startup. Execute these Cypher statements, each wrapped in
  individual try/except (Memgraph raises if constraint already exists):
    CREATE CONSTRAINT ON (n:Meeting)     ASSERT n.id IS UNIQUE;
    CREATE CONSTRAINT ON (n:Person)      ASSERT n.id IS UNIQUE;
    CREATE CONSTRAINT ON (n:Topic)       ASSERT n.id IS UNIQUE;
    CREATE CONSTRAINT ON (n:Decision)    ASSERT n.id IS UNIQUE;
    CREATE CONSTRAINT ON (n:ActionItem)  ASSERT n.id IS UNIQUE;
    CREATE CONSTRAINT ON (n:Organization) ASSERT n.id IS UNIQUE;
  Log success or "already exists" for each.

── Write functions (all use session.execute_write) ───────────────────────

def upsert_meeting(meeting: ExtractedMeeting, source_id: str) -> str
  MERGE (m:Meeting {id: $id})
  ON CREATE SET m.title=$title, m.date=$date, m.platform=$platform,
    m.duration_minutes=$duration, m.summary=$summary,
    m.sentiment=$sentiment, m.confidence=$confidence,
    m.source=$source, m.created_at=datetime()
  ON MATCH SET m.title=$title, m.summary=$summary, m.confidence=$confidence
  Return source_id.

def upsert_person(name: str, email: str) -> str
  id = email.lower()
  MERGE (p:Person {id: $id})
  ON CREATE SET p.name=$name, p.email=$id, p.organization=$org
  ON MATCH SET p.name=CASE WHEN p.name = "" THEN $name ELSE p.name END
  org = extract_domain(email). Return id.

def upsert_organization(domain: str) -> str
  MERGE (o:Organization {id: $domain})
  ON CREATE SET o.domain=$domain, o.name=$domain
  Return domain.

def upsert_topic(name: str) -> str
  id = slugify(name)
  MERGE (t:Topic {id: $id})
  ON CREATE SET t.name=$name, t.frequency=1
  ON MATCH SET t.frequency = t.frequency + 1
  Return id.

def upsert_decision(text: str, meeting_id: str) -> str
  id = uuid5_id(meeting_id, text)
  MERGE (d:Decision {id: $id})
  ON CREATE SET d.text=$text, d.status="open", d.date=date()
  Return id.

def upsert_action_item(item: ActionItem, meeting_id: str) -> str
  id = uuid5_id(meeting_id, item.task)
  MERGE (a:ActionItem {id: $id})
  ON CREATE SET a.task=$task, a.due=$due, a.priority=$priority,
    a.done=$done, a.owner=$owner, a.jira_key=null
  Return id.

def update_action_jira_key(action_id: str, jira_key: str)
  MATCH (a:ActionItem {id: $id}) SET a.jira_key=$jira_key

── Edge functions (all use session.execute_write) ────────────────────────

def create_attended(person_id: str, meeting_id: str, role: str)
  MATCH (p:Person {id:$pid}), (m:Meeting {id:$mid})
  MERGE (p)-[:ATTENDED {role:$role}]->(m)

def create_discussed(meeting_id: str, topic_id: str)
def create_produced_decision(meeting_id: str, decision_id: str)
def create_produced_action(meeting_id: str, action_id: str)
def create_assigned_to(action_id: str, person_id: str)
def create_works_at(person_id: str, org_id: str)
  MATCH (p:Person {id:$pid}), (o:Organization {id:$oid})
  MERGE (p)-[:WORKS_AT]->(o)

── Read functions (all use session.execute_read) ─────────────────────────

def get_recent_meetings(limit: int = 10) -> List[dict]
  MATCH (m:Meeting)
  OPTIONAL MATCH (p:Person)-[:ATTENDED]->(m)
  RETURN m, collect(p.name) as attendees
  ORDER BY m.date DESC LIMIT $limit

def get_person_meetings(email: str) -> List[dict]
  MATCH (p:Person {id:$email})-[:ATTENDED]->(m:Meeting)
  OPTIONAL MATCH (m)-[:DISCUSSED]->(t:Topic)
  RETURN m, collect(t.name) as topics ORDER BY m.date DESC

def get_topic_meetings(topic_name: str) -> List[dict]
  MATCH (m:Meeting)-[:DISCUSSED]->(t:Topic)
  WHERE toLower(t.name) CONTAINS toLower($name)
  RETURN m, t ORDER BY m.date DESC

def get_open_action_items(assignee_email: str = None) -> List[dict]
  If assignee_email:
    MATCH (a:ActionItem {done:false})-[:ASSIGNED_TO]->(p:Person {id:$email})
  Else:
    MATCH (a:ActionItem {done:false})
    OPTIONAL MATCH (a)-[:ASSIGNED_TO]->(p:Person)
  RETURN a, p.name as assignee ORDER BY a.due ASC

def get_weekly_digest_data() -> dict
  Run 5 separate queries covering last 7 days:
  1. meetings: MATCH (m:Meeting) WHERE m.date >= date() - duration({days:7}) RETURN m
  2. decisions: MATCH (m:Meeting)-[:PRODUCED]->(d:Decision) WHERE m.date >= ...
  3. actions created: MATCH (m:Meeting)-[:PRODUCED]->(a:ActionItem) WHERE m.date >= ...
  4. actions closed: MATCH (a:ActionItem {done:true}) WHERE ...
  5. top topics: MATCH (m:Meeting)-[:DISCUSSED]->(t:Topic) WHERE m.date >= ...
     RETURN t.name, count(m) as freq ORDER BY freq DESC LIMIT 5
  Return as dict with keys: meetings, decisions, actions_created, actions_closed, top_topics

def get_graph_counts() -> dict
  Returns {meetings: int, persons: int, topics: int, decisions: int, action_items: int}
  Used by /health endpoint. One MATCH per label.

Use structlog on every function. Log node IDs and operation type.
```

---

## PHASE 6 — Graph builder

```
Read CLAUDE.md in full.

Implement transform_service/graph_builder.py.

This module orchestrates the full graph write for one ExtractedMeeting.
NO Cypher here — only calls to memgraph_client functions.

async def build_graph(
  meeting: ExtractedMeeting,
  source_id: str,
  dry_run: bool = False
) -> BuildResult:

Steps (wrap each step in try/except, log WARNING and continue on failure):

1. Upsert the Meeting node. meeting_id = source_id.

2. For each attendee in meeting.attendees:
   a. Upsert Person (name, email). person_id = email.lower().
   b. If email domain is not empty: upsert Organization. Create WORKS_AT edge.
   c. Create ATTENDED edge (role from attendee.role).
   Collect person_ids list for later.

3. For each topic in meeting.topics:
   a. Upsert Topic. Create DISCUSSED edge.

4. For each decision in meeting.decisions:
   a. Upsert Decision. Create PRODUCED edge from Meeting.

5. For each action_item in meeting.action_items:
   a. Upsert ActionItem. Create PRODUCED edge from Meeting.
   b. Try to match action_item.owner to an attendee email or name.
      If match found: create ASSIGNED_TO edge.
   Collect action_ids list.

6. Count: nodes_created = Meeting + len(persons) + len(orgs) + len(topics) + len(decisions) + len(actions)
   edges_created = ATTENDED + WORKS_AT + DISCUSSED + PRODUCED(decisions) + PRODUCED(actions) + ASSIGNED_TO

7. Return BuildResult.

When dry_run=True: skip all memgraph_client calls, log "[DRY RUN]" prefix, still return BuildResult with estimated counts.

Log at INFO: meeting_id, title, nodes_created, edges_created, duration_ms.
```

---

## PHASE 7 — Jira pusher (ported from v1)

```
Read CLAUDE.md in full. See "Prior art to reuse" for v1 Jira logic.

Implement transform_service/jira_pusher.py by porting lib/jira_client.py
and lib/jira_pusher.py from meeting-memory v1 into a single module.

All Jira credentials from env: JIRA_ENABLED, JIRA_DOMAIN, JIRA_EMAIL,
JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_BOARD_ID, JIRA_ISSUE_TYPE.

Jira base URL: https://{JIRA_DOMAIN}/rest/api/3
Agile base URL: https://{JIRA_DOMAIN}/rest/agile/1.0
Auth: HTTP Basic (email + api_token), base64 encoded.

async def push_action_items(
  action_items: List[ActionItem],
  meeting: ExtractedMeeting,
  meeting_node_id: str
) -> List[str]:   ← returns list of created Jira issue keys

For each action_item:
  1. Create Jira issue:
     POST /rest/api/3/issue
     {
       "fields": {
         "project": {"key": JIRA_PROJECT_KEY},
         "summary": action_item.task,
         "issuetype": {"name": JIRA_ISSUE_TYPE},
         "priority": {"name": priority_map[action_item.priority]},
         "duedate": action_item.due.isoformat() if due else null,
         "labels": ["meeting-generated"] + meeting.topics[:3],
         "description": {ADF format with meeting title, date, summary, attendees}
       }
     }
     priority_map: "high"→"High", "medium"→"Medium", "low"→"Low"

  2. If action_item.priority == "high":
     a. GET /rest/agile/1.0/board/{JIRA_BOARD_ID}/sprint?state=active
     b. Take first active sprint id.
     c. POST /rest/agile/1.0/sprint/{sprint_id}/issue
        {"issues": [issue_key]}

  3. Call memgraph_client.update_action_jira_key(action_id, issue_key).

  4. Return list of issue keys.

Build the ADF description helper:
  _build_adf_description(meeting: ExtractedMeeting) -> dict
  Builds Atlassian Document Format JSON with:
  - Meeting title + date as heading
  - Summary paragraph
  - Attendees list
  - Decisions list (if any)

Guard: if JIRA_ENABLED != "true", log INFO and return [] immediately.
Wrap all HTTP calls with @with_retry(max_attempts=3, base_delay=2.0).
Use httpx.AsyncClient. Log each created issue key at INFO level.
```

---

## PHASE 8 — FastAPI service (main.py)

```
Read CLAUDE.md in full.

Implement transform_service/main.py — the FastAPI application.

── Lifespan (startup) ────────────────────────────────────────────────────

@asynccontextmanager async def lifespan(app):
  1. db.create_staging_tables()
  2. memgraph_client.create_indexes()
  3. Log service ready: Ollama URL (masked), Memgraph host, Jira enabled
  yield

── Middleware ────────────────────────────────────────────────────────────

Add CORSMiddleware (allow_origins=["*"])
Add request logging middleware: log method, path, status_code, duration_ms

── Endpoints ─────────────────────────────────────────────────────────────

POST /webhook/airbyte
  Body: AirbyteWebhookPayload
  1. If status != "succeeded": log and return {"status": "ignored", "reason": status}
  2. Add background tasks: process_new_emails(), process_new_events()
  3. Return {"status": "queued", "connection_id": payload.connection_id}
  Response is immediate — webhook must not time out.

GET /health
  {
    "status": "ok",
    "postgres": bool  (try a SELECT 1),
    "memgraph": bool  (try get_graph_counts()),
    "ollama": bool    (try GET {OLLAMA_BASE_URL}/api/tags),
    "counts": get_graph_counts()
  }

GET /graph/meetings/recent?limit=10
  Returns memgraph_client.get_recent_meetings(limit)

GET /graph/person/{email}
  Returns memgraph_client.get_person_meetings(email)

GET /graph/topic/{name}
  Returns memgraph_client.get_topic_meetings(name)

GET /graph/actions/open?assignee={email}
  Returns memgraph_client.get_open_action_items(assignee)

GET /graph/digest/weekly
  data = memgraph_client.get_weekly_digest_data()
  Return formatted dict:
  {
    "period": "last 7 days",
    "meetings_count": int,
    "meetings": [...],
    "decisions_made": [...],
    "action_items_created": int,
    "action_items_completed": int,
    "top_topics": [...],
    "generated_at": datetime.utcnow().isoformat()
  }
  This endpoint is the Airbyte demo showstopper — make it clean and complete.

── Background tasks ──────────────────────────────────────────────────────

async def process_new_emails():
  emails = db.get_unprocessed_emails(limit=50)
  results = []
  for email in emails:
    try:
      clf = classifier.classify(email)
      if not clf.is_meeting or clf.is_invite:
        db.mark_email_processed(email.message_id, success=True)
        results.append(ProcessResult(message_id=email.message_id, status="skipped",
          reason="not_meeting" if not clf.is_meeting else "is_invite"))
        continue
      meeting = await extractor.extract(email)
      build = await graph_builder.build_graph(meeting, email.message_id)
      if JIRA_ENABLED:
        await jira_pusher.push_action_items(meeting.action_items, meeting, email.message_id)
      db.mark_email_processed(email.message_id, success=True)
      results.append(ProcessResult(status="processed", meeting_title=meeting.title,
        nodes_created=build.nodes_created, edges_created=build.edges_created, ...))
    except LowConfidenceError as e:
      db.mark_email_processed(email.message_id, success=False, error_msg=str(e))
      results.append(ProcessResult(status="skipped", reason="low_confidence"))
    except Exception as e:
      db.mark_email_processed(email.message_id, success=False, error_msg=str(e))
      results.append(ProcessResult(status="error", reason=str(e)))
  log summary: processed/skipped/error counts

async def process_new_events():
  Same pattern for calendar events from db.get_unprocessed_events().
```

---

## PHASE 9 — Digest + backfill scripts

```
Read CLAUDE.md in full.

Implement transform_service/digest.py and scripts/backfill.py.

── digest.py ─────────────────────────────────────────────────────────────

async def generate_weekly_digest() -> str:
  Calls memgraph_client.get_weekly_digest_data() and formats it as a
  human-readable plain text digest (not JSON).

  Format:
  ╔══════════════════════════════════════╗
  ║  Meeting Memory — Weekly Digest      ║
  ║  Week of {start_date} to {end_date}  ║
  ╚══════════════════════════════════════╝

  📅 MEETINGS THIS WEEK ({count})
  • {title} — {date} ({attendee count} attendees)
  ...

  ✅ DECISIONS MADE ({count})
  • {decision text}
  ...

  📌 ACTION ITEMS CREATED ({count}) / CLOSED ({count})
  • [{priority}] {task} — due {due} (owner: {owner})
  ...

  🔥 TOP TOPICS
  1. {topic} — mentioned in {freq} meetings
  ...

  This text can be posted to Slack or emailed.

── scripts/backfill.py ───────────────────────────────────────────────────

CLI script using argparse:
  --source   EMAIL|CALENDAR|ALL  (default: ALL)
  --limit    int                 (default: no limit, process everything)
  --dry-run  flag                (classify + extract, skip graph + Jira writes)
  --since    YYYY-MM-DD          (only rows received after this date)
  --verbose  flag                (show per-email results)

Uses tqdm progress bar.
Uses extractor.extract_sync() (synchronous wrapper).
At completion: prints summary table with columns:
  Source | Total | Processed | Skipped | Errors | Duration

── scripts/setup_memgraph.py ─────────────────────────────────────────────

Connects to Memgraph, calls memgraph_client.create_indexes(), prints result.
Standalone script, no FastAPI dependency.

── scripts/test_pipeline.py ──────────────────────────────────────────────

End-to-end smoke test:
1. Insert sample_data/sample_email.json into local Postgres raw_emails.
2. classify → assert is_meeting=True
3. extract → assert confidence > 0.3, assert len(attendees) > 0
4. build_graph(dry_run=False) → assert nodes_created > 0
5. Query Memgraph: assert the Meeting node exists
6. Print PASS/FAIL with details for each assertion.
```

---

## PHASE 10 — Docker + Railway deployment

```
Read CLAUDE.md in full.

1. transform_service/Dockerfile:
   FROM python:3.11-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY . .
   EXPOSE 8000
   CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

   Add .dockerignore: __pycache__, *.pyc, .env, .venv, *.egg-info

2. railway.toml:
   [build]
   buildCommand = "pip install -r transform_service/requirements.txt"
   [deploy]
   startCommand = "cd transform_service && uvicorn main:app --host 0.0.0.0 --port $PORT"
   healthcheckPath = "/health"
   healthcheckTimeout = 30

3. Update Makefile:
   deploy-railway  — railway up
   tunnel          — prints the ngrok command to run on Mac:
                     echo "Run on your Mac:"
                     echo "OLLAMA_HOST=0.0.0.0 ollama serve"
                     echo "ngrok http 11434 --url https://YOUR_DOMAIN.ngrok-free.app"

4. Update README.md with a "Deployment" section:
   Step 1: Set up ngrok static domain (dashboard.ngrok.com → Domains)
   Step 2: Start Ollama with OLLAMA_HOST=0.0.0.0 ollama serve
   Step 3: Start ngrok tunnel: ngrok http 11434 --url https://YOUR_DOMAIN.ngrok-free.app
   Step 4: Set all env vars in Railway dashboard (especially OLLAMA_BASE_URL)
   Step 5: railway up
   Step 6: Set Airbyte webhook URL to https://YOUR_RAILWAY_URL/webhook/airbyte
   
   Include a warning: ngrok must stay running on your Mac for the pipeline to work.
   For demos: start ngrok first, then demo. The pipeline will stall if Mac sleeps.
```

---

## PHASE 11 — Airbyte setup guide + final CLAUDE.md update

```
Read CLAUDE.md in full.

1. Create airbyte/README.md — complete step-by-step Airbyte Cloud setup guide.

   For each of the 4 source connections, document exactly:
   - Connector name in Airbyte catalog
   - Authentication method + credentials needed
   - Which streams to enable
   - Sync mode: Incremental | Append+Dedup
   - Sync frequency: Every 15 minutes
   - Destination stream name prefix

   SOURCE 1: Gmail
     Connector: Gmail
     Auth: OAuth2 (Google Cloud Console → create OAuth2 credentials)
     Streams: messages, threads
     Prefix: raw_gmail_

   SOURCE 2: Google Calendar
     Connector: Google Calendar
     Auth: OAuth2 (same Google Cloud project as Gmail)
     Streams: events, calendars
     Prefix: raw_gcal_

   SOURCE 3: Slack
     Connector: Slack
     Auth: OAuth2 (create Slack App at api.slack.com, add bot scopes:
       channels:history, channels:read, users:read, files:read)
     Streams: messages, channels, users
     channel_filter: list your meeting channels only to avoid rate limits
     Prefix: raw_slack_

   SOURCE 4: Jira
     Connector: Jira
     Auth: Basic Auth (email=shubham.gaur@onixnet.com, api_token=from Atlassian)
     Streams: issues, sprints, projects
     Domain: shubhamgaur1.atlassian.net
     Prefix: raw_jira_

   DESTINATION: Postgres (Neon)
     Create a Neon project at neon.tech (free tier)
     Copy the connection string to DATABASE_URL in .env and Railway
     In Airbyte: add PostgreSQL destination, paste connection string, SSL=require
     Default schema: public

   WEBHOOK: In each connection → Settings → Notifications
     Enable "Sync succeeded" webhook
     URL: https://YOUR_RAILWAY_URL/webhook/airbyte

2. Create airbyte/connections.yaml — human-readable YAML documenting each
   connection's config for version control (not executable, just documentation).

3. Update CLAUDE.md:
   Add a "## Current status" section at the top (before Project summary):
   - List each phase with ✅ (complete) or 🔲 (pending)
   - Note any known issues found during build

4. Create docs/DEMO_GUIDE.md — step-by-step demo script for showing to Matteo
   and the Airbyte team:
   
   Scene 1: Airbyte Cloud UI
   - Show 4 source connectors, all green
   - Click into Gmail connection → show sync history + data preview
   - Point out: zero custom code, just connector config
   
   Scene 2: Neon Postgres
   - Show raw_emails table with 50+ rows
   - Show processed_flag column going from FALSE to TRUE in real time
   
   Scene 3: Memgraph Lab
   - Run: MATCH (p:Person)-[:ATTENDED]->(m:Meeting) RETURN p, m LIMIT 50
   - Show the visual graph — people connected to meetings
   - Run: MATCH (m:Meeting)-[:DISCUSSED]->(t:Topic) RETURN m, t LIMIT 30
   - Show topic clusters
   
   Scene 4: API demo (terminal or Postman)
   - GET /health → show all systems green
   - GET /graph/digest/weekly → the showstopper — full week in one call
   - GET /graph/person/shubham.gaur@onixnet.com → your personal meeting graph
   
   Talking points for each Airbyte feature used (connector catalog,
   incremental sync, Append+Dedup, schema evolution, webhook notifications,
   Postgres destination).
```
