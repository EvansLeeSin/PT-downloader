import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin

import httpx
import pytest
import respx

from app.clients.mteam import MTeamClient, parse_time, promotion
from app.errors import AppError
from app.models import Settings

BASE = "https://api.mteam.test"


def client():
    return MTeamClient(
        Settings(site_api_url=BASE, site_url="https://mteam.test", site_api_key="top-secret")
    )


def row():
    return {
        "id": "123",
        "name": "标题",
        "smallDescr": "副标题",
        "size": "100",
        "labelsNew": ["中字"],
        "status": {"seeders": "12", "leechers": "3", "discount": "FREE"},
    }


@respx.mock
def test_search_contract():
    route = respx.post(BASE + "/api/torrent/search").mock(
        return_value=httpx.Response(
            200, json={"code": "0", "data": {"data": [row()], "totalPages": 2, "total": 21}}
        )
    )
    result = client().search("tt1234567", 1, 20, [], False)
    assert result.items[0].id == "mteam:123"
    assert result.items[0].promotion.type == "free"
    assert result.has_more
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "top-secret"
    assert request.headers["origin"] == "https://mteam.test"
    body = json.loads(request.content)
    assert body["mode"] == "normal"
    assert body["keyword"] == "https://www.imdb.com/title/tt1234567"
    assert body["pageNumber"] == 1
    assert body["visible"] == 1
    assert "top-secret" not in result.model_dump_json()


@pytest.mark.parametrize("mode", ["normal", "adult"])
@respx.mock
def test_search_sends_mode_with_other_filters(mode):
    route = respx.post(BASE + "/api/torrent/search").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"data": [row()]}})
    )
    assert client().search("资源", 2, 50, ["410"], True, mode).items
    assert json.loads(route.calls.last.request.content) == {
        "keyword": "资源",
        "mode": mode,
        "categories": ["410"],
        "pageNumber": 2,
        "pageSize": 50,
        "visible": 1,
        "discount": "FREE",
    }


@respx.mock
def test_business_error_http_200():
    respx.post(BASE + "/api/torrent/search").mock(
        return_value=httpx.Response(
            200, json={"code": "401", "message": "secret:top-secret", "data": {}}
        )
    )
    with pytest.raises(AppError) as error:
        client().search("movie", 1, 20, [], False)
    assert error.value.code == "SITE_API_ERROR"
    assert "top-secret" not in str(error.value)


@respx.mock
def test_download_two_stage(torrent_bytes):
    token = respx.post(BASE + "/api/torrent/genDlToken").mock(
        return_value=httpx.Response(
            200, json={"code": 0, "data": BASE + "/api/rss/dl?token=signed"}
        )
    )
    download = respx.get(BASE + "/api/rss/dl?token=signed").mock(
        return_value=httpx.Response(200, content=torrent_bytes)
    )
    assert client().download("mteam:123") == torrent_bytes
    assert token.calls.last.request.content == b"id=123"
    assert "application/x-www-form-urlencoded" in token.calls.last.request.headers["content-type"]
    assert download.called
    assert download.calls.last.request.headers["x-api-key"] == "top-secret"


@pytest.mark.parametrize(
    "url",
    [
        "https://api2.m-team.cc/new/path?token=signed",
        "https://download.m-team.io/torrent",
        "https://M-TEAM.CC/torrent",
        "https://dl.m-team.co.uk/torrent",
        "https://dl.m-team-new.net:8443/torrent",
        "https://dl.m-team.cc./torrent",
        "//download.m-team.io/torrent",
        "https://cdn.example.net/torrent",
        "https://m-team.evil.com/torrent",
        "https://m-team.github.io/torrent",
        "https://127.0.0.1/torrent",
        "https://[::1]/torrent",
        "https://localhost/torrent",
        "https://downloads.internal/torrent",
    ],
)
@respx.mock
def test_download_accepts_any_https_host_without_forwarding_key(url, torrent_bytes):
    token = respx.post(BASE + "/api/torrent/genDlToken").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": url})
    )
    download = respx.get(urljoin(BASE + "/", url)).mock(
        return_value=httpx.Response(200, content=torrent_bytes)
    )
    assert client().download("mteam:123") == torrent_bytes
    assert token.calls.last.request.headers["x-api-key"] == "top-secret"
    assert "x-api-key" not in download.calls.last.request.headers


@pytest.mark.parametrize(
    "url",
    [
        "https://m-team.cc@evil.com/torrent",
        "https://user:password@dl.m-team.cc/torrent",
        "https://@dl.m-team.cc/torrent",
        "http://dl.m-team.cc/torrent",
        "https://dl.m-team.cc/torrent#fragment",
        "https://dl.m-team.cc:0/torrent",
        "https://dl.m-team.cc:65536/torrent",
        "https://dl.m-team.cc:invalid/torrent",
    ],
)
@respx.mock
def test_download_rejects_untrusted_urls_before_get(url):
    respx.post(BASE + "/api/torrent/genDlToken").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": url})
    )
    with pytest.raises(AppError) as error:
        client().download("mteam:123")
    assert error.value.code == "UNTRUSTED_DOWNLOAD_URL"
    assert len(respx.calls) == 1  # Only genDlToken was requested.


@respx.mock
def test_download_accepts_new_port_without_forwarding_key(torrent_bytes):
    base = "https://api.m-team.cc"
    respx.post(base + "/api/torrent/genDlToken").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": base + ":8443/file"})
    )
    download = respx.get(base + ":8443/file").mock(
        return_value=httpx.Response(200, content=torrent_bytes)
    )
    configured = MTeamClient(Settings(site_api_url=base, site_api_key="top-secret"))
    assert configured.download("mteam:123") == torrent_bytes
    assert "x-api-key" not in download.calls.last.request.headers


@respx.mock
def test_api_key_policy_reapplied_on_each_redirect(torrent_bytes):
    respx.post(BASE + "/api/torrent/genDlToken").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": "/file"})
    )
    first = respx.get(BASE + "/file").mock(
        return_value=httpx.Response(302, headers={"Location": "https://dl.m-team.cc/file"})
    )
    second = respx.get("https://dl.m-team.cc/file").mock(
        return_value=httpx.Response(307, headers={"Location": "https://download.m-team.io/file"})
    )
    third = respx.get("https://download.m-team.io/file").mock(
        return_value=httpx.Response(308, headers={"Location": BASE + "/final"})
    )
    final = respx.get(BASE + "/final").mock(return_value=httpx.Response(200, content=torrent_bytes))
    assert client().download("mteam:123") == torrent_bytes
    assert first.calls.last.request.headers["x-api-key"] == "top-secret"
    assert "x-api-key" not in second.calls.last.request.headers
    assert "x-api-key" not in third.calls.last.request.headers
    assert final.calls.last.request.headers["x-api-key"] == "top-secret"


@respx.mock
def test_redirect_to_unrelated_https_domain_is_accepted_without_key(torrent_bytes):
    respx.post(BASE + "/api/torrent/genDlToken").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": "https://dl.m-team.cc/file"})
    )
    respx.get("https://dl.m-team.cc/file").mock(
        return_value=httpx.Response(302, headers={"Location": "https://cdn.example.net/file"})
    )
    final = respx.get("https://cdn.example.net/file").mock(
        return_value=httpx.Response(200, content=torrent_bytes)
    )
    assert client().download("mteam:123") == torrent_bytes
    assert "x-api-key" not in final.calls.last.request.headers


@respx.mock
def test_redirect_revalidated_before_request():
    respx.post(BASE + "/api/torrent/genDlToken").mock(
        return_value=httpx.Response(200, json={"code": "0", "data": "/file"})
    )
    respx.get(BASE + "/file").mock(
        return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
    )
    with pytest.raises(AppError) as error:
        client().download("mteam:123")
    assert error.value.code == "UNTRUSTED_DOWNLOAD_URL"


def test_promotion_precedence_and_expiry():
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert (
        promotion(
            {"discount": "NORMAL", "promotionRule": {"discount": "FREE", "endTime": future}}
        ).type
        == "free"
    )
    assert (
        promotion(
            {"discount": "NORMAL", "mallSingleFree": {"status": "ONGOING", "endDate": future}}
        ).type
        == "free"
    )
    assert (
        promotion({"discount": "FREE", "discountEndTime": "2000-01-01 00:00:00"}).type == "unknown"
    )
    assert promotion({"discount": "FREE", "discountEndTime": "invalid"}).type == "unknown"
    assert promotion({"discount": "UNRECOGNIZED"}).type == "unknown"
    assert promotion({"discount": "PERCENT_70"}).download_factor == 0.3
    assert promotion({"discount": {"unexpected": "value"}}).type == "unknown"
    promo = promotion({"discount": "_2X", "promotionRule": {"discount": "FREE"}})
    assert promo.type == "free"
    assert promo.upload_factor == 2.0


@pytest.mark.parametrize("value", [1767225600, "1767225600", 1767225600000, "1767225600000"])
def test_epoch_times_accept_seconds_and_milliseconds(value):
    assert parse_time(value) == "2026-01-01T00:00:00+00:00"


def test_empty_new_labels_falls_back_to_old_flags():
    data = {**row(), "labelsNew": [], "labels": "7"}
    assert client()._torrent(data).labels == ["DIY", "国配", "中字"]


@respx.mock
def test_profile_and_detail():
    respx.post(BASE + "/api/member/profile").mock(
        return_value=httpx.Response(
            200, json={"code": "0", "data": {"id": "42", "username": "sample"}}
        )
    )
    detail = respx.post(BASE + "/api/torrent/detail").mock(
        return_value=httpx.Response(200, json={"code": "0", "data": row()})
    )
    assert client().test()["connected"]
    assert client().detail("mteam:123").id == "mteam:123"
    assert detail.calls.last.request.content == b"id=123"


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "SITE_AUTH_FAILED"),
        (403, "SITE_AUTH_FAILED"),
        (429, "SITE_RATE_LIMITED"),
        (500, "SITE_HTTP_ERROR"),
    ],
)
@respx.mock
def test_http_errors(status, code):
    respx.post(BASE + "/api/torrent/search").mock(return_value=httpx.Response(status))
    with pytest.raises(AppError) as error:
        client().search("movie", 1, 20, [], False)
    assert error.value.code == code
