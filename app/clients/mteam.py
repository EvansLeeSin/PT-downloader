import re
import time
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx

from app.errors import AppError
from app.models import Promotion, SearchResult, Settings, Torrent

MAX_TORRENT_BYTES = 10 * 1024 * 1024
SITE_TIMEZONE = timezone(timedelta(hours=8))
DISCOUNTS = {
    "NORMAL": (1.0, 1.0),
    "FREE": (0.0, 1.0),
    "PERCENT_50": (0.5, 1.0),
    "PERCENT_70": (0.3, 1.0),
    "_2X": (1.0, 2.0),
    "_2X_FREE": (0.0, 2.0),
    "_2X_PERCENT_50": (0.5, 2.0),
}


def parse_time(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (
            isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) >= 10
        ):
            timestamp = float(value)
            date = datetime.fromtimestamp(
                timestamp / 1000 if timestamp > 10**11 else timestamp, UTC
            )
        else:
            date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=SITE_TIMEZONE)
        return date.astimezone(UTC).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def promotion(status: dict) -> Promotion:
    discount = status.get("discount", "NORMAL")
    end = status.get("discountEndTime")
    factors = DISCOUNTS.get(discount) if isinstance(discount, str) else None
    download, upload = factors if factors else (None, None)
    rule = status.get("promotionRule")
    if isinstance(rule, dict) and rule.get("discount"):
        rule_discount = rule["discount"]
        rule_factors = DISCOUNTS.get(rule_discount) if isinstance(rule_discount, str) else None
        # Site-wide rules change download accounting, not the base upload factor.
        download = rule_factors[0] if rule_factors else None
        end = rule.get("endTime") or end
    single = status.get("mallSingleFree")
    if isinstance(single, dict) and single.get("status") == "ONGOING":
        download, end = 0.0, single.get("endDate") or end
    expires = parse_time(end)
    # An expired or unparseable promotion must never be advertised as free.
    if (
        download is None
        or (end and not expires)
        or (expires and datetime.fromisoformat(expires) <= datetime.now(UTC))
    ):
        return Promotion(type="unknown", expires_at=expires)
    return Promotion(
        type="free" if download == 0 else "discount" if download < 1 else "normal",
        download_factor=download,
        upload_factor=upload,
        expires_at=expires,
    )


def number(value, default=0):
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return default


class MTeamClient:
    def __init__(self, settings: Settings):
        self.config = settings

    def _client(self):
        if not self.config.site_api_key:
            raise AppError("SITE_NOT_CONFIGURED", "尚未配置 M-Team API Key", 409)
        return httpx.Client(
            timeout=httpx.Timeout(20, connect=5),
            follow_redirects=False,
            trust_env=False,
            headers={
                "x-api-key": self.config.site_api_key,
                "User-Agent": "mteam-downloader/0.1.0",
                "Origin": self.config.site_url,
                "Referer": self.config.site_url + "/",
                "Accept": "application/json",
            },
        )

    def _post(self, path: str, *, json_body=None, form=None):
        try:
            with self._client() as client:
                response = client.post(self.config.site_api_url + path, json=json_body, data=form)
                if response.status_code in (401, 403):
                    raise AppError(
                        "SITE_AUTH_FAILED", "M-Team 拒绝访问，请检查 API Key 和账号权限", 502
                    )
                if response.status_code == 429:
                    raise AppError("SITE_RATE_LIMITED", "M-Team 请求限流，请稍后重试", 429)
                if response.status_code != 200:
                    raise AppError("SITE_HTTP_ERROR", "M-Team 接口返回非成功状态", 502)
                if len(response.content) > 8 * 1024 * 1024:
                    raise AppError("SITE_INVALID_RESPONSE", "M-Team 响应过大", 502)
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError("SITE_UNREACHABLE", "M-Team 连接失败或返回无效 JSON", 502) from exc
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            # Do not forward upstream text: it may contain tokens or signed URLs.
            raise AppError(
                "SITE_API_ERROR", "M-Team 业务接口拒绝请求，请检查凭据、权限或请求频率", 502
            )
        return payload.get("data")

    def test(self):
        value = self._post("/api/member/profile", form={})
        if not isinstance(value, dict) or not value.get("id"):
            raise AppError("SITE_INVALID_RESPONSE", "M-Team 未返回有效用户信息", 502)
        return {"connected": True, "username": str(value.get("username", ""))}

    def _torrent(self, value: dict) -> Torrent:
        torrent_id = str(value.get("id", ""))
        if not re.fullmatch(r"[1-9][0-9]{0,19}", torrent_id):
            raise AppError("SITE_INVALID_RESPONSE", "M-Team 返回了无效资源 ID", 502)
        status = value.get("status") if isinstance(value.get("status"), dict) else {}
        labels = value.get("labelsNew")
        if not isinstance(labels, list) or not labels:
            old = number(value.get("labels"))
            labels = [label for bit, label in ((1, "DIY"), (2, "国配"), (4, "中字")) if old & bit]
        return Torrent(
            id="mteam:" + torrent_id,
            title=str(value.get("name") or ""),
            description=str(value.get("smallDescr") or ""),
            size_bytes=number(value.get("size")),
            seeders=number(status.get("seeders")),
            leechers=number(status.get("leechers")),
            published_at=parse_time(value.get("createdDate")),
            category=str(value["category"]) if value.get("category") is not None else None,
            labels=[str(label) for label in labels if isinstance(label, (str, int))],
            detail_url=self.config.site_url + "/detail/" + torrent_id,
            promotion=promotion(status),
        )

    def search(
        self, keyword: str, page: int, page_size: int, categories: list[str], free_only: bool
    ):
        if keyword.startswith("tt") and keyword[2:].isdigit():
            keyword = "https://www.imdb.com/title/" + keyword
        params = {
            "keyword": keyword,
            "categories": categories,
            "pageNumber": page,
            "pageSize": page_size,
            "visible": 1,
        }
        if free_only:
            params["discount"] = "FREE"
        data = self._post("/api/torrent/search", json_body=params)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise AppError("SITE_INVALID_RESPONSE", "M-Team 搜索结果格式不符合预期", 502)
        items = [self._torrent(item) for item in data["data"] if isinstance(item, dict)]
        total = number(data["total"]) if data.get("total") is not None else None
        has_more = (
            page < number(data["totalPages"])
            if data.get("totalPages") is not None
            else len(data["data"]) >= page_size
        )
        return SearchResult(
            items=items, page=page, page_size=page_size, has_more=has_more, total=total
        )

    def detail(self, resource_id: str):
        data = self._post("/api/torrent/detail", form={"id": resource_id.split(":", 1)[1]})
        if not isinstance(data, dict):
            raise AppError("SITE_INVALID_RESPONSE", "M-Team 资源详情格式不符合预期", 502)
        return self._torrent(data)

    def download(self, resource_id: str) -> bytes:
        url = self._post("/api/torrent/genDlToken", form={"id": resource_id.split(":", 1)[1]})
        if not isinstance(url, str):
            raise AppError("SITE_INVALID_RESPONSE", "M-Team 未返回种子下载地址", 502)
        url = urljoin(self.config.site_api_url + "/", url)
        origin = urlsplit(self.config.site_api_url)

        def trusted(target: str):
            parsed = urlsplit(target)
            if (
                (parsed.scheme, parsed.hostname, parsed.port or 443)
                != (origin.scheme, origin.hostname, origin.port or 443)
                or parsed.port == 0
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise AppError(
                    "UNTRUSTED_DOWNLOAD_URL",
                    "种子下载地址或重定向不属于配置的 API 来源，已拒绝访问",
                    502,
                )

        deadline = time.monotonic() + 60
        try:
            with self._client() as client:
                for _ in range(4):
                    trusted(url)
                    with client.stream(
                        "GET", url, headers={"Accept": "application/x-bittorrent"}
                    ) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            url = urljoin(url, response.headers.get("location", ""))
                            continue
                        if response.status_code != 200:
                            raise AppError(
                                "TORRENT_FETCH_FAILED",
                                "种子下载失败，请重新获取资源或稍后重试",
                                502,
                            )
                        data = bytearray()
                        for chunk in response.iter_bytes(chunk_size=65536):
                            data.extend(chunk)
                            if len(data) > MAX_TORRENT_BYTES or time.monotonic() > deadline:
                                raise AppError(
                                    "TORRENT_FETCH_FAILED", "种子文件超过大小或下载时限", 502
                                )
                        return bytes(data)
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError("TORRENT_FETCH_FAILED", "获取种子文件失败；未提交到下载器", 502) from exc
        raise AppError("TORRENT_FETCH_FAILED", "种子下载重定向次数过多", 502)
