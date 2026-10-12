SHELL := /bin/bash
# PYTHONPATH=. lets scripts/*.py import `app` (a script's own directory is sys.path[0], not the repo root).
UV := PYTHONPATH=. uv run
# Fail-closed: an unreachable Postgres is an error, never a silent skip (AGENTS.md 守则 23).
export REQUIRE_DB ?= 1
.PHONY: setup ports status db-up db-down db-reset migrate api worker web dev stop test test-all lint fmt check-backend web-check check openapi e2e eval eval-data eval-full demo screenshots

setup:        ; uv python install 3.12 && uv sync --extra dev && cd dashboard-web && npm install --no-audit --no-fund
ports:        ; bash scripts/ports.sh
status:       ; bash scripts/agent_status.sh
db-up:        ; bash scripts/db.sh up && $(UV) python scripts/wait_for_db.py && $(UV) python scripts/db_ensure_databases.py
db-down:      ; bash scripts/db.sh down
db-reset:     ; bash scripts/db.sh reset && $(UV) python scripts/wait_for_db.py && $(UV) python scripts/db_ensure_databases.py && $(MAKE) migrate
migrate:      ; $(UV) alembic upgrade head
api:          ; $(UV) python -m app.serve --reload
worker:       ; $(UV) python -m app.jobs.worker
web:          ; bash scripts/run_web.sh dev
dev:          ; bash scripts/ports.sh && bash scripts/dev_up.sh
stop:         ; bash scripts/dev_down.sh
test:         ; $(UV) pytest -m "not network and not model and not slow"
test-all:     ; $(UV) pytest
lint:         ; $(UV) ruff check app tests scripts && $(UV) ruff format --check app tests scripts
fmt:          ; $(UV) ruff check --fix app tests scripts && $(UV) ruff format app tests scripts
check-backend: lint test
web-check:    ; $(MAKE) openapi && git diff --exit-code dashboard-web/lib/api-types.ts && cd dashboard-web && npm run lint && rm -rf .next/types .next/dev/types && npx next typegen && npx tsc --noEmit && test "$$(grep -cF 'components["schemas"]' lib/types.ts)" -gt 0 && npm run build
check:        check-backend web-check
openapi:      ; $(UV) python scripts/export_openapi.py dashboard-web/openapi.json && cd dashboard-web && npx openapi-typescript openapi.json -o lib/api-types.ts
e2e:          ; bash scripts/e2e.sh
eval:         ; $(UV) python -m app.evals run --suite all --mode offline
eval-data:    ; $(UV) python -m app.evals download
eval-full:    ; $(UV) python -m app.evals run --suite all --mode full
demo:         ; $(UV) python -m app.demo.seed --email demo@sourcehero.local --password demo-password-123
screenshots:  ; bash scripts/screenshots.sh