import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SettingsUpdate

PASSWORD = "local-test-password-123"


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    return (
        b"d" + b"".join(bencode(key) + bencode(item) for key, item in sorted(value.items())) + b"e"
    )


@pytest.fixture
def torrent_bytes():
    return bencode(
        {b"info": {b"name": b"sample", b"length": 1, b"piece length": 16384, b"pieces": b"x" * 20}}
    )


@pytest.fixture
def torrent_hash(torrent_bytes):
    # Fixture encoding is deliberately canonical; production hashes raw slices.
    return hashlib.sha1(torrent_bytes[len(b"d4:info") : -1]).hexdigest()


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path, admin_password=PASSWORD, start_worker=False)
    with TestClient(app) as client:
        client.headers["origin"] = "http://testserver"
        yield client


@pytest.fixture
def admin(client):
    assert client.post("/api/v1/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def configured(admin):
    admin.app.state.store.update_settings(
        SettingsUpdate(
            site_url="https://mteam.test",
            site_api_url="https://api.mteam.test",
            site_api_key="site-key-never-expose",
            qb_url="http://qb.test:8080",
            qb_username="admin",
            qb_password="qb-secret-never-expose",
        )
    )
    return admin


@pytest.fixture
def worker_mocks(configured, monkeypatch, torrent_bytes, torrent_hash):
    site = MagicMock()
    site.download.return_value = torrent_bytes
    qb = MagicMock()
    found = {
        "hash": torrent_hash,
        "save_path": "/downloads",
        "name": "sample",
        "state": "downloading",
        "progress": 0.25,
    }
    qb.find.side_effect = [None, found]
    monkeypatch.setattr("app.service.MTeamClient", lambda config: site)
    monkeypatch.setattr("app.service.QbClient", lambda config: qb)
    monkeypatch.setattr("app.main.QbClient", lambda config: qb)
    return site, qb, found
