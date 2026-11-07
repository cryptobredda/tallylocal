"""Network MCP endpoint: Streamable HTTP, bearer-token authenticated.

The web UI runs on :5000 and this runs beside it on :8765, so a homelab agent
can reach Tally across the LAN without exposing the browser session.

Each token resolves to its owner before any tool runs, so two people sharing
one instance never see each other's ledger. Tokens without the ``write`` scope
get a server with the mutating tools removed.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any, Dict, Tuple

from mcp.server.transport_security import TransportSecuritySettings

from app.auth_service import get_user_by_id, verify_agent_token
from app.core import TallyCore
from app.mcp_server import WRITE_TOOLS, build_server

VERSION = '2.0.0'

_apps: Dict[Tuple[int, bool], Any] = {}
_started: Dict[Tuple[int, bool], Any] = {}
_apps_lock = threading.Lock()
_start_lock: asyncio.Lock | None = None


def _security() -> TransportSecuritySettings:
    """Auth is the bearer token; the Host header is not a security boundary here.

    Set TALLY_MCP_ALLOWED_HOSTS=192.168.1.50,finance.lan to turn DNS-rebinding
    protection back on with an explicit allowlist.
    """
    allowed = [h.strip() for h in (os.getenv('TALLY_MCP_ALLOWED_HOSTS') or '').split(',')
               if h.strip()]
    if allowed:
        return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=allowed,
                                         allowed_origins=['*'])
    return TransportSecuritySettings(enable_dns_rebinding_protection=False,
                                     allowed_hosts=['*'],
                                     allowed_origins=['*'])


def _app_for(user, can_write: bool):
    key = (user.id, can_write)
    with _apps_lock:
        app = _apps.get(key)
        if app is None:
            db_path = getattr(user, 'db_path', None)
            actor = f'mcp:{user.username}'
            core = TallyCore(db_path=db_path, actor=actor) if db_path else TallyCore(actor=actor)
            server = build_server(core, read_only=not can_write)
            app = server.streamable_http_app(host='0.0.0.0',
                                             transport_security=_security())
            _apps[key] = app
        return app


async def _running_app(user, can_write: bool):
    """Start the per-user app's lifespan once (session manager needs it)."""
    global _start_lock
    key = (user.id, can_write)
    app = _app_for(user, can_write)
    if key in _started:
        return app
    if _start_lock is None:
        _start_lock = asyncio.Lock()
    async with _start_lock:
        if key not in _started:
            context = app.router.lifespan_context(app)
            await context.__aenter__()
            _started[key] = context
    return app


async def _respond(send, status: int, payload: dict, extra_headers=None):
    body = json.dumps(payload).encode('utf-8')
    headers = [(b'content-type', b'application/json'),
               (b'content-length', str(len(body)).encode())]
    headers.extend(extra_headers or [])
    await send({'type': 'http.response.start', 'status': status, 'headers': headers})
    await send({'type': 'http.response.body', 'body': body})


def _bearer(scope) -> str:
    for key, value in scope.get('headers') or []:
        if key.lower() == b'authorization':
            raw = value.decode('latin-1')
            if raw.lower().startswith('bearer '):
                return raw[7:].strip()
    return ''


async def app(scope, receive, send):
    if scope['type'] == 'lifespan':
        while True:
            message = await receive()
            if message['type'] == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif message['type'] == 'lifespan.shutdown':
                await send({'type': 'lifespan.shutdown.complete'})
                return

    if scope['type'] != 'http':
        return

    path = (scope.get('path') or '').rstrip('/') or '/'

    if path in ('/health', '/healthz'):
        from app.ocr_service import ocr_service
        ocr = ocr_service.status()
        return await _respond(send, 200, {
            'status': 'ok', 'service': 'tally-mcp', 'version': VERSION,
            'transport': 'streamable-http', 'auth': 'bearer',
            'ocr': {'available': ocr['available'], 'engine': ocr['engine']},
        })

    if not path.startswith('/mcp'):
        return await _respond(send, 404, {'error': 'not_found'})

    token = _bearer(scope)
    if not token:
        return await _respond(send, 401, {'error': 'unauthorized',
                                          'detail': 'Send Authorization: Bearer <token>'},
                              [(b'www-authenticate', b'Bearer realm="tally"')])

    record = verify_agent_token(token)
    user = get_user_by_id(record.user_id) if record else None
    if not record or not user:
        return await _respond(send, 401, {'error': 'unauthorized'},
                              [(b'www-authenticate', b'Bearer realm="tally"')])

    target = await _running_app(user, 'write' in record.scope_set())
    return await target(scope, receive, send)


def serve(host: str | None = None, port: int | None = None):
    import uvicorn
    uvicorn.run(app,
                host=host or os.getenv('TALLY_MCP_HOST', '0.0.0.0'),
                port=port or int(os.getenv('TALLY_MCP_PORT', '8765')),
                log_level=os.getenv('TALLY_MCP_LOG', 'warning'))


def start_background():
    """Run MCP beside the Flask server; failures must not kill the web UI."""
    import uvicorn
    config = uvicorn.Config(app,
                            host=os.getenv('TALLY_MCP_HOST', '0.0.0.0'),
                            port=int(os.getenv('TALLY_MCP_PORT', '8765')),
                            log_level=os.getenv('TALLY_MCP_LOG', 'warning'))
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name='tally-mcp-http', daemon=True)
    thread.start()
    return thread


if __name__ == '__main__':
    serve()
