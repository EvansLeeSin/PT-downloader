import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import pytest
import requests
from qbittorrentapi.definitions import APINames

from app.clients.qbittorrent import QbClient, SingleSubmitClient
from app.errors import AppError
from app.models import Settings


@pytest.mark.parametrize("namespace", ["torrents", APINames.Torrents])
def test_sdk_never_retries_add_even_after_transport_error(namespace):
    client = SingleSubmitClient(host="http://qb.test:8080", HTTPADAPTER_ARGS={"max_retries": 0})
    client._request = MagicMock(side_effect=requests.exceptions.ReadTimeout("lost response"))
    with pytest.raises(requests.exceptions.ReadTimeout):
        client._request_manager("post", namespace, "add", version_introduced="", version_removed="")
    client._request.assert_called_once()


@pytest.fixture
def qb_server(torrent_hash):
    state = {"added": False, "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, json_body=False, cookie=False):
            data = json.dumps(value).encode() if json_body else value.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if json_body else "text/plain")
            self.send_header("Content-Length", str(len(data)))
            if cookie:
                self.send_header("Set-Cookie", "SID=fake-session; HttpOnly; path=/")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/v2/app/version":
                self.reply("v5.1.4")
            elif path == "/api/v2/app/webapiVersion":
                self.reply("2.11.3")
            elif path == "/api/v2/app/preferences":
                self.reply({"save_path": "/downloads"}, json_body=True)
            elif path == "/api/v2/torrents/categories":
                self.reply(
                    {"movies": {"name": "movies", "savePath": "/downloads/movies"}}, json_body=True
                )
            elif path == "/api/v2/torrents/info":
                self.reply(
                    [
                        {
                            "hash": torrent_hash,
                            "save_path": "/downloads/movies",
                            "name": "sample",
                            "state": "downloading",
                            "progress": 0.25,
                            "dlspeed": 1048576,
                        }
                    ]
                    if state["added"]
                    else [],
                    json_body=True,
                )
            else:
                self.send_error(404)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            path = urlsplit(self.path).path
            state["calls"].append((path, body, dict(self.headers)))
            if path == "/api/v2/auth/login":
                self.reply("Ok.", cookie=True)
            elif path == "/api/v2/torrents/info":
                self.do_GET()
            elif path == "/api/v2/torrents/add":
                state["added"] = True
                self.reply("Ok.")
            else:
                self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_actual_sdk_login_add_savepath_and_query(qb_server, torrent_bytes, torrent_hash):
    url, state = qb_server
    client = QbClient(Settings(qb_url=url, qb_username="admin", qb_password="secret"))
    try:
        result = client.test()
        assert result["version"] == "v5.1.4"
        assert result["default_save_path"] == "/downloads"
        assert result["category_paths"][0]["path"] == "/downloads/movies"
        assert client.find({torrent_hash}) is None
        client.add(torrent_bytes, "/downloads/movies", True)
        assert client.find({torrent_hash})["save_path"] == "/downloads/movies"
        assert client.find_many({torrent_hash})[torrent_hash]["progress"] == 0.25
    finally:
        client.close()
    calls = [call for call in state["calls"] if call[0] == "/api/v2/torrents/add"]
    assert len(calls) == 1
    body = calls[0][1]
    assert b"/downloads/movies" in body
    assert b'name="autotmm"\r\n\r\nfalse' in body.lower()
    assert b'name="stopped"\r\n\r\ntrue' in body.lower()
    assert torrent_bytes in body
    assert "SID=fake-session" in calls[0][2]["Cookie"]


def test_batch_hash_lookup_indexes_aliases_and_ignores_unrequested_torrents():
    client = QbClient(Settings(qb_url="http://qb.test"))
    torrent = {"hash": "A" * 40, "infohash_v2": "B" * 64, "progress": 0.5}
    client.client.torrents_info = MagicMock(return_value=[torrent, {"hash": "c" * 40}])
    try:
        assert client.find_many(set()) == {}
        client.client.torrents_info.assert_not_called()
        found = client.find_many({"a" * 40, "b" * 64, "d" * 40})
        assert found == {"a" * 40: torrent, "b" * 64: torrent}
        client.client.torrents_info.assert_called_once()
    finally:
        client.close()


def test_batch_lookup_does_not_leak_transport_details():
    client = QbClient(Settings(qb_url="http://qb.test"))
    client.client.torrents_info = MagicMock(side_effect=RuntimeError("private-password"))
    try:
        with pytest.raises(AppError) as error:
            client.find_many({"a" * 40})
        assert error.value.code == "DOWNLOADER_QUERY_FAILED"
        assert "private-password" not in str(error.value)
    finally:
        client.close()
