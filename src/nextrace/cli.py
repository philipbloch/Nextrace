from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nextrace.context import default_db_path
from nextrace.mcp_proxy import run_http_proxy, run_stdio_proxy


def _build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    subparsers = parser.add_subparsers(dest="command_name")
    database_options = argparse.ArgumentParser(add_help=False)
    database_options.add_argument(
        "--db",
        dest="db_path",
        default=str(default_db_path()),
        help="SQLite trace database path",
    )

    dashboard = subparsers.add_parser(
        "dashboard", parents=[database_options], help="Run the local dashboard"
    )
    dashboard.add_argument("--host", default="127.0.0.1", help="Host to bind")
    dashboard.add_argument("--port", default=8765, type=int, help="Port to bind")
    dashboard.add_argument("--reload", action="store_true", help="Enable uvicorn reload")

    mcp_options = argparse.ArgumentParser(add_help=False)
    mcp_options.add_argument(
        "--application",
        default="auto",
        help="Application name to record, or 'auto' to use the active project",
    )
    mcp_options.add_argument("--server", required=True, help="MCP server name to record")
    mcp_options.add_argument(
        "--session-id", default=None, help="Optional session id to attach to traces"
    )
    mcp_proxy = subparsers.add_parser(
        "mcp-proxy",
        parents=[database_options, mcp_options],
        help="Proxy a stdio MCP server and record redacted traces",
    )
    mcp_proxy.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --")

    http_proxy = subparsers.add_parser(
        "mcp-http-proxy",
        parents=[database_options, mcp_options],
        help="Proxy an HTTP MCP endpoint and record redacted traces",
    )
    http_proxy.add_argument(
        "--target", dest="target_url", required=True, help="Upstream HTTP MCP endpoint"
    )
    http_proxy.add_argument("--host", default="127.0.0.1", help="Local host to bind")
    http_proxy.add_argument("--port", default=8766, type=int, help="Local port to bind")
    http_proxy.add_argument(
        "--timeout", default=3600, type=float, help="Upstream socket timeout in seconds"
    )

    codex_import = subparsers.add_parser(
        "codex-import",
        parents=[database_options],
        help="Import local Codex token usage as redacted model traces",
    )
    codex_import.add_argument("--application", required=True, help="Application name to record")
    codex_import.add_argument("--codex-home", default="~/.codex", help="Codex home directory")
    codex_import.add_argument(
        "--session-id",
        dest="session_ids",
        action="append",
        default=[],
        help="Codex session id to import. Can be repeated.",
    )
    codex_import.add_argument(
        "--session-file",
        dest="session_files",
        action="append",
        default=[],
        help="Codex JSONL session file to import. Can be repeated.",
    )
    codex_import.add_argument(
        "--latest",
        type=int,
        default=1,
        help="Import this many latest sessions when no session id/file is provided",
    )
    codex_import.add_argument("--provider", default=None, help="Override provider name")
    codex_import.add_argument("--model", default=None, help="Override model name")

    claude_import = subparsers.add_parser(
        "claude-import",
        parents=[database_options],
        help="Import local Claude Code token usage as redacted model traces",
    )
    claude_import.add_argument("--application", required=True, help="Application name to record")
    claude_import.add_argument(
        "--claude-home", default="~/.claude", help="Claude Code home directory"
    )
    claude_import.add_argument(
        "--project-path",
        default=".",
        help="Project path whose Claude Code transcripts should be imported",
    )
    claude_import.add_argument(
        "--session-file",
        dest="session_files",
        action="append",
        default=[],
        help="Claude Code JSONL transcript file to import. Can be repeated.",
    )
    claude_import.add_argument(
        "--latest",
        type=int,
        default=None,
        help="Import this many latest project transcripts. By default all project transcripts are imported.",
    )
    claude_import.add_argument("--provider", default="anthropic", help="Provider name to record")

    pi_import = subparsers.add_parser(
        "pi-import",
        parents=[database_options],
        help="Import local Pi token usage as redacted model traces",
    )
    pi_import.add_argument("--application", required=True, help="Application name to record")
    pi_import.add_argument("--pi-home", default="~/.pi/agent", help="Pi agent home directory")
    pi_import.add_argument(
        "--project-path",
        default=".",
        help="Project path whose Pi sessions should be imported",
    )
    pi_import.add_argument(
        "--session-file",
        dest="session_files",
        action="append",
        default=[],
        help="Pi JSONL session file to import. Can be repeated.",
    )
    pi_import.add_argument(
        "--latest",
        type=int,
        default=None,
        help="Import this many latest project sessions. By default all project sessions are imported.",
    )

    local_import = subparsers.add_parser(
        "import-local-usage",
        parents=[database_options],
        help="Auto-import changed local AI-agent usage grouped by project",
    )
    local_import.add_argument("--codex-home", default="~/.codex", help="Codex home directory")
    local_import.add_argument(
        "--claude-home", default="~/.claude", help="Claude Code home directory"
    )
    local_import.add_argument("--pi-home", default="~/.pi/agent", help="Pi agent home directory")
    local_import.add_argument(
        "--state-path",
        default="~/.nextrace/local-usage-import-state.json",
        help="Path to the local importer state file",
    )
    local_import.add_argument(
        "--source",
        choices=("all", "codex", "claude", "pi"),
        default="all",
        help="Which local usage source to import",
    )
    local_import.add_argument(
        "--full",
        action="store_true",
        help="Ignore importer state and rescan all local usage files",
    )

    export = subparsers.add_parser(
        "export-otel", parents=[database_options], help="Export completed traces as OTLP/HTTP JSON"
    )
    export.add_argument(
        "--output", default="nextrace-traces.otel.json", help="Local OTLP JSON file"
    )
    export.add_argument(
        "--endpoint", default=None, help="Optional full OTLP/HTTP traces URL, including /v1/traces"
    )
    export.add_argument("--trace-id", default=None, help="Export one execution")
    export.add_argument("--application", default=None)
    export.add_argument("--since", default=None, help="Inclusive local ISO date/datetime")
    export.add_argument("--until", default=None, help="Exclusive local ISO date/datetime")

    set_project = subparsers.add_parser(
        "set-project",
        help="Set the active project used by global proxies running with --application auto",
    )
    set_project.add_argument(
        "--project-path",
        default=".",
        help="Project path to mark active",
    )
    set_project.add_argument(
        "--application",
        default=None,
        help="Optional application name. Defaults to the project directory name.",
    )
    set_project.add_argument(
        "--state-path",
        default=None,
        help="Optional active-project state file path",
    )

    for command, handler in (
        (dashboard, run_dashboard),
        (mcp_proxy, run_stdio_proxy),
        (http_proxy, run_http_proxy),
        (codex_import, run_codex_import),
        (claude_import, run_claude_import),
        (pi_import, run_pi_import),
        (local_import, run_import_local_usage),
        (set_project, run_set_project),
        (export, run_export_otel),
    ):
        command.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    prog = Path(sys.argv[0]).name if argv is None else "nextrace"
    parser = _build_parser(prog)
    options = vars(parser.parse_args(argv))
    command = options.pop("command_name")
    handler = options.pop("handler", None)
    if handler is None:
        parser.print_help()
        return 0
    if command == "mcp-proxy" and options["command"][:1] == ["--"]:
        options["command"] = options["command"][1:]
    return handler(**options)


def run_dashboard(db_path: str, host: str, port: int, reload: bool) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            'Dashboard dependencies are missing. Install with: python -m pip install -e ".[dashboard]"'
        )
        return 1

    from nextrace.dashboard.app import create_app

    app = create_app(Path(db_path))
    print(f"Nextrace dashboard: http://{host}:{port}")
    print(f"Trace database: {Path(db_path).expanduser()}")
    if reload:
        import os

        os.environ["NEXTRACE_DB"] = str(Path(db_path).expanduser().resolve())
        uvicorn.run(
            "nextrace.dashboard.app:create_app", factory=True, host=host, port=port, reload=True
        )
    else:
        uvicorn.run(app, host=host, port=port)
    return 0


def run_codex_import(
    *,
    application: str,
    db_path: str,
    codex_home: str,
    session_ids: list[str],
    session_files: list[str],
    latest: int,
    provider: str | None,
    model: str | None,
) -> int:
    from nextrace.codex_usage import find_codex_session_files, import_codex_usage

    files = [Path(path).expanduser() for path in session_files]
    if not files:
        files = find_codex_session_files(
            codex_home=codex_home,
            session_ids=session_ids,
            latest=latest,
        )
    return _import_sessions(
        files,
        application,
        db_path,
        "codex",
        "Codex",
        import_codex_usage,
        provider_override=provider,
        model_override=model,
    )


def run_claude_import(
    *,
    application: str,
    db_path: str,
    claude_home: str,
    project_path: str,
    session_files: list[str],
    latest: int | None,
    provider: str,
) -> int:
    from nextrace.claude_usage import find_claude_project_files, import_claude_usage

    files = [Path(path).expanduser() for path in session_files]
    if not files:
        files = find_claude_project_files(
            claude_home=claude_home,
            project_path=project_path,
            latest=latest,
        )
    return _import_sessions(
        files,
        application,
        db_path,
        "claude-code",
        "Claude Code",
        import_claude_usage,
        provider=provider,
    )


def run_pi_import(
    *,
    application: str,
    db_path: str,
    pi_home: str,
    project_path: str,
    session_files: list[str],
    latest: int | None,
) -> int:
    from nextrace.pi_usage import find_pi_session_files, import_pi_usage

    files = [Path(path).expanduser() for path in session_files]
    if not files:
        files = find_pi_session_files(
            pi_home=pi_home,
            project_path=project_path,
            latest=latest,
        )
    return _import_sessions(files, application, db_path, "pi", "Pi", import_pi_usage)


def _import_sessions(
    files: list[Path],
    application: str,
    db_path: str,
    source: str,
    label: str,
    importer: Callable[..., Any],
    **kwargs: Any,
) -> int:
    if not files:
        print(f"No {label} session files found.")
        return 1
    from nextrace.storage import SQLiteStore

    store = SQLiteStore(db_path)
    stats = importer(store=store, application=application, files=files, **kwargs)
    store.record_connection(
        application=application,
        source=source,
        transport="jsonl-import",
        metadata={"files": stats.files, "records": stats.imported},
    )
    print(
        f"Imported {stats.imported} {label} model records in correlated turns from {stats.files} file(s)."
    )
    return 0


def run_import_local_usage(
    *,
    db_path: str,
    codex_home: str,
    claude_home: str,
    pi_home: str,
    state_path: str,
    source: str,
    full: bool,
) -> int:
    from nextrace import SQLiteStore
    from nextrace.usage_importer import import_local_usage

    stats = import_local_usage(
        store=SQLiteStore(db_path),
        codex_home=codex_home,
        claude_home=claude_home,
        pi_home=pi_home,
        state_path=state_path,
        source=source,
        full=full,
    )
    print(
        "Imported local usage: "
        f"{stats.codex_records} Codex records from {stats.codex_files} file(s), "
        f"{stats.claude_records} Claude Code records from {stats.claude_files} file(s), "
        f"{stats.pi_records} Pi records from {stats.pi_files} file(s), "
        f"{len(stats.applications)} application(s)."
    )
    for application in sorted(stats.applications):
        app_stats = stats.applications[application]
        print(
            f"- {application}: "
            f"codex {app_stats.codex_records}/{app_stats.codex_files} file(s), "
            f"claude {app_stats.claude_records}/{app_stats.claude_files} file(s), "
            f"pi {app_stats.pi_records}/{app_stats.pi_files} file(s)"
        )
    if stats.skipped_files:
        print(f"Skipped {stats.skipped_files} file(s) without a project path.")
    return 0


def run_export_otel(
    *,
    db_path: str,
    output: str,
    endpoint: str | None,
    trace_id: str | None,
    application: str | None,
    since: str | None,
    until: str | None,
) -> int:
    from nextrace.files import write_json_atomic
    from nextrace.otel import export_traces, send_otlp
    from nextrace.storage import SQLiteStore

    try:
        start, end = _parse_local_time_arg(since), _parse_local_time_arg(until)
        if start is not None and end is not None and start >= end:
            raise ValueError("--until must be after --since")
        store = SQLiteStore(db_path)
        if trace_id:
            trace = store.get_trace(trace_id)
            if trace is None:
                raise ValueError("Trace not found")
            traces = [trace]
        else:
            traces = [
                store.get_trace(row["id"], correlate=False)
                for row in store.list_traces(
                    limit=None, application=application, since=start, until=end
                )
            ]
        payload = export_traces(t for t in traces if t is not None)
        skipped = sum(
            t is not None and (t["ended_at"] is None or t["status"] == "running") for t in traces
        )
        if skipped:
            print(f"Skipped {skipped} recordings without observed completion.")
        write_json_atomic(Path(output).expanduser(), payload)
        count = sum(
            len(scope["spans"])
            for resource in payload["resourceSpans"]
            for scope in resource["scopeSpans"]
        )
        print(f"Exported {count} spans to {Path(output).expanduser()}")
        if endpoint and count:
            send_otlp(payload, endpoint)
            print("OTLP collector accepted the export.")
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _parse_local_time_arg(value: str | None) -> float | None:
    if value is None:
        return None
    from datetime import datetime

    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized.removesuffix("Z") + "+00:00"
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError as error:
        raise ValueError(f"Invalid ISO date/datetime: {value}") from error


def run_set_project(
    *,
    project_path: str,
    application: str | None,
    state_path: str | None,
) -> int:
    from nextrace.project import write_current_project

    data = write_current_project(
        project_path=project_path,
        application=application,
        state_path=state_path,
        source="cli",
    )
    print(f"Active Nextrace project: {data['application']} ({data['project_path']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
