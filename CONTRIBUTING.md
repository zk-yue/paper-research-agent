# Contributing

Small, reviewable changes with reproducible evidence are welcome. For a substantial workflow change, describe the problem in an issue before developing a large patch. Use a synthetic research example that can be shared publicly.

## Development environment

Use Python 3.11 or later and Node.js 22.12 or later; CI also checks Python 3.13 and uses Node.js 24. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm --prefix frontend ci
```

On Windows, activate the environment with `.venv\Scripts\Activate.ps1`. Copy `.env.example` to `.env` only when no local configuration exists, then choose a model provider. Automated tests do not require provider credentials or a Codex login.

Run the API and frontend in separate terminals:

```bash
python -m uvicorn backend.server:app --reload --host 127.0.0.1 --port 8000
npm --prefix frontend run dev
```

The application uses one backend process. Do not add Uvicorn workers: active tasks, subscriptions and locks are process-local. `make help` lists optional shortcuts on systems with Make installed.

## Code boundaries

- Routes validate input and format responses; retrieval, model invocation and persistence belong in service modules.
- Use `backend.*` imports. Only legacy script entry points may adjust `sys.path`.
- Move blocking work off the asynchronous event loop. Preserve cancellation and timeout behavior when adding background jobs.
- Validate external path components with `storage.validate_identifier` or `json_path`. Persist JSON with `atomic_write_json`, and keep read-modify-write operations under the same lock.
- Send worker-thread events through `TaskManager.broadcast`, not directly to asyncio queues.
- Frontend requests use `frontend/src/api/request.ts`; retain useful server error messages instead of duplicating fetch/error logic in pages.
- Keep model-generated content distinguishable from source evidence. A missing excerpt is not evidence that a paper lacks a capability.

Python dependencies are declared in `pyproject.toml`; `requirements.txt` is a compatibility installation entry point. Commit `frontend/package-lock.json` with frontend dependency changes. Source and wheel contents are deliberately constrained; update `MANIFEST.in` when adding a distributable file type or root module.

## Checks before a pull request

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
python scripts/check_release.py
python scripts/check_release.py --history
python -m build
python scripts/check_release.py --archive dist/*.whl dist/*.tar.gz
```

`make check` runs lint, tests, frontend/Python builds and archive checks. `make release-check` checks source files and reachable Git history before building and inspecting distributions. CI runs the same privacy checks and installs the wheel outside the checkout. Python wheels contain the API and CLI; build the browser interface from the source checkout or source distribution. The wheel does not bundle local frontend build output.

Tests must use temporary data and mock external APIs/model responses. `tests/conftest.py` isolates application storage before importing it; do not bypass that isolation or call a paid model from a unit test. For data-format changes, cover old data compatibility. For background work, cover failure, cancellation and resume behavior. Optional live experiments must be explicitly invoked and documented with provider, input scope and limitations; local results stay outside version control.

## What must stay local

Never include authentication files, `.env`, access tokens, local configuration, task data, model-call logs, user templates, downloaded papers or generated reviews. Git ignore rules do not remove files from existing commits. Check both the proposed patch and its history before publishing a branch. A source-only export is distinct from sharing the existing Git repository.

Do not paste raw logs or full task exports into issues. Use sanitized error messages and a minimal synthetic example. The MIT license covers project code; it does not grant redistribution rights for imported papers or other third-party content.

## Review expectations

Explain the concrete problem, resulting behavior, verification and remaining limits. For retrieval or screening changes, include cases that demonstrate precision/coverage trade-offs; paper counts alone are not a quality metric. Keep unrelated formatting changes out of functional patches. Security reports should follow [SECURITY.md](SECURITY.md).
