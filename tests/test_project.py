from __future__ import annotations

from nextrace.project import application_from_project_path, resolve_application


def test_application_from_project_path_normalizes_case_and_symbols():
    application = application_from_project_path("/Users/me/shopify-projects/PS River_SRA.Skill")

    assert application == "ps-river_sra.skill"


def test_application_from_project_path_prefers_pyproject_name(tmp_path):
    project = tmp_path / "old-folder-name"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname = "Nextrace"\n', encoding="utf-8")

    assert application_from_project_path(project) == "nextrace"


def test_resolve_application_prefers_nextrace_env(monkeypatch):
    monkeypatch.setenv("NEXTRACE_APPLICATION", "configured-name")

    assert resolve_application("auto") == "configured-name"
