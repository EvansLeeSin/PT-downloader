import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from app.errors import AppError
from app.models import DownloadCreate
from app.store import Store
from tests.test_api import api_token


def make_job(client, key="record-action", status="added", hashes=None):
    store = client.app.state.store
    job = store.create_job("admin", key, DownloadCreate(torrent_id="mteam:123"))
    store.update_job(
        job["id"],
        submission_status=status,
        expected_hashes=json.dumps(hashes) if hashes else None,
    )
    return store.job(job["id"])


@pytest.mark.parametrize("status", ["added", "exists", "failed", "uncertain"])
def test_delete_only_hides_record_and_preserves_idempotency(configured, monkeypatch, status):
    job = make_job(configured, status=status)
    qb = MagicMock()
    monkeypatch.setattr("app.main.QbClient", qb)
    path = "/api/v1/downloads/" + job["id"]
    assert configured.delete(path).status_code == 204
    assert configured.get(path).status_code == 404
    assert configured.get("/api/v1/downloads").json() == {"items": [], "total": 0}
    assert configured.delete(path).status_code == 404
    replay = configured.post(
        "/api/v1/downloads",
        json={"torrent_id": "mteam:123"},
        headers={"Idempotency-Key": "record-action"},
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "JOB_DELETED"
    qb.assert_not_called()
    store = configured.app.state.store
    reopened = Store(store.path.parent)
    with pytest.raises(AppError) as error:
        reopened.create_job("admin", "record-action", DownloadCreate(torrent_id="mteam:123"))
    assert error.value.code == "JOB_DELETED"


@pytest.mark.parametrize("status", ["queued", "processing"])
def test_active_records_cannot_be_deleted(configured, status):
    job = make_job(configured, status=status)
    result = configured.delete("/api/v1/downloads/" + job["id"])
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "JOB_ACTIVE"
    assert configured.app.state.store.job(job["id"])["submission_status"] == status


def test_delete_and_worker_claim_do_not_lose_queued_job(configured):
    job = make_job(configured, status="queued")
    store = configured.app.state.store

    def remove():
        with pytest.raises(AppError) as error:
            store.delete_job(job["id"])
        return error.value.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim = pool.submit(store.claim_job)
        deletion = pool.submit(remove)
        assert claim.result()["id"] == job["id"]
        assert deletion.result() == "JOB_ACTIVE"
    assert store.job(job["id"])["submission_status"] == "processing"


@pytest.mark.parametrize(
    "scopes",
    [
        ["downloads:read"],
        ["downloads:create"],
        ["search:read", "downloads:read", "downloads:create"],
    ],
)
def test_delete_requires_separate_permission(configured, scopes):
    job = make_job(configured)
    token = api_token(configured, scopes)
    headers = {"Authorization": "Bearer " + token["token"]}
    result = configured.delete("/api/v1/downloads/" + job["id"], headers=headers)
    assert result.status_code == 403


@pytest.mark.parametrize(
    "scopes",
    [
        ["downloads:delete"],
        ["search:read", "downloads:read", "downloads:create", "downloads:delete"],
    ],
)
def test_delete_token_without_cookie_or_origin_and_browser_csrf(configured, scopes):
    job = make_job(configured)
    path = "/api/v1/downloads/" + job["id"]
    assert configured.delete(path, headers={"Origin": "https://evil.test"}).status_code == 403
    token = api_token(configured, scopes)
    configured.cookies.clear()
    assert configured.delete(path).status_code == 401
    headers = {"Authorization": "Bearer " + token["token"], "Origin": "https://external-ai.test"}
    assert configured.delete(path, headers=headers).status_code == 204
    assert configured.delete("/api/v1/downloads/missing", headers=headers).status_code == 404


def test_legacy_database_migration_preserves_records(configured):
    job = make_job(configured, status="failed")
    store = configured.app.state.store
    with store.connect(write=True) as con:
        con.execute("DROP INDEX jobs_visible")
        con.execute("ALTER TABLE jobs DROP COLUMN deleted_at")
    upgraded = Store(store.path.parent)
    assert upgraded.job(job["id"])["deleted_at"] is None
    assert upgraded.jobs(20, 0)[1] == 1
    upgraded.delete_job(job["id"])
    assert Store(store.path.parent).jobs(20, 0)[1] == 0


def test_list_queries_progress_in_one_batch_and_updates_on_refresh(configured, monkeypatch):
    first = make_job(configured, "first", hashes=["a" * 40, "b" * 64])
    second = make_job(configured, "second", status="exists", hashes=["c" * 40])
    missing = make_job(configured, "missing", hashes=["d" * 40])
    queued = make_job(configured, "queued", status="queued")
    failed = make_job(configured, "failed", status="failed", hashes=["e" * 40])
    old = make_job(configured, "old", hashes=["f" * 40])
    with configured.app.state.store.connect(write=True) as con:
        con.execute("UPDATE jobs SET qb_url='http://previous.test' WHERE id=?", (old["id"],))
    qb = MagicMock()
    live = {
        "name": "example",
        "state": "downloading",
        "progress": 0.25,
        "dlspeed": 1048576,
        "size": 1000,
        "amount_left": 750,
        "magnet_uri": "secret-must-not-leak",
        "tracker": "private-passkey",
    }
    qb.find_many.return_value = {
        "b" * 64: live,
        "c" * 40: {**live, "progress": 1.0, "state": "uploading"},
    }
    factory = MagicMock(return_value=qb)
    monkeypatch.setattr("app.main.QbClient", factory)
    local = configured.get("/api/v1/downloads")
    assert local.status_code == 200
    factory.assert_not_called()
    response = configured.get("/api/v1/downloads?include_live=true&limit=50")
    assert response.status_code == 200
    assert "secret-must-not-leak" not in response.text
    assert "private-passkey" not in response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert items[first["id"]]["download_status"]["progress"] == 0.25
    assert items[second["id"]]["download_status"]["progress"] == 1.0
    assert items[missing["id"]]["live_error"]["code"] == "TORRENT_NOT_FOUND"
    assert items[old["id"]]["live_error"]["code"] == "DOWNLOADER_CHANGED"
    for job in (queued, failed):
        assert items[job["id"]]["download_status"] is None
        assert items[job["id"]]["live_error"] is None
    qb.connect.assert_called_once()
    qb.find_many.assert_called_once_with({"a" * 40, "b" * 64, "c" * 40, "d" * 40})
    qb.close.assert_called_once()
    live["progress"] = 0.75
    updated = configured.get("/api/v1/downloads?include_live=true").json()["items"]
    assert (
        next(item for item in updated if item["id"] == first["id"])["download_status"]["progress"]
        == 0.75
    )
    qb.add.assert_not_called()


@pytest.mark.parametrize("stage", ["connect", "find_many"])
def test_list_keeps_history_when_downloader_fails(configured, monkeypatch, stage):
    job = make_job(configured, hashes=["a" * 40])
    qb = MagicMock()
    getattr(qb, stage).side_effect = AppError("DOWNLOADER_UNREACHABLE", "无法连接下载器", 502)
    monkeypatch.setattr("app.main.QbClient", lambda config: qb)
    result = configured.get("/api/v1/downloads?include_live=true")
    assert result.status_code == 200
    item = result.json()["items"][0]
    assert item["submission_status"] == "added"
    assert item["download_status"] is None
    assert item["live_error"]["code"] == "DOWNLOADER_UNREACHABLE"
    qb.close.assert_called_once()
    assert configured.app.state.store.job(job["id"])["submission_status"] == "added"


def test_no_live_connection_for_active_or_changed_downloader(configured, monkeypatch):
    make_job(configured, status="processing", hashes=["a" * 40])
    old = make_job(configured, "changed", hashes=["b" * 40])
    with configured.app.state.store.connect(write=True) as con:
        con.execute("UPDATE jobs SET qb_url='http://previous.test' WHERE id=?", (old["id"],))
    factory = MagicMock()
    monkeypatch.setattr("app.main.QbClient", factory)
    assert configured.get("/api/v1/downloads?include_live=true").status_code == 200
    factory.assert_not_called()


@pytest.mark.parametrize("found", [True, False])
def test_manual_detail_recheck_never_resubmits_uncertain_job(configured, monkeypatch, found):
    job = make_job(configured, status="uncertain", hashes=["a" * 40])
    qb = MagicMock()
    qb.find.return_value = (
        {"hash": "a" * 40, "save_path": "/downloads", "state": "downloading", "progress": 0.3}
        if found
        else None
    )
    monkeypatch.setattr("app.main.QbClient", lambda config: qb)
    response = configured.get("/api/v1/downloads/" + job["id"])
    assert response.status_code == 200
    result = response.json()
    assert result["submission_status"] == ("added" if found else "uncertain")
    assert (
        configured.app.state.store.job(job["id"])["submission_status"]
        == result["submission_status"]
    )
    if found:
        assert result["download_status"]["progress"] == 0.3
    else:
        assert result["live_error"]["code"] == "TORRENT_NOT_FOUND"
    qb.find.assert_called_once_with({"a" * 40})
    qb.add.assert_not_called()
    qb.close.assert_called_once()
