"""TallyCore — the one place business logic lives.

The HTTP API, the CLI and the MCP server are all thin adapters over this class.
Nothing here imports Flask, so it is safe to call from a shell, a test, or an
agent process while the web UI is running.

Design rule: `transactions` is the immutable ledger. Annotations live in
`transaction_overrides` and every change is appended to `transaction_edits`.
Reads go through `v_transactions_effective`, which merges the two.
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload

from app.database import DB_PATH, activate_db, get_session
from app.models import (
    Account, AccountType, AgentAudit, Asset, AssetHolding, AssetPriceCache,
    BankConnection, Category, DashboardPage, DashboardWidget, Goal,
    GoalContribution, ImportBatch, ImportRow, Institution, MonthlyBudget,
    Transaction, TransactionEdit, TransactionOverride, TransactionRule,
    TransactionType, TransactionView, Wallet,
)


# ---------------------------------------------------------------- errors

class TallyError(Exception):
    status = 400
    code = 'bad_request'


class NotFound(TallyError):
    status = 404
    code = 'not_found'


class ValidationError(TallyError):
    status = 400
    code = 'validation_error'


class Conflict(TallyError):
    status = 409
    code = 'conflict'


class PermissionDenied(TallyError):
    status = 403
    code = 'permission_denied'


class Unauthenticated(TallyError):
    status = 401
    code = 'unauthenticated'


class RawRecordLocked(TallyError):
    status = 409
    code = 'raw_record_locked'


# ---------------------------------------------------------------- helpers

OVERLAY_FIELDS = ('category_id', 'description', 'notes', 'tags', 'merchant',
                  'is_excluded', 'is_recurring', 'is_one_off')
# Fields that may be edited in place, and only for rows Tally owns (manual/agent).
RAW_EDITABLE_FIELDS = ('date', 'amount', 'transaction_type', 'account_id', 'balance_after')


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _parse_dt(value, field='date'):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        text = str(value).strip().replace('Z', '+00:00')
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d %b %y', '%d %b %Y'):
            try:
                return datetime.strptime(str(value).strip(), fmt)
            except ValueError:
                continue
    raise ValidationError(f"{field} must be an ISO date, got {value!r}")


def _money(value, field='amount'):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number, got {value!r}")


def _month_bounds(year: int, month: int):
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


def _period_start(period: str, now: Optional[datetime] = None) -> datetime:
    now = now or datetime.utcnow()
    period = (period or '30d').strip().lower()
    match = re.fullmatch(r'(\d+)\s*(d|day|days|m|month|months|y|year|years)', period)
    if period in ('mtd', 'month_to_date'):
        return datetime(now.year, now.month, 1)
    if period in ('ytd', 'year_to_date'):
        return datetime(now.year, 1, 1)
    if period in ('all', 'max'):
        return datetime(1970, 1, 1)
    if match:
        n, unit = int(match.group(1)), match.group(2)[0]
        if unit == 'd':
            return now - timedelta(days=n)
        if unit == 'm':
            return now - timedelta(days=30 * n)
        return now - timedelta(days=365 * n)
    if period.isdigit():
        return now - timedelta(days=int(period))
    raise ValidationError(f"Unknown period {period!r} (use 30d, 3m, ytd, mtd, all)")


def _tx_public(t: TransactionView, include_raw: bool = False) -> Dict[str, Any]:
    data = {
        'id': t.id,
        'date': _iso(t.date),
        'description': t.description,
        'amount': t.amount,
        'type': t.transaction_type.value if t.transaction_type else None,
        'category_id': t.category_id,
        'category_name': t.category.name if t.category else None,
        'account_id': t.account_id,
        'account_name': t.account.name if t.account else None,
        'notes': t.notes,
        'tags': [x for x in (t.tags or '').split(',') if x] or None,
        'is_internal_transfer': bool(t.is_internal_transfer),
        'is_recurring': bool(t.is_recurring),
        'is_one_off': bool(t.is_one_off),
        'pending': bool(t.pending),
        'currency': t.currency or (t.account.currency if t.account else None),
        'source': t.source,
        'external_id': t.external_id,
        'edited': bool(t.is_edited),
        'counterparty': t.counterparty,
    }
    if include_raw:
        data.update({
            'original_description': t.original_description,
            'balance_after': t.balance_after,
            'merchant_normalized': t.merchant_normalized,
            'import_batch_id': t.import_batch_id,
            'value_date': _iso(t.value_date),
            'fx_rate': t.fx_rate,
            'amount_base': t.amount_base,
            'raw_json': json.loads(t.raw_json) if t.raw_json else None,
        })
    return data


class TallyCore:
    def __init__(self, db_path: Optional[str] = None, actor: str = 'user',
                 token_id: Optional[int] = None):
        self.db_path = db_path or DB_PATH
        self.actor = actor
        self.token_id = token_id

    # ------------------------------------------------------------ plumbing

    @contextmanager
    def _db(self):
        with activate_db(self.db_path):
            session = get_session(self.db_path)
            try:
                yield session
            finally:
                session.close()

    def audit(self, tool: str, args: Optional[dict] = None, summary: Optional[str] = None,
              status: str = 'ok', duration_ms: Optional[int] = None, dry_run: bool = False,
              session=None):
        own = session is None
        session = session or get_session(self.db_path)
        try:
            session.add(AgentAudit(
                token_id=self.token_id, actor=self.actor, tool=tool[:80],
                args_json=json.dumps(args or {}, default=str)[:4000],
                result_summary=(summary or '')[:2000], status=status,
                duration_ms=duration_ms, dry_run=dry_run,
            ))
            session.commit()
        except Exception:
            session.rollback()
        finally:
            if own:
                session.close()

    def list_audit(self, limit: int = 50) -> List[dict]:
        with self._db() as s:
            rows = s.query(AgentAudit).order_by(AgentAudit.id.desc()).limit(min(limit, 500)).all()
            return [{'id': r.id, 'actor': r.actor, 'tool': r.tool,
                     'args': json.loads(r.args_json) if r.args_json else {},
                     'summary': r.result_summary, 'status': r.status,
                     'dry_run': bool(r.dry_run), 'created_at': _iso(r.created_at)} for r in rows]

    # ------------------------------------------------------------ accounts

    def _account_balance(self, session, account: Account) -> float:
        income, expense = session.query(
            func.coalesce(func.sum(Transaction.amount), 0.0)
        ).filter(
            Transaction.account_id == account.id,
            Transaction.transaction_type == TransactionType.INCOME,
        ).scalar(), session.query(
            func.coalesce(func.sum(Transaction.amount), 0.0)
        ).filter(
            Transaction.account_id == account.id,
            Transaction.transaction_type == TransactionType.EXPENSE,
        ).scalar()
        return round((account.opening_balance or 0.0) + float(income or 0) - float(expense or 0), 2)

    def list_accounts(self, include_balances: bool = True, include_archived: bool = False) -> List[dict]:
        with self._db() as s:
            query = s.query(Account)
            if not include_archived:
                query = query.filter(Account.is_archived.is_(False))
            out = []
            for a in query.order_by(Account.id).all():
                computed = self._account_balance(s, a)
                balance = a.balance_current if a.balance_current is not None else computed
                item = {
                    'id': a.id, 'name': a.name,
                    'type': a.account_type.value if a.account_type else None,
                    'currency': a.currency or 'GBP',
                    'opening_balance': a.opening_balance,
                    'balance': round(balance, 2) if include_balances else None,
                    'balance_computed': computed if include_balances else None,
                    'balance_source': a.balance_source,
                    'balance_synced_at': _iso(a.balance_synced_at),
                    'iban_masked': a.iban_masked,
                    'connection_id': a.connection_id,
                    'is_archived': bool(a.is_archived),
                }
                out.append({k: v for k, v in item.items() if v is not None})
            return out

    def get_account(self, account_id: int) -> dict:
        accounts = [a for a in self.list_accounts(include_archived=True) if a['id'] == account_id]
        if not accounts:
            raise NotFound(f'Account {account_id} not found')
        return accounts[0]

    def create_account(self, name: str, account_type: str = 'checking', currency: str = 'GBP',
                       opening_balance: float = 0.0, description: str = None,
                       colour: str = None, icon: str = None) -> dict:
        if not name or not name.strip():
            raise ValidationError('Account name is required')
        try:
            acc_type = AccountType(account_type)
        except ValueError:
            raise ValidationError(f"Unknown account type {account_type!r}")
        with self._db() as s:
            account = Account(name=name.strip()[:100], account_type=acc_type,
                              currency=(currency or 'GBP').upper()[:3],
                              opening_balance=_money(opening_balance, 'opening_balance'),
                              description=description, color=colour or '#10b981',
                              icon=icon or 'wallet')
            s.add(account)
            s.commit()
            s.refresh(account)
            return {'id': account.id, 'name': account.name, 'currency': account.currency,
                    'type': account.account_type.value}

    def update_account(self, account_id: int, patch: dict) -> dict:
        with self._db() as s:
            account = s.get(Account, account_id)
            if not account:
                raise NotFound(f'Account {account_id} not found')
            for field in ('name', 'description', 'color', 'icon', 'currency'):
                if field in patch:
                    setattr(account, field, patch[field])
            if 'account_type' in patch:
                account.account_type = AccountType(patch['account_type'])
            if 'opening_balance' in patch:
                account.opening_balance = _money(patch['opening_balance'], 'opening_balance')
            if 'is_archived' in patch:
                account.is_archived = bool(patch['is_archived'])
            s.commit()
        return self.get_account(account_id)

    # ------------------------------------------------------------ transactions

    def _tx_query(self, session):
        """Transactions with their category/account eager-loaded.

        Without this, serialising after the session closes raises
        DetachedInstanceError — and agents would get a 500 instead of data.
        """
        return session.query(TransactionView).options(
            joinedload(TransactionView.category),
            joinedload(TransactionView.account),
        )

    def _filter_query(self, query, filters: dict):
        f = filters or {}
        query = query.options(joinedload(TransactionView.category),
                              joinedload(TransactionView.account))
        if f.get('account_ids'):
            query = query.filter(TransactionView.account_id.in_([int(x) for x in f['account_ids']]))
        if f.get('category_ids'):
            query = query.filter(TransactionView.category_id.in_([int(x) for x in f['category_ids']]))
        if f.get('date_from'):
            query = query.filter(TransactionView.date >= _parse_dt(f['date_from'], 'date_from'))
        if f.get('date_to'):
            query = query.filter(TransactionView.date <= _parse_dt(f['date_to'], 'date_to'))
        if f.get('types'):
            query = query.filter(TransactionView.transaction_type.in_(
                [TransactionType(t) for t in f['types']]))
        if f.get('min_amount') is not None:
            query = query.filter(TransactionView.amount >= _money(f['min_amount']))
        if f.get('max_amount') is not None:
            query = query.filter(TransactionView.amount <= _money(f['max_amount']))
        if f.get('text'):
            needle = f"%{f['text'].strip()}%"
            query = query.filter(or_(TransactionView.description.ilike(needle),
                                     TransactionView.counterparty.ilike(needle)))
        if f.get('tags'):
            for tag in f['tags']:
                query = query.filter(TransactionView.tags.ilike(f'%{tag}%'))
        if f.get('pending') is not None:
            query = query.filter(TransactionView.pending.is_(bool(f['pending'])))
        if f.get('source'):
            query = query.filter(TransactionView.source == f['source'])
        if f.get('uncategorised'):
            query = query.filter(TransactionView.category_id.is_(None))
        if not f.get('include_transfers', False):
            query = query.filter(or_(TransactionView.is_internal_transfer.is_(False),
                                     TransactionView.is_internal_transfer.is_(None)))
        return query

    def list_transactions(self, filters: Optional[dict] = None, limit: int = 50,
                          offset: int = 0, order: str = 'date_desc',
                          group_by: Optional[str] = None) -> dict:
        limit = max(1, min(int(limit or 50), 500))
        offset = max(0, int(offset or 0))
        filters = dict(filters or {})
        filters.setdefault('include_transfers', True)
        with self._db() as s:
            if group_by:
                return self._group_transactions(s, filters, group_by, limit)
            query = self._filter_query(self._tx_query(s), filters)
            total = query.count()
            order_map = {
                'date_desc': TransactionView.date.desc(),
                'date_asc': TransactionView.date.asc(),
                'amount_desc': TransactionView.amount.desc(),
                'amount_asc': TransactionView.amount.asc(),
            }
            query = query.order_by(order_map.get(order, TransactionView.date.desc()),
                                   TransactionView.id.desc())
            rows = query.offset(offset).limit(limit).all()
            return {'transactions': [_tx_public(t) for t in rows], 'total': total,
                    'limit': limit, 'offset': offset, 'has_more': offset + len(rows) < total}

    def _group_transactions(self, session, filters: dict, group_by: str, limit: int) -> dict:
        group_by = group_by.lower()
        if group_by not in ('category', 'merchant', 'month', 'account', 'tag', 'type'):
            raise ValidationError('group_by must be category|merchant|month|account|tag|type')
        filters = dict(filters or {})
        filters['include_transfers'] = False
        base = self._filter_query(session.query(TransactionView), filters)
        base = base.filter(TransactionView.transaction_type == TransactionType.EXPENSE)
        if group_by == 'category':
            col = TransactionView.category_id
            name_col = None
        elif group_by == 'merchant':
            col = func.lower(TransactionView.description)
            name_col = None
        elif group_by == 'month':
            col = func.strftime('%Y-%m', TransactionView.date)
            name_col = None
        elif group_by == 'account':
            col = TransactionView.account_id
            name_col = None
        elif group_by == 'tag':
            col = TransactionView.tags
            name_col = None
        else:
            col = TransactionView.transaction_type
            name_col = None
        rows = base.with_entities(
            col.label('key'), func.sum(TransactionView.amount).label('total'),
            func.count(TransactionView.id).label('count'),
        ).group_by(col).order_by(func.sum(TransactionView.amount).desc()).limit(limit).all()

        categories = {c.id: c.name for c in session.query(Category).all()}
        accounts = {a.id: a.name for a in session.query(Account).all()}
        out = []
        for key, total, count in rows:
            label = key
            if group_by == 'category':
                label = categories.get(key, 'Uncategorised')
            elif group_by == 'account':
                label = accounts.get(key, str(key))
            out.append({'key': label, 'total': round(float(total or 0), 2), 'count': count})
        return {'group_by': group_by, 'groups': out,
                'total': round(sum(g['total'] for g in out), 2)}

    def get_transaction(self, transaction_id: int, include_raw: bool = True) -> dict:
        with self._db() as s:
            t = self._tx_query(s).filter(TransactionView.id == transaction_id).first()
            if not t:
                deleted = s.query(Transaction, TransactionOverride).outerjoin(
                    TransactionOverride, TransactionOverride.transaction_id == Transaction.id
                ).filter(Transaction.id == transaction_id).first()
                if deleted and deleted[1] and deleted[1].is_excluded:
                    raise NotFound(f'Transaction {transaction_id} is deleted (use restore)')
                raise NotFound(f'Transaction {transaction_id} not found')
            return _tx_public(t, include_raw=include_raw)

    def create_transaction(self, description: str, amount: float, date_value=None,
                           account_id: Optional[int] = None, category_id: Optional[int] = None,
                           transaction_type: str = 'expense', notes: str = None,
                           tags: str = None, source: str = 'manual') -> dict:
        if not description or not str(description).strip():
            raise ValidationError('Description is required')
        amount = abs(_money(amount))
        if transaction_type not in ('income', 'expense'):
            raise ValidationError('transaction_type must be income or expense')
        when = _parse_dt(date_value) or datetime.utcnow()
        with self._db() as s:
            if account_id is None:
                account = s.query(Account).filter(Account.is_archived.is_(False)).first()
                if not account:
                    raise ValidationError('No account exists; create one first')
                account_id = account.id
            elif not s.get(Account, account_id):
                raise NotFound(f'Account {account_id} not found')
            if category_id and not s.get(Category, category_id):
                raise NotFound(f'Category {category_id} not found')
            t = Transaction(
                date=when, description=str(description).strip()[:500],
                original_description=str(description).strip()[:500], amount=amount,
                transaction_type=TransactionType(transaction_type), account_id=account_id,
                category_id=category_id, notes=notes, tags=tags, source=source,
                currency=s.get(Account, account_id).currency,
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            tx_id = t.id
        self.audit('add_transaction', {'description': description, 'amount': amount},
                   f'created transaction {tx_id}')
        return self.get_transaction(tx_id)

    def update_transaction(self, transaction_id: int, patch: dict, dry_run: bool = False) -> dict:
        """Annotate a transaction. Raw bank rows are never modified in place."""
        patch = {k: v for k, v in (patch or {}).items() if v is not ...}
        if not patch:
            raise ValidationError('No fields to update')
        with self._db() as s:
            raw = s.get(Transaction, transaction_id)
            if not raw:
                raise NotFound(f'Transaction {transaction_id} not found')

            raw_patch = {k: patch[k] for k in RAW_EDITABLE_FIELDS if k in patch}
            overlay_patch = {k: patch[k] for k in OVERLAY_FIELDS if k in patch}
            if raw_patch and raw.is_raw_locked:
                raise RawRecordLocked(
                    'This transaction came from a bank or statement and is read-only. '
                    'Edit the annotation fields (category, description, notes, tags) instead.')
            if dry_run:
                return {'dry_run': True, 'transaction_id': transaction_id,
                        'would_update_raw': sorted(raw_patch), 'would_update_annotations': sorted(overlay_patch)}

            group = str(uuid.uuid4())
            override = raw.override
            if overlay_patch and override is None:
                override = TransactionOverride(transaction_id=raw.id)
                s.add(override)
                s.flush()

            for field, new_value in overlay_patch.items():
                old_effective = getattr(override, field) if override is not None else None
                if old_effective is None:
                    old_effective = getattr(raw, field)
                if field == 'category_id' and new_value is not None:
                    if not s.get(Category, int(new_value)):
                        raise NotFound(f'Category {new_value} not found')
                if field == 'description' and new_value is not None:
                    new_value = str(new_value).strip()[:500]
                setattr(override, field, new_value)
                s.add(TransactionEdit(transaction_id=raw.id, change_group=group,
                                      field=field, old_value=_json_value(old_effective),
                                      new_value=_json_value(new_value), actor=self.actor))

            for field, new_value in raw_patch.items():
                if field == 'amount':
                    new_value = abs(_money(new_value))
                if field == 'date':
                    new_value = _parse_dt(new_value)
                if field == 'transaction_type':
                    new_value = TransactionType(new_value)
                if field == 'account_id' and not s.get(Account, int(new_value)):
                    raise NotFound(f'Account {new_value} not found')
                old_value = getattr(raw, field)
                setattr(raw, field, new_value)
                s.add(TransactionEdit(transaction_id=raw.id, change_group=group,
                                      field=field, old_value=_json_value(old_value),
                                      new_value=_json_value(new_value), actor=self.actor))

            if override is not None:
                override.updated_by = self.actor
            s.commit()

        self.audit('update_transaction', {'id': transaction_id, 'fields': sorted(patch)},
                   f'updated transaction {transaction_id}', dry_run=dry_run)
        return self.get_transaction(transaction_id)

    def delete_transaction(self, transaction_id: int, confirm: bool = False) -> dict:
        if not confirm:
            raise ValidationError('Pass confirm=true to delete (it is reversible with restore)')
        with self._db() as s:
            raw = s.get(Transaction, transaction_id)
            if not raw:
                raise NotFound(f'Transaction {transaction_id} not found')
            override = raw.override or TransactionOverride(transaction_id=raw.id)
            override.is_excluded = True
            override.updated_by = self.actor
            s.add(override)
            s.add(TransactionEdit(transaction_id=raw.id, change_group=str(uuid.uuid4()),
                                  field='is_excluded', old_value='false', new_value='true',
                                  actor=self.actor))
            s.commit()
        self.audit('delete_transaction', {'id': transaction_id}, f'hid transaction {transaction_id}')
        return {'id': transaction_id, 'deleted': True, 'reversible': True}

    def restore_transaction(self, transaction_id: int) -> dict:
        with self._db() as s:
            raw = s.get(Transaction, transaction_id)
            if not raw:
                raise NotFound(f'Transaction {transaction_id} not found')
            override = raw.override
            if not override or not override.is_excluded:
                raise Conflict('Transaction is not deleted')
            override.is_excluded = False
            s.add(TransactionEdit(transaction_id=raw.id, change_group=str(uuid.uuid4()),
                                  field='is_excluded', old_value='true', new_value='false',
                                  actor=self.actor))
            s.commit()
        self.audit('restore_transaction', {'id': transaction_id}, f'restored {transaction_id}')
        return self.get_transaction(transaction_id)

    def undo(self, transaction_id: int, change_group: Optional[str] = None) -> dict:
        """Revert the last change (or a specific change group) for a transaction."""
        with self._db() as s:
            query = s.query(TransactionEdit).filter(TransactionEdit.transaction_id == transaction_id)
            if change_group:
                edits = query.filter(TransactionEdit.change_group == change_group).all()
            else:
                last = query.order_by(TransactionEdit.id.desc()).first()
                if not last:
                    raise NotFound('No edits to undo for this transaction')
                edits = query.filter(TransactionEdit.change_group == last.change_group).all()
            if not edits:
                raise NotFound('Change group not found')

            raw = s.get(Transaction, transaction_id)
            override = raw.override or TransactionOverride(transaction_id=raw.id)
            reverse_group = str(uuid.uuid4())
            for edit in edits:
                old = _from_json_value(edit.old_value)
                if edit.field in OVERLAY_FIELDS:
                    setattr(override, edit.field, old)
                elif edit.field in RAW_EDITABLE_FIELDS:
                    if raw.is_raw_locked:
                        continue
                    if edit.field == 'date':
                        old = _parse_dt(old)
                    elif edit.field == 'amount':
                        old = abs(_money(old))
                    elif edit.field == 'transaction_type':
                        old = TransactionType(old)
                    setattr(raw, edit.field, old)
                s.add(TransactionEdit(transaction_id=raw.id, change_group=reverse_group,
                                      field=edit.field, old_value=edit.new_value,
                                      new_value=edit.old_value, actor=self.actor))
            s.add(override)
            s.commit()
        self.audit('undo', {'transaction_id': transaction_id, 'group': change_group}, 'undid change')
        return self.get_transaction(transaction_id)

    def bulk_update(self, transaction_ids: Iterable[int], patch: dict) -> dict:
        ids = [int(x) for x in transaction_ids]
        if not ids:
            raise ValidationError('transaction_ids is required')
        if len(ids) > 500:
            raise ValidationError('At most 500 transactions per batch')
        group = str(uuid.uuid4())
        updated, failed = [], []
        for tx_id in ids:
            try:
                with self._db() as s:
                    raw = s.get(Transaction, tx_id)
                    if not raw:
                        raise NotFound(f'Transaction {tx_id} not found')
                    overlay_patch = {k: patch[k] for k in OVERLAY_FIELDS if k in patch}
                    if not overlay_patch:
                        raise ValidationError('No annotation fields to update')
                    override = raw.override or TransactionOverride(transaction_id=raw.id)
                    for field, value in overlay_patch.items():
                        old_effective = getattr(override, field) if override is not None else None
                        if old_effective is None:
                            old_effective = getattr(raw, field)
                        setattr(override, field, value)
                        s.add(TransactionEdit(transaction_id=raw.id, change_group=group,
                                              field=field, old_value=_json_value(old_effective),
                                              new_value=_json_value(value), actor=self.actor))
                    s.add(override)
                    s.commit()
                updated.append(tx_id)
            except TallyError as exc:
                failed.append({'id': tx_id, 'error': str(exc)})
        self.audit('bulk_update', {'ids': len(ids), 'fields': sorted(patch)},
                   f'{len(updated)} updated, {len(failed)} failed')
        return {'updated': len(updated), 'failed': failed, 'change_group': group}

    def list_deleted(self, limit: int = 50) -> List[dict]:
        with self._db() as s:
            rows = s.query(Transaction, TransactionOverride).join(
                TransactionOverride, TransactionOverride.transaction_id == Transaction.id
            ).filter(TransactionOverride.is_excluded.is_(True)).order_by(
                Transaction.date.desc()).limit(min(limit, 500)).all()
            return [{'id': t.id, 'date': _iso(t.date), 'description': t.description,
                     'amount': t.amount, 'account_id': t.account_id} for t, _ in rows]

    # ------------------------------------------------------------ categories & rules

    def list_categories(self) -> List[dict]:
        with self._db() as s:
            return [{'id': c.id, 'name': c.name, 'color': c.color, 'icon': c.icon,
                     'is_system': bool(c.is_system)} for c in s.query(Category).order_by(Category.name).all()]

    def create_category(self, name: str, color: str = '#6366f1', icon: str = 'tag') -> dict:
        if not name or not name.strip():
            raise ValidationError('Category name is required')
        with self._db() as s:
            existing = s.query(Category).filter(func.lower(Category.name) == name.strip().lower()).first()
            if existing:
                raise Conflict(f'Category {name!r} already exists')
            category = Category(name=name.strip()[:100], color=color, icon=icon)
            s.add(category)
            s.commit()
            return {'id': category.id, 'name': category.name}

    def find_category(self, ref) -> Optional[int]:
        if ref in (None, ''):
            return None
        with self._db() as s:
            if isinstance(ref, int) or str(ref).isdigit():
                cat = s.get(Category, int(ref))
                return cat.id if cat else None
            cat = s.query(Category).filter(func.lower(Category.name) == str(ref).strip().lower()).first()
            return cat.id if cat else None

    def list_rules(self) -> List[dict]:
        with self._db() as s:
            return [{'id': r.id, 'pattern': r.pattern, 'match_type': r.match_type,
                     'category_id': r.category_id,
                     'category_name': r.category.name if r.category else None,
                     'is_active': bool(r.is_active), 'priority': r.priority}
                    for r in s.query(TransactionRule).order_by(TransactionRule.priority.desc()).all()]

    def create_rule(self, pattern: str, category_id, match_type: str = 'contains',
                    priority: int = 0) -> dict:
        if not pattern or not pattern.strip():
            raise ValidationError('Pattern is required')
        resolved = self.find_category(category_id)
        if not resolved:
            raise NotFound(f'Category {category_id} not found')
        with self._db() as s:
            rule = TransactionRule(pattern=pattern.strip()[:255], match_type=match_type,
                                   category_id=resolved, priority=int(priority))
            s.add(rule)
            s.commit()
            return {'id': rule.id, 'pattern': rule.pattern, 'category_id': resolved}

    def apply_rules(self, dry_run: bool = True, limit: int = 1000) -> dict:
        """Categorise rows that have no category yet, using description rules."""
        with self._db() as s:
            rules = s.query(TransactionRule).filter(TransactionRule.is_active.is_(True)) \
                .order_by(TransactionRule.priority.desc()).all()
            rows = s.query(Transaction).filter(Transaction.category_id.is_(None)).limit(limit).all()
            matched = []
            for tx in rows:
                desc = (tx.description or '').lower()
                for rule in rules:
                    pattern = (rule.pattern or '').lower()
                    if rule.match_type == 'exact':
                        hit = pattern == desc
                    elif len(pattern) <= 4:
                        hit = re.search(rf'\b{re.escape(pattern)}\b', desc) is not None
                    else:
                        hit = pattern in desc
                    if hit:
                        matched.append({'id': tx.id, 'description': tx.description,
                                        'category_id': rule.category_id, 'rule_id': rule.id})
                        if not dry_run:
                            tx.category_id = rule.category_id
                        break
            if not dry_run:
                s.commit()
        return {'dry_run': dry_run, 'matched': len(matched), 'sample': matched[:20],
                'remaining_uncategorised': max(0, len(rows) - len(matched))}

    # ------------------------------------------------------------ reports

    def _analytics(self):
        from app.analytics_service import AnalyticsService
        return AnalyticsService()

    def report(self, name: str, **params) -> Any:
        service = self._analytics()
        with activate_db(self.db_path):
            mapping = {
                'dashboard': service.get_dashboard_stats,
                'monthly': service.get_monthly_summary,
                'summary': service.get_date_range_summary,
                'comprehensive': service.get_comprehensive_analytics,
                'trends': service.get_spending_trends,
                'category_trends': service.get_category_trends,
                'merchants': service.get_top_merchants,
                'recurring': service.get_recurring_transactions,
                'categories': service.get_all_time_category_breakdown,
                'balance_breakdown': service.get_balance_breakdown,
                'income_sources': self.income_sources,
                'savings_rate': self.savings_rate,
            }
            if name not in mapping:
                raise ValidationError(f'Unknown report {name!r}')
            return mapping[name](**params)

    def income_sources(self, period: str = '90d') -> dict:
        start = _period_start(period)
        with self._db() as s:
            rows = self._tx_query(s).filter(
                TransactionView.date >= start,
                TransactionView.transaction_type == TransactionType.INCOME,
                TransactionView.is_internal_transfer.is_(False)).all()
        groups: Dict[str, Dict[str, float]] = {}
        for t in rows:
            key = (t.description or 'Unknown').strip()[:60]
            g = groups.setdefault(key, {'total': 0.0, 'count': 0})
            g['total'] += t.amount
            g['count'] += 1
        items = [{'source': k, 'total': round(v['total'], 2), 'count': v['count']}
                 for k, v in groups.items()]
        items.sort(key=lambda x: -x['total'])
        total = round(sum(i['total'] for i in items), 2)
        return {'period': period, 'total': total, 'sources': items[:20]}

    def savings_rate(self, period: str = '30d') -> dict:
        start = _period_start(period)
        with self._db() as s:
            rows = self._tx_query(s).filter(
                TransactionView.date >= start,
                TransactionView.is_internal_transfer.is_(False)).all()
        income = round(sum(t.amount for t in rows if t.transaction_type == TransactionType.INCOME), 2)
        expenses = round(sum(t.amount for t in rows if t.transaction_type == TransactionType.EXPENSE), 2)
        rate = round((income - expenses) / income * 100, 1) if income else None
        return {'period': period, 'income': income, 'expenses': expenses,
                'saved': round(income - expenses, 2), 'savings_rate_percent': rate}

    def net_worth(self) -> dict:
        accounts = self.list_accounts()
        cash = round(sum(a.get('balance', 0) or 0 for a in accounts), 2)
        by_currency = {}
        for a in accounts:
            cur = a.get('currency', 'GBP')
            by_currency[cur] = round(by_currency.get(cur, 0) + (a.get('balance', 0) or 0), 2)

        with self._db() as s:
            latest_price = s.query(
                AssetPriceCache.asset_id, func.max(AssetPriceCache.price_date).label('d')
            ).group_by(AssetPriceCache.asset_id).subquery()
            rows = s.query(Asset, AssetHolding, AssetPriceCache).join(
                AssetHolding, AssetHolding.asset_id == Asset.id
            ).outerjoin(latest_price, latest_price.c.asset_id == Asset.id).outerjoin(
                AssetPriceCache,
                and_(AssetPriceCache.asset_id == Asset.id,
                     AssetPriceCache.price_date == latest_price.c.d)
            ).all()
            assets = []
            for asset, holding, price in rows:
                unit = (price.price_gbp if price and price.price_gbp else None)
                value = round((holding.quantity or 0) * unit, 2) if unit else None
                assets.append({'symbol': asset.symbol, 'name': asset.name,
                               'quantity': holding.quantity, 'value_gbp': value})
            assets_total = round(sum(a['value_gbp'] or 0 for a in assets), 2)
            wallets = s.query(func.coalesce(func.sum(Wallet.total_value_usd), 0.0)).scalar() or 0

        return {
            'cash': cash,
            'cash_by_currency': by_currency,
            'assets_gbp': assets_total,
            'assets': assets,
            'wallets_usd': round(float(wallets), 2),
            'total_gbp': round(cash + assets_total, 2),
            'note': 'total_gbp covers cash + assets priced in GBP; wallets are reported separately in USD',
        }

    def find_anomalies(self, period: str = '90d', sensitivity: float = 3.0) -> dict:
        """Outliers, probable duplicate charges and subscription price hikes."""
        import statistics

        start = _period_start(period)
        with self._db() as s:
            rows = self._tx_query(s).filter(
                TransactionView.date >= start,
                TransactionView.transaction_type == TransactionType.EXPENSE,
                TransactionView.is_internal_transfer.is_(False)).all()

        by_cat: Dict[int, list] = {}
        for t in rows:
            by_cat.setdefault(t.category_id, []).append(t)

        outliers = []
        for cat_id, items in by_cat.items():
            if len(items) < 3:
                continue
            amounts = [i.amount for i in items]
            mean = statistics.fmean(amounts)
            stdev = statistics.pstdev(amounts) or 0
            threshold = mean + sensitivity * stdev
            for t in items:
                if t.amount >= max(threshold, 20) and t.amount > mean * 1.5:
                    outliers.append({'id': t.id, 'date': _iso(t.date), 'amount': t.amount,
                                     'description': t.description,
                                     'category': t.category.name if t.category else None,
                                     'category_average': round(mean, 2)})

        duplicates = []
        seen = {}
        for t in sorted(rows, key=lambda x: x.date):
            key = ((t.description or '').lower().strip(), round(t.amount, 2))
            prior = seen.get(key)
            if prior and abs((t.date - prior.date).days) <= 3:
                duplicates.append({'description': t.description, 'amount': t.amount,
                                   'dates': [_iso(prior.date), _iso(t.date)],
                                   'ids': [prior.id, t.id]})
            seen[key] = t

        groups: Dict[str, list] = {}
        for t in rows:
            key = re.sub(r'\s+', ' ', (t.description or '').lower()).strip()[:50]
            groups.setdefault(key, []).append(t)
        price_hikes = []
        for key, items in groups.items():
            if len(items) < 3:
                continue
            items = sorted(items, key=lambda x: x.date)
            avg = statistics.fmean([i.amount for i in items[:-1]])
            latest = items[-1]
            if avg and latest.amount > avg * 1.15 and latest.amount - avg >= 1:
                price_hikes.append({'description': latest.description, 'id': latest.id,
                                    'previous_average': round(avg, 2),
                                    'latest': latest.amount,
                                    'increase_percent': round((latest.amount / avg - 1) * 100, 1),
                                    'date': _iso(latest.date)})

        return {'period': period, 'outliers': outliers[:20], 'duplicates': duplicates[:10],
                'price_hikes': price_hikes[:10],
                'counts': {'transactions': len(rows), 'outliers': len(outliers),
                           'duplicates': len(duplicates), 'price_hikes': len(price_hikes)}}

    def create_dashboard(self, name: str, widgets: Optional[List[dict]] = None,
                         slug: Optional[str] = None, icon: str = 'layout-dashboard') -> dict:
        """Create a page and its widgets in one call — the agent's main UI hook."""
        page = self.create_page(name, slug=slug, icon=icon)
        for index, widget in enumerate(widgets or []):
            self.add_widget(page['id'], widget.get('type', 'metric'),
                            widget.get('title', 'Widget'), widget.get('config') or {},
                            widget.get('layout') or {}, widget.get('position', index))
        return self.render_page(page['id'])

    def cashflow_forecast(self, days: int = 30) -> dict:
        """Project the next N days from detected recurring charges plus variable spend."""
        days = max(7, min(int(days), 365))
        today = datetime.utcnow()
        since = today - timedelta(days=120)
        with self._db() as s:
            rows = self._tx_query(s).filter(
                TransactionView.date >= since,
                TransactionView.transaction_type == TransactionType.EXPENSE,
            ).all()
        by_desc: Dict[str, List[TransactionView]] = {}
        for t in rows:
            key = re.sub(r'\s+', ' ', (t.description or '').lower()).strip()[:60]
            by_desc.setdefault(key, []).append(t)
        recurring = []
        for key, items in by_desc.items():
            if len(items) < 3:
                continue
            dates = sorted(i.date for i in items)
            gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
            if not gaps:
                continue
            median_gap = sorted(gaps)[len(gaps) // 2]
            if not (5 <= median_gap <= 35):
                continue
            amounts = [i.amount for i in items]
            recurring.append({'description': items[-1].description,
                              'amount': round(sum(amounts) / len(amounts), 2),
                              'every_days': median_gap,
                              'last_date': _iso(dates[-1])})
        upcoming = round(sum(r['amount'] for r in recurring if r['every_days'] <= days), 2)

        variable_daily = 0.0
        if rows:
            total = sum(t.amount for t in rows)
            variable_daily = round(total / 120.0, 2)
        return {'days': days, 'recurring_total': round(sum(r['amount'] for r in recurring), 2),
                'recurring_due': upcoming, 'variable_daily': variable_daily,
                'projected_spend': round(upcoming + variable_daily * days, 2),
                'recurring': sorted(recurring, key=lambda r: -r['amount'])[:15]}

    # ------------------------------------------------------------ budgets

    def list_budgets(self, year: Optional[int] = None, month: Optional[int] = None) -> List[dict]:
        now = datetime.utcnow()
        year = int(year or now.year)
        month = int(month or now.month)
        start, end = _month_bounds(year, month)
        with self._db() as s:
            budgets = s.query(MonthlyBudget).filter(
                MonthlyBudget.year == year, MonthlyBudget.month == month).all()
            out = []
            for b in budgets:
                spent = s.query(func.coalesce(func.sum(TransactionView.amount), 0.0)).filter(
                    TransactionView.category_id == b.category_id,
                    TransactionView.transaction_type == TransactionType.EXPENSE,
                    TransactionView.date >= start, TransactionView.date < end,
                    TransactionView.is_internal_transfer.is_(False),
                ).scalar() or 0
                out.append({'id': b.id, 'year': b.year, 'month': b.month,
                            'category_id': b.category_id,
                            'category_name': b.category.name if b.category else None,
                            'budget': b.budget_amount, 'spent': round(float(spent), 2),
                            'remaining': round(b.budget_amount - float(spent), 2),
                            'percent_used': round(float(spent) / b.budget_amount * 100, 1)
                            if b.budget_amount else 0})
            return out

    def set_budget(self, category_id, amount: float, year: Optional[int] = None,
                   month: Optional[int] = None) -> dict:
        resolved = self.find_category(category_id)
        if not resolved:
            raise NotFound(f'Category {category_id} not found')
        now = datetime.utcnow()
        year, month = int(year or now.year), int(month or now.month)
        amount = _money(amount, 'amount')
        if amount < 0:
            raise ValidationError('Budget amount cannot be negative')
        with self._db() as s:
            budget = s.query(MonthlyBudget).filter(
                MonthlyBudget.year == year, MonthlyBudget.month == month,
                MonthlyBudget.category_id == resolved).first()
            if budget:
                budget.budget_amount = amount
            else:
                budget = MonthlyBudget(year=year, month=month, category_id=resolved,
                                       budget_amount=amount)
                s.add(budget)
            s.commit()
            return {'id': budget.id, 'year': year, 'month': month,
                    'category_id': resolved, 'budget': amount}

    def delete_budget(self, budget_id: int) -> dict:
        with self._db() as s:
            budget = s.get(MonthlyBudget, budget_id)
            if not budget:
                raise NotFound(f'Budget {budget_id} not found')
            s.delete(budget)
            s.commit()
        return {'id': budget_id, 'deleted': True}

    # ------------------------------------------------------------ goals

    def list_goals(self) -> List[dict]:
        with self._db() as s:
            out = []
            for g in s.query(Goal).order_by(Goal.id).all():
                contributed = sum(c.amount for c in g.contributions)
                current = contributed
                if g.account_id:
                    account = s.get(Account, g.account_id)
                    if account:
                        current = self._account_balance(s, account)
                out.append({'id': g.id, 'name': g.name, 'target': g.target_amount,
                            'current': round(current, 2), 'currency': g.currency,
                            'deadline': _iso(g.deadline), 'status': g.status,
                            'percent': round(current / g.target_amount * 100, 1)
                            if g.target_amount else 0,
                            'account_id': g.account_id, 'category_id': g.category_id,
                            'notes': g.notes})
            return out

    def create_goal(self, name: str, target_amount: float, deadline=None,
                    account_id: Optional[int] = None, category_id=None,
                    notes: str = None, currency: str = 'GBP') -> dict:
        if not name or not name.strip():
            raise ValidationError('Goal name is required')
        with self._db() as s:
            if account_id and not s.get(Account, account_id):
                raise NotFound(f'Account {account_id} not found')
            goal = Goal(name=name.strip()[:120], target_amount=_money(target_amount, 'target_amount'),
                        deadline=_parse_dt(deadline) if deadline else None,
                        account_id=account_id, category_id=self.find_category(category_id),
                        notes=notes, currency=(currency or 'GBP').upper()[:3])
            s.add(goal)
            s.commit()
            return {'id': goal.id, 'name': goal.name, 'target': goal.target_amount}

    def contribute_to_goal(self, goal_id: int, amount: float, note: str = None,
                           date_value=None) -> dict:
        amount = _money(amount, 'amount')
        if amount <= 0:
            raise ValidationError('Contribution must be positive')
        with self._db() as s:
            goal = s.get(Goal, goal_id)
            if not goal:
                raise NotFound(f'Goal {goal_id} not found')
            s.add(GoalContribution(goal_id=goal_id, amount=amount, note=note,
                                   date=_parse_dt(date_value) if date_value else datetime.utcnow()))
            s.commit()
        return next(g for g in self.list_goals() if g['id'] == goal_id)

    def update_goal(self, goal_id: int, patch: dict) -> dict:
        with self._db() as s:
            goal = s.get(Goal, goal_id)
            if not goal:
                raise NotFound(f'Goal {goal_id} not found')
            for field in ('name', 'status', 'notes'):
                if field in patch:
                    setattr(goal, field, patch[field])
            if 'target_amount' in patch:
                goal.target_amount = _money(patch['target_amount'], 'target_amount')
            if 'deadline' in patch:
                goal.deadline = _parse_dt(patch['deadline']) if patch['deadline'] else None
            s.commit()
        return next(g for g in self.list_goals() if g['id'] == goal_id)

    # ------------------------------------------------------------ dashboards

    def list_pages(self) -> List[dict]:
        with self._db() as s:
            return [{'id': p.id, 'name': p.name, 'slug': p.slug, 'icon': p.icon,
                     'description': p.description, 'is_default': bool(p.is_default),
                     'widgets': len(p.widgets)} for p in
                    s.query(DashboardPage).order_by(DashboardPage.position, DashboardPage.id).all()]

    def create_page(self, name: str, slug: Optional[str] = None, icon: str = 'layout-dashboard',
                    description: str = None) -> dict:
        if not name or not name.strip():
            raise ValidationError('Page name is required')
        slug = (slug or re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-'))[:80]
        with self._db() as s:
            if s.query(DashboardPage).filter(DashboardPage.slug == slug).first():
                raise Conflict(f'Page slug {slug!r} already exists')
            page = DashboardPage(name=name.strip()[:80], slug=slug, icon=icon,
                                 description=description, created_by=self.actor,
                                 position=(s.query(func.count(DashboardPage.id)).scalar() or 0))
            s.add(page)
            s.commit()
            return {'id': page.id, 'name': page.name, 'slug': page.slug}

    def update_page(self, page_id: int, patch: dict) -> dict:
        with self._db() as s:
            page = s.get(DashboardPage, page_id)
            if not page:
                raise NotFound(f'Page {page_id} not found')
            for field in ('name', 'icon', 'description', 'position', 'is_default'):
                if field in patch:
                    setattr(page, field, patch[field])
            s.commit()
            return {'id': page.id, 'name': page.name, 'slug': page.slug}

    def delete_page(self, page_id: int) -> dict:
        with self._db() as s:
            page = s.get(DashboardPage, page_id)
            if not page:
                raise NotFound(f'Page {page_id} not found')
            s.delete(page)
            s.commit()
        return {'id': page_id, 'deleted': True}

    def add_widget(self, page_id: int, widget_type: str, title: str, config: dict = None,
                   layout: dict = None, position: Optional[int] = None) -> dict:
        valid = {'metric', 'chart', 'list', 'budget', 'goal', 'progress', 'text'}
        if widget_type not in valid:
            raise ValidationError(f'widget type must be one of {sorted(valid)}')
        with self._db() as s:
            page = s.get(DashboardPage, page_id)
            if not page:
                raise NotFound(f'Page {page_id} not found')
            widget = DashboardWidget(
                page_id=page_id, type=widget_type, title=(title or widget_type)[:120],
                config_json=json.dumps(config or {}), layout_json=json.dumps(layout or {}),
                position=position if position is not None else len(page.widgets))
            s.add(widget)
            s.commit()
            return {'id': widget.id, 'page_id': page_id, 'type': widget_type,
                    'title': widget.title}

    def update_widget(self, widget_id: int, patch: dict) -> dict:
        with self._db() as s:
            widget = s.get(DashboardWidget, widget_id)
            if not widget:
                raise NotFound(f'Widget {widget_id} not found')
            for field in ('title', 'position'):
                if field in patch:
                    setattr(widget, field, patch[field])
            if 'type' in patch:
                widget.type = patch['type']
            if 'config' in patch:
                widget.config_json = json.dumps(patch['config'])
            if 'layout' in patch:
                widget.layout_json = json.dumps(patch['layout'])
            s.commit()
            return {'id': widget.id, 'title': widget.title}

    def delete_widget(self, widget_id: int) -> dict:
        with self._db() as s:
            widget = s.get(DashboardWidget, widget_id)
            if not widget:
                raise NotFound(f'Widget {widget_id} not found')
            s.delete(widget)
            s.commit()
        return {'id': widget_id, 'deleted': True}

    def render_page(self, page_ref) -> dict:
        with self._db() as s:
            page = s.get(DashboardPage, page_ref) if str(page_ref).isdigit() else \
                s.query(DashboardPage).filter(DashboardPage.slug == page_ref).first()
            if not page:
                raise NotFound(f'Page {page_ref} not found')
            widgets = []
            for w in page.widgets:
                config = json.loads(w.config_json or '{}')
                widgets.append({'id': w.id, 'type': w.type, 'title': w.title,
                                'config': config, 'layout': json.loads(w.layout_json or '{}'),
                                'data': self._widget_data(w.type, config)})
            return {'id': page.id, 'name': page.name, 'slug': page.slug, 'icon': page.icon,
                    'widgets': widgets}

    def _widget_data(self, widget_type: str, config: dict):
        period = config.get('period', '30d')
        if widget_type == 'metric':
            metric = config.get('metric', 'net')
            start = _period_start(period)
            with self._db() as s:
                rows = self._tx_query(s).filter(
                    TransactionView.date >= start,
                    TransactionView.is_internal_transfer.is_(False)).all()
            income = sum(t.amount for t in rows if t.transaction_type == TransactionType.INCOME)
            expense = sum(t.amount for t in rows if t.transaction_type == TransactionType.EXPENSE)
            values = {'income': round(income, 2), 'expenses': round(expense, 2),
                      'net': round(income - expense, 2),
                      'transactions': len(rows),
                      'net_worth': self.net_worth().get('total_gbp')}
            return {'value': values.get(metric), 'metric': metric, 'period': period}
        if widget_type == 'chart':
            report = config.get('report', 'spending_by_category')
            if report == 'cashflow':
                return self.report('trends', months=int(config.get('months', 6)))
            if report == 'net_worth':
                return self.report('balance_breakdown')
            filters = {'date_from': _iso(_period_start(period))}
            return self.list_transactions(filters=filters, group_by='category', limit=8)
        if widget_type == 'list':
            report = config.get('report', 'recent')
            if report == 'merchants':
                return self.report('merchants', limit=int(config.get('limit', 8)))
            if report == 'recurring':
                return sorted(self.report('recurring'), key=lambda r: -abs(r.get('average_amount', 0)))[:8]
            return self.list_transactions(limit=int(config.get('limit', 8)))['transactions']
        if widget_type == 'budget':
            return self.list_budgets()
        if widget_type == 'goal':
            goals = self.list_goals()
            goal_id = config.get('goal_id')
            return [g for g in goals if g['id'] == goal_id] if goal_id else goals
        if widget_type == 'progress':
            goals = self.list_goals()
            return goals[0] if goals else None
        if widget_type == 'text':
            return {'markdown': config.get('markdown', '')}
        return None

    def ensure_default_page(self):
        with self._db() as s:
            if s.query(DashboardPage).count():
                return
        page = self.create_page('Overview', slug='overview')
        self.add_widget(page['id'], 'metric', 'Net worth', {'metric': 'net_worth', 'period': '30d'}, position=0)
        self.add_widget(page['id'], 'metric', 'Income (30d)', {'metric': 'income', 'period': '30d'}, position=1)
        self.add_widget(page['id'], 'metric', 'Spending (30d)', {'metric': 'expenses', 'period': '30d'}, position=2)
        self.add_widget(page['id'], 'chart', 'Spending by category', {'report': 'spending_by_category', 'period': '30d'}, position=3)
        self.add_widget(page['id'], 'list', 'Recent transactions', {'report': 'recent', 'limit': 8}, position=4)

    # ------------------------------------------------------------ context

    def get_context(self, period: str = '30d') -> dict:
        start = _period_start(period)
        accounts = self.list_accounts()
        with self._db() as s:
            rows = self._tx_query(s).filter(
                TransactionView.date >= start,
                TransactionView.is_internal_transfer.is_(False)).all()
            uncategorised = s.query(func.count(TransactionView.id)).filter(
                TransactionView.category_id.is_(None)).scalar() or 0
            pending = s.query(func.count(TransactionView.id)).filter(
                TransactionView.pending.is_(True)).scalar() or 0
            failed_syncs = s.query(func.count(BankConnection.id)).filter(
                BankConnection.status.in_(('error', 'expired'))).scalar() or 0
        income = round(sum(t.amount for t in rows if t.transaction_type == TransactionType.INCOME), 2)
        expense = round(sum(t.amount for t in rows if t.transaction_type == TransactionType.EXPENSE), 2)
        by_cat: Dict[str, float] = {}
        for t in rows:
            if t.transaction_type != TransactionType.EXPENSE:
                continue
            name = t.category.name if t.category else 'Uncategorised'
            by_cat[name] = round(by_cat.get(name, 0) + t.amount, 2)
        top = sorted(by_cat.items(), key=lambda kv: -kv[1])[:5]

        budgets = self.list_budgets()
        over = [b for b in budgets if b['remaining'] < 0]
        budgets_out = [{'category': b['category_name'], 'budget': b['budget'],
                        'spent': b['spent'], 'remaining': b['remaining']} for b in budgets[:8]]

        alerts = []
        if uncategorised:
            alerts.append({'type': 'uncategorised', 'count': int(uncategorised),
                           'hint': 'Ask the agent to categorise them in bulk.'})
        if over:
            alerts.append({'type': 'budget_over', 'count': len(over),
                           'detail': [b['category_name'] for b in over[:3]]})
        if failed_syncs:
            alerts.append({'type': 'bank_connection', 'count': int(failed_syncs),
                           'hint': 'A bank connection needs re-authentication.'})
        if pending:
            alerts.append({'type': 'pending', 'count': int(pending)})

        return {
            'as_of': _iso(datetime.utcnow()),
            'period': period,
            'currency': accounts[0]['currency'] if accounts else 'GBP',
            'net_worth': self.net_worth()['total_gbp'],
            'accounts': [{'id': a['id'], 'name': a['name'], 'type': a['type'],
                          'balance': a.get('balance'), 'currency': a['currency']} for a in accounts],
            'cashflow': {'income': income, 'expenses': expense, 'net': round(income - expense, 2),
                         'transactions': len(rows)},
            'top_categories': [{'category': k, 'total': v} for k, v in top],
            'budgets': budgets_out,
            'alerts': alerts,
            'counts': {'uncategorised': int(uncategorised), 'pending': int(pending)},
        }

    # ------------------------------------------------------------ imports / banking hooks

    def import_preview(self, source: str, account_id: Optional[int] = None,
                       bank_hint: Optional[str] = None, filename: Optional[str] = None) -> dict:
        from app.importers.service import ImportService
        return ImportService(self).preview(source, account_id=account_id,
                                           bank_hint=bank_hint, filename=filename)

    def import_commit(self, batch_id: int, edits: Optional[List[dict]] = None) -> dict:
        from app.importers.service import ImportService
        return ImportService(self).commit(batch_id, edits=edits)

    def import_revert(self, batch_id: int) -> dict:
        from app.importers.service import ImportService
        return ImportService(self).revert(batch_id)

    def list_import_batches(self, limit: int = 20) -> List[dict]:
        with self._db() as s:
            rows = s.query(ImportBatch).order_by(ImportBatch.id.desc()).limit(min(limit, 200)).all()
            return [{'id': b.id, 'status': b.status, 'source': b.source, 'filename': b.filename,
                     'account_id': b.account_id, 'parsed': b.parsed_count,
                     'imported': b.imported_count, 'skipped': b.skipped_count,
                     'confidence': b.confidence, 'created_at': _iso(b.created_at)} for b in rows]

    def list_bank_connections(self) -> List[dict]:
        with self._db() as s:
            out = []
            for conn in s.query(BankConnection).order_by(BankConnection.id).all():
                out.append({'id': conn.id, 'provider': conn.provider,
                            'institution': conn.institution.name if conn.institution else None,
                            'display_name': conn.display_name, 'status': conn.status,
                            'accounts': [{'id': a.id, 'name': a.name} for a in conn.accounts],
                            'consent_expires_at': _iso(conn.consent_expires_at),
                            'last_sync_at': _iso(conn.last_sync_at),
                            'last_sync_error': conn.last_sync_error})
            return out


def _json_value(value):
    return json.dumps(value, default=str)


def _from_json_value(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value
