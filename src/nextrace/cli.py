"""Command line entry points."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nextrace.context import default_db_path


def _build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    subparsers = parser.add_subparsers(dest="command")

    dashboard = subparsers.add_parser("dashboard", help="Run the local dashboard")
    dashboard.add_argument("--db", default=str(default_db_path()), help="SQLite trace database path")
    dashboard.add_argument("--host", default="127.0.0.1", help="Host to bind")
    dashboard.add_argument("--port", default=8765, type=int, help="Port to bind")
    dashboard.add_argument("--reload", action="store_true", help="Enable uvicorn reload")

    mcp_proxy = subparsers.add_parser("mcp-proxy", help="Proxy a stdio MCP server and record redacted traces")
    mcp_proxy.add_argument(
        "--application",
        default="auto",
        help="Application name to record, or 'auto' to use the active project",
    )
    mcp_proxy.add_argument("--server", required=True, help="MCP server name to record")
    mcp_proxy.add_argument("--db", default=str(default_db_path()), help="SQLite trace database path")
    mcp_proxy.add_argument("--session-id", default=None, help="Optional session id to attach to traces")
    mcp_proxy.add_argument("child_command", nargs=argparse.REMAINDER, help="Command to run after --")

    http_proxy = subparsers.add_parser(
        "mcp-http-proxy",
        help="Proxy an HTTP MCP endpoint and record redacted traces",
    )
    http_proxy.add_argument(
        "--application",
        default="auto",
        help="Application name to record, or 'auto' to use the active project",
    )
    http_proxy.add_argument("--server", required=True, help="MCP server name to record")
    http_proxy.add_argument("--target", required=True, help="Upstream HTTP MCP endpoint")
    http_proxy.add_argument("--host", default="127.0.0.1", help="Local host to bind")
    http_proxy.add_argument("--port", default=8766, type=int, help="Local port to bind")
    http_proxy.add_argument("--db", default=str(default_db_path()), help="SQLite trace database path")
    http_proxy.add_argument("--session-id", default=None, help="Optional session id to attach to traces")
    http_proxy.add_argument("--timeout", default=3600, type=float, help="Upstream socket timeout in seconds")

    codex_import = subparsers.add_parser(
        "codex-import",
        help="Import local Codex token usage as redacted model traces",
    )
    codex_import.add_argument("--application", required=True, help="Application name to record")
    codex_import.add_argument("--db", default=str(default_db_path()), help="SQLite trace database path")
    codex_import.add_argument("--codex-home", default="~/.codex", help="Codex home directory")
    codex_import.add_argument(
        "--session-id",
        action="append",
        default=[],
        help="Codex session id to import. Can be repeated.",
    )
    codex_import.add_argument(
        "--session-file",
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
    codex_import.add_argument(
        "--input-cost-per-million",
        type=float,
        default=None,
        help="Optional input-token price used to estimate cost",
    )
    codex_import.add_argument(
        "--cached-input-cost-per-million",
        type=float,
        default=None,
        help="Optional cached-input-token price used to estimate cost",
    )
    codex_import.add_argument(
        "--output-cost-per-million",
        type=float,
        default=None,
        help="Optional output-token price used to estimate cost",
    )

    claude_import = subparsers.add_parser(
        "claude-import",
        help="Import local Claude Code token usage as redacted model traces",
    )
    claude_import.add_argument("--application", required=True, help="Application name to record")
    claude_import.add_argument("--db", default=str(default_db_path()), help="SQLite trace database path")
    claude_import.add_argument("--claude-home", default="~/.claude", help="Claude Code home directory")
    claude_import.add_argument(
        "--project-path",
        default=".",
        help="Project path whose Claude Code transcripts should be imported",
    )
    claude_import.add_argument(
        "--session-file",
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
        help="Import local Pi token usage as redacted model traces",
    )
    pi_import.add_argument("--application", required=True, help="Application name to record")
    pi_import.add_argument("--db", default=str(default_db_path()), help="SQLite trace database path")
    pi_import.add_argument("--pi-home", default="~/.pi/agent", help="Pi agent home directory")
    pi_import.add_argument(
        "--project-path",
        default=".",
        help="Project path whose Pi sessions should be imported",
    )
    pi_import.add_argument(
        "--session-file",
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
        help="Auto-import changed local AI-agent usage grouped by project",
    )
    local_import.add_argument("--db", default=str(default_db_path()), help="SQLite trace database path")
    local_import.add_argument("--codex-home", default="~/.codex", help="Codex home directory")
    local_import.add_argument("--claude-home", default="~/.claude", help="Claude Code home directory")
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

    reprice = subparsers.add_parser(
        "reprice",
        help="Recalculate stored model costs with the configured pricing registry",
    )
    reprice.add_argument("--db", default=str(default_db_path()), help="SQLite trace database path")
    reprice.add_argument("--application", default=None, help="Only reprice one application")
    reprice.add_argument("--provider", default=None, help="Only reprice one provider")
    reprice.add_argument("--model", default=None, help="Only reprice one model")
    reprice.add_argument(
        "--since",
        default=None,
        help="Only include traces at or after this local ISO date/datetime",
    )
    reprice.add_argument(
        "--until",
        default=None,
        help="Only include traces before this local ISO date/datetime",
    )
    reprice.add_argument("--dry-run", action="store_true", help="Preview without updating rows")

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

    return parser


def main(argv: list[str] | None = None) -> int:
    prog = Path(sys.argv[0]).name if argv is None else "nextrace"
    parser = _build_parser(prog)
    return _run_command(parser, parser.parse_args(argv))


def _run_command(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "dashboard":
        return run_dashboard(args.db, args.host, args.port, args.reload)
    if args.command == "mcp-proxy":
        command = args.child_command
        if command and command[0] == "--":
            command = command[1:]
        return run_mcp_proxy(args.application, args.server, args.db, args.session_id, command)
    if args.command == "mcp-http-proxy":
        return run_mcp_http_proxy(
            args.application,
            args.server,
            args.target,
            args.host,
            args.port,
            args.db,
            args.session_id,
            args.timeout,
        )
    if args.command == "codex-import":
        return run_codex_import(
            application=args.application,
            db_path=args.db,
            codex_home=args.codex_home,
            session_ids=args.session_id,
            session_files=args.session_file,
            latest=args.latest,
            provider=args.provider,
            model=args.model,
            input_cost_per_million=args.input_cost_per_million,
            cached_input_cost_per_million=args.cached_input_cost_per_million,
            output_cost_per_million=args.output_cost_per_million,
        )
    if args.command == "claude-import":
        return run_claude_import(
            application=args.application,
            db_path=args.db,
            claude_home=args.claude_home,
            project_path=args.project_path,
            session_files=args.session_file,
            latest=args.latest,
            provider=args.provider,
        )
    if args.command == "pi-import":
        return run_pi_import(
            application=args.application,
            db_path=args.db,
            pi_home=args.pi_home,
            project_path=args.project_path,
            session_files=args.session_file,
            latest=args.latest,
        )
    if args.command == "import-local-usage":
        return run_import_local_usage(
            db_path=args.db,
            codex_home=args.codex_home,
            claude_home=args.claude_home,
            pi_home=args.pi_home,
            state_path=args.state_path,
            source=args.source,
            full=args.full,
        )
    if args.command == "reprice":
        return run_reprice(
            db_path=args.db,
            application=args.application,
            provider=args.provider,
            model=args.model,
            since=args.since,
            until=args.until,
            dry_run=args.dry_run,
        )
    if args.command == "set-project":
        return run_set_project(
            project_path=args.project_path,
            application=args.application,
            state_path=args.state_path,
        )
    parser.error(f"Unknown command: {args.command}")


def run_dashboard(db_path: str, host: str, port: int, reload: bool) -> int:
    try:
        import uvicorn
    except ImportError:
        print('Dashboard dependencies are missing. Install with: python -m pip install -e ".[dashboard]"')
        return 1

    from nextrace.dashboard.app import create_app

    app = create_app(Path(db_path))
    print(f"Nextrace dashboard: http://{host}:{port}")
    print(f"Trace database: {Path(db_path).expanduser()}")
    uvicorn.run(app, host=host, port=port, reload=reload)
    return 0


def run_mcp_proxy(
    application: str,
    server: str,
    db_path: str,
    session_id: str | None,
    child_command: list[str],
) -> int:
    from nextrace.mcp_proxy import run_stdio_proxy

    return run_stdio_proxy(
        application=application,
        server=server,
        db_path=db_path,
        session_id=session_id,
        command=child_command,
    )


def run_mcp_http_proxy(
    application: str,
    server: str,
    target: str,
    host: str,
    port: int,
    db_path: str,
    session_id: str | None,
    timeout: float,
) -> int:
    from nextrace.mcp_proxy import run_http_proxy

    return run_http_proxy(
        application=application,
        server=server,
        target_url=target,
        host=host,
        port=port,
        db_path=db_path,
        session_id=session_id,
        timeout=timeout,
    )


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
    input_cost_per_million: float | None,
    cached_input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> int:
    from nextrace import SQLiteStore
    from nextrace.codex_usage import find_codex_session_files, import_codex_usage

    files = [Path(path).expanduser() for path in session_files]
    if not files:
        files = find_codex_session_files(
            codex_home=codex_home,
            session_ids=session_ids,
            latest=latest,
        )
    if not files:
        print("No Codex session files found.")
        return 1
    store = SQLiteStore(db_path)
    stats = import_codex_usage(
        store=store,
        application=application,
        files=files,
        input_cost_per_million=input_cost_per_million,
        cached_input_cost_per_million=cached_input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
        provider_override=provider,
        model_override=model,
    )
    store.record_connection(
        application=application,
        source="codex",
        transport="jsonl-import",
        status="connected",
        metadata={"files": stats.files, "records": stats.imported},
    )
    print(f"Imported {stats.imported} Codex model usage records from {stats.files} file(s).")
    if (
        input_cost_per_million is None
        and cached_input_cost_per_million is None
        and output_cost_per_million is None
    ):
        print("Default pricing presets were used when available. Pass token prices to override them.")
    return 0


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
    from nextrace import SQLiteStore
    from nextrace.claude_usage import find_claude_project_files, import_claude_usage

    files = [Path(path).expanduser() for path in session_files]
    if not files:
        files = find_claude_project_files(
            claude_home=claude_home,
            project_path=project_path,
            latest=latest,
        )
    if not files:
        print("No Claude Code transcript files found.")
        return 1

    store = SQLiteStore(db_path)
    stats = import_claude_usage(
        store=store,
        application=application,
        files=files,
        provider=provider,
    )
    store.record_connection(
        application=application,
        source="claude-code",
        transport="jsonl-import",
        status="connected",
        metadata={"files": stats.files, "records": stats.imported},
    )
    print(f"Imported {stats.imported} Claude Code model usage records from {stats.files} file(s).")
    return 0


def run_pi_import(
    *,
    application: str,
    db_path: str,
    pi_home: str,
    project_path: str,
    session_files: list[str],
    latest: int | None,
) -> int:
    from nextrace import SQLiteStore
    from nextrace.pi_usage import find_pi_session_files, import_pi_usage

    files = [Path(path).expanduser() for path in session_files]
    if not files:
        files = find_pi_session_files(
            pi_home=pi_home,
            project_path=project_path,
            latest=latest,
        )
    if not files:
        print("No Pi session files found.")
        return 1

    store = SQLiteStore(db_path)
    stats = import_pi_usage(store=store, application=application, files=files)
    store.record_connection(
        application=application,
        source="pi",
        transport="jsonl-import",
        status="connected",
        metadata={"files": stats.files, "records": stats.imported},
    )
    print(f"Imported {stats.imported} Pi model usage records from {stats.files} file(s).")
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


def run_reprice(
    *,
    db_path: str,
    application: str | None,
    provider: str | None,
    model: str | None,
    since: str | None,
    until: str | None,
    dry_run: bool,
) -> int:
    from nextrace.repricing import reprice_model_spans

    try:
        since_ts = _parse_local_time_arg(since)
        until_ts = _parse_local_time_arg(until)
    except ValueError as error:
        print(str(error))
        return 2

    stats = reprice_model_spans(
        db_path=db_path,
        application=application,
        provider=provider,
        model=model,
        since=since_ts,
        until=until_ts,
        dry_run=dry_run,
    )
    action = "Would reprice" if dry_run else "Repriced"
    print(
        f"{action} {stats.repriced} of {stats.matched} model span(s); "
        f"skipped {stats.skipped} without pricing."
    )
    print(f"Cost before: ${stats.cost_before:.2f}")
    print(f"Cost after:  ${stats.cost_after:.2f}")
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
