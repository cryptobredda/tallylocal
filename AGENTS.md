# Repository Guidelines

## Project Structure & Module Organization
Code that powers Tally Local lives under `app/`: service modules handle OCR (`ocr_service.py`), categorization, analytics, and asset pricing, while `routes.py` wires Flask blueprints and `database.py` exposes the SQLAlchemy session/engine. Static UI artifacts (Tailwind + Chart.js bundle) sit in `static/` and are served directly from Flask; leave marketing captures in `screenshots/`. User-provided bank statements land in `uploads/` and the working SQLite file (`finance_tracker.db`) and JSON backups remain at the repo root—treat them as local fixtures and do not commit regenerated copies.

## Build, Test, and Development Commands
Create a fresh environment before hacking:
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```
Run the server with `python run.py` (serves on `http://localhost:5000`). Populate or migrate legacy data via `python migrate_to_accounts.py`.

## Coding Style & Naming Conventions
Follow PEP 8 with 4-space indentation and keep imports grouped stdlib/third-party/local as in `routes.py`. Service classes and SQLAlchemy models are `PascalCase`, helper functions stay `snake_case`, and API routes prefer descriptive verbs (`/upload`, `/reset`). Add type hints for public functions, concise docstrings for new services, and keep JSON payload keys consistent with existing camelCase usage on the frontend. No formatter is enforced, but `black` (line length 88) is recommended.

## Testing Guidelines
There is no automated suite yet, so prioritize PyTest modules placed under a future `tests/` folder. Name files `test_<module>.py`, seed fixtures with `finance_test_data.json`, and mock OCR/price calls so tests stay offline. Until coverage gates exist, run targeted smoke tests with `pytest tests -k upload` plus manual browser checks for affected pages; document any required DB migrations in the PR description.

## Commit & Pull Request Guidelines
Git history favors short imperative subjects (e.g., `license update`, `ready for public`); follow that style and include a detailed body when schema or UI changes occur. PRs should describe motivation, outline schema impacts, list manual test steps, and attach updated screenshots when the UI changes. Reference related issues, call out any new environment variables, and confirm that generated assets, uploads, and private databases are excluded from the diff.

## Data Handling & Security Tips
Never upload real statements or personal SQLite files to shared branches—use redacted fixtures in `uploads/` for reproductions and scrub logs before sharing. If you add new integrations (APIs, OCR models), gate secrets through environment variables loaded by `python-dotenv` and document defaults inside `.env.example` rather than hardcoding them.
