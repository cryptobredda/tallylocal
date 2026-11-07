"""`tally` — the CLI every agent can use, MCP or not.

Every command prints JSON by default so an agent can pipe it into `jq` without
parsing tables. `--table` gives humans something readable.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import click

CONTEXT_SETTINGS = {'help_option_names': ['-h', '--help']}


def _bootstrap(data_dir=None, db_path=None):
    if data_dir:
        os.environ['DATA_DIR'] = os.path.abspath(os.path.expanduser(data_dir))
    if db_path:
        os.environ['TALLY_DB'] = os.path.abspath(os.path.expanduser(db_path))
    from app.core import TallyCore
    core = TallyCore(actor=os.environ.get('TALLY_ACTOR', 'cli'))
    if not os.path.exists(core.db_path):
        from app.database import init_db
        init_db(core.db_path)
    else:
        from app.migrate import ensure_schema
        ensure_schema(core.db_path)
    return core


def _emit(data, as_table=False, columns=None):
    if as_table and isinstance(data, list) and data:
        cols = columns or list(data[0].keys())
        widths = {c: max(len(str(c)), *(len(_fmt(row.get(c))) for row in data)) for c in cols}
        print('  '.join(c.ljust(widths[c]) for c in cols))
        print('  '.join('-' * widths[c] for c in cols))
        for row in data:
            print('  '.join(_fmt(row.get(c)).ljust(widths[c]) for c in cols))
        return
    print(json.dumps(data, indent=2, default=str))


def _fmt(value):
    if value is None:
        return ''
    if isinstance(value, float):
        return f'{value:,.2f}'
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return str(value)


def _fail(err):
    payload = {'error': {'code': getattr(err, 'code', 'error'), 'message': str(err)}}
    print(json.dumps(payload, indent=2), file=sys.stderr)
    raise SystemExit(2)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--data-dir', envvar='TALLY_DATA_DIR', default=None, help='Data directory (defaults to repo)')
@click.option('--db', 'db_path', default=None, help='Explicit finance DB path')
@click.option('--table', 'as_table', is_flag=True, help='Human-readable output for list commands')
@click.version_option('2.0.0', prog_name='tally')
@click.pass_context
def cli(ctx, data_dir, db_path, as_table):
    """Tally Local — local-first personal finance for humans and agents."""
    ctx.ensure_object(dict)
    ctx.obj.update({'data_dir': data_dir, 'db_path': db_path, 'as_table': as_table})


def _core(ctx):
    return _bootstrap(ctx.obj.get('data_dir'), ctx.obj.get('db_path'))


def _call(ctx, fn, *args, **kwargs):
    from app.core import TallyError
    try:
        return fn(*args, **kwargs)
    except TallyError as err:
        _fail(err)


# ------------------------------------------------------------------ setup

@cli.command()
@click.option('--username', prompt=True)
@click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True)
@click.pass_context
def init(ctx, username, password):
    """Create the local admin account and database."""
    core = _core(ctx)
    from app.auth_service import create_user, get_user_by_username, init_auth_db
    init_auth_db()
    if get_user_by_username(username):
        _fail(Exception(f'User {username!r} already exists'))
    user = create_user(username, password, is_admin=True)
    _emit({'user': user.to_safe_dict(), 'db_path': core.db_path})


@cli.command()
@click.pass_context
def doctor(ctx):
    """Check the install: paths, schema, optional dependencies, providers."""
    core = _core(ctx)
    from app.migrate import CURRENT_VERSION
    report = {'data_dir': os.path.dirname(core.db_path), 'db_path': core.db_path,
              'db_exists': os.path.exists(core.db_path), 'schema_version': CURRENT_VERSION,
              'python': sys.version.split()[0], 'writable': os.access(os.path.dirname(core.db_path), os.W_OK)}
    for module, label in (('pdfplumber', 'pdf text parsing'), ('requests', 'bank/HTTP'),
                          ('pytesseract', 'OCR fallback'), ('mcp', 'MCP server')):
        try:
            __import__(module)
            report[label] = 'ok'
        except ImportError:
            report[label] = 'missing'
    try:
        from app.banking import describe_providers
        report['bank_providers'] = [p['id'] for p in describe_providers() if p.get('configured')]
    except Exception:
        report['bank_providers'] = []
    _emit(report)


@cli.command()
@click.option('--limit', default=30, type=int)
@click.pass_context
def audit(ctx, limit):
    """Recent agent actions (who changed what, and when)."""
    _emit(_core(ctx).list_audit(limit))


# ------------------------------------------------------------------ read

@cli.command()
@click.option('--period', default='30d')
@click.pass_context
def context(ctx, period):
    """One-call snapshot for an agent."""
    _emit(_core(ctx).get_context(period))


@cli.command()
@click.pass_context
def accounts(ctx):
    data = _core(ctx).list_accounts()
    _emit(data, ctx.obj['as_table'], ['id', 'name', 'type', 'currency', 'balance'])


@cli.command('net-worth')
@click.pass_context
def net_worth(ctx):
    _emit(_core(ctx).net_worth())


@cli.command()
@click.option('--days', default=30, type=int)
@click.pass_context
def forecast(ctx, days):
    _emit(_core(ctx).cashflow_forecast(days))


@cli.command()
@click.option('--period', default='90d')
@click.option('--sensitivity', default=3.0, type=float)
@click.pass_context
def anomalies(ctx, period, sensitivity):
    _emit(_core(ctx).find_anomalies(period, sensitivity))


# ------------------------------------------------------------------ transactions

@cli.group()
def tx():
    """Transactions."""


@tx.command('list')
@click.option('--from', 'date_from', default=None)
@click.option('--to', 'date_to', default=None)
@click.option('--account', 'account_id', type=int, default=None)
@click.option('--category', 'category_id', default=None)
@click.option('--q', default=None)
@click.option('--limit', default=25, type=int)
@click.option('--group-by', 'group_by', default=None)
@click.pass_context
def tx_list(ctx, date_from, date_to, account_id, category_id, q, limit, group_by):
    filters = {'date_from': date_from, 'date_to': date_to, 'text': q}
    if account_id:
        filters['account_ids'] = [account_id]
    if category_id:
        from app.core import TallyCore
        resolved = TallyCore().find_category(category_id)
        filters['category_ids'] = [resolved] if resolved else [category_id]
    result = _core(ctx).list_transactions(filters=filters, limit=limit, group_by=group_by)
    rows = result.get('transactions') or result.get('groups') or []
    _emit(rows if ctx.obj['as_table'] else result, ctx.obj['as_table'],
          ['id', 'date', 'description', 'amount', 'type', 'category_name'] if 'transactions' in result else None)


@tx.command('add')
@click.option('--amount', required=True, type=float)
@click.option('--description', required=True)
@click.option('--date', 'date_value', default=None)
@click.option('--account', 'account_id', type=int, default=None)
@click.option('--category', 'category_id', default=None)
@click.option('--type', 'tx_type', default='expense', type=click.Choice(['expense', 'income']))
@click.option('--notes', default=None)
@click.pass_context
def tx_add(ctx, amount, description, date_value, account_id, category_id, tx_type, notes):
    core = _core(ctx)
    cat = core.find_category(category_id) if category_id else None
    _emit(_call(ctx, core.create_transaction, description=description, amount=amount,
                date_value=date_value, account_id=account_id, category_id=cat,
                transaction_type=tx_type, notes=notes, source='agent'))


@tx.command('edit')
@click.argument('transaction_id', type=int)
@click.option('--category', 'category_id', default=None)
@click.option('--description', default=None)
@click.option('--notes', default=None)
@click.option('--tags', default=None)
@click.option('--exclude/--no-exclude', default=None)
@click.pass_context
def tx_edit(ctx, transaction_id, category_id, description, notes, tags, exclude):
    core = _core(ctx)
    patch = {}
    if category_id is not None:
        patch['category_id'] = core.find_category(category_id)
        if patch['category_id'] is None:
            _fail(Exception(f'Category {category_id!r} not found'))
    for key, value in (('description', description), ('notes', notes), ('tags', tags)):
        if value is not None:
            patch[key] = value
    if exclude is not None:
        patch['is_excluded'] = exclude
    _emit(_call(ctx, core.update_transaction, transaction_id, patch))


@tx.command('rm')
@click.argument('transaction_id', type=int)
@click.option('--confirm', is_flag=True)
@click.pass_context
def tx_rm(ctx, transaction_id, confirm):
    _emit(_call(ctx, _core(ctx).delete_transaction, transaction_id, confirm=confirm))


@tx.command('restore')
@click.argument('transaction_id', type=int)
@click.pass_context
def tx_restore(ctx, transaction_id):
    _emit(_call(ctx, _core(ctx).restore_transaction, transaction_id))


@tx.command('undo')
@click.argument('transaction_id', type=int)
@click.option('--group', 'change_group', default=None)
@click.pass_context
def tx_undo(ctx, transaction_id, change_group):
    _emit(_call(ctx, _core(ctx).undo, transaction_id, change_group))


@tx.command('categorise')
@click.argument('ids', nargs=-1, type=int)
@click.option('--category', required=True)
@click.option('--learn-rule', is_flag=True)
@click.pass_context
def tx_categorise(ctx, ids, category, learn_rule):
    core = _core(ctx)
    cat_id = core.find_category(category)
    if not cat_id:
        _fail(Exception(f'Category {category!r} not found'))
    result = _call(ctx, core.bulk_update, ids, {'category_id': cat_id})
    if learn_rule and ids:
        sample = core.get_transaction(ids[0])
        core.create_rule(sample['description'][:40], cat_id)
        result['rule_created'] = True
    _emit(result)


# ------------------------------------------------------------------ reports

@cli.command('report')
@click.argument('name')
@click.option('--period', default=None)
@click.option('--months', default=None, type=int)
@click.option('--limit', default=None, type=int)
@click.pass_context
def report(ctx, name, period, months, limit):
    params = {}
    if period:
        params['period'] = period
    if months:
        params['months'] = months
    if limit:
        params['limit'] = limit
    core = _core(ctx)
    _emit(_call(ctx, core.report, name, **params))


@cli.group()
def budgets():
    """Budgets."""


@budgets.command('list')
@click.option('--year', type=int, default=None)
@click.option('--month', type=int, default=None)
@click.pass_context
def budgets_list(ctx, year, month):
    _emit(_core(ctx).list_budgets(year, month))


@budgets.command('set')
@click.option('--category', required=True)
@click.option('--amount', required=True, type=float)
@click.option('--year', type=int, default=None)
@click.option('--month', type=int, default=None)
@click.pass_context
def budgets_set(ctx, category, amount, year, month):
    _emit(_call(ctx, _core(ctx).set_budget, category, amount, year, month))


# ------------------------------------------------------------------ goals

@cli.group()
def goals():
    """Savings goals."""


@goals.command('list')
@click.pass_context
def goals_list(ctx):
    _emit(_core(ctx).list_goals(), ctx.obj['as_table'],
          ['id', 'name', 'target', 'current', 'percent', 'status'])


@goals.command('add')
@click.option('--name', required=True)
@click.option('--target', required=True, type=float)
@click.option('--deadline', default=None)
@click.option('--account', 'account_id', type=int, default=None)
@click.pass_context
def goals_add(ctx, name, target, deadline, account_id):
    _emit(_call(ctx, _core(ctx).create_goal, name, target, deadline, account_id))


@goals.command('contribute')
@click.argument('goal_id', type=int)
@click.option('--amount', required=True, type=float)
@click.option('--note', default=None)
@click.pass_context
def goals_contribute(ctx, goal_id, amount, note):
    _emit(_call(ctx, _core(ctx).contribute_to_goal, goal_id, amount, note))


# ------------------------------------------------------------------ dashboards

@cli.group()
def pages():
    """Dashboard pages (agent-created)."""


@pages.command('list')
@click.pass_context
def pages_list(ctx):
    _emit(_core(ctx).list_pages())


@pages.command('create')
@click.option('--name', required=True)
@click.option('--slug', default=None)
@click.option('--icon', default='layout-dashboard')
@click.option('--widgets', default=None, help='JSON array of widgets')
@click.pass_context
def pages_create(ctx, name, slug, icon, widgets):
    widget_list = json.loads(widgets) if widgets else []
    _emit(_call(ctx, _core(ctx).create_dashboard, name, widget_list, slug, icon))


@pages.command('render')
@click.argument('ref')
@click.pass_context
def pages_render(ctx, ref):
    _emit(_call(ctx, _core(ctx).render_page, ref))


@pages.command('add-widget')
@click.argument('page_id', type=int)
@click.option('--type', 'widget_type', required=True,
              type=click.Choice(['metric', 'chart', 'list', 'budget', 'goal', 'progress', 'text']))
@click.option('--title', required=True)
@click.option('--config', default='{}', help='JSON config')
@click.pass_context
def pages_add_widget(ctx, page_id, widget_type, title, config):
    _emit(_call(ctx, _core(ctx).add_widget, page_id, widget_type, title, json.loads(config)))


# ------------------------------------------------------------------ imports

@cli.group('import')
def import_group():
    """Statement import (preview → commit → revert)."""


@import_group.command('preview')
@click.argument('path')
@click.option('--account', 'account_id', type=int, default=None)
@click.option('--bank', 'bank_hint', default=None)
@click.pass_context
def import_preview(ctx, path, account_id, bank_hint):
    _emit(_call(ctx, _core(ctx).import_preview, path, account_id, bank_hint))


@import_group.command('commit')
@click.argument('batch_id', type=int)
@click.option('--edits', default=None, help='JSON array of row edits')
@click.pass_context
def import_commit(ctx, batch_id, edits):
    _emit(_call(ctx, _core(ctx).import_commit, batch_id, json.loads(edits) if edits else None))


@import_group.command('revert')
@click.argument('batch_id', type=int)
@click.pass_context
def import_revert(ctx, batch_id):
    _emit(_call(ctx, _core(ctx).import_revert, batch_id))


@import_group.command('list')
@click.pass_context
def import_list(ctx):
    _emit(_core(ctx).list_import_batches())


# ------------------------------------------------------------------ banking

@cli.group()
def bank():
    """Bank connections."""


@bank.command('providers')
def bank_providers():
    from app.banking import describe_providers
    _emit(describe_providers())


@bank.command('connections')
@click.pass_context
def bank_connections(ctx):
    _emit(_core(ctx).list_bank_connections())


@bank.command('sync')
@click.option('--connection', 'connection_id', type=int, default=None)
@click.pass_context
def bank_sync(ctx, connection_id):
    from app.banking.sync import sync_all, sync_connection
    core = _core(ctx)
    _emit(sync_connection(core, connection_id) if connection_id else sync_all(core))


@bank.command('link')
@click.argument('provider')
@click.option('--institution', 'institution_id', required=True)
@click.option('--redirect-uri', default=None)
@click.pass_context
def bank_link(ctx, provider, institution_id, redirect_uri):
    from app.banking import get_provider
    core = _core(ctx)
    _emit(vars(_call(ctx, get_provider(provider, core).start_link, institution_id, redirect_uri)))


@bank.command('callback')
@click.argument('provider')
@click.option('--code', required=True)
@click.option('--state', default=None)
@click.pass_context
def bank_callback(ctx, provider, code, state):
    from app.banking import get_provider
    from app.banking.sync import save_connection
    core = _core(ctx)
    link = _call(ctx, get_provider(provider, core).complete_link, code, state)
    _emit(save_connection(core, link))


@bank.command('accounts')
@click.argument('provider')
@click.option('--country', default='GB')
@click.pass_context
def bank_accounts(ctx, provider, country):
    from app.banking import get_provider
    core = _core(ctx)
    provider_obj = get_provider(provider, core)
    _emit([{'id': i.reference, 'name': i.name, 'country': i.country}
           for i in _call(ctx, provider_obj.list_institutions, country)])


@bank.command('revoke')
@click.argument('connection_id', type=int)
@click.pass_context
def bank_revoke(ctx, connection_id):
    from app.banking.sync import revoke_connection
    _emit(revoke_connection(_core(ctx), connection_id))


# ------------------------------------------------------------------ tokens

@cli.group()
def token():
    """Agent access tokens."""


@token.command('create')
@click.option('--name', required=True)
@click.option('--scopes', default='read,write')
@click.option('--user', 'username', default=None)
@click.option('--expires-days', default=None, type=int)
def token_create(name, scopes, username, expires_days):
    from app.auth_service import (create_agent_token, get_user_by_username,
                                  list_users)
    user = get_user_by_username(username) if username else (list_users() or [None])[0]
    if not user:
        _fail(Exception('No user found. Run `tally init` first.'))
    raw, record = create_agent_token(user.id, name, [s for s in scopes.split(',') if s],
                                     expires_days)
    _emit({'token': raw, 'record': record.to_safe_dict(),
           'hint': 'Paste this into your MCP client config. It is not shown again.'})


@token.command('list')
def token_list():
    from app.auth_service import list_agent_tokens, list_users
    users = list_users()
    if not users:
        _fail(Exception('No users yet. Run `tally init`.'))
    _emit([t.to_safe_dict() for t in list_agent_tokens(users[0].id)])


@token.command('revoke')
@click.argument('token_id', type=int)
def token_revoke(token_id):
    from app.auth_service import list_users, revoke_agent_token
    users = list_users()
    if not users or not revoke_agent_token(token_id, users[0].id):
        _fail(Exception(f'Token {token_id} not found'))
    _emit({'id': token_id, 'revoked': True})


# ------------------------------------------------------------------ skill

@cli.group()
def skill():
    """Install the Tally skill into an agent, or print MCP config."""


@skill.command('print')
def skill_print():
    """Print the SKILL.md text (for any agent that takes a prompt file)."""
    from app.skill import skill_text
    click.echo(skill_text())


@skill.command('install')
@click.option('--target', type=click.Choice(['hermes', 'claude', 'cursor']), required=True)
@click.option('--path', default=None, help='Override the destination file')
def skill_install(target, path):
    """Copy the skill into Hermes, Claude Code or Cursor."""
    from app.skill import install
    destination = install(target, path)
    _emit({'target': target, 'installed': str(destination),
           'hint': 'Restart the agent so it picks up the new skill.'})


@skill.command('mcp-config')
@click.option('--actor', default='agent')
@click.pass_context
def skill_mcp_config(ctx, actor):
    """Print a ready-to-paste MCP server entry for Claude Desktop/Hermes/Cursor."""
    from app.skill import mcp_config_text
    core = _core(ctx)
    click.echo(mcp_config_text(data_dir=os.path.dirname(core.db_path), actor=actor))


# ------------------------------------------------------------------ servers

@cli.command()
@click.option('--transport', default='stdio', type=click.Choice(['stdio', 'streamable-http']))
@click.option('--host', default='127.0.0.1')
@click.option('--port', default=8765, type=int)
@click.pass_context
def mcp(ctx, transport, host, port):
    """Run the MCP server (stdio for Claude Desktop/Hermes, http for remote)."""
    core = _core(ctx)
    os.environ['TALLY_DB_PATH'] = core.db_path
    from app.mcp_server import build_server
    build_server(core).run(transport, host=host, port=port)


@cli.command()
@click.option('--host', default=None)
@click.option('--port', default=None, type=int)
@click.pass_context
def serve(ctx, host, port):
    """Run the web UI + REST API."""
    _core(ctx)
    from app import create_app
    app = create_app()
    from app.config import get_host, get_port
    app.run(host=host or get_host(), port=port or get_port())


if __name__ == '__main__':
    cli()
