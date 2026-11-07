"""Sync engine: provider → accounts → balances → transactions → ledger.

Idempotent by design: bank transactions carry an external id, so re-running a
sync is a no-op. Everything it inserts is tagged `source='bank_sync'` and grouped
in an ImportBatch, so the whole pull can be audited or reverted.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.banking import get_provider
from app.banking.base import BankAccount, ProviderError, mask_iban
from app.banking.crypto import decrypt_dict, encrypt_dict
from app.database import activate_db, get_session
from app.models import (Account, AccountType, BankConnection, ImportBatch,
                        Institution, Transaction, TransactionType)


def _account_type(value: str) -> AccountType:
    try:
        return AccountType(value)
    except ValueError:
        return AccountType.CHECKING


def _row_hash(account_id: int, txn) -> str:
    key = '|'.join([str(account_id),
                    txn.booked_date.date().isoformat() if txn.booked_date else 'nodate',
                    f'{txn.absolute_amount:.2f}', (txn.description or '').lower()[:80]])
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def save_connection(core, link: dict) -> dict:
    """Persist a completed provider link: institution, connection, accounts."""
    with activate_db(core.db_path):
        session = get_session(core.db_path)
        try:
            institution_data = link.get('institution') or {}
            institution = session.query(Institution).filter(
                Institution.provider == link['provider'],
                Institution.provider_ref == institution_data.get('reference')).first()
            if not institution:
                institution = Institution(provider=link['provider'],
                                          provider_ref=institution_data.get('reference'),
                                          name=institution_data.get('name') or 'Bank',
                                          country=institution_data.get('country', 'GB'))
                session.add(institution)
                session.flush()

            consent = link.get('consent_expires_at')
            connection = BankConnection(
                provider=link['provider'], institution_id=institution.id,
                display_name=link.get('display_name') or institution.name,
                status='active',
                credentials=encrypt_dict(link.get('credentials') or {}),
                consent_expires_at=datetime.fromisoformat(consent) if consent else None,
            )
            session.add(connection)
            session.flush()

            created = []
            for item in (link.get('credentials') or {}).get('accounts', []):
                account = Account(
                    name=item.get('name') or 'Bank account',
                    account_type=_account_type(item.get('type', 'checking')),
                    currency=(item.get('currency') or 'GBP').upper()[:3],
                    connection_id=connection.id,
                    provider_account_id=item.get('uid') or item.get('id'),
                    iban_masked=mask_iban(item.get('iban')),
                    balance_source='bank_sync',
                    description=f"{institution.name} (synced)",
                )
                session.add(account)
                created.append(account)
            session.commit()
            return {'connection_id': connection.id, 'provider': connection.provider,
                    'institution': institution.name,
                    'accounts': [{'id': a.id, 'name': a.name} for a in created]}
        finally:
            session.close()


def sync_connection(core, connection_id: int, since_days: int = 90) -> dict:
    with activate_db(core.db_path):
        session = get_session(core.db_path)
        try:
            connection = session.get(BankConnection, connection_id)
            if not connection:
                return {'error': f'Connection {connection_id} not found'}
            if connection.status == 'revoked':
                return {'error': 'Connection is revoked'}

            provider = get_provider(connection.provider, core)
            credentials = decrypt_dict(connection.credentials)

            try:
                bank_accounts = provider.fetch_accounts(credentials)
            except ProviderError as exc:
                connection.status = 'error'
                connection.last_sync_error = str(exc)[:500]
                session.commit()
                return {'connection_id': connection.id, 'error': str(exc)}

            since = connection.last_sync_at or (datetime.utcnow() - timedelta(days=since_days))
            batch = ImportBatch(
                source='bank_sync', provider=connection.provider,
                bank_name=connection.institution.name if connection.institution else None,
                filename=f'sync-{datetime.utcnow().date().isoformat()}',
                status='committed', committed_at=datetime.utcnow(),
                currency=bank_accounts[0].currency if bank_accounts else 'GBP',
            )
            session.add(batch)
            session.flush()

            from app.categorization_service import categorization_service

            totals = {'added': 0, 'skipped': 0}
            errors = []
            account_results = []

            for bank_account in bank_accounts:
                account = session.query(Account).filter(
                    Account.connection_id == connection.id,
                    Account.provider_account_id == bank_account.provider_account_id).first()
                if not account:
                    account = Account(
                        name=bank_account.name,
                        account_type=_account_type(bank_account.account_type),
                        currency=bank_account.currency,
                        connection_id=connection.id,
                        provider_account_id=bank_account.provider_account_id,
                        iban_masked=bank_account.iban_masked,
                        balance_source='bank_sync',
                    )
                    session.add(account)
                    session.flush()

                # Balances are authoritative; the raw ledger stays untouched.
                if bank_account.balance_current is not None:
                    account.balance_current = bank_account.balance_current
                if bank_account.balance_available is not None:
                    account.balance_available = bank_account.balance_available
                account.balance_synced_at = datetime.utcnow()
                account.balance_source = 'bank_sync'

                added = skipped = 0
                try:
                    transactions = provider.fetch_transactions(credentials, bank_account, since)
                except ProviderError as exc:
                    errors.append({'account': bank_account.name, 'error': str(exc)})
                    transactions = []

                for txn in transactions:
                    if not txn.booked_date:
                        skipped += 1
                        continue
                    external_id = (txn.external_id or '')[:160] or None
                    if external_id:
                        exists = session.query(Transaction.id).filter(
                            Transaction.account_id == account.id,
                            Transaction.external_id == external_id).first()
                    else:
                        exists = session.query(Transaction.id).filter(
                            Transaction.account_id == account.id,
                            Transaction.import_hash == _row_hash(account.id, txn)).first()
                    if exists:
                        skipped += 1
                        continue

                    category = categorization_service.categorize_transaction(
                        txn.description, txn.absolute_amount, session=session)
                    transaction = Transaction(
                        date=txn.booked_date,
                        value_date=txn.booked_date,
                        description=txn.description or 'Bank transaction',
                        original_description=txn.description or '',
                        amount=txn.absolute_amount,
                        transaction_type=(TransactionType.INCOME if txn.amount > 0
                                          else TransactionType.EXPENSE),
                        account_id=account.id,
                        category_id=category['category_id'] if category else None,
                        currency=txn.currency,
                        source='bank_sync',
                        external_id=external_id,
                        import_hash=_row_hash(account.id, txn),
                        import_batch_id=batch.id,
                        pending=txn.pending,
                        counterparty=txn.counterparty,
                        raw_json=json.dumps(txn.raw, default=str)[:4000],
                    )
                    session.add(transaction)
                    added += 1

                totals['added'] += added
                totals['skipped'] += skipped
                account_results.append({'id': account.id, 'name': account.name,
                                        'provider_account_id': bank_account.provider_account_id,
                                        'added': added, 'skipped': skipped,
                                        'balance': bank_account.balance_current})

            batch.parsed_count = totals['added'] + totals['skipped']
            batch.imported_count = totals['added']
            batch.skipped_count = totals['skipped']
            connection.status = 'active'
            connection.last_sync_at = datetime.utcnow()
            connection.last_sync_error = None
            session.commit()

            result = {'connection_id': connection.id, 'provider': connection.provider,
                      'accounts': account_results, **totals, 'batch_id': batch.id,
                      'errors': errors}
        finally:
            session.close()

    core.audit('bank_sync', {'connection_id': connection_id},
               f"{result.get('added', 0)} added, {result.get('skipped', 0)} skipped")
    return result


def sync_all(core) -> dict:
    with activate_db(core.db_path):
        session = get_session(core.db_path)
        try:
            ids = [row[0] for row in session.query(BankConnection.id).filter(
                BankConnection.status != 'revoked').all()]
        finally:
            session.close()
    results = [sync_connection(core, connection_id) for connection_id in ids]
    return {'connections': len(results), 'added': sum(r.get('added', 0) for r in results),
            'skipped': sum(r.get('skipped', 0) for r in results), 'results': results}


def revoke_connection(core, connection_id: int) -> dict:
    with activate_db(core.db_path):
        session = get_session(core.db_path)
        try:
            connection = session.get(BankConnection, connection_id)
            if not connection:
                return {'error': f'Connection {connection_id} not found'}
            try:
                provider = get_provider(connection.provider, core)
                provider.revoke(decrypt_dict(connection.credentials))
            except Exception:
                pass  # local revocation still matters
            connection.status = 'revoked'
            connection.credentials = encrypt_dict({})
            session.commit()
            return {'connection_id': connection_id, 'revoked': True}
        finally:
            session.close()
