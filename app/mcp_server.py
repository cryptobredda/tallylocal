"""MCP server: Tally Local as tools any agent can call.

Transport:
  stdio              — Claude Desktop, Hermes, Cursor (no network surface)
  streamable-http    — remote agents, behind a bearer token

Run with `tally mcp` (stdio) or `tally mcp --transport streamable-http`.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from mcp.server import MCPServer

from app.core import TallyCore, TallyError


def build_server(core: Optional[TallyCore] = None) -> MCPServer:
    core = core or TallyCore(actor='agent')

    server = MCPServer(
        name='tally',
        version='2.0.0',
        instructions=(
            "Tally Local is the user's private finance ledger, running on their machine. "
            "Amounts are positive floats; every row has type income|expense. "
            "Raw bank rows are immutable — update_transaction writes annotations and is "
            "reversible with undo_transaction. Prefer get_context first, then drill down. "
            "Never invent figures: if a tool returns nothing, say so."
        ),
    )

    def safe(fn):
        try:
            return fn()
        except TallyError as err:
            return {'error': {'code': err.code, 'message': str(err)}}

    # ---------------------------------------------------------- read tools

    @server.tool()
    def get_context(period: str = '30d') -> Dict[str, Any]:
        """Compact snapshot: net worth, balances, cashflow, top categories, budgets, alerts."""
        return safe(lambda: core.get_context(period))

    @server.tool()
    def list_accounts() -> List[Dict[str, Any]]:
        """All accounts with current balances and currency."""
        return safe(core.list_accounts)

    @server.tool()
    def query_transactions(date_from: Optional[str] = None, date_to: Optional[str] = None,
                           account_id: Optional[int] = None, category: Optional[str] = None,
                           text: Optional[str] = None, type: Optional[str] = None,
                           min_amount: Optional[float] = None, max_amount: Optional[float] = None,
                           group_by: Optional[str] = None, limit: int = 25,
                           offset: int = 0) -> Dict[str, Any]:
        """Query transactions. group_by: category|merchant|month|account|tag."""
        def run():
            filters = {'date_from': date_from, 'date_to': date_to, 'text': text,
                       'types': [type] if type else None, 'min_amount': min_amount,
                       'max_amount': max_amount}
            if account_id:
                filters['account_ids'] = [account_id]
            if category:
                resolved = core.find_category(category)
                filters['category_ids'] = [resolved] if resolved else [-1]
            filters = {k: v for k, v in filters.items() if v not in (None, '', [])}
            return core.list_transactions(filters=filters, limit=limit, offset=offset,
                                          group_by=group_by)
        return safe(run)

    @server.tool()
    def get_transaction(transaction_id: int) -> Dict[str, Any]:
        """One transaction, including the original bank description and edit state."""
        return safe(lambda: core.get_transaction(transaction_id))

    @server.tool()
    def search_transactions(query: str, limit: int = 20) -> Dict[str, Any]:
        """Substring search over descriptions and counterparties."""
        return safe(lambda: core.list_transactions(filters={'text': query}, limit=limit))

    @server.tool()
    def spending_summary(period: str = '30d', group_by: str = 'category') -> Dict[str, Any]:
        """Spend totals grouped by category, merchant, month or account."""
        def run():
            start = core.report('savings_rate', period=period)
            grouped = core.list_transactions(filters={'date_from': _since(period)},
                                             group_by=group_by, limit=20)
            return {'period': period, 'totals': start, 'breakdown': grouped}
        return safe(run)

    @server.tool()
    def list_recurring() -> Any:
        """Detected recurring charges / subscriptions with amounts and cadence."""
        return safe(lambda: core.report('recurring'))

    @server.tool()
    def budget_status(year: Optional[int] = None, month: Optional[int] = None) -> List[Dict[str, Any]]:
        """Budgets with actual spend and remaining amount."""
        return safe(lambda: core.list_budgets(year, month))

    @server.tool()
    def net_worth() -> Dict[str, Any]:
        """Cash, assets and crypto wallets."""
        return safe(core.net_worth)

    @server.tool()
    def forecast(days: int = 30) -> Dict[str, Any]:
        """Projected spend for the next N days from recurring + variable spending."""
        return safe(lambda: core.cashflow_forecast(days))

    @server.tool()
    def find_anomalies(period: str = '90d', sensitivity: float = 3.0) -> Dict[str, Any]:
        """Unusual transactions, possible duplicate charges, subscription price hikes."""
        return safe(lambda: core.find_anomalies(period, sensitivity))

    @server.tool()
    def get_audit(limit: int = 20) -> List[Dict[str, Any]]:
        """Recent agent actions recorded in the audit log."""
        return safe(lambda: core.list_audit(limit))

    # ---------------------------------------------------------- annotation tools

    @server.tool()
    def update_transaction(transaction_id: int, category: Optional[str] = None,
                           description: Optional[str] = None, notes: Optional[str] = None,
                           tags: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
        """Annotate a transaction. Raw bank fields are never modified."""
        def run():
            patch = {}
            if category is not None:
                resolved = core.find_category(category)
                if not resolved:
                    return {'error': {'code': 'not_found',
                                      'message': f'Category {category!r} not found'}}
                patch['category_id'] = resolved
            if description is not None:
                patch['description'] = description
            if notes is not None:
                patch['notes'] = notes
            if tags is not None:
                patch['tags'] = tags
            return core.update_transaction(transaction_id, patch, dry_run=dry_run)
        return safe(run)

    @server.tool()
    def categorize_transactions(transaction_ids: List[int], category: str,
                                learn_rule: bool = False,
                                dry_run: bool = False) -> Dict[str, Any]:
        """Bulk categorise rows; optionally create a rule from the first description."""
        def run():
            resolved = core.find_category(category)
            if not resolved:
                return {'error': {'code': 'not_found',
                                  'message': f'Category {category!r} not found'}}
            if dry_run:
                return {'dry_run': True, 'would_update': len(transaction_ids),
                        'category_id': resolved}
            result = core.bulk_update(transaction_ids, {'category_id': resolved})
            if learn_rule and transaction_ids:
                sample = core.get_transaction(transaction_ids[0])
                rule = core.create_rule(sample['description'][:40], resolved)
                result['rule'] = rule
            return result
        return safe(run)

    @server.tool()
    def add_transaction(description: str, amount: float, date: Optional[str] = None,
                        type: str = 'expense', account_id: Optional[int] = None,
                        category: Optional[str] = None, notes: Optional[str] = None) -> Dict[str, Any]:
        """Record a manual transaction (cash, correction, etc.)."""
        def run():
            cat = core.find_category(category) if category else None
            return core.create_transaction(description, amount, date, account_id, cat,
                                           type, notes, source='agent')
        return safe(run)

    @server.tool()
    def delete_transaction(transaction_id: int, confirm: bool = False) -> Dict[str, Any]:
        """Soft-delete a transaction. Reversible with undo/restore."""
        return safe(lambda: core.delete_transaction(transaction_id, confirm=confirm))

    @server.tool()
    def undo_transaction(transaction_id: int, change_group: Optional[str] = None) -> Dict[str, Any]:
        """Undo the last annotation change on a transaction."""
        return safe(lambda: core.undo(transaction_id, change_group))

    # ---------------------------------------------------------- planning tools

    @server.tool()
    def set_budget(category: str, amount: float, year: Optional[int] = None,
                   month: Optional[int] = None) -> Dict[str, Any]:
        """Create or update a monthly budget for a category."""
        def run():
            resolved = core.find_category(category)
            if not resolved:
                return {'error': {'code': 'not_found',
                                  'message': f'Category {category!r} not found'}}
            return core.set_budget(resolved, amount, year, month)
        return safe(run)

    @server.tool()
    def list_goals() -> List[Dict[str, Any]]:
        """Savings goals with progress."""
        return safe(core.list_goals)

    @server.tool()
    def create_goal(name: str, target_amount: float, deadline: Optional[str] = None,
                    account_id: Optional[int] = None, notes: Optional[str] = None) -> Dict[str, Any]:
        """Create a savings goal."""
        return safe(lambda: core.create_goal(name, target_amount, deadline, account_id,
                                             notes=notes))

    @server.tool()
    def contribute_to_goal(goal_id: int, amount: float, note: Optional[str] = None) -> Dict[str, Any]:
        """Record a contribution towards a goal."""
        return safe(lambda: core.contribute_to_goal(goal_id, amount, note))

    # ---------------------------------------------------------- dashboards

    @server.tool()
    def list_dashboards() -> List[Dict[str, Any]]:
        """Dashboard pages the user can open."""
        return safe(core.list_pages)

    @server.tool()
    def create_dashboard(name: str, widgets: Optional[List[Dict[str, Any]]] = None,
                         slug: Optional[str] = None,
                         icon: str = 'layout-dashboard') -> Dict[str, Any]:
        """Create a dashboard page with widgets in one call.

        Widget: {"type": "metric|chart|list|budget|goal|progress|text", "title": str,
                 "config": {...}, "layout": {"w": 4, "h": 2}}
        metric config: {"metric": "net_worth|income|expenses|net|transactions", "period": "30d"}
        chart config:  {"report": "spending_by_category|cashflow|net_worth", "period": "30d"}
        list config:   {"report": "recent|merchants|recurring", "limit": 8}
        """
        return safe(lambda: core.create_dashboard(name, widgets or [], slug, icon))

    @server.tool()
    def render_dashboard(slug: str) -> Dict[str, Any]:
        """Resolved data for a dashboard page."""
        return safe(lambda: core.render_page(slug))

    # ---------------------------------------------------------- imports & banking

    @server.tool()
    def import_preview(path: str, account_id: Optional[int] = None,
                       bank_hint: Optional[str] = None) -> Dict[str, Any]:
        """Parse a statement (PDF/CSV/OFX/QIF/MT940) into a reviewable batch."""
        return safe(lambda: core.import_preview(path, account_id, bank_hint))

    @server.tool()
    def import_commit(batch_id: int) -> Dict[str, Any]:
        """Commit a previewed batch into the ledger."""
        return safe(lambda: core.import_commit(batch_id))

    @server.tool()
    def import_revert(batch_id: int) -> Dict[str, Any]:
        """Revert a committed batch (removes the rows it created)."""
        return safe(lambda: core.import_revert(batch_id))

    @server.tool()
    def sync_bank(connection_id: Optional[int] = None) -> Dict[str, Any]:
        """Pull fresh transactions from bank connections."""
        def run():
            from app.banking.sync import sync_all, sync_connection
            return sync_connection(core, connection_id) if connection_id else sync_all(core)
        return safe(run)

    @server.tool()
    def list_bank_connections() -> List[Dict[str, Any]]:
        """Connected banks and their sync state."""
        return safe(core.list_bank_connections)

    # ---------------------------------------------------------- resources & prompts

    @server.resource('tally://context')
    def context_resource() -> str:
        """Current financial context as JSON."""
        return json.dumps(core.get_context('30d'), default=str)

    @server.resource('tally://accounts')
    def accounts_resource() -> str:
        return json.dumps(core.list_accounts(), default=str)

    @server.prompt()
    def monthly_review() -> str:
        """Run a monthly money review."""
        return (
            "Review the user's last calendar month. Call get_context('30d'), then "
            "query_transactions for the previous month grouped by category and merchant. "
            "Report: total in/out, the three biggest changes vs the month before, any "
            "subscription price rises, and one concrete saving. Use only tool data, cite amounts."
        )

    @server.prompt()
    def subscription_audit() -> str:
        """Find subscriptions worth cancelling."""
        return (
            "Call list_recurring and find_anomalies('180d'). List each subscription with "
            "amount and cadence, flag price increases, and estimate annual cost. Ask before "
            "suggesting cancellation; do not modify anything."
        )

    @server.prompt()
    def statement_cleanup() -> str:
        """Categorise uncategorised transactions."""
        return (
            "Call query_transactions with group_by='merchant' and look for rows without a "
            "category (get_context alerts). Propose a category for each merchant, then apply "
            "with categorize_transactions(learn_rule=true). Never guess wildly: if unsure, ask."
        )

    return server


def _since(period: str) -> str:
    from app.core import _period_start
    return _period_start(period).isoformat()


def main():
    db_path = os.environ.get('TALLY_DB_PATH') or os.environ.get('TALLY_DB')
    core = TallyCore(db_path=db_path, actor='mcp') if db_path else TallyCore(actor='mcp')
    build_server(core).run('stdio')


if __name__ == '__main__':
    main()
