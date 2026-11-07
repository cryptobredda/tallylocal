"""Bank adapters, offline. No sockets: providers take an injectable HTTP client."""
from datetime import datetime

import pytest

from app.banking.base import BankAccount, BankTxn
from app.banking.crypto import decrypt_dict, encrypt_dict
from app.banking.enablebanking import EnableBankingProvider
from app.banking.simplefin import SimpleFINProvider
from app.banking.sync import save_connection, sync_connection, revoke_connection
from tests.conftest import FakeHttp


def _simplefin_payload():
    return {
        'errlist': [],
        'connections': [{'conn_id': 'c1', 'name': 'My Bank'}],
        'accounts': [{
            'id': '2930002', 'name': 'Current', 'currency': 'USD',
            'balance': '100.23', 'available-balance': '75.23',
            'transactions': [
                {'id': 'sf1', 'posted': 1793664000, 'amount': '-42.10',
                 'description': 'TESCO STORES', 'payee': 'Tesco'},
                {'id': 'sf2', 'posted': 1793836800, 'amount': '3200.00',
                 'description': 'ACME SALARY', 'payee': 'Acme'},
            ],
        }],
    }


def test_simplefin_link_and_sync(core, monkeypatch):
    import base64 as b64
    token = b64.b64encode(b'https://bridge.simplefin.org/simplefin/claim/demo').decode()
    fake = FakeHttp([
        (200, 'https://user:pass@bridge.simplefin.org/simplefin'),
        (200, _simplefin_payload()),
        (200, _simplefin_payload()),
        (200, _simplefin_payload()),
        (200, _simplefin_payload()),
        (200, _simplefin_payload()),
    ])
    provider = SimpleFINProvider(core=core, http=fake)
    link = provider.complete_link(token)
    saved = save_connection(core, link)

    assert saved['institution'] == 'SimpleFIN Bridge'
    assert len(saved['accounts']) == 1

    monkeypatch.setattr('app.banking.sync.get_provider', lambda *a, **k: provider)
    result = sync_connection(core, saved['connection_id'])

    assert result['added'] == 2
    assert core.list_transactions()['total'] == 2
    kinds = sorted(t['type'] for t in core.list_transactions()['transactions'])
    assert kinds == ['expense', 'income']

    again = sync_connection(core, saved['connection_id'])
    assert again['added'] == 0
    assert again['skipped'] == 2


def test_sync_updates_balances_without_touching_ledger(core, monkeypatch):
    import base64 as b64
    token = b64.b64encode(b'https://bridge.simplefin.org/simplefin/claim/demo').decode()
    fake = FakeHttp([
        (200, 'https://user:pass@bridge.simplefin.org/simplefin'),
        (200, _simplefin_payload()),
        (200, _simplefin_payload()),
        (200, _simplefin_payload()),
        (200, _simplefin_payload()),
        (200, _simplefin_payload()),
    ])
    provider = SimpleFINProvider(core=core, http=fake)
    saved = save_connection(core, provider.complete_link(token))

    monkeypatch.setattr('app.banking.sync.get_provider', lambda *a, **k: provider)
    sync_connection(core, saved['connection_id'])

    account = next(a for a in core.list_accounts() if a.get('connection_id'))
    assert account['balance_source'] == 'bank_sync'
    assert account['balance'] == 100.23


def test_enablebanking_link_and_data(core):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()

    fake = FakeHttp([
        (200, {'url': 'https://bank.example/auth'}),
        (200, {'session_id': 'sess-1', 'accounts': [
            {'uid': 'acc-1', 'name': 'HSBC Current', 'currency': 'GBP',
             'cash_account_type': 'CACC',
             'account_id': {'iban': 'GB29NWBK60161331926819'}},
        ]}),
        (200, {'balances': [
            {'balance_amount': {'currency': 'GBP', 'amount': '1284.55'},
             'balance_type': 'CLBD'},
            {'balance_amount': {'currency': 'GBP', 'amount': '1200.00'},
             'balance_type': 'XPCD'},
        ]}),
        (200, {'transactions': [
            {'transaction_id': 'eb-1', 'booking_date': '2026-11-03',
             'transaction_amount': {'currency': 'GBP', 'amount': '-42.10'},
             'creditor': {'name': 'Tesco'},
             'remittance_information': ['TESCO STORES 1234'],
             'status': 'BOOK'},
        ]}),
    ])
    provider = EnableBankingProvider(core=core, app_id='app-id', private_key=pem, http=fake)

    start = provider.start_link('HSBC UK|GB', 'http://127.0.0.1:5000/api/v1/bank/enablebanking/callback')
    assert start.mode == 'redirect'
    assert start.url == 'https://bank.example/auth'

    link = provider.complete_link('code-123', start.state)
    saved = save_connection(core, link)
    assert saved['institution'] == 'HSBC UK'
    assert saved['accounts'][0]['name'] == 'HSBC Current'

    accounts = provider.fetch_accounts(link['credentials'])
    assert accounts[0].balance_current == 1284.55
    assert accounts[0].iban_masked == 'GB29 **** 6819'

    txns = provider.fetch_transactions(link['credentials'], accounts[0], datetime(2026, 11, 1))
    assert txns[0].external_id == 'eb-1'
    assert txns[0].absolute_amount == 42.10
    assert txns[0].kind == 'expense'


def test_enablebanking_requires_configuration():
    from app.banking.base import ProviderError
    provider = EnableBankingProvider(app_id='', private_key='')
    with pytest.raises(ProviderError):
        provider._jwt()


def test_credentials_are_encrypted_at_rest(core):
    payload = {'session_id': 'secret-session', 'accounts': []}
    token = encrypt_dict(payload)
    assert 'secret-session' not in token
    assert decrypt_dict(token) == payload


def test_revoked_connection_does_not_sync(core):
    link = {
        'provider': 'simplefin', 'display_name': 'SimpleFIN Bridge',
        'institution': {'name': 'SimpleFIN Bridge', 'reference': 'simplefin', 'country': 'US'},
        'credentials': {'access_url': 'https://x', 'accounts': [{'id': '1', 'name': 'A'}]},
        'consent_expires_at': None,
    }
    saved = save_connection(core, link)
    revoke_connection(core, saved['connection_id'])
    result = sync_connection(core, saved['connection_id'])
    assert 'revoked' in result['error'].lower()
