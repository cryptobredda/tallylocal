import json
import os
import threading
from copy import deepcopy

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.getenv('DATA_DIR', APP_DIR)
CONFIG_PATH = os.path.join(DATA_ROOT, 'integrations.json')
_lock = threading.Lock()

DEFAULT_CONFIG = {
    "moralis": {"enabled": False, "api_key": ""},
    "helius": {"enabled": False, "api_key": ""},
}


def _ensure_file():
    os.makedirs(DATA_ROOT, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)


def _read_config():
    _ensure_file()
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = deepcopy(DEFAULT_CONFIG)
    # Merge defaults to ensure missing keys exist
    merged = deepcopy(DEFAULT_CONFIG)
    for key, value in data.items():
        if isinstance(value, dict):
            merged[key].update(value)
    return merged


def _write_config(data):
    tmp_path = CONFIG_PATH + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def get_wallet_integrations():
    with _lock:
        return deepcopy(_read_config())


def update_wallet_integrations(updates):
    with _lock:
        config = _read_config()
        for key, value in (updates or {}).items():
            if key not in config or not isinstance(value, dict):
                continue
            config[key]['enabled'] = bool(value.get('enabled'))
            config[key]['api_key'] = (value.get('api_key') or '').strip()
        _write_config(config)
        return deepcopy(config)


def get_wallet_api_key(provider):
    provider = (provider or '').lower()
    config = get_wallet_integrations()
    entry = config.get(provider)
    if entry and entry.get('enabled') and entry.get('api_key'):
        return entry['api_key']
    return None
