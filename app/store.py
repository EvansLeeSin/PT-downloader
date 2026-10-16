import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.errors import AppError
from app.models import DownloadCreate, Location, Settings, SettingsUpdate


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "app.sqlite3"
        key_path = Path(os.environ.get("APP_SECRET_KEY_FILE", str(directory / "encryption.key")))
        if not key_path.exists():
            if self.path.exists():
                raise RuntimeError("加密密钥不存在；请恢复原始密钥，不要为现有数据库生成新密钥")
            key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
        self.cipher = Fernet(key_path.read_bytes().strip())
        with self.connect() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tokens (
                    id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                    scopes TEXT NOT NULL, kind TEXT NOT NULL, created_at REAL NOT NULL,
                    expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, idem_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL, torrent_id TEXT NOT NULL,
                    location_id TEXT NOT NULL, save_path TEXT NOT NULL, start_paused INTEGER NOT NULL,
                    qb_url TEXT NOT NULL, submission_status TEXT NOT NULL,
                    info_hash TEXT, expected_hashes TEXT, actual_save_path TEXT, error_code TEXT, message TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, deleted_at REAL,
                    UNIQUE(owner, idem_key)
                );
                CREATE INDEX IF NOT EXISTS jobs_state ON jobs(submission_status, created_at);
            """)
            # Upgrade existing data directories without losing history or idempotency.
            columns = {row["name"] for row in con.execute("PRAGMA table_info(jobs)")}
            if "deleted_at" not in columns:
                con.execute("ALTER TABLE jobs ADD COLUMN deleted_at REAL")
            con.execute(
                "CREATE INDEX IF NOT EXISTS jobs_visible ON jobs(created_at DESC) "
                "WHERE deleted_at IS NULL"
            )
            con.execute(
                "INSERT OR IGNORE INTO config VALUES ('settings', ?)",
                (self.encrypt(Settings().model_dump()),),
            )
            con.execute(
                "INSERT OR IGNORE INTO config VALUES ('locations', ?)",
                (
                    json.dumps(
                        [
                            Location(
                                id="default",
                                name="默认下载目录",
                                path="/downloads",
                                is_default=True,
                            ).model_dump()
                        ]
                    ),
                ),
            )
        self.path.chmod(0o600)
        # Fail closed immediately when a different key was supplied.
        self.settings()

    @contextmanager
    def connect(self, write: bool = False):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA busy_timeout=15000")
            if write:
                con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def encrypt(self, value: dict) -> str:
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def _settings(self, con) -> Settings:
        try:
            value = con.execute("SELECT value FROM config WHERE key='settings'").fetchone()[0]
            return Settings.model_validate_json(self.cipher.decrypt(value.encode()))
        except InvalidToken as exc:
            raise RuntimeError("配置解密失败，请检查 APP_SECRET_KEY_FILE 与数据库是否匹配") from exc

    def settings(self) -> Settings:
        with self.connect() as con:
            return self._settings(con)

    def update_settings(self, update: SettingsUpdate):
        with self.connect(write=True) as con:
            if con.execute(
                "SELECT 1 FROM jobs WHERE submission_status IN ('queued', 'processing') LIMIT 1"
            ).fetchone():
                raise AppError("JOBS_ACTIVE", "有待提交的下载任务，请稍后再修改连接配置", 409)
            settings = self._settings(con)
            values = update.model_dump()
            for field in ("site_api_key", "qb_password"):
                if values[field] is None:
                    values[field] = getattr(settings, field)
            validated = Settings.model_validate(values)
            con.execute(
                "UPDATE config SET value=? WHERE key='settings'",
                (self.encrypt(validated.model_dump()),),
            )
        return validated

    def locations(self) -> list[Location]:
        with self.connect() as con:
            values = json.loads(
                con.execute("SELECT value FROM config WHERE key='locations'").fetchone()[0]
            )
        return [Location.model_validate(value) for value in values]

    def update_locations(self, items: list[Location]):
        with self.connect(write=True) as con:
            con.execute(
                "UPDATE config SET value=? WHERE key='locations'",
                (json.dumps([item.model_dump() for item in items]),),
            )

    def new_token(self, name: str, scopes: list[str], kind: str, lifetime: int):
        token = ("mtd_" if kind == "api" else "mts_") + secrets.token_urlsafe(32)
        now = time.time()
        result = {
            "id": uuid.uuid4().hex,
            "name": name,
            "scopes": sorted(set(scopes)),
            "created_at": now,
            "expires_at": now + lifetime,
        }
        with self.connect(write=True) as con:
            con.execute("DELETE FROM tokens WHERE expires_at < ?", (now,))
            con.execute(
                "INSERT INTO tokens (id,digest,name,scopes,kind,created_at,expires_at) VALUES (?,?,?,?,?,?,?)",
                (
                    result["id"],
                    token_digest(token),
                    name,
                    json.dumps(result["scopes"]),
                    kind,
                    now,
                    result["expires_at"],
                ),
            )
        return token, result

    def authenticate(self, token: str):
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM tokens WHERE digest=? AND revoked=0 AND expires_at>?",
                (token_digest(token), time.time()),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result.pop("digest")
        result["scopes"] = json.loads(result["scopes"])
        return result

    def list_tokens(self):
        with self.connect() as con:
            rows = con.execute(
                "SELECT id,name,scopes,created_at,expires_at,revoked FROM tokens WHERE kind='api' ORDER BY created_at DESC"
            ).fetchall()
        return [{**dict(row), "scopes": json.loads(row["scopes"])} for row in rows]

    def revoke_token(self, token_id: str):
        with self.connect(write=True) as con:
            con.execute("UPDATE tokens SET revoked=1 WHERE id=?", (token_id,))

    def create_job(self, owner: str, key: str, request: DownloadCreate):
        digest = token_digest(request.model_dump_json())
        with self.connect(write=True) as con:
            previous = con.execute(
                "SELECT * FROM jobs WHERE owner=? AND idem_key=?", (owner, key)
            ).fetchone()
            if previous:
                if previous["request_digest"] != digest:
                    raise AppError(
                        "IDEMPOTENCY_CONFLICT", "同一个幂等键不能用于不同的下载请求", 409
                    )
                if previous["deleted_at"] is not None:
                    raise AppError(
                        "JOB_DELETED", "该幂等键对应的提交记录已删除，不会重新提交下载", 409
                    )
                return dict(previous)
            config = self._settings(con)
            if not config.site_api_key or not config.qb_url:
                raise AppError("NOT_CONFIGURED", "请先配置 M-Team API Key 和 qBittorrent 地址", 409)
            locations = json.loads(
                con.execute("SELECT value FROM config WHERE key='locations'").fetchone()[0]
            )
            location = (
                next((item for item in locations if item["id"] == request.location_id), None)
                if request.location_id
                else next((item for item in locations if item["is_default"]), None)
            )
            if not location:
                raise AppError("LOCATION_NOT_FOUND", "保存目录不存在", 404)
            if (
                con.execute(
                    "SELECT COUNT(*) FROM jobs WHERE submission_status IN ('queued','processing')"
                ).fetchone()[0]
                >= 100
            ):
                raise AppError("QUEUE_FULL", "提交队列已满，请稍后再试", 429)
            now = time.time()
            job_id = uuid.uuid4().hex
            con.execute(
                "INSERT INTO jobs (id,owner,idem_key,request_digest,torrent_id,location_id,save_path,start_paused,qb_url,submission_status,message,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'queued',?,?,?)",
                (
                    job_id,
                    owner,
                    key,
                    digest,
                    request.torrent_id,
                    location["id"],
                    location["path"],
                    request.start_paused,
                    config.qb_url,
                    "已排队，等待提交到下载器",
                    now,
                    now,
                ),
            )
            return dict(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def recover_jobs(self):
        with self.connect(write=True) as con:
            con.execute(
                "UPDATE jobs SET submission_status='uncertain', error_code='PROCESS_RESTARTED', message='服务在处理中重启，请核对下载器；不会自动重新添加', updated_at=? WHERE submission_status='processing'",
                (time.time(),),
            )

    def claim_job(self):
        with self.connect(write=True) as con:
            row = con.execute(
                "SELECT * FROM jobs WHERE submission_status='queued' AND deleted_at IS NULL ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            con.execute(
                "UPDATE jobs SET submission_status='processing', message='正在获取种子并提交', updated_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
            return dict(row)

    def update_job(self, job_id: str, **values):
        allowed = {
            "submission_status",
            "info_hash",
            "expected_hashes",
            "actual_save_path",
            "error_code",
            "message",
        }
        if not values.keys() <= allowed:
            raise ValueError("Unsupported job field")
        values["updated_at"] = time.time()
        with self.connect(write=True) as con:
            con.execute(
                f"UPDATE jobs SET {','.join(field + '=?' for field in values)} WHERE id=?",
                (*values.values(), job_id),
            )

    def job(self, job_id: str):
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM jobs WHERE id=? AND deleted_at IS NULL", (job_id,)
            ).fetchone()
        if not row:
            raise AppError("JOB_NOT_FOUND", "提交记录不存在", 404)
        return dict(row)

    def delete_job(self, job_id: str):
        # Retain a tombstone so replaying an old idempotency key cannot resubmit.
        # The status check and deletion share a writer lock with the queue worker.
        with self.connect(write=True) as con:
            row = con.execute(
                "SELECT submission_status FROM jobs WHERE id=? AND deleted_at IS NULL", (job_id,)
            ).fetchone()
            if not row:
                raise AppError("JOB_NOT_FOUND", "提交记录不存在", 404)
            if row["submission_status"] in ("queued", "processing"):
                raise AppError("JOB_ACTIVE", "任务正在排队或提交，暂不能删除记录", 409)
            con.execute("UPDATE jobs SET deleted_at=? WHERE id=?", (time.time(), job_id))

    def jobs(self, limit: int, offset: int):
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM jobs WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = con.execute("SELECT COUNT(*) FROM jobs WHERE deleted_at IS NULL").fetchone()[0]
        return [dict(row) for row in rows], total
