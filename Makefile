.PHONY: setup setup-all dev run backfill test graph-setup google-auth \
        airbyte-setup render-setup verify logs stop

# ── One-time local setup ───────────────────────────────────────────────────────
setup:
	python3 -m venv .venv
	.venv/bin/pip install -r transform_service/requirements.txt
	cp -n .env.example .env || true
	@echo ""
	@echo "Next steps:"
	@echo "  1. Fill in .env with your credentials"
	@echo "  2. make google-auth   — get Google refresh token (one-time)"
	@echo "  3. make setup-all     — deploy to Render + configure Airbyte"
	@echo "  4. make verify        — confirm everything is green"

# ── Full automated setup ───────────────────────────────────────────────────────
setup-all: render-setup airbyte-setup
	@echo ""
	@echo "Setup complete. Run 'make verify' to confirm everything is healthy."

render-setup:
	@echo "==> Pushing env vars to Render + triggering deploy..."
	.venv/bin/python scripts/configure_render.py

airbyte-setup:
	@echo "==> Configuring Airbyte connections + triggering syncs..."
	.venv/bin/python scripts/setup_airbyte.py

# ── Verify ────────────────────────────────────────────────────────────────────
verify:
	.venv/bin/python scripts/verify.py

# ── Local dev ─────────────────────────────────────────────────────────────────
dev:
	docker compose up

run:
	cd transform_service && uvicorn main:app --reload --port 8000

# ── Maintenance ───────────────────────────────────────────────────────────────
google-auth:
	.venv/bin/python scripts/get_google_token.py

graph-setup:
	.venv/bin/python scripts/setup_memgraph.py

backfill:
	.venv/bin/python scripts/backfill.py

test:
	.venv/bin/python scripts/test_pipeline.py

logs:
	docker compose logs -f transform_service

stop:
	docker compose down
