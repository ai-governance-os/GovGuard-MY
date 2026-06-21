"""Phase C — session auth tests.

Auth is OFF by default (no TEOW_AGL_AUTH_PASSWORD) — every existing
test and deployment keeps working. With the env var set, /api/* needs
a session cookie from /api/login (or the same token as a Bearer
header); /api/health and /api/login stay open.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from server.app import app
    return TestClient(app)


def test_auth_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TEOW_AGL_AUTH_PASSWORD", raising=False)
    c = _client()
    assert c.get("/api/config").status_code == 200
    # login endpoint reports auth disabled instead of erroring
    r = c.post("/api/login", json={"password": "anything"})
    assert r.json().get("auth") == "disabled"


def test_api_locked_when_password_set(monkeypatch):
    monkeypatch.setenv("TEOW_AGL_AUTH_PASSWORD", "s3cret")
    c = _client()
    r = c.get("/api/config")
    assert r.status_code == 401
    assert r.json()["error"] == "auth_required"
    # tasks API equally locked
    assert c.get("/api/tasks").status_code == 401
    assert c.post("/api/tasks", json={"raw_goal": "hi"}).status_code == 401


def test_health_and_login_stay_open(monkeypatch):
    monkeypatch.setenv("TEOW_AGL_AUTH_PASSWORD", "s3cret")
    c = _client()
    assert c.get("/api/health").status_code == 200


def test_wrong_password_rejected(monkeypatch):
    monkeypatch.setenv("TEOW_AGL_AUTH_PASSWORD", "s3cret")
    c = _client()
    assert c.post("/api/login", json={"password": "nope"}).status_code == 401
    # still locked afterwards
    assert c.get("/api/config").status_code == 401


def test_login_sets_cookie_and_unlocks(monkeypatch):
    monkeypatch.setenv("TEOW_AGL_AUTH_PASSWORD", "s3cret")
    c = _client()
    r = c.post("/api/login", json={"password": "s3cret"})
    assert r.status_code == 200
    assert "teow_session" in r.cookies or "teow_session" in c.cookies
    # TestClient carries the cookie jar forward
    assert c.get("/api/config").status_code == 200
    assert c.get("/api/tasks").status_code == 200


def test_bearer_token_works_for_scripts(monkeypatch):
    monkeypatch.setenv("TEOW_AGL_AUTH_PASSWORD", "s3cret")
    c = _client()
    token = c.post("/api/login", json={"password": "s3cret"}).json()["token"]
    fresh = _client()  # no cookies
    assert fresh.get("/api/config").status_code == 401
    r = fresh.get("/api/config",
                  headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_forged_token_rejected(monkeypatch):
    monkeypatch.setenv("TEOW_AGL_AUTH_PASSWORD", "s3cret")
    c = _client()
    r = c.get("/api/config",
              headers={"Authorization": "Bearer aaaa.bbbb"})
    assert r.status_code == 401
    c.cookies.set("teow_session", "deadbeef.cafebabe")
    assert c.get("/api/config").status_code == 401


def test_static_ui_shell_stays_served(monkeypatch):
    monkeypatch.setenv("TEOW_AGL_AUTH_PASSWORD", "s3cret")
    c = _client()
    r = c.get("/")
    assert r.status_code == 200
    assert "login-overlay" in r.text  # the overlay markup ships with the shell