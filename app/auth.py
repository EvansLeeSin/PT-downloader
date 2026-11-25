import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.errors import AppError

bearer = HTTPBearer(auto_error=False)
COOKIE = "mtd_session"
SCOPES = ["search:read", "downloads:read", "downloads:create", "downloads:delete"]


class RateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.windows = defaultdict(deque)

    def check(self, key: str, limit: int, period: int = 60):
        now = time.monotonic()
        with self.lock:
            # Bound the map even when untrusted clients vary their addresses.
            if len(self.windows) >= 5000:
                self.windows = defaultdict(
                    deque, {k: v for k, v in self.windows.items() if v and now - v[-1] < 600}
                )
            if key not in self.windows and len(self.windows) >= 5000:
                raise AppError("RATE_LIMITED", "请求过于频繁，请稍后再试", 429)
            queue = self.windows[key]
            while queue and now - queue[0] >= period:
                queue.popleft()
            if len(queue) >= limit:
                raise AppError("RATE_LIMITED", "请求过于频繁，请稍后再试", 429)
            queue.append(now)


def password_digest(password: str) -> bytes:
    return hashlib.sha256(password.encode()).digest()


def matches_password(password: str, digest: bytes) -> bool:
    return secrets.compare_digest(password_digest(password), digest)


def _origin_key(value: str) -> tuple[str, str, int]:
    """Normalize a bare origin (scheme://host[:port]) or raise ValueError."""
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.path
        or parts.query
        or parts.fragment
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError(value)
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    return parts.scheme, parts.hostname.lower(), port


def parse_trusted_origins(raw: str | None) -> frozenset[tuple[str, str, int]]:
    """Parse APP_TRUSTED_ORIGINS, e.g. 'https://pt.example.com,https://pt2.example.com'."""
    result = set()
    for item in (raw or "").replace("\n", ",").split(","):
        item = item.strip().rstrip("/")
        if not item:
            continue
        try:
            result.add(_origin_key(item))
        except ValueError:
            raise RuntimeError(
                f"APP_TRUSTED_ORIGINS 格式错误：{item!r}，应为 https://域名[:端口]，多个用逗号分隔"
            ) from None
    return frozenset(result)


def check_origin(request: Request):
    """CSRF guard for cookie-authenticated browser writes.

    Accept the Origin when it equals the URL the app sees itself at, or when it is
    explicitly listed in APP_TRUSTED_ORIGINS (needed behind HTTPS reverse proxies or
    Cloudflare Tunnel, where the app itself only receives plain HTTP).
    """
    origin = request.headers.get("origin") or ""
    trusted = getattr(request.app.state, "trusted_origins", frozenset())
    try:
        actual = _origin_key(origin)
        base = urlsplit(str(request.base_url))
        expected = _origin_key(f"{base.scheme}://{base.netloc}")
        same = actual == expected or actual in trusted
    except ValueError:
        same = False
    if not same:
        raise AppError(
            "CSRF_REJECTED",
            f"浏览器请求来源不匹配（来源 {origin[:200] or '缺失'}，应用地址 "
            f"{str(request.base_url).rstrip('/')}）。请从应用页面操作；"
            "如经反向代理或 Cloudflare Tunnel 访问，请在 APP_TRUSTED_ORIGINS 中配置访问地址",
            403,
        )


def principal(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
):
    token = credentials.credentials if credentials else request.cookies.get(COOKIE)
    if not token or len(token) > 256:
        raise AppError("UNAUTHORIZED", "请先登录或提供有效的 Bearer Token", 401)
    identity = request.app.state.store.authenticate(token)
    if (
        not identity
        or (credentials and identity["kind"] != "api")
        or (not credentials and identity["kind"] != "session")
    ):
        raise AppError("UNAUTHORIZED", "登录或访问令牌已失效", 401)
    if identity["kind"] == "session" and request.method not in ("GET", "HEAD", "OPTIONS"):
        check_origin(request)
    request.app.state.limiter.check("api:" + identity["id"], 120)
    return identity


def require_scope(scope: str):
    def dependency(identity: Annotated[dict, Depends(principal)]):
        if scope not in identity["scopes"]:
            raise AppError("FORBIDDEN", "访问令牌没有此操作权限", 403)
        return identity

    return dependency


def require_admin(identity: Annotated[dict, Depends(principal)]):
    if identity["kind"] != "session":
        raise AppError("FORBIDDEN", "设置与令牌管理仅允许管理员网页登录", 403)
    return identity
