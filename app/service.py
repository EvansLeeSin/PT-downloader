import json
import logging
import threading
import time

from app.clients.mteam import MTeamClient
from app.clients.qbittorrent import QbClient
from app.errors import AppError
from app.models import SearchMode, SearchResult
from app.store import Store, token_digest
from app.torrent import identify_torrent

logger = logging.getLogger(__name__)


class SearchService:
    def __init__(self, store: Store):
        self.store = store
        self.lock = threading.Lock()
        self.cache: dict[tuple, tuple[float, SearchResult]] = {}
        self.last_call = 0.0

    def search(self, keyword, page, page_size, categories, free_only, mode: SearchMode = "normal"):
        config = self.store.settings()
        key = (
            token_digest(config.model_dump_json()),
            keyword,
            page,
            page_size,
            tuple(categories),
            free_only,
            mode,
        )
        with self.lock:
            now = time.monotonic()
            self.cache = {key: value for key, value in self.cache.items() if now - value[0] < 20}
            if key in self.cache:
                return self.cache[key][1].model_copy(deep=True)
            if now - self.last_call < 1:
                raise AppError("SEARCH_RATE_LIMITED", "搜索过于频繁，请至少间隔 1 秒", 429)
            self.last_call = now
            result = MTeamClient(config).search(
                keyword, page, page_size, categories, free_only, mode
            )
            if len(self.cache) >= 100:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic(), result)
            return result


class DownloadWorker:
    def __init__(self, store: Store):
        self.store = store
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="download-worker", daemon=True)

    def start(self):
        self.store.recover_jobs()
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join()

    def run(self):
        while not self.stop_event.is_set():
            try:
                if not self.run_once():
                    self.stop_event.wait(0.5)
            except Exception:
                # Deliberately do not log external exception strings/tracebacks:
                # requests exceptions can embed signed URLs and credentials.
                logger.error("下载队列内部错误，稍后继续处理")
                self.stop_event.wait(2)

    def run_once(self):
        job = self.store.claim_job()
        if not job:
            return False
        client = None
        submission_started = False
        try:
            config = self.store.settings()
            content = MTeamClient(config).download(job["torrent_id"])
            identity = identify_torrent(content)
            hashes = set(identity.hashes)
            self.store.update_job(job["id"], expected_hashes=json.dumps(sorted(hashes)))
            client = QbClient(config)
            client.connect()
            existing = client.find(hashes)
            if existing:
                self._confirmed(job, existing, already_exists=True)
                return True
            # Persist hash identity before crossing the side-effect boundary.
            submission_started = True
            try:
                client.add(content, job["save_path"], bool(job["start_paused"]))
            except Exception:
                # A lost response is not proof of failure. Query before deciding.
                pass
            for attempt in range(5):
                try:
                    found = client.find(hashes)
                except AppError:
                    found = None
                if found:
                    self._confirmed(job, found, already_exists=False)
                    return True
                if attempt < 4:
                    self.stop_event.wait(1)
            self.store.update_job(
                job["id"],
                submission_status="uncertain",
                error_code="SUBMISSION_UNCONFIRMED",
                message="已尝试提交但未能确认任务；请核对 qBittorrent，不会自动重新添加",
            )
        except AppError as exc:
            self.store.update_job(
                job["id"],
                submission_status="uncertain" if submission_started else "failed",
                error_code=exc.code,
                message=exc.message,
            )
        except Exception:
            self.store.update_job(
                job["id"],
                submission_status="uncertain" if submission_started else "failed",
                error_code="INTERNAL_ERROR",
                message="处理异常，请核对提交记录与下载器",
            )
        finally:
            if client:
                client.close()
        return True

    def _confirmed(self, job, torrent, already_exists: bool):
        actual = str(torrent.get("save_path") or "") or None
        message = (
            "下载器已有相同任务，未修改目录或启停状态"
            if already_exists
            else "已确认添加到 qBittorrent"
        )
        if actual and actual.rstrip("/") != job["save_path"].rstrip("/"):
            message += "；实际保存目录与请求目录不同，请检查下载器设置"
        self.store.update_job(
            job["id"],
            submission_status="exists" if already_exists else "added",
            info_hash=str(torrent["hash"]),
            actual_save_path=actual,
            error_code=None,
            message=message,
        )


def public_download_status(torrent: dict) -> dict:
    return {
        key: torrent.get(key)
        for key in (
            "name",
            "state",
            "progress",
            "dlspeed",
            "upspeed",
            "size",
            "amount_left",
            "save_path",
        )
    }


def public_job(job: dict) -> dict:
    fields = (
        "id",
        "torrent_id",
        "location_id",
        "save_path",
        "actual_save_path",
        "submission_status",
        "info_hash",
        "error_code",
        "message",
        "created_at",
        "updated_at",
    )
    result = {key: job[key] for key in fields}
    result["start_paused"] = bool(job["start_paused"])
    return result
