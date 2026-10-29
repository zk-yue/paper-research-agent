PYTHON ?= python
NPM ?= npm

.PHONY: help install api web lint test build release-check check

help:
	@echo "install  Install Python development and locked frontend dependencies"
	@echo "api      Start the local API with development reload (single worker)"
	@echo "web      Start the Vite development server in a second terminal"
	@echo "lint     Check Python formatting/lint and TypeScript lint"
	@echo "test     Run offline Python and frontend tests"
	@echo "build    Build frontend, wheel and source distribution"
	@echo "release-check  Check source files, build distributions and scan archives"
	@echo "check    Run lint, test and build"

install:
	$(PYTHON) -m pip install -e '.[dev]'
	$(NPM) --prefix frontend ci

api:
	$(PYTHON) -m uvicorn backend.server:app --reload --host 127.0.0.1 --port 8000

web:
	$(NPM) --prefix frontend run dev

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(NPM) --prefix frontend run lint

test:
	$(PYTHON) -m pytest
	$(NPM) --prefix frontend test

build:
	$(NPM) --prefix frontend run build
	$(PYTHON) -m build
	$(PYTHON) scripts/check_release.py --archive dist/*.whl dist/*.tar.gz

release-check:
	$(PYTHON) scripts/check_release.py
	$(PYTHON) scripts/check_release.py --history
	$(PYTHON) -m build
	$(PYTHON) scripts/check_release.py --archive dist/*.whl dist/*.tar.gz

check: lint test build
