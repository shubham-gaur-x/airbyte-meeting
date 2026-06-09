# Airbyte Cloud Setup Guide — meeting-memory-graph

Step-by-step guide to configure the 4 source connectors, Neon Postgres destination, and webhooks.

---

## Destination: Neon Postgres

1. Create a free project at [neon.tech](https://neon.tech)
2. Copy the connection string → set as `DATABASE_URL` in `.env` and Railway dashboard
3. In Airbyte Cloud → **Destinations** → **New destination** → search **PostgreSQL**
4. Paste the connection string. Set **SSL mode = require**. Default schema: `public`
5. Save as `meeting-memory-staging`

---

## Source 1: Gmail

| Setting | Value |
|---|---|
| Connector | Gmail |
| Auth | OAuth2 — create credentials at [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials |
| Streams | `messages`, `threads` |
| Sync mode | Incremental \| Append+Dedup |
| Sync frequency | Every 15 minutes |
| Stream prefix | `raw_gmail_` |

**Webhook:** Connection Settings → Notifications → enable **Sync succeeded** → URL: `https://YOUR_RAILWAY_URL/webhook/airbyte`

---

## Source 2: Google Calendar

| Setting | Value |
|---|---|
| Connector | Google Calendar |
| Auth | OAuth2 — reuse the same Google Cloud project as Gmail |
| Streams | `events`, `calendars` |
| Sync mode | Incremental \| Append+Dedup |
| Sync frequency | Every 15 minutes |
| Stream prefix | `raw_gcal_` |

**Webhook:** Same as Gmail.

---

## Source 3: Slack

| Setting | Value |
|---|---|
| Connector | Slack |
| Auth | OAuth2 — create a Slack App at [api.slack.com](https://api.slack.com/apps) |
| Bot token scopes | `channels:history`, `channels:read`, `users:read`, `files:read` |
| Streams | `messages`, `channels`, `users` |
| `channel_filter` | List only your meeting channels to avoid rate limits |
| Sync mode | Incremental \| Append+Dedup |
| Sync frequency | Every 15 minutes |
| Stream prefix | `raw_slack_` |

**Webhook:** Same as Gmail.

---

## Source 4: Jira

| Setting | Value |
|---|---|
| Connector | Jira |
| Auth | Basic Auth — email: `shubham.gaur@onixnet.com`, API token from [Atlassian API tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |
| Domain | `shubhamgaur1.atlassian.net` |
| Streams | `issues`, `sprints`, `projects` |
| Sync mode | Incremental \| Append+Dedup |
| Sync frequency | Every 15 minutes |
| Stream prefix | `raw_jira_` |

**Webhook:** Same as Gmail.

---

## Airbyte Features Showcased

- **Connector catalog** — 4 official connectors, zero custom ingestion code
- **Incremental sync** — only new/changed records synced each run
- **Append+Dedup mode** — safe re-runs, no duplicate rows
- **Schema evolution** — Airbyte handles source schema changes automatically
- **Webhook notifications** — triggers the transform pipeline immediately on sync complete
- **Postgres destination** — well-supported, serverless Neon backend
