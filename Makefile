.PHONY: setup dev run backfill test graph-setup pull-model logs stop deploy-railway tunnel

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r transform_service/requirements.txt
	cp -n .env.example .env || true

dev:
	docker compose up

run:
	cd transform_service && uvicorn main:app --reload --port 8000

backfill:
	python scripts/backfill.py

test:
	python scripts/test_pipeline.py

graph-setup:
	python scripts/setup_memgraph.py

pull-model:
	docker compose exec ollama ollama pull qwen2.5:14b

logs:
	docker compose logs -f transform_service

stop:
	docker compose down

deploy-railway:
	railway up

tunnel:
	@echo "Run these commands on your Mac:"
	@echo ""
	@echo "  OLLAMA_HOST=0.0.0.0 ollama serve"
	@echo "  ngrok http 11434 --url https://YOUR_DOMAIN.ngrok-free.app"
	@echo ""
	@echo "Then set OLLAMA_BASE_URL=https://YOUR_DOMAIN.ngrok-free.app in Railway."
