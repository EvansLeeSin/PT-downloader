import qbittorrentapi

from app.errors import AppError
from app.models import Settings


class SingleSubmitClient(qbittorrentapi.Client):
    """The pinned SDK retries even with _retries=0; never retry torrents/add.

    This narrow private-method override is covered by an SDK contract test.
    HTTP adapter retries are independently disabled below.
    """

    def _request_manager(self, http_method, api_namespace, api_method, **kwargs):
        if getattr(api_namespace, "value", api_namespace) == "torrents" and api_method == "add":
            for field in (
                "_retries",
                "_retry_backoff_factor",
                "version_introduced",
                "version_removed",
            ):
                kwargs.pop(field, None)
            return self._request(
                http_method=http_method,
                api_namespace=api_namespace,
                api_method=api_method,
                **kwargs,
            )
        return super()._request_manager(
            http_method=http_method, api_namespace=api_namespace, api_method=api_method, **kwargs
        )


class QbClient:
    def __init__(self, settings: Settings):
        if not settings.qb_url:
            raise AppError("DOWNLOADER_NOT_CONFIGURED", "尚未配置 qBittorrent 地址", 409)
        self.config = settings
        self.client = SingleSubmitClient(
            host=settings.qb_url,
            username=settings.qb_username,
            password=settings.qb_password,
            VERIFY_WEBUI_CERTIFICATE=settings.qb_verify_tls,
            FORCE_SCHEME_FROM_HOST=True,
            REQUESTS_ARGS={"timeout": (5, 25), "allow_redirects": False},
            HTTPADAPTER_ARGS={"max_retries": 0},
            DISABLE_LOGGING_DEBUG_OUTPUT=True,
        )
        self.client._session.trust_env = False

    def close(self):
        self.client._session.close()

    def connect(self):
        try:
            self.client.auth_log_in()
        except (
            qbittorrentapi.LoginFailed,
            qbittorrentapi.Forbidden403Error,
            qbittorrentapi.Unauthorized401Error,
        ) as exc:
            raise AppError(
                "DOWNLOADER_AUTH_FAILED",
                "qBittorrent 登录失败，请检查 WebUI 账号、密码和访问限制",
                502,
            ) from exc
        except Exception as exc:
            raise AppError(
                "DOWNLOADER_UNREACHABLE", "无法连接 qBittorrent，请检查地址、网络和证书", 502
            ) from exc

    def test(self):
        self.connect()
        try:
            prefs = self.client.app_preferences()
            categories = self.client.torrents_categories()
            return {
                "connected": True,
                "version": str(self.client.app_version()),
                "web_api_version": str(self.client.app_web_api_version()),
                "default_save_path": prefs.get("save_path"),
                "category_paths": [
                    {"name": str(name), "path": value.get("savePath", "")}
                    for name, value in categories.items()
                    if isinstance(value, dict)
                ],
            }
        except Exception as exc:
            raise AppError(
                "DOWNLOADER_QUERY_FAILED", "已登录但无法读取 qBittorrent 信息", 502
            ) from exc

    def find(self, hashes: set[str]):
        try:
            # Full v2 and truncated v2 identities can differ across client versions.
            torrents = self.client.torrents_info(torrent_hashes="|".join(sorted(hashes)))
            for torrent in torrents:
                candidates = {
                    str(torrent.get(key) or "").lower()
                    for key in ("hash", "infohash_v1", "infohash_v2")
                }
                if hashes.intersection(candidates):
                    return dict(torrent)
            return None
        except Exception as exc:
            raise AppError("DOWNLOADER_QUERY_FAILED", "无法核对 qBittorrent 任务状态", 502) from exc

    def add(self, content: bytes, save_path: str, paused: bool):
        # Return shape may be legacy text or structured metadata. The worker
        # verifies by hash regardless; an HTTP success alone is not confirmation.
        return self.client.torrents_add(
            torrent_files=content,
            save_path=save_path,
            is_paused=paused,
            use_auto_torrent_management=False,
            tags=self.config.download_tag or None,
        )
