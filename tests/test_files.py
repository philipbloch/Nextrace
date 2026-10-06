import pytest

from nextrace.files import write_json_atomic


def test_failed_json_write_preserves_existing_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"files": {}}\n')
    with pytest.raises(TypeError):
        write_json_atomic(path, {"partial": 1, "invalid": object()})
    assert path.read_text() == '{"files": {}}\n'
    assert list(tmp_path.iterdir()) == [path]
