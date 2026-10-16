import fcntl
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import (
    COOKIE,
    SCOPES,
    RateLimiter,
    check_origin,
    matches_password,
    password_digest,
    principal,
    require_admin,
    require_scope,
)
from app.clients.mteam import MTeamClient
from app.clients.qbittorrent import QbClient
from app.errors import AppError
from app.middleware import RequestBodyLimit
from app.models import (
    DownloadCreate,
    DownloadDetail,
    DownloadJob,
    DownloadList,
    ErrorResponse,
    LocationsResult,
    LocationsUpdate,
    Login,
    SearchMode,
    SearchResult,
    SettingsUpdate,
    TokenCreate,
    Torrent,
)
from app.service import DownloadWorker, SearchService, public_download_status, public_job
from app.store import Store

STATIC = Path(__file__).parent / "static"


def create_app(data_dir: Path | None = None, admin_password: str | None = None, start_worker=True):
    @asynccontextmanager
    async def lifespan(application):
        password = admin_password or os.environ.get("APP_ADMIN_PASSWORD", "")
        password_file = os.environ.get("APP_ADMIN_PASSWORD_FILE")
        if admin_password is None and password_file:
            password = Path(password_file).read_text().strip()
        if len(password) < 12:
            raise RuntimeError(
                "请通过 APP_ADMIN_PASSWORD 或 APP_ADMIN_PASSWORD_FILE 设置至少 12 位管理员密码"
            )
        # httpx INFO logs include full signed torrent URLs. Never emit them.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        directory = Path(data_dir or os.environ.get("APP_DATA_DIR", "data"))
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (directory / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "同一个数据目录只能启动一个应用进程，请使用 --workers 1"
                ) from exc
            application.state.store = Store(directory)
            application.state.admin_password_digest = password_digest(password)
            application.state.limiter = RateLimiter()
            application.state.search = SearchService(application.state.store)
            application.state.worker = DownloadWorker(application.state.store)
            # A password change/restart invalidates browser sessions, not API tokens.
            with application.state.store.connect(write=True) as con:
                con.execute("DELETE FROM tokens WHERE kind='session'")
            if start_worker:
                application.state.worker.start()
            try:
                yield
            finally:
                if start_worker:
                    application.state.worker.stop()
                fcntl.flock(lock, fcntl.LOCK_UN)

    application = FastAPI(
        title="M-Team Downloader",
        version="0.1.0",
        lifespan=lifespan,
        description="M-Team 资源搜索与单个 qBittorrent 下载控制。AI 使用带最小权限的 Bearer Token。",
        redoc_url=None,
        responses={
            status: {"model": ErrorResponse}
            for status in (401, 403, 404, 409, 413, 422, 429, 500, 502)
        },
    )

    @application.middleware("http")
    async def safety_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path != "/docs":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    @application.exception_handler(AppError)
    async def application_error(request, exc: AppError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else {}
        if exc.status == 429:
            headers["Retry-After"] = "60" if exc.code == "RATE_LIMITED" else "2"
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}},
            status_code=exc.status,
            headers=headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default error embeds input values, including passwords.
        return JSONResponse(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数无效",
                    "fields": [
                        {"field": ".".join(map(str, item["loc"])), "message": item["msg"]}
                        for item in exc.errors()
                    ],
                }
            },
            status_code=422,
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse(
            {"error": {"code": "INTERNAL_ERROR", "message": "服务内部异常"}}, status_code=500
        )

    @application.get("/healthz", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @application.post("/api/v1/auth/login", tags=["Web 登录"])
    def login(payload: Login, request: Request, response: Response):
        # This route is for browsers, not programmatic integration.
        check_origin(request)
        host = request.client.host if request.client else "unknown"
        request.app.state.limiter.check("login:" + host, 5, 300)
        if not matches_password(payload.password, request.app.state.admin_password_digest):
            raise AppError("LOGIN_FAILED", "管理员密码错误", 401)
        token, _ = request.app.state.store.new_token("web", SCOPES, "session", 12 * 3600)
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            max_age=12 * 3600,
            path="/",
        )
        return {"authenticated": True}

    @application.get("/api/v1/auth/me", tags=["Web 登录"])
    def me(identity: Annotated[dict, Depends(principal)]):
        return {"kind": identity["kind"], "scopes": identity["scopes"]}

    @application.post("/api/v1/auth/logout", tags=["Web 登录"])
    def logout(
        request: Request, response: Response, identity: Annotated[dict, Depends(require_admin)]
    ):
        request.app.state.store.revoke_token(identity["id"])
        response.delete_cookie(COOKIE, path="/")
        return {"authenticated": False}

    def settings_view(config):
        result = config.model_dump(exclude={"site_api_key", "qb_password"})
        result.update(
            site_api_key_configured=bool(config.site_api_key),
            qb_password_configured=bool(config.qb_password),
        )
        return result

    @application.get(
        "/api/v1/admin/settings", tags=["管理员"], dependencies=[Depends(require_admin)]
    )
    def settings(request: Request):
        return settings_view(request.app.state.store.settings())

    @application.put(
        "/api/v1/admin/settings", tags=["管理员"], dependencies=[Depends(require_admin)]
    )
    def update_settings(payload: SettingsUpdate, request: Request):
        return settings_view(request.app.state.store.update_settings(payload))

    @application.post(
        "/api/v1/admin/connections/{service}/test",
        tags=["管理员"],
        dependencies=[Depends(require_admin)],
    )
    def test_connection(service: str, request: Request):
        request.app.state.limiter.check("connection-tests", 10)
        config = request.app.state.store.settings()
        if service == "mteam":
            return MTeamClient(config).test()
        if service == "qbittorrent":
            client = QbClient(config)
            try:
                return client.test()
            finally:
                client.close()
        raise AppError("SERVICE_NOT_FOUND", "未知服务", 404)

    @application.get(
        "/api/v1/download-locations",
        response_model=LocationsResult,
        tags=["目录"],
        dependencies=[Depends(require_scope("downloads:read"))],
    )
    def locations(request: Request):
        return {"items": request.app.state.store.locations()}

    @application.put(
        "/api/v1/admin/download-locations", tags=["管理员"], dependencies=[Depends(require_admin)]
    )
    def update_locations(payload: LocationsUpdate, request: Request):
        request.app.state.store.update_locations(payload.items)
        return {"items": payload.items}

    @application.get("/api/v1/admin/tokens", tags=["管理员"], dependencies=[Depends(require_admin)])
    def tokens(request: Request):
        return {"items": request.app.state.store.list_tokens()}

    @application.post(
        "/api/v1/admin/tokens",
        status_code=201,
        tags=["管理员"],
        dependencies=[Depends(require_admin)],
    )
    def create_token(payload: TokenCreate, request: Request):
        token, metadata = request.app.state.store.new_token(
            payload.name, payload.scopes, "api", payload.expires_in_days * 86400
        )
        return {**metadata, "token": token}

    @application.delete(
        "/api/v1/admin/tokens/{token_id}",
        status_code=204,
        tags=["管理员"],
        dependencies=[Depends(require_admin)],
    )
    def revoke_token(token_id: str, request: Request):
        request.app.state.store.revoke_token(token_id)
        return Response(status_code=204)

    @application.get(
        "/api/v1/torrents/search",
        response_model=SearchResult,
        tags=["资源"],
        dependencies=[Depends(require_scope("search:read"))],
    )
    def search(
        request: Request,
        keyword: Annotated[str, Query(min_length=1, max_length=200)],
        page: Annotated[int, Query(ge=1, le=10000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        categories: Annotated[list[str] | None, Query(max_length=20)] = None,
        free_only: bool = False,
        mode: Annotated[
            SearchMode, Query(description="搜索分区：normal 综合，adult 成人")
        ] = "normal",
    ):
        keyword = keyword.strip()
        if not keyword:
            raise AppError("INVALID_KEYWORD", "搜索词不能为空", 422)
        return request.app.state.search.search(
            keyword, page, page_size, categories or [], free_only, mode
        )

    @application.get(
        "/api/v1/torrents/{resource_id}",
        response_model=Torrent,
        tags=["资源"],
        dependencies=[Depends(require_scope("search:read"))],
    )
    def detail(
        resource_id: Annotated[str, PathParam(pattern=r"^mteam:[1-9][0-9]{0,19}$")],
        request: Request,
    ):
        request.app.state.limiter.check("site-details", 20)
        return MTeamClient(request.app.state.store.settings()).detail(resource_id)

    @application.post(
        "/api/v1/downloads", response_model=DownloadJob, status_code=202, tags=["下载"]
    )
    def create_download(
        payload: DownloadCreate,
        request: Request,
        idempotency_key: Annotated[
            str, Header(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
        ],
        identity: Annotated[dict, Depends(require_scope("downloads:create"))],
    ):
        request.app.state.limiter.check("submit:" + identity["id"], 20)
        owner = "admin" if identity["kind"] == "session" else identity["id"]
        return public_job(request.app.state.store.create_job(owner, idempotency_key, payload))

    def live_downloads(store, jobs):
        results = [dict(public_job(job), download_status=None, live_error=None) for job in jobs]
        eligible = [
            (job, result)
            for job, result in zip(jobs, results, strict=True)
            if job["expected_hashes"]
            and job["submission_status"] in ("added", "exists", "uncertain")
        ]
        if not eligible:
            return results
        config = store.settings()
        pending = []
        for job, result in eligible:
            if config.qb_url != job["qb_url"]:
                result["live_error"] = {
                    "code": "DOWNLOADER_CHANGED",
                    "message": "下载器地址已变更，仅显示原提交记录",
                }
            else:
                pending.append((job, result))
        if not pending:
            return results
        client = None
        try:
            client = QbClient(config)
            client.connect()
            hashes = {value for job, _ in pending for value in json.loads(job["expected_hashes"])}
            found = client.find_many(hashes)
            for job, result in pending:
                torrent = next(
                    (
                        found[value]
                        for value in json.loads(job["expected_hashes"])
                        if value in found
                    ),
                    None,
                )
                if torrent:
                    result["download_status"] = public_download_status(torrent)
                else:
                    result["live_error"] = {
                        "code": "TORRENT_NOT_FOUND",
                        "message": "下载器当前未找到该任务；不会自动重新添加",
                    }
        except AppError as exc:
            for _, result in pending:
                result["live_error"] = {"code": exc.code, "message": exc.message}
        finally:
            if client:
                client.close()
        return results

    @application.delete(
        "/api/v1/downloads/{job_id}",
        status_code=204,
        tags=["下载"],
        dependencies=[Depends(require_scope("downloads:delete"))],
        summary="仅删除提交记录，不删除下载器任务或文件",
    )
    def delete_download(job_id: str, request: Request):
        request.app.state.store.delete_job(job_id)
        return Response(status_code=204)

    @application.get(
        "/api/v1/downloads",
        response_model=DownloadList,
        tags=["下载"],
        dependencies=[Depends(require_scope("downloads:read"))],
    )
    def downloads(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        include_live: bool = False,
    ):
        store = request.app.state.store
        jobs, total = store.jobs(limit, offset)
        items = live_downloads(store, jobs) if include_live else [public_job(job) for job in jobs]
        return {"items": items, "total": total}

    @application.get(
        "/api/v1/downloads/{job_id}",
        response_model=DownloadDetail,
        tags=["下载"],
        dependencies=[Depends(require_scope("downloads:read"))],
    )
    def download(job_id: str, request: Request, include_live: bool = True):
        store = request.app.state.store
        job = store.job(job_id)
        result = public_job(job)
        result.update(download_status=None, live_error=None)
        if not include_live or not job["expected_hashes"]:
            return result
        config = store.settings()
        if config.qb_url != job["qb_url"]:
            result["live_error"] = {
                "code": "DOWNLOADER_CHANGED",
                "message": "下载器地址已变更，仅显示原提交记录",
            }
            return result
        client = None
        try:
            client = QbClient(config)
            client.connect()
            found = client.find(set(json.loads(job["expected_hashes"])))
            if found:
                if job["submission_status"] == "uncertain":
                    request.app.state.worker._confirmed(job, found, already_exists=False)
                    result.update(public_job(store.job(job_id)))
                result["download_status"] = public_download_status(found)
            else:
                result["live_error"] = {
                    "code": "TORRENT_NOT_FOUND",
                    "message": "下载器当前未找到该任务；不会自动重新添加",
                }
        except AppError as exc:
            result["live_error"] = {"code": exc.code, "message": exc.message}
        finally:
            if client:
                client.close()
        return result

    application.add_middleware(RequestBodyLimit, max_bytes=65536)
    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    return application


app = create_app()
