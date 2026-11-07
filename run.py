#!/usr/bin/env python3
"""Development/production entry point.

Honours TALLY_HOST (default 127.0.0.1), PORT (default 5000) and TALLY_ALLOW_REMOTE.
Binding to loopback by default is deliberate: this app holds your entire financial
life and has no business being reachable from the LAN without a token.
"""
import os
import sys

from app import create_app
from app.config import get_host, get_port

app = create_app()

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_ENV') == 'development'
    host = get_host()
    port = get_port()

    if host not in ('127.0.0.1', 'localhost', '::1') and \
            os.getenv('TALLY_ALLOW_REMOTE', '').lower() not in ('1', 'true', 'yes'):
        print(f"Refusing to bind {host} without TALLY_ALLOW_REMOTE=1 (use a token and TLS).",
              file=sys.stderr)
        sys.exit(2)

    print("=" * 56)
    print(" Tally Local")
    print(f" http://{host}:{port}")
    print("=" * 56)

    app.run(host=host, port=port, debug=debug_mode)
