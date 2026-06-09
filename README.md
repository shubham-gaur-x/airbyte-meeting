# meeting-memory-graph

A meeting intelligence pipeline that ingests emails, calendar events, Slack messages, and Jira issues via **Airbyte Cloud**, transforms them with an LLM, and writes a property graph to **Memgraph Cloud**. Built to showcase Airbyte's full connector + destination + webhook stack.

## Quickstart

1. **Clone** this repo
2. **Setup** the local environment: `make setup`
3. **Add credentials** to `.env` (copy from `.env.example`, fill in Neon, Memgraph, Jira, ngrok values)
4. **Start** local dev stack: `make dev`
5. **Pull the LLM model**: `make pull-model`

The service will be available at `http://localhost:8000`. Check health at `http://localhost:8000/health`.

## Deployment (Railway)

1. Get a free static ngrok domain at [dashboard.ngrok.com → Domains](https://dashboard.ngrok.com/domains)
2. On your Mac: `OLLAMA_HOST=0.0.0.0 ollama serve`
3. Start the ngrok tunnel: `ngrok http 11434 --url https://YOUR_DOMAIN.ngrok-free.app`
4. Set all environment variables in the Railway dashboard (especially `OLLAMA_BASE_URL=https://YOUR_DOMAIN.ngrok-free.app`)
5. Deploy: `railway up` (or `make deploy-railway`)
6. Set the Airbyte webhook URL to `https://YOUR_RAILWAY_URL/webhook/airbyte` in each connection's Notifications settings

> **Warning:** ngrok must stay running on your Mac for the pipeline to work. Start ngrok before the demo. The pipeline will stall if your Mac sleeps or ngrok disconnects.

## Links

- [Architecture](ARCHITECTURE.md)
- [Airbyte Setup Guide](airbyte/README.md)
