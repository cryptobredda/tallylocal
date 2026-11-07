# Tally Local — Agentic + Multi-Bank Plan

Status: proposal · Author: Clawd (Hermes) · Based on repo @ `04af605`

---

## 1. Executive summary

Tally Local today is a solid **local-first finance app with a human UI**. It is not yet usable by an agent:
the only interface is a cookie-session SPA API, statement parsing is hardcoded to one bank's pixel
layout, and there is no way to authenticate a machine client.

The benchmark (Claude "Personal Finance", Nov 2026) is chat-first, US-only, cloud-connected, and
locked to one vendor. The opening is the same product shape — **ask your money questions in plain
language** — but **local, provider-agnostic, and agent-agnostic**.

This plan delivers three things:

1. **An agent surface**: one core service layer exposed as MCP (primary), CLI, and OpenAPI v1, with
   scoped tokens, audit logging, and safe-by-default writes.
2. **A real import engine**: CSV/OFX/QIF/MT940 + PDF text-layer parsing driven by declarative bank
   profiles, with OCR only as a fallback, confidence scoring, and balance-continuity validation.
3. **Direct bank connections** through cheap/open providers (Enable Banking for UK/EU, SimpleFIN for
   US/CA, bank-native tokens where they exist), behind one adapter interface, with statement import
   as the always-works fallback.

Non-goals: payment initiation, cloud sync, an LLM baked into the app, Plaid/GoCardless dependencies.

---

## 2. What exists today (verified)

Booted the app on Python 3.12 / Flask 3.1.3 / SQLAlchemy 2.1.8 (the pins are stale but the code runs).
`/api/auth/status` → `{"authenticated":false,"needs_setup":true}`; `/api/accounts` → 401. Auth and
multi-user DB-per-user work.

| Area | State | Consequence |
|---|---|---|
| Auth | Flask session cookie only (`users.db` + per-user SQLite) | No machine client can authenticate |
| API | ~70 endpoints under `/api`, SPA-shaped, snake/camel mixed | Unstable contract for agents; no versioning |
| Statement import | `HSBCStatementParser` with hardcoded X-ranges (`COL_PAID_OUT = (980,1150)`), PaddleOCR + pdf2image only | Works for one bank at one DPI; no text-layer path; no CSV/OFX/QIF/MT940 |
| Dedupe | SHA-256 of the uploaded file only | Bank sync needs per-transaction idempotency, not file hashes |
| Schema | No currency, no external transaction id, no pending flag, no connection/consent tables | Cannot represent a synced account honestly |
| Concurrency | No WAL, no `busy_timeout` | Agent + UI writing simultaneously → `database is locked` |
| Tests | None (`tests/` doesn't exist) despite AGENTS.md claiming pytest | Parser changes are unverifiable |
| Migrations | `create_all` only; alembic pinned but unused | Adding columns won't migrate existing installs |
| Port | hardcoded `5000` in `run.py`; `PORT` env ignored | Painful deployment / clashes with macOS AirPlay |

### Security findings (fix before any agent surface ships)

1. **CORS wildcard with credentials** — verified: `Origin: http://evil.example` returns
   `Access-Control-Allow-Origin: http://evil.example` **and** `Access-Control-Allow-Credentials: true`.
   Any website the user visits can read their finances from `127.0.0.1:5000`. This is the single most
   important bug in the repo.
2. `SECRET_KEY` falls back to `'dev-only-fallback'` → session forgery if unset.
3. No CSRF protection on mutating routes; no `SameSite`/`Secure` cookie settings.
4. Raw `str(e)` returned from several handlers → path/internals leak.
5. `per_page` unbounded → an agent can pull the whole DB in one call.
6. README says MIT, `LICENSE` is GPL-3.0, GitHub reports GPL-3.0. Must be resolved before promotion.

---

## 3. The benchmark, and what "more open" means

Claude's feature: connect accounts (read-only), ask questions in natural language, get answers over
your own balances/transactions. It is beta, US-only, runs in Anthropic's cloud, and only Claude can
use it.

Our version should be the same interaction with four deliberate differences:

| Dimension | Claude PF | Tally (target) |
|---|---|---|
| Data location | Vendor cloud | Your machine, SQLite, no upload |
| Agent | Claude only | Any MCP client, any CLI-driven agent, any HTTP agent |
| Bank coverage | US (Plaid) | UK/EU via Enable Banking, US/CA via SimpleFIN, plus bank-native tokens and statements anywhere |
| Cost | Bundled subscription | £0 + optional ~£1/mo aggregator, or fully free via statements |
| Actions | Read-only | Read by default; scoped, audited writes (categorise, import, budget) with undo |

Positioning line: **"Your money, your machine, your agent."**

---

## 4. Target architecture

```
                    ┌─────────────── agents ───────────────┐
                    │ Claude Desktop · Hermes · Cursor ·   │
                    │ Codex CLI · ChatGPT (HTTP MCP) · …   │
                    └───────┬──────────────┬───────────────┘
                     MCP stdio          MCP streamable-http
                            │              │  Bearer token
                    ┌───────▼──────────────▼───────┐
                    │        tally/mcp_server.py    │  thin: schema + auth + audit
                    └───────────────┬──────────────┘
   CLI `tally`  ────────────────────┤
   HTTP /api/v1 ────────────────────┤
                    ┌───────────────▼──────────────┐
                    │          TallyCore           │  all business logic
                    │  accounts · txns · reports   │  (no Flask, no HTTP)
                    │  imports · banking · budgets │
                    └───┬───────────┬───────────┬──┘
                        │           │           │
                  ┌─────▼────┐ ┌────▼─────┐ ┌───▼──────────┐
                  │ importers│ │ banking  │ │ analytics    │
                  │ csv/ofx/ │ │ enable   │ │ existing     │
                  │ qif/mt940│ │ simplefin│ │ services     │
                  │ pdf/ocr  │ │ monzo/…  │ │              │
                  └──────────┘ └──────────┘ └──────────────┘
                        │           │
                  ┌─────▼───────────▼─────┐
                  │  SQLite (WAL)         │
                  │  per-user DB + audit  │
                  └───────────────────────┘
```

**Key move: extract `TallyCore`.** Today `routes.py` is 2,645 lines mixing validation, queries and
serialisation. MCP/CLI/HTTP must not each reimplement that. `TallyCore` is a plain-Python facade over
the existing service modules; `routes.py` becomes a thin adapter (and shrinks).

### 4.1 Interface decision

| Interface | Verdict | Why |
|---|---|---|
| **MCP (stdio)** | **Primary** | The only protocol Claude Desktop, Cursor, Hermes, VS Code and ChatGPT connectors all speak. Zero network surface for local use. |
| **MCP (streamable HTTP)** | Primary for remote | Same tools, token-authenticated, for VPS/hosted Tally and phone agents. |
| **CLI (`tally`)** | **Secondary** | Agents without MCP (Codex CLI, shell loops, cron) are first-class. Also the human escape hatch and the installer's friend. `--json` everywhere. |
| **OpenAPI v1** | Third | For HTTP-native agents/frameworks and for generating clients. A *new, curated* `/api/v1`, not the legacy SPA API. |
| **Skill (SKILL.md)** | Glue | Teaches Hermes/Claude Code *when* and *how* to chain the CLI; 200 lines beats a 40-tool schema dump. |
| **Library (`pip install tallylocal`)** | Later | Only if someone wants to embed it. Not now. |

All five share `TallyCore`, so behaviour cannot drift.

### 4.2 Agent tool catalogue (v1)

Designed for **few calls and small payloads**, not CRUD symmetry.

Read (scope `read`):
- `get_context(period, include[])` — one call returns balances, cashflow, top categories, budget
  status, recurring charges, anomalies. Target < 2 KB. This is the tool that makes agents feel smart.
- `list_accounts(include_balances)`
- `query_transactions(filters, group_by, limit, cursor)` — filter by account, date, category, merchant,
  text, amount range, type, tag, pending, source; `group_by` ∈ category|merchant|month|account|tag.
- `get_transaction(id)` · `search_transactions(q, limit)`
- `spending_summary(period, group_by, compare_to)`
- `list_recurring(min_occurrences, since)` — subscriptions, with price-change flags
- `budget_status(period)` · `net_worth(include_assets, include_wallets)`
- `cashflow_forecast(days)` · `find_anomalies(period, sensitivity)`
- `list_import_batches()` · `list_bank_connections()`

Write (scope `write`, dry-run supported, audited, idempotency key required):
- `add_transaction(...)` · `update_transaction(id, patch)` · `delete_transaction(id, confirm)`
- `categorize_transactions(ids, category, learn_rule)` — the learning loop that makes the DB better
  every time an agent touches it
- `create_category` · `create_rule` · `create_transfer` · `set_budget`

Import (scope `import`):
- `import_preview(source, account_id, bank_hint)` → batch + parsed rows + per-row confidence + issues
- `import_commit(batch_id, edits[])` · `import_revert(batch_id)`
- `sync_bank(connection_id | all)`

Admin (scope `admin`): tokens, connections, provider credentials, export.

MCP extras:
- Resources: `tally://context`, `tally://accounts`, `tally://budgets` (cheap context refresh).
- Prompts: `monthly_review`, `subscription_audit`, `statement_cleanup`, `affordability_check`.

### 4.3 Authentication & safety model

- `tally token create --name claude --scopes read,write --expires 90d` → prints `tal_…` once; stores
  SHA-256 only. Scopes: `read`, `write`, `import`, `admin`.
- Bearer header on `/api/v1` and HTTP MCP; stdio MCP reads `~/.config/tally/config.json`.
- Loopback-only by default. Remote requires `TALLY_ALLOW_REMOTE=1` + TLS terminator in front.
- Every mutation writes `agent_audit` (token, tool, args hash, result, duration, dry_run).
- Destructive ops need `confirm: true` **and** the `write` scope; imports are revertible by batch.
- Outputs redact IBANs/account numbers (`GB29 **** 1234`) unless `include_sensitive=true` on an
  `admin` token.
- Rate limit per token (default 120 req/min) and hard caps on `limit`/`per_page`.

---

## 5. Data model changes

New tables:
- `institutions` — provider, name, country, logo, provider_institution_id
- `bank_connections` — provider, institution, status, `credentials_encrypted`, consent_expiry,
  last_sync_at, last_error
- `import_batches` — account, source (statement|bank_sync|manual|agent), filename/provider, counts,
  status, committed_at
- `import_rows` — batch, index, raw text, parsed JSON, confidence, issues, status (review queue)
- `agent_tokens` — user, name, token_hash, scopes, expiry, last_used, revoked_at
- `agent_audit` — token, tool, args_hash, result_summary, duration_ms, dry_run, created_at
- `fx_rates` — date, base, quote, rate

Columns added:
- `accounts`: `currency` (default GBP), `connection_id`, `provider_account_id`, `iban_masked`,
  `balance_current`, `balance_available`, `balance_synced_at`, `balance_source`
- `transactions`: `currency`, `amount_base`, `fx_rate`, `external_id`, `import_hash`, `import_batch_id`,
  `pending`, `value_date`, `counterparty`, `merchant_normalized`, `source`, `raw_json`
- Unique indexes: `(account_id, external_id)` where external_id not null; `(account_id, import_hash)`

Migrations: add a tiny `migrate.py` (idempotent `ALTER TABLE` with a `schema_version` row) rather than
adopting Alembic now — it is 80 lines and works for `create_all` installs. Alembic can come later.

Balance semantics: `balance_current` is authoritative when present (bank sync); otherwise
`opening_balance + Σ(signed tx)`. `reconcile_account()` creates an adjustment transaction when the two
drift, so agents never report a number the bank disagrees with.

---

## 6. Import engine (the "more bank statements" work)

Four tiers, cheapest first. This is the part that replaces the hardcoded HSBC parser.

**Tier 0 — structured files (free, deterministic, ~40% of real usage):** CSV (with saved column maps +
presets for Monzo, Revolut, Wise, N26, ING, DKB, Chase, Amex, generic), OFX/QFX, QIF, MT940.

**Tier 1 — PDF text layer (~45%):** `pdfplumber` word extraction. Algorithm (validated, see §9):
1. group words into visual lines by y-bucket
2. find the header row by labels (`Date` + ≥1 of `Paid out / Paid in / Money out / Money in / Amount /
   Balance / Debit / Credit`)
3. cluster the right edges of money tokens → discover numeric columns empirically
4. bind each header label to its nearest cluster (one-to-one), so semantics come from the bank's own
   headers, not from coordinates
5. for each line: assemble the date from 1–3 leading tokens, description = text between date and first
   numeric column, amounts assigned by column binding
6. multi-line descriptions: rows without a date/amount continue the previous row

**Tier 2 — scanned PDFs/images (~10%):** rasterise with `pdf2image`, OCR with **Tesseract** (already in
the Dockerfile, apt-installable, ~30 MB) and feed the same geometry algorithm. PaddleOCR stays as an
optional accelerator if installed; it stops being a hard dependency (it is ~600 MB of models and pins
Python ≤3.12).

**Tier 3 — agent repair (the agentic twist):** if confidence is low, `import_preview` returns the raw
lines plus the rows it could not parse. The agent fixes rows and calls `import_commit(batch_id, edits)`.
A human can do the same in the review UI. **The agent becomes the parser of last resort**, with the
user seeing exactly what will be written.

**Bank profiles** (`app/importers/profiles/*.yaml`) are hints, not code:

```yaml
id: hsbc_uk
match: {text_any: ["hsbc"], headers_all: ["paid out", "paid in"]}
dates: ["%d %b %y", "%d/%m/%Y"]
amount: {decimal: ".", thousands: ","}
sign: {out_label: "paid out", in_label: "paid in"}
skip_rows: ["balance brought forward", "balance carried forward"]
continuation: {requires_date: false}
```

Shipping targets: HSBC, Barclays, Lloyds, NatWest, Santander, Nationwide, Monzo, Starling, Revolut,
Chase UK, Amex, Wise, N26, ING, DKB, Commerzbank, BNP, plus a `generic` profile. The generic path
should handle most of these without any profile at all.

**Confidence & correctness** — the thing a spreadsheet can't do:
- per-row score: date parsed, amount parsed, description non-empty, column binding unambiguous
- per-statement **balance continuity**: `opening + Σ(signed) == closing`. If it doesn't hold, report the
  exact row where the running balance breaks. This turns "did the OCR work?" into a verifiable check.
- duplicate detection: `import_hash = sha256(account|date|amount|normalised_desc|occurrence)`.
- preview → commit → revert, with counts and a diff.

---

## 7. Bank connections

One interface, `BankProvider`:

```python
class BankProvider(Protocol):
    id: str
    def list_institutions(self, country: str) -> list[Institution]: ...
    def start_auth(self, institution_id: str, redirect_uri: str, state: str) -> str: ...
    def complete_auth(self, code: str) -> Connection: ...
    def accounts(self, conn) -> list[BankAccount]: ...
    def balances(self, conn, account) -> Balance: ...
    def transactions(self, conn, account, since: date) -> list[BankTxn]: ...
    def revoke(self, conn) -> None: ...
```

Normalised `BankTxn`: `external_id, booked_date, value_date, amount (signed), currency, description,
counterparty, merchant, pending, raw`.

### Provider matrix (as of Nov 2026)

| Provider | Region | Cost | Auth | Verdict |
|---|---|---|---|---|
| **Enable Banking** | EU + UK, ~2,700 banks | Free "restricted production" for your own linked accounts; volume-priced for public apps | OAuth redirect + SCA, RS256 JWT | **Primary UK/EU** — the closest self-serve replacement for the dead Nordigen free tier |
| **SimpleFIN Bridge** | US (some CA) | $15/yr, 25 institutions | paste token → Basic auth access URL | **Primary US/CA** — dead simple protocol, used by Actual Budget |
| **Teller** | US | Free dev tier, 100 live connections | mTLS client certs | Optional US |
| **Bank-native tokens** | varies | free | API token | Wise, Bunq, Coinbase, Kraken — no redirect, zero cost |
| **Monzo** | UK | free | OAuth | Optional; requires an HTTPS redirect URI, so needs a tunnel for pure-local use |
| **Statements (CSV/PDF/OFX) + IMAP watcher** | anywhere | free | none | **Always available fallback**; also the "no third party at all" story |
| GoCardless (ex-Nordigen) | EU/UK | — | — | **Not supported**: closed to new signups, free tier wound down |
| Plaid | US/EU | paid | — | Not supported: expensive, not open |

Design consequences:
- The consent flow is provider-agnostic: `tally bank link <provider>` starts a local callback server on
  a configurable port, prints the URL, waits for the redirect, stores the connection encrypted.
- Local-only installs that can't host an HTTPS redirect use SimpleFIN, bank-native tokens, or
  statements. Document that honestly instead of pretending every provider works offline.
- Credentials encrypted at rest with Fernet; key in `~/.config/tally/key` (0600) or
  `TALLY_ENCRYPTION_KEY`. Never in the DB or repo.
- Sync is idempotent: upsert by `(account_id, external_id)`, update balances, write an `import_batch`
  with `source=bank_sync`, record audit. Runs from `tally sync` (cron) or the UI, not an in-process
  scheduler.
- Sync window respects provider rate limits (Enable Banking: as low as 4 calls/day per account) — cache
  aggressively, never poll on every query.

---

## 8. Delivery plan

Estimates are focused solo-dev days.

### P0 — Security & foundations (0.5–1 d)
- Restrict CORS to same-origin + explicit allowlist; drop credentialed wildcard.
- Fail closed on missing `SECRET_KEY`; generate and persist one on first run.
- Cookie flags (`HttpOnly`, `SameSite=Lax`, `Secure` when TLS), CSRF header check for cookie writes.
- SQLite `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`.
- `PORT` env honoured; bind `127.0.0.1` by default, `TALLY_HOST` to override.
- Stop leaking exceptions; cap `per_page` (max 500) and default 100.
- `pytest` scaffold + CI job (`ruff` + `pytest`) alongside the Docker publish.
- Resolve licence: pick one, make README/LICENSE/GitHub agree.
- **Acceptance**: `curl -H 'Origin: http://evil.example' /api/auth/status` returns no ACAO header;
  `pytest` runs green in CI; app boots on 3.12 with no PaddleOCR installed.

### P1 — Agent surface (2–3 d)
- `app/core/` — `TallyCore` with account/txn/report/import/banking methods; routes refactored onto it.
- `app/tokens.py` + `agent_tokens`/`agent_audit` tables + `tally token` CLI.
- `app/api_v1.py` — curated endpoints, stable JSON envelope, pagination cursors, OpenAPI 3.1 doc.
- `app/cli.py` — `tally` (click, no new deps): `context`, `accounts`, `tx`, `report`, `import`, `bank`,
  `sync`, `token`, `mcp`, `serve`, `doctor`. Global `--json`.
- `app/mcp_server.py` — MCP SDK 2.x `MCPServer`, stdio + streamable-http, scopes enforced, resources,
  prompts, structured output.
- `skills/tally/SKILL.md` — agent playbook + `tally skill install --target claude|hermes|cursor`.
- **Acceptance**: from a clean DB, an MCP client lists ≥15 tools and answers "what did I spend on
  groceries last month?" end-to-end; `tally context --json | jq .net_worth` works; every write appears
  in `agent_audit`; a `read`-scoped token gets 403 on writes.

### P2 — Import engine (3–4 d)
- `app/importers/` — base types, CSV/OFX/QIF/MT940, PDF text, OCR fallback, detector, confidence.
- Profile registry + 8 profiles shipped; `generic` profile as default.
- `import_batches`/`import_rows`, preview/commit/revert, review UI in the SPA.
- Schema columns + `migrate.py`.
- Fixture generator (ReportLab) + golden JSON for every profile; pytest matrix in CI.
- **Acceptance**: 3 synthetic layouts with different columns parse to identical totals (already proven,
  §9); a 200-row statement round-trips preview→commit→revert with zero duplicates on re-import.

### P3 — Bank connections (3–4 d)
- `app/banking/` — protocol, registry, Enable Banking adapter, SimpleFIN adapter, CSV/IMAP watcher.
- Consent flow + local callback, encrypted credential store, connection UI.
- Sync engine + `tally sync` + balance reconciliation + connection health.
- **Acceptance**: Enable Banking sandbox/mock end-to-end (auth → accounts → balances → txns → sync),
  SimpleFIN demo access URL syncs, re-sync is a no-op, expired consent surfaces a clear error.

### P4 — Agent-native reports (2 d)
- `find_anomalies`, `list_recurring` (subscription audit with price hikes), `cashflow_forecast`,
  `net_worth` (bank + assets + wallets), `compare_periods`.
- `get_context` compaction tuned to < 2 KB.
- **Acceptance**: 20-question eval suite (`tests/evals/questions.yaml`) answered from a seeded DB with
  expected values; run against ≥2 agents, publish the score.

### P5 — Packaging, docs, demo (1–2 d)
- Docker image without PaddleOCR; optional `tally-mcp` sidecar compose profile.
- Docs: `docs/agents.md` (MCP/CLI/HTTP), `docs/banks.md` (provider setup + honest limits),
  `docs/statements.md` (supported layouts, how to add a profile).
- 90-second demo: Hermes/Claude Desktop asking real questions against a seeded demo DB, chart output.
- Release notes + version bump.

**Total: ~12–16 focused days.** P0+P1 is the shippable milestone: "any agent can read your finances
safely". P2 makes it work for real statements. P3 removes the last manual step.

---

## 9. What I already validated (spikes, not speculation)

1. **The app runs** on Python 3.12 + Flask 3.1.3 + SQLAlchemy 2.1.8; auth gating works; `/api/accounts`
   returns 401 unauthenticated.
2. **MCP works** with the current SDK (`mcp 2.3.0`, `MCPServer` — note FastMCP was renamed in v2).
   A 60-line server exposed `list_accounts`, `spending_summary`, `search_transactions` over stdio and
   returned correct structured JSON to an official `ClientSession`:
   `spending_summary({"months":1}) → {income: 3200.0, expenses: 1151.88, net: 2048.12, top_categories: […]}`.
3. **One parser, three layouts**: generated HSBC-style (`Paid out/Paid in/Balance`), Barclays-style
   (`Money out/Money in/Balance`), and Monzo-style (single signed `Amount`) PDFs. A single
   header-driven, column-clustering parser extracted 5/5 rows from each with identical net
   (**3105.66**), correct signs, and correct running balances — no OCR, no per-bank pixel constants.
   This is the proof that "more banks" is an engineering task, not a research project.
4. **The current parser cannot do this**: it hardcodes HSBC X-ranges, requires PaddleOCR + pdf2image, and
   has no CSV/OFX/QIF path at all.

Spike code: `/root/.hermes/cache/scratch/stmt/parse_v4.py`, `/root/.hermes/cache/scratch/mcp_spike/`.

---

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Real statements vary far more than fixtures | Ship `generic` + confidence + agent repair; build the corpus from real (redacted) statements before claiming N banks |
| Enable Banking restricted production changes terms | Provider abstraction; statements remain the zero-dependency path; SimpleFIN/bank-native tokens as alternates |
| Local callback URLs rejected by providers | Support a configurable `PUBLIC_BASE_URL`; document tunnel requirement; prefer token providers for local |
| SQLite contention with agent + UI | WAL + busy_timeout + short transactions; single-writer service layer |
| Agents doing damage | Read-only default scopes, dry-run, audit, revertible imports, confirm on delete |
| GPL blocks ecosystem adoption | Relicense to MIT/Apache-2.0 (sole author; all deps permissive) — decision needed |
| Scope creep into "AI CFO" | Tool surface, not opinions: the agent composes; the app never calls an LLM |

---

## 11. Decisions needed

1. **Licence** — stay GPL-3.0, or relicense MIT/Apache-2.0 for maximum agent-ecosystem pickup?
   (README already claims MIT; only one author, all deps permissive.)
2. **Bank priority** — UK/EU first (Enable Banking, free restricted), US first (SimpleFIN, $15/yr), or
   both behind the adapter from day one?
3. **Write scope** — read-only agents by default (safest, matches Claude), or read+write with audit
   from the start (more useful, needs trust)?

Default if unanswered: MIT, UK/EU first + SimpleFIN adapter stubbed, read-only default with opt-in
`write` scope.
