"""Curated, versioned API for agents.

The legacy /api surface exists for the SPA and is not a contract. This one is:
stable shapes, scoped bearer tokens, bounded pagination, and errors that tell an
agent what to fix. OpenAPI lives at /api/v1/openapi.json.
"""
import os
import tempfile
from datetime import datetime

from flask import (Blueprint, Response, current_app, g, jsonify, request,
                   session as flask_session)
from werkzeug.utils import secure_filename

from app.auth_service import (create_agent_token, list_agent_tokens,
                              revoke_agent_token)
from app.core import (Conflict, NotFound, PermissionDenied, RawRecordLocked,
                      TallyCore, TallyError, Unauthenticated, ValidationError)
from app.database import DB_PATH

api_v1_bp = Blueprint('api_v1', __name__)

SCOPES = ('read', 'write', 'import', 'admin')


# ------------------------------------------------------------------ auth

def _current_db_path():
    return getattr(g, 'current_db_path', None) or DB_PATH


def _actor():
    token = g.get('agent_token')
    if token:
        return f'agent:{token.name}'
    user = g.get('current_user')
    return user.username if user else 'anonymous'


def _is_human():
    return bool(g.get('current_user')) and not g.get('agent_token')


def core() -> TallyCore:
    token = g.get('agent_token')
    return TallyCore(db_path=_current_db_path(), actor=_actor(),
                     token_id=token.id if token else None)


def require_scope(scope):
    if _is_human():
        return
    token = g.get('agent_token')
    if not token:
        raise PermissionDenied('Authentication required')
    granted = token.scope_set()
    if scope not in granted and 'admin' not in granted:
        raise PermissionDenied(f'Token lacks the {scope!r} scope (has: {sorted(granted)})')


@api_v1_bp.before_request
def authenticate_v1():
    if request.endpoint == 'api_v1.health':
        return None
    if not g.get('current_user'):
        raise Unauthenticated('Authentication required: send Authorization: Bearer tal_...')


@api_v1_bp.errorhandler(TallyError)
def handle_tally_error(err):
    return jsonify({'error': {'code': err.code, 'message': str(err)}}), err.status


@api_v1_bp.errorhandler(404)
def handle_404(err):
    return jsonify({'error': {'code': 'not_found', 'message': 'Endpoint not found'}}), 404


# ------------------------------------------------------------------ meta

@api_v1_bp.get('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'tally', 'api': 'v1',
                    'time': datetime.utcnow().isoformat() + 'Z'})


@api_v1_bp.get('/me')
def me():
    require_scope('read')
    token = g.get('agent_token')
    user = g.current_user
    return jsonify({
        'user': {'id': user.id, 'username': user.username, 'is_admin': bool(user.is_admin)},
        'auth': 'token' if token else 'session',
        'scopes': sorted(token.scope_set()) if token else ['admin'],
        'token': token.to_safe_dict() if token else None,
    })


@api_v1_bp.get('/context')
def context():
    require_scope('read')
    return jsonify(core().get_context(request.args.get('period', '30d')))


# ------------------------------------------------------------------ accounts

@api_v1_bp.get('/accounts')
def accounts_list():
    require_scope('read')
    return jsonify({'accounts': core().list_accounts(
        include_archived=request.args.get('include_archived') in ('1', 'true'))})


@api_v1_bp.post('/accounts')
def accounts_create():
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().create_account(
        name=body.get('name'), account_type=body.get('account_type', 'checking'),
        currency=body.get('currency', 'GBP'),
        opening_balance=body.get('opening_balance', 0),
        description=body.get('description'), colour=body.get('color'),
        icon=body.get('icon'))), 201


@api_v1_bp.get('/accounts/<int:account_id>')
def accounts_get(account_id):
    require_scope('read')
    return jsonify(core().get_account(account_id))


@api_v1_bp.patch('/accounts/<int:account_id>')
def accounts_patch(account_id):
    require_scope('write')
    return jsonify(core().update_account(account_id, request.get_json(silent=True) or {}))


# ------------------------------------------------------------------ transactions

def _filters_from_args(args):
    def csv(name):
        raw = args.get(name)
        return [x for x in raw.split(',') if x] if raw else None

    filters = {
        'account_ids': csv('account_ids') or csv('account_id'),
        'category_ids': csv('category_ids') or csv('category_id'),
        'date_from': args.get('date_from') or args.get('from'),
        'date_to': args.get('date_to') or args.get('to'),
        'text': args.get('q') or args.get('text'),
        'types': csv('types') or csv('type'),
        'min_amount': args.get('min_amount'),
        'max_amount': args.get('max_amount'),
        'tags': csv('tags'),
        'uncategorised': args.get('uncategorised') in ('1', 'true'),
    }
    for key in ('pending',):
        if args.get(key) is not None:
            filters[key] = args.get(key) in ('1', 'true')
    if args.get('source'):
        filters['source'] = args.get('source')
    if args.get('include_transfers') is not None:
        filters['include_transfers'] = args.get('include_transfers') in ('1', 'true')
    return {k: v for k, v in filters.items() if v not in (None, '', [])}


@api_v1_bp.get('/transactions')
def transactions_list():
    require_scope('read')
    result = core().list_transactions(
        filters=_filters_from_args(request.args),
        limit=request.args.get('limit', 50, type=int),
        offset=request.args.get('offset', 0, type=int),
        order=request.args.get('order', 'date_desc'),
        group_by=request.args.get('group_by'),
    )
    return jsonify(result)


@api_v1_bp.get('/transactions/deleted')
def transactions_deleted():
    require_scope('read')
    return jsonify({'transactions': core().list_deleted(request.args.get('limit', 50, type=int))})


@api_v1_bp.get('/transactions/<int:transaction_id>')
def transactions_get(transaction_id):
    require_scope('read')
    return jsonify(core().get_transaction(transaction_id))


@api_v1_bp.post('/transactions')
def transactions_create():
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().create_transaction(
        description=body.get('description'), amount=body.get('amount'),
        date_value=body.get('date'), account_id=body.get('account_id'),
        category_id=body.get('category_id'), transaction_type=body.get('type', 'expense'),
        notes=body.get('notes'), tags=_join_tags(body.get('tags')),
        source=body.get('source', 'agent'))), 201


@api_v1_bp.patch('/transactions/<int:transaction_id>')
def transactions_patch(transaction_id):
    require_scope('write')
    dry_run = request.args.get('dry_run') in ('1', 'true')
    return jsonify(core().update_transaction(transaction_id,
                                             request.get_json(silent=True) or {},
                                             dry_run=dry_run))


@api_v1_bp.delete('/transactions/<int:transaction_id>')
def transactions_delete(transaction_id):
    require_scope('write')
    confirm = request.args.get('confirm') in ('1', 'true')
    return jsonify(core().delete_transaction(transaction_id, confirm=confirm))


@api_v1_bp.post('/transactions/<int:transaction_id>/restore')
def transactions_restore(transaction_id):
    require_scope('write')
    return jsonify(core().restore_transaction(transaction_id))


@api_v1_bp.post('/transactions/<int:transaction_id>/undo')
def transactions_undo(transaction_id):
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().undo(transaction_id, body.get('change_group')))


@api_v1_bp.post('/transactions/bulk')
def transactions_bulk():
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().bulk_update(body.get('ids') or [], body.get('patch') or {}))


def _join_tags(tags):
    if tags is None:
        return None
    if isinstance(tags, (list, tuple)):
        return ','.join(str(t) for t in tags)
    return str(tags)


# ------------------------------------------------------------------ taxonomy

@api_v1_bp.get('/categories')
def categories_list():
    require_scope('read')
    return jsonify({'categories': core().list_categories()})


@api_v1_bp.post('/categories')
def categories_create():
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().create_category(body.get('name'), body.get('color', '#6366f1'),
                                          body.get('icon', 'tag'))), 201


@api_v1_bp.get('/rules')
def rules_list():
    require_scope('read')
    return jsonify({'rules': core().list_rules()})


@api_v1_bp.post('/rules')
def rules_create():
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().create_rule(body.get('pattern'), body.get('category_id'),
                                      body.get('match_type', 'contains'),
                                      body.get('priority', 0))), 201


@api_v1_bp.post('/rules/apply')
def rules_apply():
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().apply_rules(dry_run=body.get('dry_run', True),
                                      limit=body.get('limit', 1000)))


# ------------------------------------------------------------------ reports

REPORT_PARAMS = {
    'monthly': ('year', 'month'),
    'summary': ('start_date_str', 'end_date_str'),
    'comprehensive': ('start_date_str', 'end_date_str'),
    'trends': ('months',),
    'category_trends': ('category_id', 'months'),
    'merchants': ('limit', 'days', 'year', 'month'),
    'dashboard': (),
    'recurring': (),
    'categories': (),
    'balance_breakdown': (),
    'income_sources': ('period',),
    'savings_rate': ('period',),
}


@api_v1_bp.get('/reports/<name>')
def reports(name):
    require_scope('read')
    if name not in REPORT_PARAMS:
        raise NotFound(f"Unknown report {name!r}. Try: {', '.join(sorted(REPORT_PARAMS))}")
    params = {}
    for key in REPORT_PARAMS[name]:
        if key in request.args:
            raw = request.args.get(key)
            params[key] = int(raw) if raw.lstrip('-').isdigit() else raw
    return jsonify({'report': name, 'data': core().report(name, **params)})


@api_v1_bp.get('/net-worth')
def net_worth():
    require_scope('read')
    return jsonify(core().net_worth())


@api_v1_bp.get('/forecast')
def forecast():
    require_scope('read')
    return jsonify(core().cashflow_forecast(request.args.get('days', 30, type=int)))


# ------------------------------------------------------------------ budgets & goals

@api_v1_bp.get('/budgets')
def budgets_list():
    require_scope('read')
    return jsonify({'budgets': core().list_budgets(
        request.args.get('year', type=int), request.args.get('month', type=int))})


@api_v1_bp.post('/budgets')
def budgets_create():
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().set_budget(body.get('category_id'), body.get('amount'),
                                     body.get('year'), body.get('month'))), 201


@api_v1_bp.delete('/budgets/<int:budget_id>')
def budgets_delete(budget_id):
    require_scope('write')
    return jsonify(core().delete_budget(budget_id))


@api_v1_bp.get('/goals')
def goals_list():
    require_scope('read')
    return jsonify({'goals': core().list_goals()})


@api_v1_bp.post('/goals')
def goals_create():
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().create_goal(body.get('name'), body.get('target_amount'),
                                      body.get('deadline'), body.get('account_id'),
                                      body.get('category_id'), body.get('notes'),
                                      body.get('currency', 'GBP'))), 201


@api_v1_bp.patch('/goals/<int:goal_id>')
def goals_patch(goal_id):
    require_scope('write')
    return jsonify(core().update_goal(goal_id, request.get_json(silent=True) or {}))


@api_v1_bp.post('/goals/<int:goal_id>/contributions')
def goals_contrib(goal_id):
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().contribute_to_goal(goal_id, body.get('amount'),
                                             body.get('note'), body.get('date'))), 201


# ------------------------------------------------------------------ dashboards

@api_v1_bp.get('/pages')
def pages_list():
    require_scope('read')
    return jsonify({'pages': core().list_pages()})


@api_v1_bp.post('/pages')
def pages_create():
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().create_page(body.get('name'), body.get('slug'),
                                      body.get('icon', 'layout-dashboard'),
                                      body.get('description'))), 201


@api_v1_bp.get('/pages/<page_ref>')
def pages_get(page_ref):
    require_scope('read')
    return jsonify(core().render_page(page_ref))


@api_v1_bp.patch('/pages/<int:page_id>')
def pages_patch(page_id):
    require_scope('write')
    return jsonify(core().update_page(page_id, request.get_json(silent=True) or {}))


@api_v1_bp.delete('/pages/<int:page_id>')
def pages_delete(page_id):
    require_scope('write')
    return jsonify(core().delete_page(page_id))


@api_v1_bp.post('/pages/<int:page_id>/widgets')
def widgets_create(page_id):
    require_scope('write')
    body = request.get_json(silent=True) or {}
    return jsonify(core().add_widget(page_id, body.get('type', 'metric'),
                                     body.get('title', 'Widget'), body.get('config') or {},
                                     body.get('layout') or {}, body.get('position'))), 201


@api_v1_bp.patch('/widgets/<int:widget_id>')
def widgets_patch(widget_id):
    require_scope('write')
    return jsonify(core().update_widget(widget_id, request.get_json(silent=True) or {}))


@api_v1_bp.delete('/widgets/<int:widget_id>')
def widgets_delete(widget_id):
    require_scope('write')
    return jsonify(core().delete_widget(widget_id))


# ------------------------------------------------------------------ imports

@api_v1_bp.post('/imports/preview')
def imports_preview():
    require_scope('import')
    body = request.get_json(silent=True) or {}
    account_id = body.get('account_id')
    bank_hint = body.get('bank_hint')

    if 'file' in request.files:
        upload = request.files['file']
        suffix = os.path.splitext(secure_filename(upload.filename or ''))[1] or '.pdf'
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        upload.save(path)
        try:
            return jsonify(core().import_preview(path, account_id=account_id,
                                                 bank_hint=bank_hint,
                                                 filename=upload.filename)), 201
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    source = body.get('path') or body.get('source') or body.get('url')
    if not source:
        raise ValidationError('Provide a file upload, or "path"/"url"/"content_base64"')
    return jsonify(core().import_preview(source, account_id=account_id,
                                         bank_hint=bank_hint,
                                         filename=body.get('filename'))), 201


@api_v1_bp.post('/imports/<int:batch_id>/commit')
def imports_commit(batch_id):
    require_scope('import')
    body = request.get_json(silent=True) or {}
    return jsonify(core().import_commit(batch_id, body.get('edits')))


@api_v1_bp.post('/imports/<int:batch_id>/revert')
def imports_revert(batch_id):
    require_scope('import')
    return jsonify(core().import_revert(batch_id))


@api_v1_bp.get('/imports')
def imports_list():
    require_scope('read')
    return jsonify({'imports': core().list_import_batches(
        request.args.get('limit', 20, type=int))})


# ------------------------------------------------------------------ bank connections

@api_v1_bp.get('/bank/connections')
def bank_connections():
    require_scope('read')
    return jsonify({'connections': core().list_bank_connections()})


@api_v1_bp.get('/bank/providers')
def bank_providers():
    require_scope('read')
    from app.banking import describe_providers
    return jsonify({'providers': describe_providers()})


@api_v1_bp.post('/bank/<provider>/link')
def bank_link(provider):
    require_scope('admin')
    body = request.get_json(silent=True) or {}
    from app.banking import get_provider
    service = get_provider(provider, core())
    return jsonify(vars(service.start_link(body.get('institution_id'),
                                           body.get('redirect_uri'), body.get('state'))))


@api_v1_bp.post('/bank/<provider>/callback')
def bank_callback(provider):
    require_scope('admin')
    body = request.get_json(silent=True) or {}
    from app.banking import get_provider
    from app.banking.sync import save_connection
    service = get_provider(provider, core())
    link = service.complete_link(body.get('code'), body.get('state'))
    return jsonify(save_connection(core(), link))


@api_v1_bp.post('/bank/sync')
def bank_sync():
    require_scope('import')
    body = request.get_json(silent=True) or {}
    from app.banking.sync import sync_all, sync_connection
    if body.get('connection_id'):
        return jsonify(sync_connection(core(), body['connection_id']))
    return jsonify(sync_all(core()))


@api_v1_bp.delete('/bank/connections/<int:connection_id>')
def bank_revoke(connection_id):
    require_scope('admin')
    from app.banking.sync import revoke_connection
    return jsonify(revoke_connection(core(), connection_id))


# ------------------------------------------------------------------ audit & tokens

@api_v1_bp.get('/audit')
def audit_list():
    require_scope('read')
    return jsonify({'audit': core().list_audit(request.args.get('limit', 50, type=int))})


@api_v1_bp.get('/tokens')
def tokens_list():
    require_scope('admin')
    return jsonify({'tokens': [t.to_safe_dict() for t in list_agent_tokens(g.current_user.id)]})


@api_v1_bp.post('/tokens')
def tokens_create():
    require_scope('admin')
    body = request.get_json(silent=True) or {}
    scopes = body.get('scopes') or ['read']
    if isinstance(scopes, str):
        scopes = [s for s in scopes.split(',') if s]
    invalid = [s for s in scopes if s not in SCOPES]
    if invalid:
        raise ValidationError(f'Unknown scopes: {invalid}; valid: {list(SCOPES)}')
    raw, token = create_agent_token(g.current_user.id, body.get('name', 'agent'),
                                    scopes, body.get('expires_days'))
    return jsonify({'token': raw, 'record': token.to_safe_dict(),
                    'note': 'Store this token now; it is not shown again.'}), 201


@api_v1_bp.delete('/tokens/<int:token_id>')
def tokens_revoke(token_id):
    require_scope('admin')
    if not revoke_agent_token(token_id, g.current_user.id):
        raise NotFound(f'Token {token_id} not found')
    return jsonify({'id': token_id, 'revoked': True})


# ------------------------------------------------------------------ agent setup

def _mcp_port() -> int:
    try:
        return int(os.getenv('TALLY_MCP_PORT', '8765'))
    except ValueError:
        return 8765


def _mcp_enabled() -> bool:
    return os.getenv('TALLY_MCP_HTTP', '0').lower() in ('1', 'true', 'yes')


@api_v1_bp.get('/agent/setup')
def agent_setup():
    """Everything the UI needs to connect an agent, computed from this request."""
    host = (request.host or '').split(':')[0] or '127.0.0.1'
    scheme = 'https' if request.is_secure or os.getenv('TALLY_HTTPS', '0').lower() in ('1', 'true') else 'http'
    port = _mcp_port()
    base = f'{scheme}://{host}:{port}'
    from app.ocr_service import ocr_service
    from app.skill import skill_text
    return jsonify({
        'mcp': {
            'enabled': _mcp_enabled(),
            'transport': 'streamable-http',
            'auth': 'bearer',
            'url': f'{base}/mcp',
            'health': f'{base}/health',
            'port': port,
        },
        'skill': {
            'name': 'tally',
            'version': '2.0.0',
            'url': '/api/v1/agent/skill',
            'install': {
                'claude': 'tally skill install --target claude',
                'hermes': 'tally skill install --target hermes',
                'cursor': 'tally skill install --target cursor',
            },
        },
        'tokens': [t.to_safe_dict() for t in list_agent_tokens(g.current_user.id)],
        'ocr': ocr_service.status(),
        'skill_preview': skill_text()[:400],
    })


@api_v1_bp.get('/agent/skill')
def agent_skill():
    """The SKILL.md an agent harness installs or pastes into its prompt."""
    from app.skill import skill_text
    return Response(
        skill_text(), mimetype='text/markdown',
        headers={'Content-Disposition': 'attachment; filename="SKILL.md"'},
    )


@api_v1_bp.post('/agent/test')
def agent_test():
    """Server-side probe of the MCP port, so the browser never needs CORS."""
    import requests as http
    url = f'http://127.0.0.1:{_mcp_port()}/health'
    try:
        response = http.get(url, timeout=3)
        payload = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
        return jsonify({'ok': response.ok, 'status': response.status_code, 'detail': payload})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc),
                        'hint': 'Set TALLY_MCP_HTTP=1 and publish port 8765.'})


# ------------------------------------------------------------------ openapi

@api_v1_bp.get('/openapi.json')
def openapi():
    return jsonify(OPENAPI)


# ------------------------------------------------------------------ spec

def _op(summary, tag, params=None, body=None):
    op = {
        'summary': summary,
        'tags': [tag],
        'responses': {
            '200': {'description': 'OK'},
            '400': {'description': 'Validation error'},
            '401': {'description': 'Unauthenticated'},
            '403': {'description': 'Missing scope'},
        },
    }
    if params:
        op['parameters'] = [
            {'name': n, 'in': 'query', 'required': False, 'schema': {'type': t}}
            for n, t in params
        ]
    if body:
        op['requestBody'] = {'required': True,
                             'content': {'application/json': {'schema': body}}}
    return op


OPENAPI = {
    'openapi': '3.1.0',
    'info': {
        'title': 'Tally Local — Agent API',
        'version': '1.0.0',
        'description': (
            'Local-first personal finance for agents. Amounts are positive numbers with a '
            'transaction_type of income/expense. Raw bank rows are immutable; edits are stored '
            'as annotations and every write is audited. Authenticate with a scoped bearer token '
            'created via `tally token create`.'
        ),
    },
    'servers': [{'url': '/api/v1'}],
    'security': [{'bearerAuth': []}],
    'tags': [{'name': 'read'}, {'name': 'write'}, {'name': 'import'}, {'name': 'admin'}],
    'components': {
        'securitySchemes': {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}},
        'schemas': {
            'Transaction': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'integer'}, 'date': {'type': 'string', 'format': 'date-time'},
                    'description': {'type': 'string'}, 'amount': {'type': 'number'},
                    'type': {'type': 'string', 'enum': ['income', 'expense', 'transfer']},
                    'category_id': {'type': ['integer', 'null']},
                    'category_name': {'type': ['string', 'null']},
                    'account_id': {'type': 'integer'}, 'notes': {'type': ['string', 'null']},
                    'tags': {'type': ['array', 'null'], 'items': {'type': 'string'}},
                    'source': {'type': 'string'}, 'edited': {'type': 'boolean'},
                },
            },
            'Error': {
                'type': 'object',
                'properties': {'error': {'type': 'object', 'properties': {
                    'code': {'type': 'string'}, 'message': {'type': 'string'}}}},
            },
            'Goal': {
                'type': 'object',
                'properties': {'name': {'type': 'string'}, 'target_amount': {'type': 'number'},
                               'deadline': {'type': 'string', 'format': 'date'},
                               'account_id': {'type': ['integer', 'null']}},
                'required': ['name', 'target_amount'],
            },
            'Widget': {
                'type': 'object',
                'properties': {
                    'type': {'type': 'string', 'enum': ['metric', 'chart', 'list', 'budget',
                                                        'goal', 'progress', 'text']},
                    'title': {'type': 'string'}, 'config': {'type': 'object'},
                    'layout': {'type': 'object'}, 'position': {'type': 'integer'},
                },
                'required': ['type', 'title'],
            },
        },
    },
    'paths': {
        '/context': {'get': _op('One-call snapshot: balances, cashflow, budgets, alerts', 'read',
                                [('period', 'string')])},
        '/accounts': {
            'get': _op('List accounts with balances', 'read'),
            'post': _op('Create an account', 'write', body={'type': 'object'}),
        },
        '/transactions': {
            'get': _op('Query transactions', 'read', [
                ('account_id', 'string'), ('category_id', 'string'), ('from', 'string'),
                ('to', 'string'), ('q', 'string'), ('limit', 'integer'), ('offset', 'integer'),
                ('group_by', 'string'), ('uncategorised', 'boolean')]),
            'post': _op('Record a manual transaction', 'write', body={'type': 'object'}),
        },
        '/transactions/{id}': {
            'patch': _op('Annotate a transaction (raw bank fields stay immutable)', 'write',
                         body={'type': 'object'}),
            'delete': _op('Soft-delete a transaction (reversible)', 'write',
                          [('confirm', 'boolean')]),
        },
        '/transactions/{id}/undo': {'post': _op('Undo the last change group', 'write')},
        '/transactions/bulk': {'post': _op('Bulk annotate up to 500 rows', 'write',
                                           body={'type': 'object'})},
        '/categories': {
            'get': _op('List categories', 'read'),
            'post': _op('Create a category', 'write', body={'type': 'object'}),
        },
        '/rules': {
            'get': _op('List categorisation rules', 'read'),
            'post': _op('Create a rule', 'write', body={'type': 'object'}),
        },
        '/reports/{name}': {'get': _op('Run a named report', 'read', [('period', 'string')])},
        '/net-worth': {'get': _op('Cash, assets and wallets', 'read')},
        '/forecast': {'get': _op('Forward cashflow projection', 'read', [('days', 'integer')])},
        '/budgets': {
            'get': _op('Budgets with actual spend', 'read'),
            'post': _op('Create or update a budget', 'write', body={'type': 'object'}),
        },
        '/goals': {
            'get': _op('Goals with progress', 'read'),
            'post': _op('Create a goal', 'write', body={'$ref': '#/components/schemas/Goal'}),
        },
        '/pages': {
            'get': _op('List dashboard pages', 'read'),
            'post': _op('Create a dashboard page', 'write', body={'type': 'object'}),
        },
        '/pages/{ref}': {'get': _op('Render a dashboard page with resolved widget data', 'read')},
        '/pages/{id}/widgets': {'post': _op('Add a widget to a page', 'write',
                                            body={'$ref': '#/components/schemas/Widget'})},
        '/widgets/{id}': {
            'patch': _op('Update a widget', 'write', body={'type': 'object'}),
            'delete': _op('Delete a widget', 'write'),
        },
        '/imports/preview': {'post': _op('Parse a statement into a reviewable batch', 'import',
                                         body={'type': 'object'})},
        '/imports/{id}/commit': {'post': _op('Commit a previewed batch', 'import')},
        '/imports/{id}/revert': {'post': _op('Revert a committed batch', 'import')},
        '/bank/connections': {'get': _op('List bank connections', 'read')},
        '/bank/sync': {'post': _op('Sync one or all bank connections', 'import')},
        '/audit': {'get': _op('Recent agent actions', 'read')},
        '/tokens': {
            'get': _op('List tokens (never returns secrets)', 'admin'),
            'post': _op('Create a scoped token', 'admin', body={'type': 'object'}),
        },
    },
}
