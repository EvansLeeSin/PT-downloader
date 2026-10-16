from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Scope = Literal["search:read", "downloads:read", "downloads:create", "downloads:delete"]
SearchMode = Literal["normal", "adult"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def validate_base_url(value: str, https_only: bool = False) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("地址无效") from exc
    if (
        any(ord(char) < 33 for char in value)
        or parsed.scheme not in (("https",) if https_only else ("http", "https"))
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("请使用不含账号、密码、查询参数的 HTTP(S) 地址")
    return value.rstrip("/")


class Settings(StrictModel):
    site_url: str = "https://kp.m-team.cc"
    site_api_url: str = "https://api.m-team.cc"
    site_api_key: str = Field(default="", repr=False, max_length=4096)
    qb_url: str = ""
    qb_username: str = Field(default="admin", max_length=200)
    qb_password: str = Field(default="", repr=False, max_length=4096)
    qb_verify_tls: bool = True
    download_tag: str = Field(default="mteam-downloader", max_length=100)

    @field_validator("site_url", "site_api_url")
    @classmethod
    def site_address(cls, value: str) -> str:
        return validate_base_url(value, https_only=True)

    @field_validator("qb_url")
    @classmethod
    def qb_address(cls, value: str) -> str:
        return validate_base_url(value) if value else ""


class SettingsUpdate(Settings):
    # null/omitted retains existing credentials; empty string deliberately clears them.
    site_api_key: str | None = Field(default=None, repr=False, max_length=4096)
    qb_password: str | None = Field(default=None, repr=False, max_length=4096)


class Location(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=1000)
    is_default: bool = False

    @field_validator("path")
    @classmethod
    def absolute_posix(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "\\" in value
            or any(ord(char) < 32 for char in value)
            or ".." in value.split("/")
        ):
            raise ValueError("必须是 qBittorrent 容器内的绝对路径，不能含 ..")
        return str(PurePosixPath(value))


class LocationsUpdate(StrictModel):
    items: list[Location] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def valid_list(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("目录 ID 不能重复")
        if sum(item.is_default for item in self.items) != 1:
            raise ValueError("必须且只能设置一个默认目录")
        return self


class Login(StrictModel):
    password: str = Field(min_length=1, max_length=4096, repr=False)


class TokenCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Scope] = Field(min_length=1, max_length=4)
    expires_in_days: int = Field(default=30, ge=1, le=365)


class DownloadCreate(StrictModel):
    torrent_id: str = Field(pattern=r"^mteam:[1-9][0-9]{0,19}$")
    location_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    start_paused: bool = False


class Promotion(StrictModel):
    type: Literal["free", "discount", "normal", "unknown"]
    download_factor: float | None = None
    upload_factor: float | None = None
    expires_at: str | None = None


class Torrent(StrictModel):
    id: str
    title: str
    description: str
    size_bytes: int
    seeders: int
    leechers: int
    published_at: str | None = None
    category: str | None = None
    labels: list[str] = Field(default_factory=list)
    detail_url: str
    promotion: Promotion


class SearchResult(StrictModel):
    items: list[Torrent]
    page: int
    page_size: int
    has_more: bool
    total: int | None = None


class LocationsResult(StrictModel):
    items: list[Location]


class DownloadJob(StrictModel):
    id: str
    torrent_id: str
    location_id: str
    save_path: str
    actual_save_path: str | None
    start_paused: bool
    submission_status: Literal["queued", "processing", "added", "exists", "failed", "uncertain"]
    info_hash: str | None
    error_code: str | None
    message: str
    created_at: float = Field(description="Unix timestamp in seconds")
    updated_at: float = Field(description="Unix timestamp in seconds")


class LiveError(StrictModel):
    code: str
    message: str


class LiveStatus(StrictModel):
    name: str | None = None
    state: str | None = None
    progress: float | None = None
    dlspeed: int | None = None
    upspeed: int | None = None
    size: int | None = None
    amount_left: int | None = None
    save_path: str | None = None


class DownloadDetail(DownloadJob):
    download_status: LiveStatus | None = None
    live_error: LiveError | None = None


class DownloadList(StrictModel):
    items: list[DownloadDetail]
    total: int


class ValidationField(StrictModel):
    field: str
    message: str


class ErrorBody(StrictModel):
    code: str
    message: str
    fields: list[ValidationField] | None = None


class ErrorResponse(StrictModel):
    error: ErrorBody
