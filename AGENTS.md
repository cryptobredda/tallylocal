# Repository Guidelines

## Project Structure & Module Organization
Core Flask code sits in `app/`: `__init__.py` wires blueprints, `routes.py` exposes REST handlers, and service modules (`wallet_service.py`, `integration_service.py`, `asset_service.py`, OCR helpers) encapsulate business logic plus API clients. Models live in `models.py`, DB helpers in `database.py`, and the Tailwind/Chart.js SPA lives under `static/`. Treat `finance_tracker.db`, `users.db`, `uploads/`, `user_data/`, and backup JSON files as local state—inspect them while debugging but do not commit regenerated copies. Docker assets (`Dockerfile`, `docker-compose.yml`, `.dockerignore`) remain at the repo root next to `requirements.txt`.

## Build, Test, and Development Commands
Create a venv (`python -m venv venv && source venv/bin/activate`), install deps via `pip install -r requirements.txt`, then run `python run.py` to serve the API + SPA on `http://localhost:5000`. Use `python migrate_to_accounts.py` when promoting legacy installs to the multi-user schema. Container users rely on `docker compose up --build`; the compose file mounts named volumes so SQLite files and uploads persist through rebuilds.

## Coding Style & Naming Conventions
Stick to PEP 8 (4-space indents, grouped imports) and keep functions tight. Services/models stay `PascalCase`; helpers, REST handlers, and DOM IDs remain `snake_case`/kebab-case; JSON payloads must follow the existing camelCase API schema. Keep wallet logic in `wallet_service.py` and integration settings in `integration_service.py`, document new helpers with a one-line docstring, and reuse the existing formatting utilities in `static/app.js` instead of custom number formatting.

## Testing Guidelines
PyTest is the preferred harness (`pytest tests -k wallet`). Mirror the package layout (`tests/test_wallet_service.py`, `tests/test_routes.py`), seed fixtures from `finance_test_data.json`, and stub network clients (Clearbit, Coingecko, Moralis, Helius) so the suite can run offline. Until broader coverage lands, include manual QA steps—multi-account login, wallet sync, asset filters—in each PR.

## Commit & Pull Request Guidelines
Use short imperative subjects (`add wallet pagination`, `fix docker db path`) and include bodies whenever schemas, admin flows, or Docker settings move. PRs should outline scope, schema impacts, new settings/env vars, manual tests, and screenshots for UI changes (wallet tab, analytics panels, admin dashboard). Double-check that regenerated DBs/uploads stay untracked before requesting review.

## Security & Configuration Tips
Manage Moralis, Helius, and OCR keys through the admin integrations panel—never hardcode them or commit `.env` snapshots. Redact personal statements, exports, and SQLite files when sharing branches. Destructive admin actions (user deletion, full reset) must go through the service helpers so permission checks remain centralized; note these flows explicitly in PRs.
