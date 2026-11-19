.PHONY: help env up down logs db venv migrate backend frontend-install frontend sandbox-images test test-backend test-frontend lint e2e

COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.dev.yml
PY = backend/.venv/bin/python

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | expand -t 18

env: ## Create .env with random secrets. Never overwrites an existing file.
	@if [ -f .env ]; then echo ".env already exists."; else \
	  secret=$$(openssl rand -base64 48 | tr -d '/+=\n'); db=$$(openssl rand -base64 24 | tr -d '/+=\n'); \
	  sed -e "s|^SECRET_KEY=.*|SECRET_KEY=$$secret|" \
	      -e "s|change-me-db-password|$$db|g" .env.example > .env; chmod 600 .env; echo "Created .env"; fi

up: env ## Build and start everything with Docker
	docker compose up --build -d
	@echo "LegacyLens: http://localhost:$${FRONTEND_PORT:-3000}"

down: ## Stop everything. Data stays in Docker volumes.
	docker compose down

logs: ## Follow logs
	docker compose logs -f backend frontend

db: env ## Start only PostgreSQL, published on localhost:5433
	$(COMPOSE_DEV) up -d --wait postgres

venv: ## Create the Python environment (CPU-only torch)
	python3 -m venv backend/.venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install torch --index-url https://download.pytorch.org/whl/cpu
	$(PY) -m pip install -r backend/requirements-dev.txt

migrate: ## Apply database migrations
	cd backend && .venv/bin/alembic upgrade head

backend: migrate ## Run the API with reload on port 8000
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend-install:
	cd frontend && npm ci

frontend: ## Run the web app with reload on port 3000
	cd frontend && npm run dev

sandbox-images: ## Build test runner images on the local Docker engine
	sh sandbox/build-images.sh

test: test-backend test-frontend ## Run all tests

test-backend: ## Backend tests. Docker tests are skipped when the images are missing.
	cd backend && .venv/bin/python -m pytest

test-frontend: ## Type check, lint and unit tests
	cd frontend && npm run typecheck && npm run lint && npm test

lint: ## Lint the backend and check its formatting
	cd backend && .venv/bin/ruff check app tests alembic/env.py && .venv/bin/ruff format --check app tests alembic/env.py

e2e: ## Browser tests against a running stack
	cd frontend && npm run e2e
