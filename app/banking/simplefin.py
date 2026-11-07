"""SimpleFIN Bridge adapter (US/CA, ~$15/year).

SimpleFIN is a tiny read-only protocol: POST the setup token to claim an access
URL, then GET /accounts. No OAuth, no redirect, no certificates.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.banking.base import (BankAccount, BankTxn, HttpClient, Institution,
                              LinkStart, ProviderError, mask_iban)

BRIDGE_CREATE_URL = os.getenv('SIMPLEFIN_CREATE_URL',
                              'https://bridge.simplefin.org/simplefin/create')


class SimpleFINProvider:
    id = 'simplefin'
    label = 'SimpleFIN Bridge'
    region = 'US / CA'
    docs = 'https://www.simplefin.org/protocol.html'

    def __init__(self, core=None, http: Optional[HttpClient] = None):
        self.core = core
        self.http = http or HttpClient()

    @property
    def configured(self) -> bool:
        return True  # token-based: nothing to configure up front

    def list_institutions(self, country: str = 'US') -> List[Institution]:
        return [Institution(provider=self.id, reference='simplefin',
                            name='SimpleFIN Bridge', country=country)]

    def start_link(self, institution_id: str = 'simplefin', redirect_uri: Optional[str] = None,
                   state: Optional[str] = None) -> LinkStart:
        return LinkStart(
            mode='token', url=BRIDGE_CREATE_URL,
            instructions='Open the URL, connect your bank, copy the setup token it gives you, '
                         'then run the callback command with that token as --code.')

    def complete_link(self, token: str, state: Optional[str] = None) -> Dict[str, Any]:
        token = (token or '').strip()
        if not token:
            raise ProviderError('Paste the SimpleFIN setup token')
        try:
            claim_url = base64.b64decode(token + '=' * (-len(token) % 4)).decode('utf-8').strip()
        except Exception as exc:
            raise ProviderError('That does not look like a SimpleFIN setup token') from exc
        if not claim_url.startswith('https://'):
            raise ProviderError('SimpleFIN token must decode to an HTTPS claim URL')

        response = self.http.post(claim_url, timeout=40)
        if response.status in (402,):
            raise ProviderError('SimpleFIN Bridge subscription required (402)')
        if response.status == 403:
            raise ProviderError('This SimpleFIN token was already claimed or revoked (403)')
        if not response.ok:
            raise ProviderError(f'SimpleFIN claim failed ({response.status})')
        access_url = (response.text or '').strip()
        if not access_url.startswith('https://'):
            raise ProviderError('SimpleFIN did not return an access URL')

        accounts = self._fetch_accounts_payload(access_url).get('accounts', [])
        return {
            'provider': self.id,
            'display_name': 'SimpleFIN Bridge',
            'institution': {'name': 'SimpleFIN Bridge', 'reference': 'simplefin', 'country': 'US'},
            'credentials': {'access_url': access_url,
                            'accounts': [{'id': a.get('id'), 'name': a.get('name'),
                                          'currency': a.get('currency')} for a in accounts]},
            'consent_expires_at': None,
        }

    def _fetch_accounts_payload(self, access_url: str, since: Optional[datetime] = None) -> dict:
        params = {'version': 2}
        if since:
            params['start-date'] = int(since.timestamp())
        response = self.http.get(access_url.rstrip('/') + '/accounts', params=params, timeout=60)
        if response.status == 402:
            raise ProviderError('SimpleFIN Bridge payment required (402)')
        if response.status == 403:
            raise ProviderError('SimpleFIN access revoked or credentials invalid (403)')
        if not response.ok:
            raise ProviderError(f'SimpleFIN request failed ({response.status})')
        return response.json()

    def fetch_accounts(self, credentials: dict) -> List[BankAccount]:
        payload = self._fetch_accounts_payload(credentials['access_url'])
        out = []
        for item in payload.get('accounts', []):
            out.append(BankAccount(
                provider_account_id=str(item.get('id')),
                name=item.get('name') or 'SimpleFIN account',
                currency=(item.get('currency') or 'USD').upper()[:3],
                iban_masked=mask_iban(item.get('id')),
                account_type='checking',
                balance_current=self._number(item.get('balance')),
                balance_available=self._number(item.get('available-balance')),
                raw={k: v for k, v in item.items() if k != 'transactions'},
            ))
        return out

    def fetch_transactions(self, credentials: dict, account: BankAccount,
                           since: Optional[datetime] = None) -> List[BankTxn]:
        since = since or (datetime.utcnow() - timedelta(days=90))
        payload = self._fetch_accounts_payload(credentials['access_url'], since=since)
        match = next((a for a in payload.get('accounts', [])
                      if str(a.get('id')) == str(account.provider_account_id)), None)
        if not match:
            return []
        out = []
        for item in match.get('transactions', []):
            amount = self._number(item.get('amount'))
            if amount is None:
                continue
            posted = item.get('posted')
            when = datetime.utcfromtimestamp(int(posted)) if posted else None
            description = (item.get('description') or item.get('payee') or 'Transaction').strip()
            memo = (item.get('memo') or '').strip()
            out.append(BankTxn(
                external_id=str(item.get('id') or ''),
                booked_date=when,
                amount=amount,
                currency=(item.get('currency') or account.currency),
                description=(description + (f' — {memo}' if memo else ''))[:500],
                counterparty=item.get('payee'),
                pending=bool(item.get('pending')),
                raw=item,
            ))
        return out

    def revoke(self, credentials: dict) -> None:
        # SimpleFIN access is revoked from the bridge UI; nothing server-side to call.
        return None

    @staticmethod
    def _number(value) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
