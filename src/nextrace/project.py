"""Project/application resolution helpers."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    tomllib = None  # type: ignore[assignment]


AUTO_APPLICATION_NAMES = {"auto", "@auto", ""}


def default_project_state_path() -> Path:
    configured = os.getenv("NEXTRACE_PROJECT_STATE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".nextrace" / "current-project.json"


def application_from_project_path(project_path: str | Path) -> str:
    path = Path(project_path).expanduser()
    name = _project_name_from_pyproject(path) or path.name or path.resolve().name or "unknown-project"
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-._").lower()
    return normalized or "unknown-project"

def _project_name_from_pyproject(project_path: Path) -> str | None:
    pyproject_path = (
        project_path / "pyproject.toml"
        if project_path.is_dir()
        else project_path.parent / "pyproject.toml"
    )
    if not pyproject_path.exists():
        return None

    if tomllib is not None:
        try:
            data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        name = data.get("project", {}).get("name")
        return name if isinstance(name, str) and name else None

    return _project_name_from_pyproject_text(pyproject_path)


def _project_name_from_pyproject_text(pyproject_path: Path) -> str | None:
    try:
        lines = pyproject_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    in_project_section = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project_section = stripped == "[project]"
            continue
        if not in_project_section or not stripped.startswith("name"):
            continue
        key, separator, value = stripped.partition("=")
        if separator and key.strip() == "name":
            parsed = value.strip().strip('"').strip("'")
            return parsed or None
    return None


def read_current_project(state_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(state_path).expanduser() if state_path else default_project_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_current_project(
    *,
    project_path: str | Path,
    application: str | None = None,
    state_path: str | Path | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    path = Path(state_path).expanduser() if state_path else default_project_state_path()
    resolved_project_path = str(Path(project_path).expanduser().resolve())
    data = {
        "application": application or application_from_project_path(resolved_project_path),
        "project_path": resolved_project_path,
        "source": source,
        "updated_at": time.time(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def resolve_application(application: str | None, state_path: str | Path | None = None) -> str:
    configured = (application or "auto").strip()
    if configured not in AUTO_APPLICATION_NAMES:
        return configured

    env_application = os.getenv("NEXTRACE_APPLICATION")
    if env_application:
        return env_application

    state = read_current_project(state_path)
    state_application = state.get("application")
    if isinstance(state_application, str) and state_application:
        return state_application

    project_path = state.get("project_path")
    if isinstance(project_path, str) and project_path:
        return application_from_project_path(project_path)

    return application_from_project_path(Path.cwd())
