"""POST /api/uploads + GET /api/uploads/{file} + list + traversal guard."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from server.app import app
    return TestClient(app)


def test_upload_round_trip(tmp_path):
    c = _client()
    files = {"file": ("hello.txt", b"hello world", "text/plain")}
    r = c.post("/api/uploads", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"]
    assert body["filename"] in ("hello.txt", "hello_") or body["filename"].startswith("hello")
    # serve it back
    r2 = c.get(body["url"])
    assert r2.status_code == 200
    assert r2.content == b"hello world"


def test_path_traversal_filename_rejected():
    c = _client()
    files = {"file": ("../etc/passwd", b"x", "text/plain")}
    r = c.post("/api/uploads", files=files)
    # The server strips path components defensively, so this should succeed
    # with the basename being "passwd". Either accept (basename stripped) or
    # 400 — both are safe. Assert we never end up outside the uploads dir.
    if r.status_code == 200:
        body = r.json()
        assert ".." not in body["filename"]
        assert "/" not in body["filename"]
    else:
        assert r.status_code == 400


def test_uploaded_file_listed_in_index():
    c = _client()
    files = {"file": ("note_for_test.txt", b"abc", "text/plain")}
    r = c.post("/api/uploads", files=files)
    assert r.status_code == 200
    saved = r.json()["filename"]
    r2 = c.get("/api/uploads")
    assert r2.status_code == 200
    names = [f["filename"] for f in r2.json()["files"]]
    assert saved in names


def test_serve_unknown_returns_404():
    c = _client()
    r = c.get("/api/uploads/does_not_exist_xyz.bin")
    assert r.status_code == 404


def test_serve_with_path_traversal_returns_400():
    c = _client()
    # Path with ".." should be rejected at the route level
    r = c.get("/api/uploads/..%2F..%2Fetc%2Fpasswd")
    # FastAPI may route it to a sibling endpoint; either 400 or 404 are OK
    # — never 200, never serves an outside file.
    assert r.status_code in (400, 404)
