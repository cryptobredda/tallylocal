"""Credential encryption at rest.

Bank sessions and SimpleFIN access URLs are as sensitive as the transactions.
They are encrypted with Fernet using a key that never lives in the database:

  TALLY_ENCRYPTION_KEY  (urlsafe base64, 32 bytes)  or
  <DATA_DIR>/.encryption_key  (created 0600 on first use)
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from app.config import DATA_DIR

ENV_KEY = 'TALLY_ENCRYPTION_KEY'


def _key_path() -> str:
    return os.path.join(DATA_DIR, '.encryption_key')


def _load_key() -> bytes:
    env = os.getenv(ENV_KEY)
    if env:
        return env.encode('utf-8')

    path = _key_path()
    if os.path.exists(path):
        with open(path, 'rb') as fh:
            return fh.read().strip()

    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as fh:
        fh.write(key)
    return key


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            'cryptography is required for bank connections: pip install cryptography') from exc
    return Fernet(_load_key())


def encrypt_dict(payload: Dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(payload).encode('utf-8')).decode('ascii')


def decrypt_dict(token: str) -> Dict[str, Any]:
    if not token:
        return {}
    return json.loads(_fernet().decrypt(token.encode('ascii')).decode('utf-8'))
