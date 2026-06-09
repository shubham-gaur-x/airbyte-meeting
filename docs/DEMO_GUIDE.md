# Demo Guide — meeting-memory-graph

Step-by-step demo script for showing to Matteo and the Airbyte team.

---

## Scene 1: Airbyte Cloud UI

**Goal:** Show zero custom ingestion code.

1. Open [Airbyte Cloud](https://cloud.airbyte.com) → **Connections**
2. Show 4 connections: Gmail, Google Calendar, Slack, Jira — all green
3. Click into **Gmail connection** → **Sync history** tab — show completed syncs
4. Click **Data preview** — show raw email rows landing in Neon Postgres
5. **Talking point:** "No custom ingestion code. Just connector config. Airbyte handles auth, pagination, incremental sync, and schema evolution."

---

## Scene 2: Neon Postgres (staging layer)

**Goal:** Show data moving through the pipeline in real time.

1. Open [Neon Console](https://console.neon.tech) → SQL Editor
2. Run: `SELECT count(*) FROM raw_emails WHERE processed = FALSE;`
3. Trigger a manual sync in Airbyte → watch count rise
4. Watch processed flag flip: `SELECT message_id, processed, processed_at FROM raw_emails ORDER BY created_at DESC LIMIT 10;`
5. **Talking point:** "Postgres is the durable buffer. Airbyte writes here, our transform service reads, marks processed. Idempotent by design."

---

## Scene 3: Memgraph Lab (visual graph)

**Goal:** The visual wow moment.

1. Open [Memgraph Lab](https://cloud.memgraph.com) — share the shareable URL
2. Run: `MATCH (p:Person)-[:ATTENDED]->(m:Meeting) RETURN p, m LIMIT 50`
   - Show people nodes connected to meeting nodes
3. Run: `MATCH (m:Meeting)-[:DISCUSSED]->(t:Topic) RETURN m, t LIMIT 30`
   - Show topic clusters forming around meetings
4. Run: `MATCH (a:ActionItem)-[:ASSIGNED_TO]->(p:Person) RETURN a, p`
   - Show who owns what
5. **Talking point:** "Every meeting email or calendar event that flows through Airbyte becomes graph structure. Relationships, not rows."

---

## Scene 4: API Demo (terminal or Postman)

**Goal:** Show the query layer and the showstopper endpoint.

```bash
# System health — all green
curl https://YOUR_RAILWAY_URL/health | jq

# The showstopper — full week in one call
curl https://YOUR_RAILWAY_URL/graph/digest/weekly | jq

# Personal meeting graph
curl https://YOUR_RAILWAY_URL/graph/person/shubham.gaur@onixnet.com | jq

# Open action items
curl https://YOUR_RAILWAY_URL/graph/actions/open | jq
```

**Talking point for `/graph/digest/weekly`:** "One API call. Meetings, decisions, action items, top topics — all from a graph traversal. This is what raw rows can't give you."

---

## Airbyte Features to Call Out

| Feature | Where to point |
|---|---|
| Connector catalog | Scene 1 — 4 connectors, zero custom code |
| Incremental sync | Scene 1 — sync history shows only delta |
| Append+Dedup mode | Scene 2 — no duplicate rows even after re-sync |
| Schema evolution | Airbyte settings — auto-handled |
| Webhook notifications | The trigger that kicks off Scene 2→3→4 |
| Postgres destination | The staging layer in Scene 2 |
