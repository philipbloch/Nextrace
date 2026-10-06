import inspect
import sys
from types import SimpleNamespace

import pytest

from nextrace.cli import _build_parser, main


@pytest.mark.parametrize("reload", [False, True])
def test_dashboard_reload_uses_an_importable_factory(tmp_path, monkeypatch, reload):
    from nextrace.cli import run_dashboard

    calls = []
    app = object()
    monkeypatch.setitem(
        sys.modules, "uvicorn", SimpleNamespace(run=lambda *a, **kw: calls.append((a, kw)))
    )
    monkeypatch.setattr("nextrace.dashboard.app.create_app", lambda path: app)
    monkeypatch.setenv("NEXTRACE_DB", "before-test.db")
    assert run_dashboard(str(tmp_path / "traces.db"), "127.0.0.1", 8765, reload) == 0
    args, kwargs = calls[0]
    assert args[0] == ("nextrace.dashboard.app:create_app" if reload else app)
    assert kwargs.get("factory", False) is reload
    assert kwargs.get("reload", False) is reload


@pytest.mark.parametrize(
    "arguments",
    [
        ["dashboard"],
        ["mcp-proxy", "--server", "example", "--", "python", "server.py"],
        ["mcp-http-proxy", "--server", "example", "--target", "https://example.com/mcp"],
        ["codex-import", "--application", "app"],
        ["claude-import", "--application", "app"],
        ["pi-import", "--application", "app"],
        ["import-local-usage"],
        ["export-otel"],
        ["set-project"],
    ],
)
def test_commands_bind_to_their_handlers(arguments):
    options = vars(_build_parser("nextrace").parse_args(arguments))
    handler = options.pop("handler")
    options.pop("command_name")
    inspect.signature(handler).bind(**options)


def test_stdio_dispatch_strips_separator_and_forwards_options(monkeypatch):
    captured = {}

    def run_proxy(**options):
        captured.update(options)
        return 7

    monkeypatch.setattr("nextrace.cli.run_stdio_proxy", run_proxy)
    assert main(["mcp-proxy", "--server", "test", "--db", "test.db", "--", "python", "-V"]) == 7
    assert captured["command"] == ["python", "-V"]
    assert captured["db_path"] == "test.db"
