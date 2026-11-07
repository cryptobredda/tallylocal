"""The agent-connection surface the Settings panel talks to."""
import json


def test_agent_setup_reports_endpoint_and_tokens(client):
    response = client.get('/api/v1/agent/setup')
    assert response.status_code == 200
    data = response.get_json()

    assert data['mcp']['transport'] == 'streamable-http'
    assert data['mcp']['auth'] == 'bearer'
    assert data['mcp']['url'].endswith(':8765/mcp')
    assert data['mcp']['port'] == 8765
    assert isinstance(data['tokens'], list)
    assert data['skill']['url'] == '/api/v1/agent/skill'
    assert data['ocr']['available'] in (True, False)
    assert 'engine' in data['ocr']


def test_agent_setup_uses_the_host_the_browser_used(client, user):
    from app.auth_service import create_agent_token
    raw, _ = create_agent_token(user.id, 'host-test', ['read'])
    response = client.get('/api/v1/agent/setup',
                          headers={'Host': 'finance.lan:5000',
                                   'Authorization': f'Bearer {raw}'})
    data = response.get_json()
    assert response.status_code == 200
    assert data['mcp']['url'] == 'http://finance.lan:8765/mcp'
    assert data['mcp']['health'] == 'http://finance.lan:8765/health'


def test_skill_download_is_markdown(client):
    response = client.get('/api/v1/agent/skill')
    assert response.status_code == 200
    assert response.mimetype == 'text/markdown'
    assert 'attachment; filename="SKILL.md"' in response.headers['Content-Disposition']
    assert response.get_data(as_text=True).startswith('---')


def test_agent_token_creation_returns_a_usable_secret(client):
    response = client.post('/api/v1/tokens', json={'name': 'test-agent',
                                                   'scopes': ['read', 'write']},
                           headers={'X-Tally-Client': 'web'})
    assert response.status_code == 201
    data = response.get_json()
    assert data['token'].startswith('tal_')
    assert data['record']['scopes'] == ['read', 'write']

    tokens = client.get('/api/v1/agent/setup').get_json()['tokens']
    assert any(t['id'] == data['record']['id'] for t in tokens)
    assert all('token' not in t for t in tokens)


def test_agent_test_endpoint_is_well_formed(client):
    response = client.post('/api/v1/agent/test', headers={'X-Tally-Client': 'web'})
    assert response.status_code == 200
    payload = response.get_json()
    assert 'ok' in payload
    json.dumps(payload)
