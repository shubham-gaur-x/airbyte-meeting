.PHONY: setup dev run backfill test graph-setup google-auth airbyte-setup logs stop deploy-render

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r transform_service/requirements.txt
	cp -n .env.example .env || true
	@echo ""
	@echo "Next steps:"
	@echo "  1. Fill in .env with your credentials"
	@echo "  2. make google-auth   (get Google refresh token)"
	@echo "  3. make slack-auth    (get Slack refresh token)"
	@echo "  4. make graph-setup   (create Memgraph indexes)"
	@echo "  5. make airbyte-setup (configure Airbyte Cloud)"
	@echo "  6. make deploy-render (deploy to Render.com)"

dev:
	docker compose up

run:
	cd transform_service && uvicorn main:app --reload --port 8000

backfill:
	.venv/bin/python scripts/backfill.py

test:
	.venv/bin/python scripts/test_pipeline.py

graph-setup:
	.venv/bin/python scripts/setup_memgraph.py

google-auth:
	.venv/bin/python scripts/get_google_token.py

airbyte-setup:
	.venv/bin/python scripts/setup_airbyte.py

logs:
	docker compose logs -f transform_service

stop:
	docker compose down

deploy-render:
	@echo "Push to GitHub → Render auto-deploys from main branch."
	@echo "Make sure render.yaml is present and env vars are set in Render dashboard."
