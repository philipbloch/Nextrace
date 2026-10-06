import threading
from http.server import ThreadingHTTPServer

import pytest

from nextrace import SQLiteStore
from nextrace.mcp_proxy import run_http_proxy


@pytest.fixture
def http_proxy(tmp_path, monkeypatch):
    ready = threading.Event()
    servers = []

    def create_server(address, handler):
        server = ThreadingHTTPServer(address, handler)
        servers.append(server)
        ready.set()
        return server

    monkeypatch.setattr("nextrace.mcp_proxy.ThreadingHTTPServer", create_server)
    path = tmp_path / "proxy.db"
    thread = threading.Thread(
        target=run_http_proxy,
        kwargs={
            "application": "test",
            "server": "test-mcp",
            "target_url": "https://example.com/mcp",
            "host": "127.0.0.1",
            "port": 0,
            "db_path": path,
            "timeout": 5,
        },
        daemon=True,
    )
    thread.start()
    try:
        assert ready.wait(5)
        yield servers[0].server_port, SQLiteStore(path)
    finally:
        if servers:
            servers[0].shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()
