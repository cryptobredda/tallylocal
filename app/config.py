"""Central configuration. Every env var the app understands lives here."""
import os
import secrets
import stat

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.getenv('DATA_DIR', APP_DIR)

TRUTHY = {'1', 'true', 'yes', 'on'}


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUTHY


def _persisted_secret(data_dir):
    """Load the instance secret, creating it 0600 on first run.

    Never falls back to a hardcoded value: a known SECRET_KEY lets anyone forge
    session cookies and read another user's finances.
    """
    path = os.path.join(data_dir, '.secret_key')
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            existing = fh.read().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass

    key = secrets.token_hex(32)
    try:
        os.makedirs(data_dir, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(key)
    except OSError:
        # Read-only data dir (e.g. a mounted config): an ephemeral key is safer
        # than a shared constant. Sessions reset on restart.
        pass
    return key


def get_secret_key():
    env = os.getenv('SECRET_KEY', '').strip()
    if env and env not in {'changeme', 'changeme-generate-with-openssl-rand-hex-32', 'dev-only-fallback'}:
        return env
    return _persisted_secret(DATA_DIR)


def allowed_origins():
    """Explicit CORS allowlist. Empty means same-origin only (the default)."""
    raw = os.getenv('TALLY_CORS_ORIGINS', '')
    return [o.strip().rstrip('/') for o in raw.split(',') if o.strip()]


def allow_remote():
    return _env_bool('TALLY_ALLOW_REMOTE', False)


def https_enabled():
    return _env_bool('TALLY_HTTPS', False)


def get_host():
    return os.getenv('TALLY_HOST', '127.0.0.1')


def get_port():
    try:
        return int(os.getenv('PORT', '5000'))
    except ValueError:
        return 5000


def max_upload_bytes():
    try:
        return int(os.getenv('TALLY_MAX_UPLOAD_MB', '50')) * 1024 * 1024
    except ValueError:
        return 50 * 1024 * 1024


def load_config():
    return {
        'SECRET_KEY': get_secret_key(),
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'SESSION_COOKIE_SECURE': https_enabled(),
        'PERMANENT_SESSION_LIFETIME': 60 * 60 * 12,
        'MAX_CONTENT_LENGTH': max_upload_bytes(),
        'TALLY_ALLOW_REMOTE': allow_remote(),
        'TALLY_CORS_ORIGINS': allowed_origins(),
    }
