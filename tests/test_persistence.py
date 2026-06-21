"""JSONL persistence: append + replay round-trip."""
from __future__ import annotations

from pathlib import Path

from server.persistence import JsonlStore


def test_append_and_replay(tmp_path: Path):
    store = JsonlStore(tmp_path / "x.jsonl")
    store.append({"id": "a", "v": 1})
    store.append({"id": "b", "v": 2})
    rows = store.replay()
    assert rows == [{"id": "a", "v": 1}, {"id": "b", "v": 2}]


def test_latest_by_id_keeps_last_write(tmp_path: Path):
    store = JsonlStore(tmp_path / "x.jsonl")
    store.append({"patch_id": "p1", "status": "proposed"})
    store.append({"patch_id": "p2", "status": "proposed"})
    store.append({"patch_id": "p1", "status": "approved"})
    by_id = store.latest_by_id("patch_id")
    assert by_id["p1"]["status"] == "approved"
    assert by_id["p2"]["status"] == "proposed"


def test_replay_skips_blank_and_corrupt_lines(tmp_path: Path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"id":"a"}\n\n{"bad":\n{"id":"b"}\n', encoding="utf-8")
    rows = JsonlStore(p).replay()
    assert {r["id"] for r in rows} == {"a", "b"}
