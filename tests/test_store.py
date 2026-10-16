from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import DownloadCreate, SettingsUpdate
from app.store import Store
from tests.conftest import PASSWORD


def test_concurrent_idempotency(tmp_path):
    store = Store(tmp_path)
    store.update_settings(SettingsUpdate(site_api_key="key", qb_url="http://qb.test"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(
            pool.map(
                lambda _: store.create_job(
                    "owner", "same-action", DownloadCreate(torrent_id="mteam:1")
                )["id"],
                range(20),
            )
        )
    assert len(set(ids)) == 1
    assert store.jobs(100, 0)[1] == 1


def test_persistence_and_missing_key_fail_closed(tmp_path):
    store = Store(tmp_path)
    store.update_settings(SettingsUpdate(site_api_key="persisted-key"))
    assert Store(tmp_path).settings().site_api_key == "persisted-key"
    (tmp_path / "encryption.key").unlink()
    with pytest.raises(RuntimeError, match="加密密钥不存在"):
        Store(tmp_path)
    assert not (tmp_path / "encryption.key").exists()


def test_wrong_key_fails_on_startup(tmp_path, monkeypatch):
    Store(tmp_path)
    wrong_key = tmp_path / "wrong.key"
    wrong_key.write_bytes(Fernet.generate_key())
    monkeypatch.setenv("APP_SECRET_KEY_FILE", str(wrong_key))
    with pytest.raises(RuntimeError, match="配置解密失败"):
        Store(tmp_path)


def test_restart_invalidates_sessions_not_api_tokens(tmp_path):
    with TestClient(create_app(tmp_path, PASSWORD, start_worker=False)) as client:
        client.headers["Origin"] = "http://testserver"
        client.post("/api/v1/auth/login", json={"password": PASSWORD})
        cookie = client.cookies.get("mtd_session")
        result = client.post(
            "/api/v1/admin/tokens", json={"name": "AI", "scopes": ["downloads:read"]}
        )
        token = result.json()["token"]
    with TestClient(create_app(tmp_path, PASSWORD, start_worker=False)) as client:
        assert (
            client.get("/api/v1/auth/me", headers={"Cookie": "mtd_session=" + cookie}).status_code
            == 401
        )
        assert (
            client.get(
                "/api/v1/download-locations", headers={"Authorization": "Bearer " + token}
            ).status_code
            == 200
        )


def test_single_process_lock(tmp_path):
    with TestClient(create_app(tmp_path, PASSWORD, start_worker=False)):
        with pytest.raises(RuntimeError, match="只能启动一个应用进程"):
            with TestClient(create_app(tmp_path, PASSWORD, start_worker=False)):
                pass
