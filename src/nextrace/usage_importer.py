from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nextrace.claude_usage import find_claude_project_files, import_claude_usage
from nextrace.codex_usage import import_codex_usage
from nextrace.files import write_json_atomic
from nextrace.pi_usage import find_pi_session_files, import_pi_usage
from nextrace.project import application_from_project_path
from nextrace.storage import SQLiteStore
from nextrace.usage import read_jsonl, string_or_none

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
    selected_sources = _selected_sources(source)
    state = _empty_state() if full else _load_state(state_path)
    stats = LocalUsageImportStats()

    sources = (
        ("codex", codex_home, _find_codex_files, _codex_project_path, import_codex_usage),
        (
            "claude",
            claude_home,
            lambda home: find_claude_project_files(claude_home=home),
            _claude_project_path,
            import_claude_usage,
        ),
        (
            "pi",
            pi_home,
            lambda home: find_pi_session_files(pi_home=home),
            _pi_project_path,
            import_pi_usage,
        ),
    )
    for name, home, find_files, project_path, importer in sources:
        if name not in selected_sources:
            continue
        changed = _changed_files(find_files(home), state, full=full)
        grouped = _group_files_by_application(changed, project_path, stats)
        for application, files in grouped.items():
            result = importer(store=store, application=application, files=files)
            app_stats = stats.applications.setdefault(application, ApplicationUsageImportStats())
            for metric, value in (("files", result.files), ("records", result.imported)):
                key = f"{name}_{metric}"
                setattr(app_stats, key, getattr(app_stats, key) + value)
            store.record_connection(
                application=application,
                source="claude-code" if name == "claude" else name,
                transport="jsonl-auto-import",
                metadata={"files": result.files, "records": result.imported, "auto_import": True},
            )
            # A writer may append during import; never mark unread bytes as imported.
            state["files"].update({_state_key(path): changed[path] for path in files})

    if full:
        store.consolidate_legacy_models()
    if state_path is not None:
        write_json_atomic(Path(state_path).expanduser(), state)
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


def _group_files_by_application(
    files: Iterable[Path],
    project_path_for_file: Callable[[Path], str | None],
    stats: LocalUsageImportStats,
) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    for file_path in files:
        try:
            project_path = project_path_for_file(file_path)
        except OSError:
            project_path = None
        if project_path is None:
            stats.skipped_files += 1
            continue
        application = application_from_project_path(project_path)
        grouped.setdefault(application, []).append(file_path)
    return grouped


def _codex_project_path(file_path: Path) -> str | None:
    project_path: str | None = None
    for _, event in read_jsonl(file_path):
        payload = event.get("payload")
        if isinstance(payload, dict):
            cwd = string_or_none(payload.get("cwd"))
            if cwd:
                project_path = cwd
    return project_path


def _claude_project_path(file_path: Path) -> str | None:
    for _, event in read_jsonl(file_path):
        cwd = string_or_none(event.get("cwd"))
        if cwd:
            return cwd
    return None


def _pi_project_path(file_path: Path) -> str | None:
    for _, event in read_jsonl(file_path):
        if event.get("type") != "session":
            continue
        cwd = string_or_none(event.get("cwd"))
        if cwd:
            return cwd
    return None


def _changed_files(
    files: Iterable[Path],
    state: dict[str, Any],
    *,
    full: bool,
) -> dict[Path, dict[str, int]]:
    changed = {}
    file_state = state.setdefault("files", {})
    for file_path in files:
        fingerprint = _fingerprint(file_path)
        if fingerprint is None:
            continue
        key = _state_key(file_path)
        if full or file_state.get(key) != fingerprint:
            changed[file_path] = fingerprint
    return changed


def _fingerprint(file_path: Path) -> dict[str, int] | None:
    try:
        stat = file_path.stat()
    except OSError:
        return None
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def _state_key(file_path: Path) -> str:
    return str(file_path.expanduser().resolve())


def _empty_state() -> dict[str, Any]:
    return {"version": 3, "files": {}}


def _load_state(state_path: str | Path | None) -> dict[str, Any]:
    if state_path is None:
        return _empty_state()
    path = Path(state_path).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if (
        not isinstance(data, dict)
        or data.get("version") != 3
        or not isinstance(data.get("files"), dict)
    ):
        return _empty_state()
    return data
