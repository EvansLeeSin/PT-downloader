import sqlite3

import pytest

from app.errors import AppError
from app.models import DownloadCreate
from tests.conftest import PASSWORD


def api_token(client, scopes):
    response = client.post(
        "/api/v1/admin/tokens", json={"name": "AI", "scopes": scopes, "expires_in_days": 30}
    )
    assert response.status_code == 201
    return response.json()


def test_health_static_and_openapi(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/v1/torrents/search" in schema["paths"]
    assert schema["paths"]["/api/v1/downloads"]["post"]["responses"]["202"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("DownloadJob")
    assert client.get("/api/v1/download-locations").status_code == 401


def test_password_and_csrf(client):
    assert client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"password": PASSWORD},
            headers={"origin": "https://evil.test"},
        ).status_code
        == 403
    )
    result = client.post("/api/v1/auth/login", json={"password": PASSWORD})
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "SameSite=strict" in result.headers["set-cookie"]
    assert (
        client.put(
            "/api/v1/admin/settings", json={}, headers={"origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/auth/me").json()["kind"] == "session"


def test_login_rate_limit(client):
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"password": PASSWORD}).status_code == 429


def test_scopes_and_revoke(admin):
    token = api_token(admin, ["search:read"])
    headers = {"Authorization": "Bearer " + token["token"]}
    assert admin.get("/api/v1/download-locations", headers=headers).status_code == 403
    assert admin.get("/api/v1/admin/settings", headers=headers).status_code == 403
    assert (
        admin.post(
            "/api/v1/downloads",
            json={"torrent_id": "mteam:1"},
            headers={**headers, "Idempotency-Key": "ai-action-0001"},
        ).status_code
        == 403
    )
    assert token["token"] not in admin.get("/api/v1/admin/tokens").text
    admin.delete("/api/v1/admin/tokens/" + token["id"])
    assert admin.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_credentials_encrypted_and_never_returned(configured):
    result = configured.get("/api/v1/admin/settings")
    assert result.json()["site_api_key_configured"]
    assert result.json()["qb_password_configured"]
    assert "site-key-never-expose" not in result.text
    assert "qb-secret-never-expose" not in result.text
    store = configured.app.state.store
    with sqlite3.connect(store.path) as con:
        data = con.execute("SELECT value FROM config WHERE key='settings'").fetchone()[0]
    assert "site-key-never-expose" not in data
    assert "qb-secret-never-expose" not in data
    assert store.path.with_name("encryption.key").stat().st_mode & 0o777 == 0o600
    configured.put("/api/v1/admin/settings", json={"qb_url": "http://qb.test:8080"})
    assert store.settings().site_api_key == "site-key-never-expose"
    assert store.settings().qb_password == "qb-secret-never-expose"


def test_default_location_and_path_validation(admin):
    data = admin.get("/api/v1/download-locations").json()
    assert data["items"][0]["path"] == "/downloads"
    for path in ("relative/path", "/downloads/../private", "//downloads", "/downloads\\bad"):
        result = admin.put(
            "/api/v1/admin/download-locations",
            json={"items": [{"id": "default", "name": "默认", "path": path, "is_default": True}]},
        )
        assert result.status_code == 422
    assert admin.put("/api/v1/admin/download-locations", json={"items": []}).status_code == 422


def test_idempotency_and_directory_snapshot(configured):
    body = {"torrent_id": "mteam:123", "location_id": "default"}
    headers = {"Idempotency-Key": "user-action-0001"}
    first = configured.post("/api/v1/downloads", json=body, headers=headers)
    assert first.status_code == 202
    assert first.json()["save_path"] == "/downloads"
    assert first.json()["submission_status"] == "queued"
    repeated = configured.post("/api/v1/downloads", json=body, headers=headers)
    assert repeated.json()["id"] == first.json()["id"]
    conflict = configured.post(
        "/api/v1/downloads", json={"torrent_id": "mteam:456"}, headers=headers
    )
    assert conflict.status_code == 409
    assert configured.post("/api/v1/downloads", json=body).status_code == 422
    assert (
        configured.post(
            "/api/v1/downloads", json={**body, "save_path": "/private"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        configured.put("/api/v1/admin/settings", json={"qb_url": "http://other.test"}).status_code
        == 409
    )
    configured.put(
        "/api/v1/admin/download-locations",
        json={
            "items": [
                {"id": "default", "name": "新目录", "path": "/downloads/movies", "is_default": True}
            ]
        },
    )
    assert (
        configured.get("/api/v1/downloads/" + first.json()["id"]).json()["save_path"]
        == "/downloads"
    )


def test_bad_ids_and_validation_do_not_echo_secrets(admin):
    assert admin.get("/api/v1/torrents/mteam:not-a-number").status_code == 422
    result = admin.post("/api/v1/auth/login", json={"password": {"secret": "never-echo"}})
    assert result.status_code == 422
    assert "never-echo" not in result.text


def test_programmatic_token_without_cookie_origin(configured):
    token = api_token(configured, ["downloads:read", "downloads:create"])
    configured.cookies.clear()
    headers = {
        "Authorization": "Bearer " + token["token"],
        "origin": "https://external-ai.test",
        "Idempotency-Key": "ai-action-0001",
    }
    result = configured.post("/api/v1/downloads", json={"torrent_id": "mteam:123"}, headers=headers)
    assert result.status_code == 202
    assert (
        configured.get("/api/v1/downloads/" + result.json()["id"], headers=headers).status_code
        == 200
    )


def test_missing_location(configured):
    result = configured.post(
        "/api/v1/downloads",
        json={"torrent_id": "mteam:123", "location_id": "absent"},
        headers={"Idempotency-Key": "action-0001"},
    )
    assert result.status_code == 404


def test_expired_token(admin):
    token = api_token(admin, ["search:read"])
    with admin.app.state.store.connect(write=True) as con:
        con.execute("UPDATE tokens SET expires_at=0 WHERE id=?", (token["id"],))
    assert (
        admin.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer " + token["token"]}
        ).status_code
        == 401
    )


def test_recovery_marks_processing_uncertain(configured):
    store = configured.app.state.store
    job = store.create_job("test", "action-0001", DownloadCreate(torrent_id="mteam:1"))
    assert store.claim_job()["id"] == job["id"]
    store.recover_jobs()
    assert store.job(job["id"])["submission_status"] == "uncertain"
    assert store.claim_job() is None


def test_no_config_no_submit(admin):
    result = admin.post(
        "/api/v1/downloads",
        json={"torrent_id": "mteam:1"},
        headers={"Idempotency-Key": "action-0001"},
    )
    assert result.status_code == 409


def test_chunked_body_limit_and_declared_limit(client):
    def chunks():
        for _ in range(70):
            yield b"x" * 1024

    result = client.post(
        "/api/v1/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert result.status_code == 413
    assert result.json()["error"]["code"] == "BODY_TOO_LARGE"
    assert (
        client.post(
            "/api/v1/auth/login", content=b"{}", headers={"Content-Length": "9999999999999999999"}
        ).status_code
        == 413
    )


def test_queue_limit(configured):
    store = configured.app.state.store
    for i in range(100):
        store.create_job("test", "action-" + str(i), DownloadCreate(torrent_id="mteam:1"))
    with pytest.raises(AppError, match="队列已满"):
        store.create_job("test", "overflow-key", DownloadCreate(torrent_id="mteam:1"))
