.PHONY: install dev test test-pg lint frontend demo docs migrate serve

install:            ## install the platform (+ dev, ML extras)
	pip install -e ".[dev,ml,postgres,s3]"

test:               ## full test-suite (SQLite)
	pytest

test-pg:            ## run DB/API tests against PostgreSQL (set IRTCRI_TEST_DATABASE_URL)
	IRTCRI_TEST_DATABASE_URL=$${IRTCRI_TEST_DATABASE_URL:?set it} pytest tests/test_hub_api.py tests/test_db_and_integrations.py

lint:
	ruff check src tests

frontend:
	cd frontend && npm ci && npm run typecheck && npm run build

demo:               ## end-to-end run on SYNTHETIC data
	scripts/run_demo.sh var/demo 60

docs:               ## regenerate the data-dictionary documentation
	irtcri dictionary-docs --out docs/data_dictionary.md

migrate:
	alembic upgrade head

serve:
	irtcri serve --reload
