import pytest

from nextrace import SQLiteStore
from nextrace.claude_usage import import_claude_usage
from nextrace.codex_usage import import_codex_usage
from nextrace.pi_usage import import_pi_usage
from nextrace.usage import read_jsonl


@pytest.mark.parametrize("importer", [import_codex_usage, import_claude_usage, import_pi_usage])
def test_importers_skip_non_objects_and_partial_records(tmp_path, importer):
    path = tmp_path / "session.jsonl"
    path.write_text('null\n[]\n"text"\n{"type":"ignored"}\n{"partial":\n')
    assert list(read_jsonl(path)) == [(4, {"type": "ignored"})]
    store = SQLiteStore(tmp_path / "traces.db")
    assert importer(store=store, application="test", files=[path]).imported == 0
    assert store.list_traces() == []


def test_incomplete_utf8_does_not_hide_complete_events(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"type":"first"}\n{"message":"\xe2\x82')
    assert list(read_jsonl(path)) == [(1, {"type": "first"})]
    with path.open("ab") as handle:
        handle.write(b'\xac"}\n')
    assert list(read_jsonl(path)) == [(1, {"type": "first"}), (2, {"message": "€"})]
