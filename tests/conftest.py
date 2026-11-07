import base64
import os
import tempfile
import uuid

# Must be set before any app module reads configuration at import time.
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='tally-tests-'))
os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production')
os.environ.setdefault('TALLY_ENCRYPTION_KEY',
                      base64.urlsafe_b64encode(b'0' * 32).decode())

import pytest  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    from app.database import init_db
    path = str(tmp_path / 'finance.db')
    init_db(path)
    return path


@pytest.fixture
def core(db_path):
    from app.core import TallyCore
    return TallyCore(db_path=db_path, actor='test')


@pytest.fixture
def user(tmp_path):
    from app.auth_service import create_user, get_user_by_username, init_auth_db
    init_auth_db()
    username = f'user_{uuid.uuid4().hex[:8]}'
    db_file = str(tmp_path / f'{username}.db')
    from app.database import init_db
    init_db(db_file)
    create_user(username, 'password123', is_admin=True, db_path=db_file)
    return get_user_by_username(username)


@pytest.fixture
def client(user):
    from app import create_app
    app = create_app({'TESTING': True, 'WTF_CSRF_ENABLED': False})
    client = app.test_client()
    response = client.post('/api/auth/login',
                           json={'username': user.username, 'password': 'password123'},
                           headers={'X-Tally-Client': 'web'})
    assert response.status_code == 200, response.get_data(as_text=True)
    return client


class FakeHttp:
    """Records requests and returns queued responses. No sockets in tests."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def _next(self, method, url, **kwargs):
        self.calls.append({'method': method, 'url': url, **kwargs})
        if not self.responses:
            raise AssertionError(f'Unexpected HTTP call: {method} {url}')
        status, payload = self.responses.pop(0)
        from app.banking.base import HttpResponse
        return HttpResponse(status, payload if isinstance(payload, (dict, list)) else None,
                            payload if isinstance(payload, str) else '')

    def get(self, url, **kwargs):
        return self._next('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self._next('POST', url, **kwargs)

    def request(self, method, url, **kwargs):
        return self._next(method, url, **kwargs)


@pytest.fixture
def fake_http():
    return FakeHttp
