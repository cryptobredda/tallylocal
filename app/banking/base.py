"""Shared bank-provider types and the HTTP seam used by tests."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Institution:
    provider: str
    reference: str
    name: str
    country: str = 'GB'
    logo_url: Optional[str] = None


@dataclass
class BankAccount:
    provider_account_id: str
    name: str
    currency: str = 'GBP'
    iban_masked: Optional[str] = None
    account_type: str = 'checking'
    balance_current: Optional[float] = None
    balance_available: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BankTxn:
    external_id: str
    booked_date: Optional[datetime]
    amount: float          # signed: negative = money out
    currency: str = 'GBP'
    description: str = ''
    counterparty: Optional[str] = None
    pending: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return 'income' if self.amount > 0 else 'expense'

    @property
    def absolute_amount(self) -> float:
        return round(abs(self.amount), 2)


@dataclass
class LinkStart:
    mode: str                     # redirect | token
    url: str
    state: Optional[str] = None
    instructions: Optional[str] = None


class HttpResponse:
    def __init__(self, status: int, data: Any = None, text: str = ''):
        self.status = status
        self.data = data
        self.text = text

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        if self.data is not None:
            return self.data
        try:
            return json.loads(self.text or '{}')
        except ValueError:
            return {}


class ProviderError(Exception):
    """A bank provider failed. Message is safe to show to a user."""


class HttpClient:
    """Thin requests wrapper so providers can be tested without a network."""

    def request(self, method: str, url: str, headers=None, params=None, json_body=None,
                data=None, auth=None, timeout=40) -> HttpResponse:
        import requests
        try:
            response = requests.request(method, url, headers=headers, params=params,
                                        json=json_body, data=data, auth=auth,
                                        timeout=timeout)
        except Exception as exc:
            raise ProviderError(f'Network error calling {url}: {exc}') from exc
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return HttpResponse(response.status_code, payload, response.text[:2000])

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)


def mask_iban(iban: Optional[str]) -> Optional[str]:
    if not iban:
        return None
    compact = iban.replace(' ', '')
    if len(compact) <= 4:
        return '****'
    return f'{compact[:4]} **** {compact[-4:]}'
