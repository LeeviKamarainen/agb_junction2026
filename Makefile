.PHONY: dev dev-backend dev-frontend up check check-backend check-frontend api-types reset-db \
	prod-ssh prod-logs prod-reset-db prod-import-keys

# GitHub accounts whose SSH keys may log in to the VM (see prod-import-keys).
TEAM_GITHUB_USERS := ilmari99 arnotoro LeeviKamarainen PauliAnt
DEPLOY_DIR := /opt/junction2026
# Where `next dev` forwards /api; next.config.ts also needs it for typegen.
LOCAL_BACKEND_URL := http://localhost:8000
# The VM address lives only in the VM_IP GitHub variable.
VM_IP = $(or $(shell gh api 'repos/{owner}/{repo}/actions/variables/VM_IP' --jq .value),$(error Could not read the VM_IP GitHub variable, check `gh auth status`))
VM = deploy@$(VM_IP)

# --- Local development -------------------------------------------------------

dev:
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend:
	cd backend && DATABASE_PATH=data/dev.db uv run fastapi dev app/main.py --port 8000

dev-frontend: frontend/node_modules
	cd frontend && BACKEND_URL=$(LOCAL_BACKEND_URL) npm run dev

frontend/node_modules: frontend/package-lock.json
	cd frontend && npm ci
	touch frontend/node_modules

# Full production stack in Docker, served at http://localhost.
up:
	SITE_ADDRESS=http://localhost docker compose up --build

# Same checks as CI.
check: check-backend check-frontend

check-backend:
	cd backend && uv run ruff format --check && uv run ruff check && uv run pytest -q

check-frontend: frontend/node_modules
	cd frontend && npm run lint && BACKEND_URL=$(LOCAL_BACKEND_URL) npm run typecheck

# Regenerate frontend/src/lib/api/schema.d.ts from FastAPI's OpenAPI schema.
api-types: frontend/node_modules
	cd backend && uv run python -c 'import json; from app.main import app; print(json.dumps(app.openapi()))' > ../frontend/openapi.json
	cd frontend && npx openapi-typescript openapi.json -o src/lib/api/schema.d.ts && rm openapi.json

reset-db:
	rm -f backend/data/dev.db backend/data/dev.db-wal backend/data/dev.db-shm
	@echo "Local database deleted; restart the backend to rebuild and re-seed it."

# --- Production VM -----------------------------------------------------------

prod-ssh:
	ssh $(VM)

prod-logs:
	ssh $(VM) 'cd $(DEPLOY_DIR) && docker compose logs --follow --tail 200'

# Wipes production data; the backend rebuilds the database from seed.py on start.
prod-reset-db:
	ssh $(VM) 'cd $(DEPLOY_DIR) && docker compose stop backend \
		&& docker compose run --rm --no-deps --entrypoint sh backend -c "rm -f /data/app.db /data/app.db-wal /data/app.db-shm" \
		&& docker compose start backend'

prod-import-keys:
	ssh $(VM) 'ssh-import-id $(addprefix gh:,$(TEAM_GITHUB_USERS))'

# --- Memory API (the Gemma server) -------------------------------------------
# The visual memory in memory/ runs beside vLLM on the Gemma server; nothing deploys there
# automatically. memory/deploy/deploy.sh does the work, and its header describes each command.
.PHONY: check-memory memory-deploy memory-setup memory-logs memory-ssh memory-link memory-token memory-send

# Not part of `check`: it needs torch (a 6 GB environment), which CI does not install.
check-memory:
	cd memory && uv run ruff format --check && uv run ruff check \
		&& uv run marimo check notebooks/template.py notebooks/*/*.py && uv run pytest -q

# memory-token: the token API clients send as "Authorization: Bearer <token>".
memory-deploy memory-setup memory-logs memory-ssh memory-link memory-token:
	@memory/deploy/deploy.sh $(@:memory-%=%)

# Posts a folder of JPEG stills as one batch: make memory-send FRAMES=<folder> [EVERY=<seconds>],
# with the folder given from the repo root.
memory-send:
	@memory/deploy/deploy.sh send $(FRAMES) $(if $(EVERY),--every $(EVERY))
