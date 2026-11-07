"""Import pipeline: preview → commit → revert, with duplicate detection.

Nothing touches the ledger until commit, and every commit is revertible as a unit.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.request
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from app.database import activate_db, get_session
from app.importers.pdf import parse_pdf
from app.importers.profiles import detect_profile, load_profiles
from app.importers.tabular import parse_structured
from app.importers.types import ImportError_, ParsedRow, ParseResult
from app.models import (Account, ImportBatch, ImportRow, Transaction,
                        TransactionType)


def _normalise(text: str) -> str:
    return re.sub(r'[^a-z0-9 ]+', ' ', (text or '').lower()).strip()[:120]


def _hash_row(account_id: int, row: ParsedRow, occurrence: int) -> str:
    key = '|'.join([
        str(account_id),
        row.date.date().isoformat() if row.date else 'nodate',
        f'{row.amount:.2f}',
        _normalise(row.description),
        str(occurrence),
    ])
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


class ImportService:
    def __init__(self, core):
        self.core = core

    # ------------------------------------------------------------ preview

    def preview(self, source: str, account_id: Optional[int] = None,
                bank_hint: Optional[str] = None, filename: Optional[str] = None) -> dict:
        path, cleanup, resolved_name = self._materialise(source, filename)
        try:
            result = self._parse(path, resolved_name, bank_hint)
            with activate_db(self.core.db_path):
                session = get_session(self.core.db_path)
                try:
                    account = self._resolve_account(session, account_id)
                    batch = ImportBatch(
                        account_id=account.id,
                        source='statement',
                        provider=result.file_type,
                        bank_name=result.bank,
                        profile_id=result.profile_id,
                        filename=resolved_name,
                        file_hash=self._file_hash(path),
                        status='preview',
                        parsed_count=len(result.rows),
                        opening_balance=result.opening_balance,
                        closing_balance=result.closing_balance,
                        currency=result.currency,
                        confidence=result.confidence,
                        issues_json=json.dumps(result.warnings),
                    )
                    session.add(batch)
                    session.flush()

                    occurrences = {}
                    skipped = 0
                    for index, row in enumerate(result.rows):
                        key = (row.date.date().isoformat() if row.date else 'nodate',
                               round(row.amount, 2), _normalise(row.description))
                        occurrences[key] = occurrences.get(key, 0) + 1
                        occurrence = occurrences[key] - 1
                        import_hash = _hash_row(account.id, row, occurrence)
                        duplicate = session.query(Transaction.id).filter(
                            Transaction.account_id == account.id,
                            Transaction.import_hash == import_hash).first()
                        status = 'skipped' if duplicate else 'pending'
                        if duplicate:
                            skipped += 1
                            row.issues = list(row.issues) + ['already imported']
                        session.add(ImportRow(
                            batch_id=batch.id, row_index=index, raw=row.raw[:2000],
                            parsed_json=json.dumps({**row.to_dict(), 'import_hash': import_hash}),
                            confidence=row.confidence,
                            issues_json=json.dumps(row.issues),
                            status=status, external_id=row.external_id,
                        ))
                    batch.skipped_count = skipped
                    session.commit()

                    rows = self._batch_rows(session, batch.id)
                    return {
                        'batch': self._batch_dict(batch),
                        'account': {'id': account.id, 'name': account.name,
                                    'currency': account.currency},
                        'rows': rows[:200],
                        'row_count': len(rows),
                        'next': f'POST /api/v1/imports/{batch.id}/commit after reviewing rows',
                    }
                finally:
                    session.close()
        finally:
            if cleanup:
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _parse(self, path: str, filename: str, bank_hint: Optional[str]) -> ParseResult:
        head = ''
        if path.lower().endswith('.pdf'):
            # PDF bytes are compressed: detect from the text layer, not the raw file.
            from app.importers.pdf import extract_text_sample
            head = extract_text_sample(path)
        else:
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                    head = fh.read(8000)
            except OSError:
                head = ''
        profile = detect_profile(head, filename or os.path.basename(path), bank_hint)
        try:
            if path.lower().endswith('.pdf'):
                return parse_pdf(path, profile)
            return parse_structured(path, profile)
        except ImportError_:
            raise
        except Exception as exc:
            raise ImportError_(f'Could not parse {filename}: {exc}') from exc

    # ------------------------------------------------------------ commit

    def commit(self, batch_id: int, edits: Optional[List[dict]] = None) -> dict:
        from app.categorization_service import categorization_service

        with activate_db(self.core.db_path):
            session = get_session(self.core.db_path)
            try:
                batch = session.get(ImportBatch, batch_id)
                if not batch:
                    raise ImportError_(f'Import batch {batch_id} not found')
                if batch.status != 'preview':
                    raise ImportError_(f'Batch {batch_id} is {batch.status}, not preview')

                edits_by_row = {int(e['row_id']): e for e in (edits or []) if e.get('row_id')}
                account = session.get(Account, batch.account_id) if batch.account_id else None
                if not account:
                    raise ImportError_('Import batch has no target account')

                created, skipped, failed = [], 0, []
                rows = session.query(ImportRow).filter(ImportRow.batch_id == batch_id) \
                    .order_by(ImportRow.row_index).all()
                for row in rows:
                    if row.status != 'pending':
                        skipped += 1
                        continue
                    data = json.loads(row.parsed_json or '{}')
                    data.update({k: v for k, v in (edits_by_row.get(row.id) or {}).items()
                                 if k != 'row_id'})
                    try:
                        when = data.get('date')
                        when = datetime.fromisoformat(when) if when else None
                        amount = float(data.get('amount') or 0)
                        if not when or amount <= 0:
                            raise ValueError('date and amount are required')
                        kind = data.get('type') or 'expense'
                        if kind not in ('income', 'expense'):
                            raise ValueError('type must be income or expense')

                        category_id = data.get('category_id')
                        if not category_id:
                            match = categorization_service.categorize_transaction(
                                data.get('description', ''), amount, session=session)
                            category_id = match['category_id'] if match else None

                        # Savepoint: a duplicate row must not roll back the whole batch.
                        with session.begin_nested():
                            tx = Transaction(
                                date=when,
                                description=(data.get('description') or 'Imported transaction')[:500],
                                original_description=(data.get('description') or '')[:500],
                                amount=amount,
                                transaction_type=TransactionType(kind),
                                balance_after=data.get('balance'),
                                category_id=category_id,
                                account_id=account.id,
                                currency=batch.currency or account.currency,
                                source='statement',
                                external_id=row.external_id,
                                import_hash=data.get('import_hash'),
                                import_batch_id=batch.id,
                                raw_json=json.dumps(data.get('raw') or '')[:4000],
                            )
                            session.add(tx)
                            session.flush()
                        row.status = 'imported'
                        row.transaction_id = tx.id
                        created.append(tx.id)
                    except (ValueError, IntegrityError) as exc:
                        row.status = 'skipped'
                        failed.append({'row_id': row.id, 'error': str(exc)[:200]})
                        continue

                batch.imported_count = len(created)
                batch.skipped_count = skipped + len(failed)
                batch.status = 'committed'
                batch.committed_at = datetime.utcnow()
                session.commit()

                summary = {'batch_id': batch.id, 'imported': len(created),
                           'skipped': batch.skipped_count, 'failed': failed,
                           'account': {'id': account.id, 'name': account.name},
                           'transaction_ids': created[:200],
                           'reversible': True}
            finally:
                session.close()

        self.core.audit('import_commit', {'batch_id': batch_id}, f'{summary["imported"]} rows imported')
        return summary

    # ------------------------------------------------------------ revert

    def revert(self, batch_id: int) -> dict:
        with activate_db(self.core.db_path):
            session = get_session(self.core.db_path)
            try:
                batch = session.get(ImportBatch, batch_id)
                if not batch:
                    raise ImportError_(f'Import batch {batch_id} not found')
                if batch.status != 'committed':
                    raise ImportError_(f'Batch {batch_id} is {batch.status}, not committed')
                # Break the ImportRow -> Transaction references first, otherwise the
                # FK blocks deleting the imported rows.
                session.query(ImportRow).filter(ImportRow.batch_id == batch.id).update(
                    {'transaction_id': None, 'status': 'reverted'})
                session.flush()
                deleted = session.query(Transaction).filter(
                    Transaction.import_batch_id == batch.id).delete(synchronize_session=False)
                batch.status = 'reverted'
                batch.reverted_at = datetime.utcnow()
                session.commit()
                summary = {'batch_id': batch.id, 'deleted': deleted, 'reverted': True}
            finally:
                session.close()
        self.core.audit('import_revert', {'batch_id': batch_id}, f'{summary["deleted"]} rows removed')
        return summary

    # ------------------------------------------------------------ helpers

    def _batch_rows(self, session, batch_id: int) -> List[dict]:
        rows = session.query(ImportRow).filter(ImportRow.batch_id == batch_id) \
            .order_by(ImportRow.row_index).all()
        out = []
        for row in rows:
            data = json.loads(row.parsed_json or '{}')
            out.append({'row_id': row.id, 'index': row.row_index,
                        'date': data.get('date'), 'description': data.get('description'),
                        'amount': data.get('amount'), 'type': data.get('type'),
                        'balance': data.get('balance'), 'confidence': row.confidence,
                        'issues': json.loads(row.issues_json or '[]'), 'status': row.status})
        return out

    def _batch_dict(self, batch: ImportBatch) -> dict:
        return {
            'id': batch.id, 'status': batch.status, 'source': batch.source,
            'filename': batch.filename, 'account_id': batch.account_id,
            'bank': batch.bank_name, 'profile': batch.profile_id,
            'file_type': batch.provider,
            'parsed': batch.parsed_count, 'skipped': batch.skipped_count,
            'currency': batch.currency, 'confidence': batch.confidence,
            'opening_balance': batch.opening_balance,
            'closing_balance': batch.closing_balance,
            'warnings': json.loads(batch.issues_json or '[]'),
            'created_at': batch.created_at.isoformat() if batch.created_at else None,
        }

    def _resolve_account(self, session, account_id: Optional[int]) -> Account:
        if account_id:
            account = session.get(Account, int(account_id))
            if not account:
                raise ImportError_(f'Account {account_id} not found')
            return account
        account = session.query(Account).filter(Account.is_archived.is_(False)).first()
        if not account:
            raise ImportError_('No account exists; create one before importing')
        return account

    def _file_hash(self, path: str) -> str:
        sha = hashlib.sha256()
        with open(path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(65536), b''):
                sha.update(chunk)
        return sha.hexdigest()

    def _materialise(self, source: str, filename: Optional[str]) -> Tuple[str, bool, str]:
        if source.startswith(('http://', 'https://')):
            name = filename or os.path.basename(source.split('?')[0]) or 'statement.pdf'
            fd, path = tempfile.mkstemp(suffix=os.path.splitext(name)[1] or '.pdf')
            os.close(fd)
            request = urllib.request.Request(source, headers={'User-Agent': 'TallyLocal/2.0'})
            with urllib.request.urlopen(request, timeout=60) as response:
                with open(path, 'wb') as out:
                    out.write(response.read())
            return path, True, name

        if source.startswith('data:'):
            import base64
            header, _, payload = source.partition(',')
            suffix = '.pdf' if 'pdf' in header else '.csv'
            fd, path = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            with open(path, 'wb') as out:
                out.write(base64.b64decode(payload))
            return path, True, filename or f'upload{suffix}'

        path = os.path.abspath(os.path.expanduser(source))
        if not os.path.exists(path):
            raise ImportError_(f'File not found: {source}')
        return path, False, filename or os.path.basename(path)
