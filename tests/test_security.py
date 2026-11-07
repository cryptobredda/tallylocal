"""Security regressions: these are the bugs that made the app unsafe to expose."""
import base64


def test_cors_is_not_wildcard(client):
    response = client.get('/api/auth/status', headers={'Origin': 'http://evil.example'})
    assert 'Access-Control-Allow-Origin' not in response.headers
    assert 'Access-Control-Allow-Credentials' not in response.headers


def test_configured_origin_is_allowed(client):
    from flask import current_app
    with client.application.app_context():
        current_app.config['TALLY_CORS_ORIGINS'] = ['https://app.example']
    response = client.get('/api/auth/status', headers={'Origin': 'https://app.example'})
    assert response.headers.get('Access-Control-Allow-Origin') == 'https://app.example'


def test_cookie_write_requires_csrf_marker(client):
    response = client.post('/api/transactions', json={'description': 'x', 'amount': 1})
    assert response.status_code == 403
    payload = response.get_json()
    assert payload['error']['code'] == 'csrf_missing'


def test_api_requires_auth():
    from app import create_app
    anonymous = create_app({'TESTING': True}).test_client()
    response = anonymous.get('/api/v1/accounts')
    assert response.status_code == 401


def test_token_scopes_are_enforced(client, user, db_path):
    from app.auth_service import create_agent_token
    raw, _ = create_agent_token(user.id, 'readonly', ['read'])

    read = client.get('/api/v1/accounts', headers={'Authorization': f'Bearer {raw}'})
    assert read.status_code == 200

    write = client.post('/api/v1/transactions',
                        headers={'Authorization': f'Bearer {raw}'},
                        json={'description': 'nope', 'amount': 5})
    assert write.status_code == 403
    assert 'write' in write.get_json()['error']['message']


def test_bad_token_is_rejected(client):
    response = client.get('/api/v1/accounts', headers={'Authorization': 'Bearer tal_bogus'})
    assert response.status_code == 401


def test_session_cookie_flags(client):
    response = client.post('/api/auth/login',
                           json={'username': 'nobody', 'password': 'x'},
                           headers={'X-Tally-Client': 'web'})
    cookie = response.headers.get('Set-Cookie', '')
    if cookie:
        assert 'HttpOnly' in cookie
        assert 'SameSite=Lax' in cookie or 'SameSite=lax' in cookie


def test_security_headers(client):
    response = client.get('/api/v1/health')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['Cache-Control'] == 'no-store'
