# Contributing to LegacyLens

## Set up

You need Python 3.12, Node.js 20 or newer, and Docker with Compose.

```bash
make env venv frontend-install   # Create .env and install dependencies
make db                          # PostgreSQL on localhost:5433
make sandbox-images              # Test runner images on your local Docker engine
make backend                     # API on http://localhost:8000
make frontend                    # Web app on http://localhost:3000
```

## Run the checks

```bash
make lint            # Backend lint and format check
make test            # Backend tests, type check, frontend lint and unit tests
make up && make e2e  # Browser tests against the full Docker stack
```

Backend tests use a real PostgreSQL database with the real migrations, and the sandbox tests start
real containers. They are skipped if Docker or the images are missing. Run
`npx playwright install chromium` in `frontend/` once before the browser tests.

## Ground rules

- **No paid AI.** Do not add a paid provider, an SDK for one, or a paid fallback, even as an option. `backend/tests/test_llm.py` enforces this.
- **Keys stay in environment variables.** They must never reach the database, an API response, a log or a prompt.
- **Repositories are untrusted.** Treat file contents, file names, commit messages and AI output as hostile input.
- **Report only what is true.** Results must come from the code. If something cannot be determined, say so instead of guessing. For example, the graph leaves out a call it cannot resolve.
- **Bugs get a regression test.** It should fail before the fix.

## Common changes

| Change | Where |
| --- | --- |
| Route, model or symbol extraction | `backend/app/services/analysis/languages/` and `frameworks.py` |
| A new quality rule | `backend/app/services/analysis/quality.py` |
| A new risk in commit comparison | `backend/app/services/compare.py` |
| A new page | `frontend/src/app/` |
| A database change | Edit `backend/app/db/models.py`, then run the command below |

```bash
cd backend && .venv/bin/alembic revision --autogenerate -m "describe the change"
```

Read the generated migration before committing it. Migrations run automatically when the backend starts.

## Pull requests

Keep each pull request to one purpose, and explain why the change is needed. Fill in the checklist in the template.
