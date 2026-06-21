"""Phase C — backup/restore + alert webhook tests (offline)."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from teow_agl.util.backup import create_backup, list_backups, restore_backup
from teow_agl.util.alerts import send_alert


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "deploy"
    (root / "state" / "memory").mkdir(parents=True)
    (root / "traces").mkdir(parents=True)
    (root / "configs").mkdir(parents=True)
    (root / "state" / "plan_cache.jsonl").write_text("cache", encoding="utf-8")
    (root / "state" / "memory" / "USER.md").write_text("user facts", encoding="utf-8")
    (root / "traces" / "t1.jsonl").write_text("audit", encoding="utf-8")
    (root / "configs" / "x.json").write_text("{}", encoding="utf-8")
    return root


# ===========================================================================
# Backup
# ===========================================================================

def test_backup_round_trip(tmp_path):
    root = _make_root(tmp_path)
    archive = create_backup(root)
    assert archive is not None and archive.exists()

    # Simulate state loss, then restore.
    (root / "state" / "plan_cache.jsonl").unlink()
    (root / "state" / "memory" / "USER.md").write_text("corrupted", encoding="utf-8")
    restored = restore_backup(archive, root)
    assert "state/plan_cache.jsonl" in restored
    assert (root / "state" / "plan_cache.jsonl").read_text(encoding="utf-8") == "cache"
    assert (root / "state" / "memory" / "USER.md").read_text(encoding="utf-8") == "user facts"


def test_backup_returns_none_when_nothing_to_save(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert create_backup(empty) is None


def test_backup_rotation_keeps_newest(tmp_path):
    root = _make_root(tmp_path)
    last = None
    for _ in range(5):
        last = create_backup(root, keep=2)
    archives = list_backups(root)
    assert len(archives) == 2
    assert last in archives  # the newest always survives rotation


def test_backup_same_second_never_overwrites(tmp_path):
    root = _make_root(tmp_path)
    a = create_backup(root, keep=10)
    b = create_backup(root, keep=10)
    assert a != b
    assert a.exists() and b.exists()


def test_restore_skips_non_whitelisted_and_zip_slip(tmp_path):
    root = _make_root(tmp_path)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("state/ok.txt", "fine")
        zf.writestr("configs/hijack.json", "{}")        # not whitelisted by default
        zf.writestr("state/../../escape.txt", "bad")     # zip-slip attempt
    restored = restore_backup(evil, root)
    assert restored == ["state/ok.txt"]
    assert (root / "state" / "ok.txt").exists()
    assert not (tmp_path / "escape.txt").exists()
    assert not (root / "configs" / "hijack.json").exists()


def test_restore_configs_requires_explicit_whitelist(tmp_path):
    root = _make_root(tmp_path)
    archive = create_backup(root)
    (root / "configs" / "x.json").write_text('{"changed": 1}', encoding="utf-8")
    restore_backup(archive, root)  # default whitelist: state, traces
    assert (root / "configs" / "x.json").read_text(encoding="utf-8") == '{"changed": 1}'
    restore_backup(archive, root, items=("configs",))
    assert (root / "configs" / "x.json").read_text(encoding="utf-8") == "{}"


# ===========================================================================
# Alerts
# ===========================================================================

def test_alert_noop_without_env(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    assert send_alert("boom") is False


def test_alert_posts_payload(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _Resp()

    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr("httpx.post", fake_post)
    ok = send_alert("task failed: X", details={"task_id": "t1"}, kind="error")
    assert ok is True
    assert captured["url"] == "https://example.test/hook"
    assert "[TEOW-AGL][error] task failed: X" == captured["payload"]["text"]
    assert captured["payload"]["details"]["task_id"] == "t1"


def test_alert_swallows_network_failure(monkeypatch):
    def fake_post(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr("httpx.post", fake_post)
    assert send_alert("boom") is False  # never raises


# ===========================================================================
# Health endpoint
# ===========================================================================

def test_health_reports_checks_and_budget():
    from fastapi.testclient import TestClient
    from server.app import app

    r = TestClient(app).get("/api/health")
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is True
    assert body["checks"]["state_writable"] is True
    assert body["checks"]["traces_writable"] is True
    assert "budget" in body
    assert body["budget"].get("enabled") in (True, False)
