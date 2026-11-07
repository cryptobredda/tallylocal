# Using Tally from an agent

Tally exposes one core (`app/core.py`) through four interfaces. Pick whichever
your agent speaks; they all read and write the same database with the same rules.

| Interface | Use it for | Entry point |
|---|---|---|
| **MCP (stdio)** | Claude Desktop, Hermes, Cursor, VS Code | `tally mcp` |
| **MCP (HTTP)** | Remote agents, phone, another machine | `tally mcp --transport streamable-http` |
| **CLI** | Codex CLI, shell loops, cron | `tally <command>` |
| **HTTP API** | Any framework, generated clients | `/api/v1` + `/api/v1/openapi.json` |

---

## 1. Create a token

Tokens are scoped and hashed at rest. The raw value is printed once.

```bash
tally token create --name claude --scopes read,write --expires-days 90
# {"token": "tal_...", "record": {...}}
```

Scopes:

| Scope | Grants |
|---|---|
| `read` | accounts, transactions, reports, context, dashboards, audit |
| `write` | annotations, manual transactions, budgets, goals, pages |
| `import` | statement preview/commit/revert, bank sync |
| `admin` | tokens, bank linking, connection revocation |

A token with `admin` implies every other scope. A browser session is treated as
admin.

---

## 2. MCP — stdio (recommended for a local agent)

Generate the exact snippet for this machine:

```bash
tally skill mcp-config
```

```json
{
  "mcpServers": {
    "tally": {
      "command": "/path/to/tallylocal/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "env": {
        "DATA_DIR": "/path/to/tallylocal",
        "TALLY_ACTOR": "claude"
      }
    }
  }
}
```

Also install the skill so the agent knows *when* to use the tools:

```bash
tally skill install --target claude   # ~/.claude/skills/tally/SKILL.md
tally skill install --target hermes   # ~/.hermes/skills/tally/SKILL.md
tally skill install --target cursor   # ./.cursor/rules/tally.mdc
tally skill print                     # or paste it anywhere
```

Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows).
Hermes and Cursor use the same shape in their MCP config.

Restart the client, then ask:

> What did I spend on groceries last month, and which subscriptions went up?

The agent should call `get_context` first, then `query_transactions`.

### Tools exposed

Read: `get_context`, `list_accounts`, `query_transactions`, `get_transaction`,
`search_transactions`, `spending_summary`, `list_recurring`, `budget_status`,
`net_worth`, `forecast`, `find_anomalies`, `get_audit`, `list_dashboards`,
`render_dashboard`, `list_bank_connections`.

Write: `update_transaction`, `categorize_transactions`, `add_transaction`,
`delete_transaction`, `undo_transaction`, `set_budget`, `list_goals`,
`create_goal`, `contribute_to_goal`, `create_dashboard`.

Import: `import_preview`, `import_commit`, `import_revert`, `sync_bank`.

Resources: `tally://context`, `tally://accounts`.
Prompts: `monthly_review`, `subscription_audit`, `statement_cleanup`.

---

## 3. MCP — HTTP (remote)

```bash
TALLY_ALLOW_REMOTE=1 tally mcp --transport streamable-http --host 0.0.0.0 --port 8765
```

Put TLS in front (Caddy/nginx) and send the token as `Authorization: Bearer tal_...`.
The MCP server binds loopback by default; `--host 0.0.0.0` is an explicit choice.

---

## 4. CLI

Every command prints JSON, so agents can pipe it.

```bash
tally context --period 30d
tally accounts --table
tally tx list --from 2026-10-01 --to 2026-10-31 --group-by category
tally tx add --amount 42.10 --description "Tesco" --date 2026-11-03
tally tx edit 42 --category Groceries --notes "weekly shop"
tally tx undo 42
tally report merchants --limit 10
tally anomalies --period 180d
tally import preview statement.pdf --account 1
tally import commit 3
tally pages create --name "Goals" --widgets '[{"type":"goal","title":"Goals"}]'
```

`tally doctor` prints paths, schema version, optional dependencies and configured
bank providers.

---

## 5. HTTP API

```bash
curl -H "Authorization: Bearer $TALLY_TOKEN" \
     "http://127.0.0.1:5000/api/v1/context?period=30d"

curl -H "Authorization: Bearer $TALLY_TOKEN" \
     "http://127.0.0.1:5000/api/v1/transactions?from=2026-10-01&group_by=category"
```

Full spec: `GET /api/v1/openapi.json`.

Errors are structured:

```json
{"error": {"code": "permission_denied", "message": "Token lacks the 'write' scope (has: ['read'])"}}
```

---

## 6. Safety model

- **Raw bank rows are immutable.** `transactions` is append-only for anything
  imported or synced. Annotations live in `transaction_overrides`; every change is
  appended to `transaction_edits` with a change group.
- **Everything is reversible.** `undo_transaction` reverts the last change group;
  imports revert as a batch; deletes are soft.
- **Every agent action is audited.** `agent_audit` records token, tool, arguments,
  result summary and duration. Read it with `get_audit` or `tally audit`.
- **Least privilege.** Give `read` by default. Add `write` only if the agent should
  categorise or create goals. Keep `admin` for yourself.
- **Local by default.** The web server binds `127.0.0.1`; MCP stdio never opens a
  socket.
- **No LLM inside Tally.** The app never calls a model. Your agent reasons; Tally
  supplies grounded data and safe writes.

---

## 7. Agent habits that work well

1. Start with `get_context` — one call returns balances, cashflow, top categories,
   budgets and alerts.
2. Use `group_by` instead of fetching hundreds of rows.
3. When categorising, call `categorize_transactions(..., learn_rule=true)` so the
   rule engine improves for next time.
4. Ask before deleting; it is reversible but still surprising.
5. If a statement parse is ambiguous, `import_preview` returns per-row confidence
   and issues — fix the rows, then commit.
