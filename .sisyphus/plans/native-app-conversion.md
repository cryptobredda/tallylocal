# Electron Desktop Packaging for Tally Local

## TL;DR
> **Summary**: Package the existing Flask + static SPA app into an Electron desktop shell by bundling a PyInstaller-built backend sidecar, localizing CDN assets for offline use, and wiring data storage to OS app data paths.
> **Deliverables**: Electron project scaffold, backend packaging scripts/spec, localized static assets, OCR native deps bundling plan, electron-builder config, and desktop build/run docs.
> **Effort**: Large
> **Parallel**: YES - 2 waves
> **Critical Path**: Backend runtime updates → PyInstaller bundle → Electron main process integration → Electron builder packaging

## Context
### Original Request
review the current project and evaluate the best option and if its applicable to convert this project into a native app using tauri or electron so it can be multi-platfrom supported. currently we got docker setup but not sure how ww can make that into an app.

### Interview Summary
- Priority selected: fastest packaging → Electron preferred over Tauri.
- Assumptions applied: target Windows/macOS/Linux; installer outputs (NSIS/DMG/AppImage); no auto-updater; fully offline UI (no CDN at runtime).
- Architecture: Electron shell spawns a bundled Flask backend sidecar; data stored under `app.getPath("userData")`.

### Metis Review (gaps addressed)
- Enforce backend lifecycle (spawn → healthcheck → shutdown) and dynamic port selection.
- Vendor all CDN assets to keep offline operation.
- Bundle OCR native deps per OS and surface clear errors if missing.
- Keep data storage scoped to `app.getPath("userData")` and avoid API/schema changes.

## Work Objectives
### Core Objective
Deliver a decision-complete path to run the existing Flask + static SPA as a multi-platform Electron desktop app with a local backend sidecar and offline assets.

### Deliverables
- `desktop/` Electron project with main/preload and build scripts.
- PyInstaller spec + build scripts to package the Flask backend.
- Bundled OCR dependencies (tesseract + poppler) per OS layout.
- Local vendor copies of Tailwind/DaisyUI/Chart.js/Font Awesome assets.
- electron-builder configuration for NSIS/DMG/AppImage outputs.
- Desktop build/run documentation in `README.md`.

### Definition of Done (verifiable conditions with commands)
- `python packaging/build_backend.py --platform <platform>` produces a backend bundle under `desktop/resources/backend/`.
- `npm install` in `desktop/` completes without errors.
- `npm run electron:dev` launches Electron, spawns backend, and loads `http://127.0.0.1:<port>`.
- `npm run electron:dist -- --dir` produces packaged artifacts in `desktop/dist/` for the host OS.
- `curl http://127.0.0.1:<port>/api/status` returns `{"status":"running"...}` while the desktop app is running.

### Must Have
- Electron main process manages backend lifecycle and readiness checks.
- Backend supports env-driven host/port and bundled static asset paths.
- Offline-ready UI (no CDN references at runtime).
- Data stored under OS app data directory via existing env vars.
- Electron builder config includes backend + OCR resources and unpacks binaries.

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- No API schema or business logic changes unrelated to packaging.
- No storing user data outside `app.getPath("userData")`.
- No auto-updater integration or cloud services.
- No Docker runtime dependency for desktop builds.

## Verification Strategy
> ZERO HUMAN INTERVENTION — all verification is agent-executed.
- Test decision: none (no existing test infra); rely on command checks + manual QA automation.
- QA policy: Every task includes explicit, tool-driven scenarios.
- Evidence: .sisyphus/evidence/task-{N}-{slug}.{ext}

## Execution Strategy
### Parallel Execution Waves
Wave 1: Electron scaffold, backend runtime updates, asset vendoring, backend packaging scripts.
Wave 2: Electron lifecycle integration, OCR deps bundling, electron-builder config, docs update.

### Dependency Matrix (full, all tasks)
T1 → T2, T7
T3 → T2, T5
T4 → T8
T5 → T2, T7
T6 → T7
T2 → T7
T7 → T8

### Agent Dispatch Summary (wave → task count → categories)
Wave 1 → 4 tasks → unspecified-high, deep
Wave 2 → 4 tasks → unspecified-high, deep

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [ ] 1. Create Electron project scaffold and base config

  **What to do**: Create `desktop/` with `package.json`, `src/`, and `assets/`; add `electron`, `electron-builder`, `get-port`, and `cross-env` dependencies; add scripts (`electron:dev`, `electron:dist`, `electron:dist:dir`); add a minimal `src/main.js` that opens a blank window and a stub `src/preload.js`; add `desktop/.gitignore` for `node_modules`, `dist`, and build artifacts.
  **Must NOT do**: Do not implement backend lifecycle logic here (reserved for Task 2).

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: new Node/Electron project structure and scripts.
  - Skills: []
  - Omitted: [`playwright`] — no UI automation needed yet.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: T2, T7 | Blocked By: none

  **References**:
  - Asset: `tallylocal-icon.png` — use as base icon source.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `desktop/package.json` defines `electron:dev`, `electron:dist`, `electron:dist:dir` scripts.
  - [ ] `npm install` in `desktop/` completes without errors.

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Scaffold installs cleanly
    Tool: Bash
    Steps: npm install (workdir=desktop)
    Expected: command exits 0 without missing module errors
    Evidence: .sisyphus/evidence/task-1-electron-scaffold.txt

  Scenario: Missing install fails
    Tool: Bash
    Steps: rmdir /s /q desktop\node_modules ; npm run electron:dev (workdir=desktop)
    Expected: command fails with "Cannot find module" or missing dependency error
    Evidence: .sisyphus/evidence/task-1-electron-scaffold-error.txt
  ```

  **Commit**: NO | Message: `build(electron): scaffold desktop shell` | Files: [desktop/package.json, desktop/src/main.js, desktop/src/preload.js, desktop/.gitignore]

- [ ] 2. Implement Electron main process with backend lifecycle

  **What to do**: Replace `desktop/src/main.js` with a full main process that (1) acquires single-instance lock, (2) computes `dataRoot = app.getPath("userData")/tally-local`, (3) selects a dynamic port using `get-port`, (4) spawns the backend (dev: `python run.py`; prod: bundled `resources/backend/tally-local-backend[.exe]`), (5) injects env vars (`PORT`, `HOST=127.0.0.1`, `DATA_DIR`, `USERS_DB_PATH`, `USER_DATA_DIR`, `UPLOADS_DIR`, `PADDLEOCR_HOME`) and extends PATH/LD_LIBRARY_PATH to include `resources/ocr/bin` and `resources/ocr/lib`, (6) polls `http://127.0.0.1:<port>/api/status` until ready, then loads the URL in `BrowserWindow`, and (7) shuts down the backend on app quit. Add log file output under `${dataRoot}/logs/backend.log` and a fatal error dialog with log path if the backend fails to start. Ensure `BrowserWindow` uses secure defaults (`contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`).
  **What to log**: Emit a line exactly like `Backend ready at http://127.0.0.1:<port>` after the healthcheck passes.
  **Must NOT do**: Do not expose Node APIs to the renderer; no devtools enabled by default in production.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: Electron lifecycle + process management logic.
  - Skills: []
  - Omitted: [`playwright`] — UI automation not required for backend lifecycle.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: T7 | Blocked By: T1, T3, T5

  **References**:
  - API: `app/routes.py` — use `/api/status` for readiness checks.
  - Env usage: `app/database.py`, `app/auth_service.py` — DATA_DIR/USERS_DB_PATH/USER_DATA_DIR patterns.
  - Electron: https://www.electronjs.org/docs/latest/api/app#appgetpathname
  - Electron: https://www.electronjs.org/docs/latest/api/browser-window
  - Electron security: https://www.electronjs.org/docs/latest/tutorial/security
  - Node child process: https://nodejs.org/api/child_process.html#child_processspawncommand-args-options
  - get-port: https://www.npmjs.com/package/get-port

  **Acceptance Criteria** (agent-executable only):
  - [ ] `npm run electron:dev` logs a "Backend ready" line containing the chosen localhost URL.
  - [ ] While Electron is running, `curl http://127.0.0.1:<port>/api/status` returns JSON with `"status":"running"`.

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Backend starts and UI loads
    Tool: Bash
    Steps: npm run electron:dev (workdir=desktop) ; read console for "Backend ready" URL ; curl http://127.0.0.1:<port>/api/status
    Expected: curl returns JSON with "status":"running" and process stays alive
    Evidence: .sisyphus/evidence/task-2-electron-backend.txt

  Scenario: Backend missing surfaces error
    Tool: Bash
    Steps: BACKEND_DEV_CMD=missing-python npm run electron:dev (workdir=desktop)
    Expected: Electron exits with non-zero status and logs a backend spawn error
    Evidence: .sisyphus/evidence/task-2-electron-backend-error.txt
  ```

  **Commit**: NO | Message: `feat(electron): manage backend lifecycle` | Files: [desktop/src/main.js, desktop/src/preload.js]

- [ ] 3. Update Flask runtime for env-driven host/port and bundled static path

  **What to do**: In `run.py`, read `HOST` and `PORT` env vars with validation (fallback to `0.0.0.0:5000` and log when invalid). In `app/__init__.py`, resolve `static_folder` using `sys._MEIPASS` when bundled (PyInstaller) so static assets load from the packaged directory. Keep current DATA_DIR/UPLOADS_DIR logic intact.
  **Must NOT do**: Do not change API routes or database schema.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: backend runtime changes with packaging implications.
  - Skills: []
  - Omitted: [`playwright`] — not needed for backend env parsing.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: T2, T5 | Blocked By: none

  **References**:
  - Entry point: `run.py`
  - Static config: `app/__init__.py`
  - Data root: `app/database.py`, `app/auth_service.py`

  **Acceptance Criteria** (agent-executable only):
  - [ ] `HOST=127.0.0.1 PORT=5055 python run.py` starts the server on port 5055.
  - [ ] `curl http://127.0.0.1:5055/api/status` returns JSON with `"status":"running"`.

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Env-driven port works
    Tool: Bash
    Steps: HOST=127.0.0.1 PORT=5055 python run.py ; curl http://127.0.0.1:5055/api/status
    Expected: curl returns JSON with "status":"running"
    Evidence: .sisyphus/evidence/task-3-backend-env.txt

  Scenario: Invalid PORT falls back
    Tool: Bash
    Steps: PORT=abc python run.py
    Expected: server logs fallback to 5000 and continues to start
    Evidence: .sisyphus/evidence/task-3-backend-env-error.txt
  ```

  **Commit**: NO | Message: `feat(server): support env host/port and bundle paths` | Files: [run.py, app/__init__.py]

- [ ] 4. Vendor CDN assets for offline desktop use

  **What to do**: Download current CDN assets into `static/vendor/` (Tailwind CDN script, DaisyUI CSS, Chart.js JS, Font Awesome CSS + webfonts), add `static/vendor/README.md` listing source URLs and versions, and update `static/index.html` to load local vendor assets instead of external URLs.
  **Must NOT do**: Do not change HTML structure or UI logic beyond asset references.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: static asset changes and HTML updates.
  - Skills: []
  - Omitted: [`playwright`] — manual UI automation not required.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: T8 | Blocked By: none

  **References**:
  - HTML entry: `static/index.html`
  - Current CDN URLs: `static/index.html` lines 9-12
  - Font Awesome CDN: https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/

  **Acceptance Criteria** (agent-executable only):
  - [ ] `static/index.html` contains no `https://cdn.` references for Tailwind/DaisyUI/Chart.js/Font Awesome.
  - [ ] `curl http://127.0.0.1:5000/vendor/chart.min.js` returns 200 when backend is running.

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Local vendor assets load
    Tool: Bash
    Steps: python run.py ; curl -I http://127.0.0.1:5000/vendor/daisyui.min.css
    Expected: HTTP 200 response
    Evidence: .sisyphus/evidence/task-4-vendor-assets.txt

  Scenario: External CDN references removed
    Tool: Bash
    Steps: python -c "import re, pathlib; text=pathlib.Path('static/index.html').read_text(); print(bool(re.search('https://cdn|cdnjs', text)))"
    Expected: output is "False"
    Evidence: .sisyphus/evidence/task-4-vendor-assets-error.txt
  ```

  **Commit**: NO | Message: `chore(ui): vendor CDN assets` | Files: [static/index.html, static/vendor/*]

- [ ] 5. Add PyInstaller backend packaging scripts and OCR model prefetch

  **What to do**: Create `packaging/pyinstaller.spec` to build a `tally-local-backend` executable from `run.py` with bundled `app/` and `static/` data; add `packaging/build_backend.py` to (a) prefetch PaddleOCR models into `packaging/ocr_models/` and copy them to `desktop/resources/backend/ocr_models`, (b) invoke PyInstaller, and (c) copy the built executable into `desktop/resources/backend/` (use `.exe` on Windows). Ensure the spec includes `static/` and any required data files.
  **Must NOT do**: Do not bundle user data (`finance_tracker.db`, `users.db`, `uploads/`) into the build output.

  **Recommended Agent Profile**:
  - Category: `deep` — Reason: Python packaging + binary bundling across platforms.
  - Skills: []
  - Omitted: [`playwright`] — not needed for build scripting.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: T2, T7 | Blocked By: T3

  **References**:
  - Entry point: `run.py`
  - Static assets: `static/index.html`
  - OCR availability: `app/ocr_service.py`
  - PyInstaller spec docs: https://pyinstaller.org/en/stable/spec-files.html

  **Acceptance Criteria** (agent-executable only):
  - [ ] `python packaging/build_backend.py --platform <platform>` produces `desktop/resources/backend/tally-local-backend(.exe)`.
  - [ ] `desktop/resources/backend/ocr_models/` exists after build.

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Packaged backend starts
    Tool: Bash
    Steps: python packaging/build_backend.py --platform <platform> ; set DATA_DIR=%TEMP%\tally-test ; set PORT=5056 ; start /b desktop\resources\backend\tally-local-backend(.exe) ; timeout /t 3 >nul ; curl http://127.0.0.1:5056/api/status
    Expected: curl returns JSON with "status":"running" and "ocr_available": true
    Evidence: .sisyphus/evidence/task-5-backend-build.txt

  Scenario: Missing models disables OCR
    Tool: Bash
    Steps: set PADDLEOCR_HOME=missing ; set PORT=5057 ; start /b desktop\resources\backend\tally-local-backend(.exe) ; timeout /t 3 >nul ; curl http://127.0.0.1:5057/api/status
    Expected: "ocr_available" is false and backend stays running
    Evidence: .sisyphus/evidence/task-5-backend-build-error.txt
  ```

  **Commit**: NO | Message: `build(pyinstaller): package backend sidecar` | Files: [packaging/pyinstaller.spec, packaging/build_backend.py]

- [ ] 6. Bundle OCR native dependencies per platform

  **What to do**: Add platform-specific scripts in `packaging/ocr/` to collect OCR binaries and libs into `desktop/resources/ocr/`:
  - Windows: `packaging/ocr/prepare-ocr-windows.ps1` downloads Tesseract from https://github.com/UB-Mannheim/tesseract/wiki and Poppler from https://github.com/oschwartz10612/poppler-windows/releases; extract `tesseract.exe`, `pdftoppm.exe`, and required DLLs into `desktop/resources/ocr/bin` and `desktop/resources/ocr/lib`.
  - macOS: `packaging/ocr/prepare-ocr-macos.sh` installs `tesseract` and `poppler` via Homebrew; copy `bin/` and required `.dylib` files into `desktop/resources/ocr/`.
  - Linux: `packaging/ocr/prepare-ocr-linux.sh` installs `tesseract-ocr` and `poppler-utils` via apt; copy `bin/` and required `.so` files into `desktop/resources/ocr/`.
  Update documentation for where Electron sets PATH/LD_LIBRARY_PATH to include these folders.
  **Must NOT do**: Do not modify OCR logic in `app/ocr_service.py` beyond environment usage.

  **Recommended Agent Profile**:
  - Category: `deep` — Reason: platform-specific native dependency bundling.
  - Skills: []
  - Omitted: [`playwright`] — not needed for packaging scripts.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: T7 | Blocked By: T5

  **References**:
  - Docker deps list: `Dockerfile`
  - OCR usage: `app/ocr_service.py`
  - Windows Poppler: https://github.com/oschwartz10612/poppler-windows/releases
  - Windows Tesseract: https://github.com/UB-Mannheim/tesseract/wiki

  **Acceptance Criteria** (agent-executable only):
  - [ ] Running the platform script produces `desktop/resources/ocr/bin/tesseract(.exe)` and `pdftoppm(.exe)`.
  - [ ] With PATH pointing to `desktop/resources/ocr/bin`, `tesseract --version` exits 0.

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: OCR binaries run
    Tool: Bash
    Steps: powershell -File packaging\ocr\prepare-ocr-windows.ps1 (use matching script for mac/linux) ; set PATH=desktop\resources\ocr\bin;%PATH% ; tesseract --version
    Expected: version output printed and exit code 0
    Evidence: .sisyphus/evidence/task-6-ocr-deps.txt

  Scenario: OCR missing triggers unavailable status
    Tool: Bash
    Steps: set PATH= ; set PORT=5058 ; start /b desktop\resources\backend\tally-local-backend(.exe) ; timeout /t 3 >nul ; curl http://127.0.0.1:5058/api/status
    Expected: "ocr_available" is false
    Evidence: .sisyphus/evidence/task-6-ocr-deps-error.txt
  ```

  **Commit**: NO | Message: `build(ocr): bundle native OCR deps` | Files: [packaging/ocr/*, desktop/resources/ocr/*]

- [ ] 7. Configure electron-builder packaging outputs

  **What to do**: Add `build` config in `desktop/package.json` with `appId`, `productName`, `files`, `extraResources` (backend + ocr), `asar`/`asarUnpack` for executable binaries, and target outputs (Windows NSIS, macOS DMG, Linux AppImage). Generate platform icons from `tallylocal-icon.png` into `desktop/assets/icons/` (`icon.ico`, `icon.icns`, `icon.png`) and reference them in build config.
  **Must NOT do**: Do not enable auto-updates or external download dependencies.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: packaging configuration across OS targets.
  - Skills: []
  - Omitted: [`playwright`] — not required for config updates.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: T8 | Blocked By: T1, T2, T5, T6

  **References**:
  - electron-builder config: https://www.electron.build/configuration/configuration
  - Icons source: `tallylocal-icon.png`

  **Acceptance Criteria** (agent-executable only):
  - [ ] `npm run electron:dist -- --dir` produces output under `desktop/dist/`.
  - [ ] Packaged app includes `resources/backend/` and `resources/ocr/` in output.

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Directory build succeeds
    Tool: Bash
    Steps: npm run electron:dist -- --dir (workdir=desktop)
    Expected: desktop/dist/ exists with platform output folder
    Evidence: .sisyphus/evidence/task-7-electron-build.txt

  Scenario: Missing backend fails build
    Tool: Bash
    Steps: ren desktop\resources\backend backend-missing ; npm run electron:dist -- --dir (workdir=desktop)
    Expected: electron-builder fails with missing file error
    Evidence: .sisyphus/evidence/task-7-electron-build-error.txt
  ```

  **Commit**: NO | Message: `build(electron): configure packaging outputs` | Files: [desktop/package.json, desktop/assets/icons/*]

- [ ] 8. Document desktop build/run workflow

  **What to do**: Update `README.md` with a "Desktop App (Electron)" section covering prerequisites (Node, Python, platform OCR deps), build steps (`packaging/ocr/prepare-ocr-*`, `python packaging/build_backend.py`, `npm run electron:dev`, `npm run electron:dist -- --dir`), data storage location (`app.getPath("userData")/tally-local`), and troubleshooting (backend log path, OCR availability).
  **Must NOT do**: Do not remove existing Docker instructions.

  **Recommended Agent Profile**:
  - Category: `writing` — Reason: documentation update with build steps and caveats.
  - Skills: []
  - Omitted: [`playwright`] — not required for docs.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: none | Blocked By: T4, T7

  **References**:
  - Existing docs: `README.md`
  - Backend entrypoint: `run.py`
  - Docker deps: `Dockerfile`

  **Acceptance Criteria** (agent-executable only):
  - [ ] `README.md` contains a "Desktop App (Electron)" section with commands for build and run.

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Desktop section present
    Tool: Bash
    Steps: python -c "import pathlib; text=pathlib.Path('README.md').read_text(); print('Desktop App (Electron)' in text)"
    Expected: output is "True"
    Evidence: .sisyphus/evidence/task-8-docs.txt

  Scenario: Docs preserve Docker instructions
    Tool: Bash
    Steps: python -c "import pathlib; text=pathlib.Path('README.md').read_text(); print('docker compose up --build' in text)"
    Expected: output is "True"
    Evidence: .sisyphus/evidence/task-8-docs-error.txt
  ```

  **Commit**: NO | Message: `docs: add desktop build instructions` | Files: [README.md]

## Final Verification Wave (4 parallel agents, ALL must APPROVE)
- [ ] F1. Plan Compliance Audit — oracle
- [ ] F2. Code Quality Review — unspecified-high
- [ ] F3. Real Manual QA — unspecified-high (+ playwright if UI)
- [ ] F4. Scope Fidelity Check — deep

## Commit Strategy
- Single commit after all tasks: `build(electron): package desktop app with sidecar backend`.

## Success Criteria
- Electron app launches, backend starts, and UI loads without external network access.
- Local data persists under OS user data directories across restarts.
- Desktop build produces platform-specific installers (NSIS/DMG/AppImage).
