ETL_DIR      := etl
BACKEND_DIR  := backend
FRONTEND_DIR := frontend
DATA_DIR     := data

# Local dev dataset over South America, all four satellites, continuous from 2025-01-01
# to today: science-quality SP for every month FIRMS has published, then NRT for the
# months after it. Set FIRMS_AREA to the same bbox in etl/.env so the scheduled runs
# keep that coverage (see etl/.env.example). Ranges past what FIRMS has published are
# clamped by the DAGs, so the last quarter can go beyond today's SP.
DEV_AREA      := -82,-56,-34,13
DEV_SP_RANGES := 2025-01-01:2025-03-31 2025-04-01:2025-06-30 2025-07-01:2025-09-30 2025-10-01:2025-12-31 \
                 2026-01-01:2026-03-31 2026-04-01:2026-06-30 2026-07-01:2026-09-30
# Day after the last published VIIRS SP month (MODIS NRT starts later on its own).
DEV_NRT_START := 2026-08-01

.DEFAULT_GOAL := help
.PHONY: help etl-setup etl-start etl-stop etl-restart etl-parse etl-seed-dev etl-load-local etl-reprocess backend-setup backend-dev backend-test frontend-setup frontend-dev

help: ## List available commands
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- etl (Astro / Airflow) ---------------------------------------------------

etl-setup: ## Once per machine: local venv (editor/tests) + data/ writable by Airflow
	cd $(ETL_DIR) && uv sync
	mkdir -p $(DATA_DIR)
	chmod 777 $(DATA_DIR)
	@# Airflow writes as uid 50000; the default ACL keeps your user able to read/delete it.
	@if command -v setfacl >/dev/null; then \
		setfacl -m u:$$(id -u):rwx -d -m u:$$(id -u):rwx $(DATA_DIR); \
	else echo "setfacl not found: skipping ACL (only needed on Linux)"; fi

etl-start: ## Build the image and start Airflow on http://localhost:8080
	cd $(ETL_DIR) && astro dev start

etl-stop: ## Stop the Airflow containers
	cd $(ETL_DIR) && astro dev stop

etl-restart: ## Rebuild and restart (after changing Dockerfile, requirements.txt or .env)
	cd $(ETL_DIR) && astro dev restart

etl-parse: ## Check that every DAG imports without errors
	cd $(ETL_DIR) && astro dev parse

etl-seed-dev: ## Load the dev dataset: South America, SP 2025 -> latest + NRT after it (~2 h; Airflow running)
	@# SP: one quarterly run each; they queue and run one at a time (max_active_runs=1).
	cd $(ETL_DIR) && for r in $(DEV_SP_RANGES); do \
		astro dev run dags trigger firms_ingest_sp \
			--conf "{\"area\": \"$(DEV_AREA)\", \"start_date\": \"$${r%%:*}\", \"end_date\": \"$${r##*:}\"}"; \
	done
	@# NRT: from the day after the last SP month up to yesterday.
	cd $(ETL_DIR) && astro dev run dags trigger firms_ingest_nrt \
		--conf "{\"area\": \"$(DEV_AREA)\", \"start_date\": \"$(DEV_NRT_START)\"}"

etl-load-local: ## Load FIRMS CSVs you already downloaded into the lake: make etl-load-local DIR=path/to/csvs [ARGS="--dry-run"]
	@test -n "$(DIR)" || { echo 'usage: make etl-load-local DIR=path/to/csvs [ARGS="--dry-run --product MODIS_SP ..."]'; exit 1; }
	@# umask 000: Airflow runs as another user (uid 50000) and must be able to write next to what we create.
	@# Stop Airflow's writers first (one writer at a time). Then `make etl-reprocess` builds silver and gold from it.
	cd $(ETL_DIR) && umask 000 && FIRMS_LAKE_PATH=$(abspath $(DATA_DIR))/lake uv run python -m include.firms.load_local --root "$(abspath $(DIR))" $(ARGS)

etl-reprocess: ## Rebuild silver + gold from the raw data already loaded, no download (Airflow running)
	@# Full refresh of every dbt model in dependency order: bronze stays, silver and gold are rebuilt.
	@# Parse the DAGs first (~10 s): a just-added DAG isn't known until the dag-processor's next pass.
	cd $(ETL_DIR) && astro dev run dags reserialize
	cd $(ETL_DIR) && astro dev run dags unpause firms_dbt_reprocess
	cd $(ETL_DIR) && astro dev run dags trigger firms_dbt_reprocess

# --- backend (FastAPI) -------------------------------------------------------

backend-setup: ## Once per machine: create the backend venv
	cd $(BACKEND_DIR) && uv sync

backend-dev: ## Run the API with auto-reload on http://localhost:8000 (docs at /docs)
	cd $(BACKEND_DIR) && uv run fastapi dev app/main.py

backend-test: ## Run the backend tests
	cd $(BACKEND_DIR) && uv run pytest

# --- frontend (Vue + Vite) ---------------------------------------------------

frontend-setup: ## Once per machine: install the frontend dependencies
	cd $(FRONTEND_DIR) && npm install

frontend-dev: ## Run the UI on http://localhost:5173 (needs make backend-dev running)
	cd $(FRONTEND_DIR) && npm run dev
