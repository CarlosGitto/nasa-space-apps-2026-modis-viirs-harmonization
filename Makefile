ETL_DIR  := etl
DATA_DIR := data

.DEFAULT_GOAL := help
.PHONY: help etl-setup etl-start etl-stop etl-restart etl-parse

help: ## List available commands
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

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
