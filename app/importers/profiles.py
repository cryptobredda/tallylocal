"""Bank profiles: declarative hints, not code.

A profile never replaces the generic algorithm. It tells the parser what this
bank calls its columns, how it writes dates, and which rows are noise. If a bank
changes its layout, editing YAML is enough.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, List, Optional

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'profiles')

GENERIC = {
    'id': 'generic',
    'name': 'Generic statement',
    'match': {'any': []},
    'dates': ['%d %b %y', '%d %b %Y', '%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d', '%d-%m-%Y'],
    'columns': {
        'date': ['date', 'transaction date', 'posted', 'booking date'],
        'description': ['description', 'details', 'payee', 'reference', 'transaction', 'narrative'],
        'amount': ['amount', 'value', 'transaction amount'],
        'out': ['paid out', 'money out', 'debit', 'withdrawal'],
        'in': ['paid in', 'money in', 'credit', 'deposit'],
        'balance': ['balance', 'running balance'],
        'type': ['type', 'debit/credit'],
    },
    'skip_rows': ['balance brought forward', 'balance carried forward', 'opening balance',
                  'closing balance', 'page 1 of', 'sort code', 'account number'],
    'currency': 'GBP',
}


def _load_yaml(path):
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('pyyaml is required for bank profiles') from exc
    with open(path, 'r', encoding='utf-8') as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def load_profiles() -> List[dict]:
    profiles = []
    if os.path.isdir(PROFILE_DIR):
        for name in sorted(os.listdir(PROFILE_DIR)):
            if not name.endswith(('.yaml', '.yml')):
                continue
            try:
                data = _load_yaml(os.path.join(PROFILE_DIR, name))
                if data and data.get('id'):
                    profiles.append(data)
            except Exception:
                continue
    profiles.append(GENERIC)
    return profiles


def detect_profile(text: str, filename: str = '', hint: Optional[str] = None) -> dict:
    """Pick the best profile by keyword score. Falls back to generic."""
    haystack = f'{filename}\n{text[:8000]}'.lower()
    if hint:
        for profile in load_profiles():
            if profile['id'] == hint or profile.get('name', '').lower() == hint.lower():
                return profile

    best, best_score = None, 0
    for profile in load_profiles():
        if profile['id'] == 'generic':
            continue
        score = 0
        for keyword in (profile.get('match') or {}).get('any', []):
            if keyword.lower() in haystack:
                score += 2
        for header in (profile.get('match') or {}).get('headers_all', []):
            if header.lower() in haystack:
                score += 1
        if score > best_score:
            best, best_score = profile, score
    return best or GENERIC


def column_aliases(profile: dict, field: str) -> List[str]:
    return [a.lower() for a in (profile.get('columns') or {}).get(field, [])]
