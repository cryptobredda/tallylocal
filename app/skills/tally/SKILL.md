---
name: tally
description: Use when the user asks about their money, spending, budgets, goals, subscriptions, or bank statements in Tally Local.
---

# Tally Local

Tally is the user's **private** finance ledger. It runs on their machine; the data
never leaves it. You read and write that ledger through Tally's tools — you are the
only reasoning layer, Tally never calls a model itself.

If the Tally MCP server is connected, use its tools directly. If not, run the CLI
(`tally …`) and parse the JSON it prints. Both hit the same database and obey the
same rules.

## Start every session with context

Call `get_context(period="30d")` first. It returns net worth, balances, cashflow,
top categories, budgets and alerts in one small payload. Only then drill down.

Do not ask the user for figures you can fetch.

## Data model you must respect

- `amount` is always **positive**; direction lives in `type` (`income` | `expense`).
- Dates are ISO (`2026-11-03`). Periods are strings: `30d`, `3m`, `ytd`, `mtd`, `all`.
- Bank and statement rows are **immutable**. `update_transaction` writes an
  annotation overlay (category, description, notes, tags) and is reversible with
  `undo_transaction`. You cannot change a synced amount or date — do not try.
- Deletes are soft; `undo_transaction` / restore brings the row back.
- Every write you make is recorded in the audit log, attributed to your token.

## Tool map

**Read** — `get_context`, `list_accounts`, `query_transactions`, `get_transaction`,
`search_transactions`, `spending_summary`, `list_recurring`, `budget_status`,
`net_worth`, `forecast`, `find_anomalies`, `get_audit`, `list_dashboards`,
`render_dashboard`, `list_bank_connections`.

**Write** — `update_transaction`, `categorize_transactions`, `add_transaction`,
`delete_transaction`, `undo_transaction`, `set_budget`, `list_goals`,
`create_goal`, `contribute_to_goal`, `create_dashboard`.

**Import** — `import_preview`, `import_commit`, `import_revert`, `sync_bank`.

Prefer `query_transactions(group_by="category"|"merchant"|"month")` over fetching
hundreds of rows. Cap `limit` at what you actually need.

## Recipes

**Monthly review**
1. `get_context("30d")`, then `spending_summary("30d", "category")`.
2. Compare with the previous month: `query_transactions(from=…, to=…, group_by="category")`.
3. Report total in/out, the three biggest changes, and one concrete saving.
4. Quote real amounts and dates; never estimate.

**Subscription audit**
1. `list_recurring()` and `find_anomalies("180d")`.
2. List each subscription with amount and cadence, flag price rises, annualise.
3. Suggest; do not cancel or delete anything without explicit confirmation.

**Categorise the inbox**
1. `query_transactions(group_by="merchant", limit=50)` plus the `uncategorised` alert.
2. Propose a category per merchant; if unsure, ask instead of guessing.
3. Apply with `categorize_transactions(ids, category, learn_rule=true)` so the rule
   engine improves next time.

**Import a statement**
1. `import_preview(path)` → check `confidence`, `warnings`, and per-row `issues`.
2. If rows look wrong, fix them (or re-run with a `bank_hint`) before committing.
3. `import_commit(batch_id)`. If the totals are wrong afterwards: `import_revert`.
4. Never commit a preview with balance-continuity warnings without telling the user.

**Build a dashboard page**
`create_dashboard(name, widgets=[…])` with widget types:
- `metric` — `{"metric": "net_worth|income|expenses|net|transactions", "period": "30d"}`
- `chart` — `{"report": "spending_by_category|cashflow|net_worth", "period": "30d"}`
- `list` — `{"report": "recent|merchants|recurring", "limit": 8}`
- `budget` — `{}` (renders budget vs actual, editable by the user)
- `goal` — `{}` or `{"goal_id": 3}` (renders progress, user can add contributions)
- `text` — `{"markdown": "…"}`

Example: a "Goals" page with one `goal` widget and one `budget` widget. The user can
edit budgets and add goal contributions directly from that page.

**Planning questions** ("can I afford X?")
1. `get_context`, `forecast(30)`, `budget_status()`.
2. Answer against the forecast, not the raw balance. State the assumption.

## Hard rules

- **Never invent a number.** If a tool returns nothing, say so.
- **Ask before deleting** or bulk-changing more than ~10 rows.
- **Never claim a bank sync succeeded** without a result showing `added`/`skipped`.
- **Respect scopes.** If a write returns `permission_denied`, tell the user their
  token is read-only; do not retry in a loop.
- **One question at a time** when the data is genuinely ambiguous.
- Amounts: show the currency from the account. Do not mix GBP/USD/EUR silently.

## Troubleshooting

| Symptom | Meaning |
|---|---|
| `permission_denied` | Token lacks `write`/`import`/`admin` |
| `raw_record_locked` | Tried to edit a bank row's amount/date — annotate instead |
| `404 not_found` | Wrong id; re-query |
| Empty `transactions` | Filters too narrow, or the period has no data |
| `import_preview` confidence < 0.8 | Layout not recognised; check warnings, try `bank_hint` |

The full interface reference is in `docs/agent-setup.md`; bank setup in
`docs/banks.md`; statement formats in `docs/statements.md`.
