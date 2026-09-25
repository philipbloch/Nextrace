"""Bulk local usage imports for supported AI coding agents."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nextrace.claude_usage import import_claude_usage
from nextrace.codex_usage import import_codex_usage
from nextrace.pi_usage import import_pi_usage
from nextrace.project import application_from_project_path
from nextrace.storage import SQLiteStore

DEFAULT_USAGE_IMPORT_STATE = Path("~/.nextrace/local-usage-import-state.json")


@dataclass
class ApplicationUsageImportStats:
    codex_files: int = 0
    codex_records: int = 0
    claude_files: int = 0
    claude_records: int = 0
    pi_files: int = 0
    pi_records: int = 0


@dataclass
class LocalUsageImportStats:
    applications: dict[str, ApplicationUsageImportStats] = field(default_factory=dict)
    skipped_files: int = 0

    @property
    def codex_files(self) -> int:
        return sum(item.codex_files for item in self.applications.values())

    @property
    def codex_records(self) -> int:
        return sum(item.codex_records for item in self.applications.values())

    @property
    def claude_files(self) -> int:
        return sum(item.claude_files for item in self.applications.values())

    @property
    def claude_records(self) -> int:
        return sum(item.claude_records for item in self.applications.values())

    @property
    def pi_files(self) -> int:
        return sum(item.pi_files for item in self.applications.values())

    @property
    def pi_records(self) -> int:
        return sum(item.pi_records for item in self.applications.values())


def import_local_usage(
    *,
    store: SQLiteStore,
    codex_home: str | Path = "~/.codex",
    claude_home: str | Path = "~/.claude",
    pi_home: str | Path = "~/.pi/agent",
    state_path: str | Path | None = DEFAULT_USAGE_IMPORT_STATE,
    source: str = "all",
    full: bool = False,
) -> LocalUsageImportStats:
    """Import changed local AI-agent usage files grouped by project."""
    selected_sources = _selected_sources(source)
    state = _empty_state() if full else _load_state(state_path)
    stats = LocalUsageImportStats()

    if "codex" in selected_sources:
        codex_files = _changed_files(_find_codex_files(codex_home), state, full=full)
        grouped = _group_files_by_application(codex_files, _codex_project_path, stats)
        for application, files in grouped.items():
            result = import_codex_usage(store=store, application=application, files=files)
            app_stats = stats.applications.setdefault(application, ApplicationUsageImportStats())
            app_stats.codex_files += len(files)
            app_stats.codex_records += result.imported
            store.record_connection(
                application=application,
                source="codex",
                transport="jsonl-auto-import",
                status="connected",
                metadata={"files": len(files), "records": result.imported, "auto_import": True},
            )
            _mark_imported(state, files)

    if "claude" in selected_sources:
        claude_files = _changed_files(_find_claude_files(claude_home), state, full=full)
        grouped = _group_files_by_application(claude_files, _claude_project_path, stats)
        for application, files in grouped.items():
            result = import_claude_usage(store=store, application=application, files=files)
            app_stats = stats.applications.setdefault(application, ApplicationUsageImportStats())
            app_stats.claude_files += len(files)
            app_stats.claude_records += result.imported
            store.record_connection(
                application=application,
                source="claude-code",
                transport="jsonl-auto-import",
                status="connected",
                metadata={"files": len(files), "records": result.imported, "auto_import": True},
            )
            _mark_imported(state, files)

    if "pi" in selected_sources:
        pi_files = _changed_files(_find_pi_files(pi_home), state, full=full)
        grouped = _group_files_by_application(pi_files, _pi_project_path, stats)
        for application, files in grouped.items():
            result = import_pi_usage(store=store, application=application, files=files)
            app_stats = stats.applications.setdefault(application, ApplicationUsageImportStats())
            app_stats.pi_files += len(files)
            app_stats.pi_records += result.imported
            store.record_connection(
                application=application,
                source="pi",
                transport="jsonl-auto-import",
                status="connected",
                metadata={"files": len(files), "records": result.imported, "auto_import": True},
            )
            _mark_imported(state, files)

    if state_path is not None:
        _save_state(state_path, state)
    return stats


def _selected_sources(source: str) -> set[str]:
    normalized = source.strip().lower()
    if normalized == "all":
        return {"codex", "claude", "pi"}
    if normalized in {"codex", "claude", "pi"}:
        return {normalized}
    raise ValueError("source must be one of: all, codex, claude, pi")


def _find_codex_files(codex_home: str | Path) -> list[Path]:
    home = Path(codex_home).expanduser()
    files: list[Path] = []
    for root in (home / "sessions", home / "archived_sessions"):
        if root.exists():
            files.extend(root.rglob("*.jsonl"))
    return sorted(set(files))


def _find_claude_files(claude_home: str | Path) -> list[Path]:
    root = Path(claude_home).expanduser() / "projects"
    if not root.exists():
        return []
    return sorted(set(root.glob("*/*.jsonl")))


def _find_pi_files(pi_home: str | Path) -> list[Path]:
    root = Path(pi_home).expanduser() / "sessions"
    if not root.exists():
        return []
    return sorted(set(root.glob("*/*.jsonl")))


def _group_files_by_application(
    files: Iterable[Path],
    project_path_for_file: Callable[[Path], str | None],
    stats: LocalUsageImportStats,
) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    for file_path in files:
        project_path = project_path_for_file(file_path)
        if project_path is None:
            stats.skipped_files += 1
            continue
        application = application_from_project_path(project_path)
        grouped.setdefault(application, []).append(file_path)
    return grouped


def _codex_project_path(file_path: Path) -> str | None:
    project_path: str | None = None
    for event in _read_jsonl_objects(file_path):
        payload = event.get("payload")
        if isinstance(payload, dict):
            cwd = _string_or_none(payload.get("cwd"))
            if cwd:
                project_path = cwd
    return project_path


def _claude_project_path(file_path: Path) -> str | None:
    for event in _read_jsonl_objects(file_path):
        cwd = _string_or_none(event.get("cwd"))
        if cwd:
            return cwd
    return None


def _pi_project_path(file_path: Path) -> str | None:
    for event in _read_jsonl_objects(file_path):
        if event.get("type") != "session":
            continue
        cwd = _string_or_none(event.get("cwd"))
        if cwd:
            return cwd
    return None


def _read_jsonl_objects(file_path: Path) -> Iterable[dict[str, Any]]:
    try:
        with file_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event
    except OSError:
        return


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _changed_files(files: Iterable[Path], state: dict[str, Any], *, full: bool) -> list[Path]:
    changed: list[Path] = []
    file_state = state.setdefault("files", {})
    for file_path in files:
        fingerprint = _fingerprint(file_path)
        if fingerprint is None:
            continue
        key = _state_key(file_path)
        if full or file_state.get(key) != fingerprint:
            changed.append(file_path)
    return changed


def _mark_imported(state: dict[str, Any], files: Iterable[Path]) -> None:
    file_state = state.setdefault("files", {})
    for file_path in files:
        fingerprint = _fingerprint(file_path)
        if fingerprint is not None:
            file_state[_state_key(file_path)] = fingerprint


def _fingerprint(file_path: Path) -> dict[str, int] | None:
    try:
        stat = file_path.stat()
    except OSError:
        return None
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def _state_key(file_path: Path) -> str:
    return str(file_path.expanduser().resolve())


def _empty_state() -> dict[str, Any]:
    return {"version": 1, "files": {}}


def _load_state(state_path: str | Path | None) -> dict[str, Any]:
    if state_path is None:
        return _empty_state()
    path = Path(state_path).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return _empty_state()
    return data


def _save_state(state_path: str | Path, state: dict[str, Any]) -> None:
    path = Path(state_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
