"""Provider registry."""
from __future__ import annotations

from typing import Dict, List, Optional

from app.banking.base import HttpClient
from app.banking.enablebanking import EnableBankingProvider
from app.banking.simplefin import SimpleFINProvider


def provider_classes() -> Dict[str, type]:
    return {'enablebanking': EnableBankingProvider, 'simplefin': SimpleFINProvider}


def get_provider(name: str, core=None, http: Optional[HttpClient] = None):
    key = (name or '').lower().replace('-', '').replace('_', '')
    aliases = {'enable': 'enablebanking', 'eb': 'enablebanking', 'nordigen': 'enablebanking',
               'simplefinbridge': 'simplefin', 'sf': 'simplefin'}
    key = aliases.get(key, key)
    cls = provider_classes().get(key)
    if not cls:
        raise ValueError(f'Unknown bank provider {name!r}. '
                         f'Available: {", ".join(sorted(provider_classes()))}')
    return cls(core=core, http=http) if http else cls(core=core)


def describe_providers() -> List[dict]:
    out = []
    for key, cls in provider_classes().items():
        instance = cls()
        out.append({
            'id': key,
            'name': instance.label,
            'region': instance.region,
            'configured': bool(instance.configured),
            'docs': instance.docs,
            'auth': 'redirect' if key == 'enablebanking' else 'token',
            'cost': ('Free restricted production for your own accounts; paid for public apps'
                     if key == 'enablebanking' else '~$15/year for the hosted bridge'),
        })
    out.append({'id': 'statement', 'name': 'Statement import (CSV/PDF/OFX/QIF/MT940)',
                'region': 'Anywhere', 'configured': True, 'docs': 'docs/statements.md',
                'auth': 'none', 'cost': 'free'})
    return out
