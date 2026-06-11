# Airbyte Cloud Setup Guide — meeting-memory-graph

Step-by-step guide to configure the 2 source connectors (Gmail + Google Calendar),
the Neon Postgres destination, and the sync-complete webhook.

---

## Destination: Neon Postgres

1. In Airbyte Cloud → **Destinations** → **New destination** → search **PostgreSQL**
2. Paste your `DATABASE_URL` fields (host, port, database, user, password)
3. Set **SSL mode = require**. Default schema: `public`
4. Save as `meeting-memory-neon`

---

## Source 1: Gmail

| Setting | Value |
|---|---|
| Connector | Gmail |
| Auth | OAuth2 — use the Google Client ID/Secret from `.env` |
| Streams | `messages` |
| Sync mode | Incremental \| Append+Dedup |
| Sync frequency | Every 1 hour (free tier minimum) |

**After saving the connection:**
Connection → Settings → Notifications → Sync succeeded → URL:
```
https://meeting-memory-graph.onrender.com/webhook/airbyte
```

---

## Source 2: Google Calendar

| Setting | Value |
|---|---|
| Connector | Google Calendar |
| Auth | OAuth2 — reuse the same Google credentials |
| Streams | `events` |
| Sync mode | Incremental \| Append+Dedup |
| Sync frequency | Every 1 hour (free tier minimum) |

**After saving:** same webhook URL as Gmail.

---

## Airbyte Features Showcased for the Demo

- **Connector catalog** — official connectors, zero custom ingestion code
- **Incremental sync** — only new/changed records each run
- **Append+Dedup mode** — safe re-runs, no duplicate rows
- **Schema evolution** — Airbyte handles source schema changes automatically
- **Webhook notifications** — triggers the transform pipeline immediately on sync complete
- **Postgres destination** — serverless Neon backend visible in Airbyte UI
