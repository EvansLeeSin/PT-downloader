from app.errors import AppError


def submit(client, key="test-action-0001"):
    result = client.post(
        "/api/v1/downloads", json={"torrent_id": "mteam:123"}, headers={"Idempotency-Key": key}
    )
    assert result.status_code == 202
    return result.json()["id"]


def test_submit_verify_path_and_read_status(configured, worker_mocks):
    site, qb, found = worker_mocks
    job_id = submit(configured)
    assert configured.app.state.worker.run_once()
    job = configured.app.state.store.job(job_id)
    assert job["submission_status"] == "added"
    assert job["actual_save_path"] == "/downloads"
    qb.add.assert_called_once()
    assert qb.add.call_args.args[1:] == ("/downloads", False)
    qb.find.side_effect = None
    qb.find.return_value = found
    result = configured.get("/api/v1/downloads/" + job_id).json()
    assert result["download_status"]["progress"] == 0.25


def test_existing_never_moves_or_restarts(configured, worker_mocks):
    _, qb, found = worker_mocks
    found["save_path"] = "/downloads/old"
    qb.find.side_effect = [found]
    job_id = submit(configured)
    configured.app.state.worker.run_once()
    job = configured.app.state.store.job(job_id)
    assert job["submission_status"] == "exists"
    assert job["actual_save_path"] == "/downloads/old"
    qb.add.assert_not_called()


def test_lost_add_response_still_checks_client(configured, worker_mocks):
    _, qb, _ = worker_mocks
    qb.add.side_effect = TimeoutError("credentials-must-not-escape")
    job_id = submit(configured)
    configured.app.state.worker.run_once()
    assert configured.app.state.store.job(job_id)["submission_status"] == "added"
    qb.add.assert_called_once()


def test_unknown_response_never_retries_submit(configured, worker_mocks):
    _, qb, _ = worker_mocks
    qb.find.side_effect = None
    qb.find.return_value = None
    configured.app.state.worker.stop_event.set()  # Don't sleep during verification.
    job_id = submit(configured)
    configured.app.state.worker.run_once()
    assert configured.app.state.store.job(job_id)["submission_status"] == "uncertain"
    assert not configured.app.state.worker.run_once()
    qb.add.assert_called_once()


def test_invalid_download_never_calls_qb(configured, worker_mocks):
    site, qb, _ = worker_mocks
    site.download.return_value = b"<html>Error</html>"
    job_id = submit(configured)
    configured.app.state.worker.run_once()
    job = configured.app.state.store.job(job_id)
    assert job["submission_status"] == "failed"
    assert job["error_code"] == "INVALID_TORRENT"
    qb.connect.assert_not_called()
    qb.add.assert_not_called()


def test_failed_query_before_submit_does_not_add(configured, worker_mocks):
    _, qb, _ = worker_mocks
    qb.find.side_effect = AppError("DOWNLOADER_QUERY_FAILED", "查询失败", 502)
    job_id = submit(configured)
    configured.app.state.worker.run_once()
    assert configured.app.state.store.job(job_id)["submission_status"] == "failed"
    qb.add.assert_not_called()
