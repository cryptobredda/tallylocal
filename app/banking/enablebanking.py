"""Enable Banking adapter (EU/UK, PSD2 AIS).

Free "restricted production" is enough to link your own accounts, which is the
closest thing left to the old Nordigen free tier. Auth is a JWT signed with the
application's private key; the flow is POST /auth -> bank redirect -> code ->
POST /sessions.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.banking.base import (BankAccount, BankTxn, HttpClient, Institution,
                              LinkStart, ProviderError, mask_iban)
from app.models import Settings

BASE_URL = os.getenv('ENABLEBANKING_BASE_URL', 'https://api.enablebanking.com')
JWT_ISSUER = os.getenv('ENABLEBANKING_JWT_ISSUER', 'enablebanking.com')
JWT_AUDIENCE = os.getenv('ENABLEBANKING_JWT_AUDIENCE', 'api.enablebanking.com')

ACCOUNT_TYPES = {'CACC': 'checking', 'SVGS': 'savings', 'CARD': 'credit_card',
                 'LOAN': 'other', 'CASH': 'cash'}


class EnableBankingProvider:
    id = 'enablebanking'
    label = 'Enable Banking'
    region = 'EU / UK'
    docs = 'https://enablebanking.com/docs/api/reference/'

    def __init__(self, core=None, app_id: Optional[str] = None,
                 private_key: Optional[str] = None, http: Optional[HttpClient] = None,
                 base_url: str = BASE_URL):
        self.core = core
        self.app_id = app_id or os.getenv('ENABLEBANKING_APP_ID', '')
        self.private_key = private_key or os.getenv('ENABLEBANKING_PRIVATE_KEY', '')
        self.http = http or HttpClient()
        self.base_url = base_url.rstrip('/')

    # ------------------------------------------------------------ config

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.private_key)

    def _jwt(self) -> str:
        if not self.configured:
            raise ProviderError('Enable Banking is not configured: set ENABLEBANKING_APP_ID '
                                'and ENABLEBANKING_PRIVATE_KEY')
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover
            raise ProviderError('PyJWT is required: pip install "pyjwt[crypto]"') from exc

        now = int(datetime.utcnow().timestamp())
        payload = {'iss': JWT_ISSUER, 'aud': JWT_AUDIENCE, 'iat': now, 'exp': now + 3600}
        try:
            return jwt.encode(payload, self.private_key, algorithm='RS256',
                              headers={'kid': self.app_id})
        except Exception as exc:
            raise ProviderError(f'Could not sign Enable Banking JWT: {exc}') from exc

    def _headers(self) -> Dict[str, str]:
        return {'Authorization': f'Bearer {self._jwt()}', 'Accept': 'application/json',
                'Content-Type': 'application/json'}

    def _check(self, response, action: str):
        if response.ok:
            return response.json()
        detail = ''
        try:
            payload = response.json()
            detail = payload.get('detail') or payload.get('message') or json.dumps(payload)[:300]
        except Exception:
            detail = (response.text or '')[:300]
        raise ProviderError(f'Enable Banking {action} failed ({response.status}): {detail}')

    # ------------------------------------------------------------ state

    def _save_state(self, state: str, payload: dict):
        if not self.core:
            return
        with self.core._db() as session:
            key = f'bank_state:{state}'
            row = session.query(Settings).filter(Settings.key == key).first()
            if row:
                row.value = json.dumps(payload)
            else:
                session.add(Settings(key=key, value=json.dumps(payload)))
            session.commit()

    def _pop_state(self, state: Optional[str]) -> dict:
        if not self.core or not state:
            return {}
        with self.core._db() as session:
            key = f'bank_state:{state}'
            row = session.query(Settings).filter(Settings.key == key).first()
            if not row:
                return {}
            payload = json.loads(row.value or '{}')
            session.delete(row)
            session.commit()
            return payload

    # ------------------------------------------------------------ flow

    def list_institutions(self, country: str = 'GB') -> List[Institution]:
        response = self.http.get(f'{self.base_url}/aspsps',
                                 headers=self._headers(),
                                 params={'country': country, 'psu_type': 'personal'})
        data = self._check(response, 'aspsps')
        out = []
        for item in data.get('aspsps', []):
            name, ctry = item.get('name'), item.get('country') or country
            if not name:
                continue
            out.append(Institution(provider=self.id, reference=f'{name}|{ctry}',
                                   name=name, country=ctry, logo_url=item.get('logo')))
        return out

    def start_link(self, institution_id: str, redirect_uri: str,
                   state: Optional[str] = None) -> LinkStart:
        if not redirect_uri:
            raise ProviderError('A redirect URI is required (the URL the bank returns to).')
        if '|' not in institution_id:
            raise ProviderError('institution_id must look like "Bank Name|GB"')
        name, country = institution_id.rsplit('|', 1)
        state = state or os.urandom(16).hex()
        body = {
            'access': {'valid_until': (datetime.utcnow() + timedelta(days=90)).isoformat() + 'Z'},
            'aspsp': {'name': name, 'country': country},
            'state': state,
            'redirect_url': redirect_uri,
            'psu_type': 'personal',
        }
        response = self.http.post(f'{self.base_url}/auth', headers=self._headers(), json_body=body)
        data = self._check(response, 'auth')
        if not data.get('url'):
            raise ProviderError('Enable Banking did not return an authorisation URL')
        self._save_state(state, {'redirect_uri': redirect_uri, 'institution': name,
                                 'country': country})
        return LinkStart(mode='redirect', url=data['url'], state=state,
                         instructions='Open the URL, authorise at your bank, then pass the '
                                      'returned code to the callback command.')

    def complete_link(self, code: str, state: Optional[str] = None) -> Dict[str, Any]:
        if not code:
            raise ProviderError('Missing authorisation code')
        response = self.http.post(f'{self.base_url}/sessions', headers=self._headers(),
                                  json_body={'code': code})
        data = self._check(response, 'sessions')
        session_id = data.get('session_id')
        if not session_id:
            raise ProviderError('Enable Banking did not return a session id')

        accounts = []
        for item in data.get('accounts', []):
            uid = item.get('uid')
            if not uid:
                continue
            accounts.append({
                'uid': uid,
                'name': item.get('name') or item.get('product') or 'Bank account',
                'currency': item.get('currency') or 'GBP',
                'iban': item.get('account_id', {}).get('iban') if isinstance(item.get('account_id'), dict) else None,
                'type': ACCOUNT_TYPES.get(item.get('cash_account_type', ''), 'checking'),
                'raw': item,
            })

        pending = self._pop_state(state) if state else {}
        return {
            'provider': self.id,
            'display_name': pending.get('institution') or 'Enable Banking',
            'institution': {'name': pending.get('institution') or 'Enable Banking',
                            'reference': f"{pending.get('institution', 'Enable Banking')}|{pending.get('country', 'GB')}",
                            'country': pending.get('country', 'GB')},
            'credentials': {'session_id': session_id, 'accounts': accounts},
            'consent_expires_at': (datetime.utcnow() + timedelta(days=90)).isoformat(),
        }

    # ------------------------------------------------------------ data

    def _balance(self, uid: str, currency: str) -> Dict[str, Optional[float]]:
        response = self.http.get(f'{self.base_url}/accounts/{uid}/balances',
                                 headers=self._headers())
        data = self._check(response, 'balances')
        current = available = None
        for balance in data.get('balances', []):
            amount = balance.get('balance_amount') or {}
            try:
                value = float(amount.get('amount'))
            except (TypeError, ValueError):
                continue
            kind = (balance.get('balance_type') or '').upper()
            if kind in ('CLBD', 'ITBD', 'OPBD') and current is None:
                current = value
            elif kind in ('XPCD', 'AVLB', 'FWAV') and available is None:
                available = value
        return {'current': current if current is not None else available,
                'available': available if available is not None else current}

    def fetch_accounts(self, credentials: dict) -> List[BankAccount]:
        out = []
        for item in credentials.get('accounts', []):
            balances = self._balance(item['uid'], item.get('currency', 'GBP'))
            out.append(BankAccount(
                provider_account_id=item['uid'],
                name=item.get('name') or 'Bank account',
                currency=item.get('currency', 'GBP'),
                iban_masked=mask_iban(item.get('iban')),
                account_type=item.get('type', 'checking'),
                balance_current=balances['current'],
                balance_available=balances['available'],
                raw=item.get('raw', {}),
            ))
        return out

    def fetch_transactions(self, credentials: dict, account: BankAccount,
                           since: Optional[datetime] = None) -> List[BankTxn]:
        since = since or (datetime.utcnow() - timedelta(days=90))
        params = {'date_from': since.date().isoformat()}
        transactions: List[Dict[str, Any]] = []
        for _ in range(10):  # bounded pagination
            response = self.http.get(
                f'{self.base_url}/accounts/{account.provider_account_id}/transactions',
                headers=self._headers(), params=params)
            data = self._check(response, 'transactions')
            transactions.extend(data.get('transactions', []))
            continuation = data.get('continuation_key')
            if not continuation:
                break
            params['continuation_key'] = continuation

        out = []
        for item in transactions:
            amount = item.get('transaction_amount') or {}
            try:
                value = float(amount.get('amount'))
            except (TypeError, ValueError):
                continue
            booked = item.get('booking_date') or item.get('value_date')
            when = self._parse_date(booked)
            creditor = (item.get('creditor') or {}).get('name')
            debtor = (item.get('debtor') or {}).get('name')
            remittance = item.get('remittance_information') or []
            description = ' '.join(str(r) for r in remittance if r) or creditor or debtor or 'Bank transaction'
            out.append(BankTxn(
                external_id=str(item.get('transaction_id') or item.get('entry_reference') or ''),
                booked_date=when,
                amount=value,
                currency=amount.get('currency') or account.currency,
                description=description.strip()[:500],
                counterparty=creditor or debtor,
                pending=(item.get('status') or '').upper() == 'PDNG',
                raw=item,
            ))
        return out

    def revoke(self, credentials: dict) -> None:
        session_id = credentials.get('session_id')
        if not session_id:
            return
        self.http.request('DELETE', f'{self.base_url}/sessions/{session_id}',
                          headers=self._headers())

    @staticmethod
    def _parse_date(value) -> Optional[datetime]:
        if not value:
            return None
        text = str(value)[:10]
        try:
            return datetime.strptime(text, '%Y-%m-%d')
        except ValueError:
            try:
                return datetime.fromisoformat(str(value))
            except ValueError:
                return None
