"""Network MCP: bearer auth, per-token scoping, real client handshake."""
import asyncio
import socket
import threading
import time

import pytest


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def mcp_server(user):
    import uvicorn
    from app.auth_service import create_agent_token
    from app.mcp_http import app

    write_token, _ = create_agent_token(user.id, 'rw', ['read', 'write'])
    read_token, _ = create_agent_token(user.id, 'ro', ['read'])
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                           log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    import requests
    for _ in range(100):
        try:
            if requests.get(f'http://127.0.0.1:{port}/health', timeout=1).ok:
                break
        except Exception:
            time.sleep(0.1)

    yield {'port': port, 'write': write_token, 'read': read_token}
    server.should_exit = True
    thread.join(timeout=5)


def test_health_needs_no_token(mcp_server):
    import requests
    response = requests.get(f"http://127.0.0.1:{mcp_server['port']}/health", timeout=5)
    assert response.status_code == 200
    payload = response.json()
    assert payload['service'] == 'tally-mcp'
    assert payload['auth'] == 'bearer'


def test_mcp_rejects_missing_and_bad_tokens(mcp_server):
    import requests
    base = f"http://127.0.0.1:{mcp_server['port']}/mcp"
    assert requests.post(base, json={}, timeout=5).status_code == 401
    assert requests.post(base, json={}, timeout=5,
                         headers={'Authorization': 'Bearer tal_not_real'}).status_code == 401


def test_mcp_client_handshake_and_read_only_scoping(mcp_server):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = f"http://127.0.0.1:{mcp_server['port']}/mcp"

    async def tools_for(token):
        async with httpx2.AsyncClient(headers={'Authorization': f'Bearer {token}'},
                                      timeout=20) as client:
            async with streamable_http_client(url, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    names = [tool.name for tool in result.tools]
                    context = await session.call_tool('get_context', {'period': '30d'})
                    assert context.content
                    return names

    writable = asyncio.run(tools_for(mcp_server['write']))
    readonly = asyncio.run(tools_for(mcp_server['read']))

    assert 'get_context' in writable and 'update_transaction' in writable
    assert 'get_context' in readonly
    assert 'update_transaction' not in readonly
    assert len(readonly) < len(writable)
