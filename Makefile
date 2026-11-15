ETL_DIR      := etl
BACKEND_DIR  := backend
FRONTEND_DIR := frontend
DATA_DIR     := data

# Local dev dataset over South America, all four satellites, continuous from 2025-01-01
# to today: science-quality SP for every month FIRMS has published, then NRT for the
# months after it, and the weather around those fires. Set FIRMS_AREA to the same bbox in
# etl/.env so the scheduled runs keep that coverage (see etl/.env.example).
#
# Each seed is one run: the DAG plans everything from DEV_START to what NASA has
# published and loads it in monthly chunks (dynamic task mapping).
#
# The whole world instead: FIRMS_AREA=world in etl/.env, `make etl-restart`, then
#   make etl-seed-dev DEV_AREA=world && make etl-seed-weather-dev DEV_AREA=world
# (hours of fire downloads; weather is NASA POWER daily near the fires, see data/README.md).
#
# KEEP FIRE AND WEATHER IN SYNC. If you change the region or the dates, change both:
# DEV_AREA / DEV_START (used by both seeds), FIRMS_AREA in etl/.env (the weather DAG
# follows it unless WEATHER_AREA is set), and SP_DEFAULT_START / WEATHER_DEFAULT_START in
# the DAGs. See data/README.md, "Changing the region or the dates".
DEV_AREA      := -82,-56,-34,13
DEV_START     := 2025-01-01
# Day after the last published VIIRS SP month (MODIS NRT starts later on its own).
DEV_NRT_START := 2026-08-01

.DEFAULT_GOAL := help
.PHONY: help etl-setup etl-start etl-stop etl-restart etl-parse etl-seed-dev etl-seed-weather-dev etl-reprocess backend-setup backend-dev backend-test frontend-setup frontend-dev frontend-lint

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

etl-seed-dev: ## Load the dev dataset (fire): SP from DEV_START to the latest + NRT after it (Airflow running)
	@# SP: one run; it plans every month since DEV_START and loads them one chunk at a time.
	cd $(ETL_DIR) && astro dev run dags trigger firms_ingest_sp \
		--conf "{\"area\": \"$(DEV_AREA)\", \"start_date\": \"$(DEV_START)\"}"
	@# NRT: from the day after the last SP month up to yesterday.
	cd $(ETL_DIR) && astro dev run dags trigger firms_ingest_nrt \
		--conf "{\"area\": \"$(DEV_AREA)\", \"start_date\": \"$(DEV_NRT_START)\"}"

etl-seed-weather-dev: ## Load the weather around the dev dataset's fires (NASA POWER daily), same area and dates
	@# One run; it plans the days since DEV_START whose fires (same area) are already in gold.
	@# Weather waits for FIRMS: as more fires land, the daily schedule (or a trigger without
	@# conf) resumes where this stopped. Don't re-run this target: it starts over from DEV_START.
	cd $(ETL_DIR) && astro dev run dags trigger weather_ingest \
		--conf "{\"area\": \"$(DEV_AREA)\", \"start_date\": \"$(DEV_START)\"}"

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

frontend-lint: ## Check the UI: untranslated text, missing translations, accessibility
	cd $(FRONTEND_DIR) && npm run lint
