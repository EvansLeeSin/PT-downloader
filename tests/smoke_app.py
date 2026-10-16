"""Local-only browser fixture. Never use this module in a real deployment.

Run: .venv/bin/uvicorn tests.smoke_app:app --host 127.0.0.1 --port 18088
No real site or downloader traffic is performed.
"""

import tempfile
from pathlib import Path

from app import main, service
from app.models import SearchResult, Settings
from app.torrent import identify_torrent
from tests.conftest import PASSWORD, bencode

CONTENT = bencode(
    {b"info": {b"name": b"smoke-test", b"length": 1, b"piece length": 16384, b"pieces": b"x" * 20}}
)
HASH = next(iter(identify_torrent(CONTENT).hashes))


class SmokeSite:
    def __init__(self, config: Settings):
        self.config = config

    def search(self, keyword, page, page_size, categories, free_only, mode="normal"):
        from app.clients.mteam import MTeamClient

        item = MTeamClient(self.config)._torrent(
            {
                "id": "123456",
                "name": "成人分区测试资源 1080p" if mode == "adult" else "浏览器测试资源 1080p",
                "smallDescr": "仅用于测试，不会访问真实站点",
                "size": "1073741824",
                "status": {"seeders": 12, "discount": "FREE"},
            }
        )
        return SearchResult(
            items=[item], page=page, page_size=page_size, has_more=page == 1, total=2
        )

    def test(self):
        return {"connected": True, "username": "smoke"}

    def download(self, resource_id):
        return CONTENT


class SmokeQb:
    tasks = {}

    def __init__(self, config):
        pass

    def connect(self):
        pass

    def close(self):
        pass

    def test(self):
        return {
            "connected": True,
            "version": "v5-test",
            "web_api_version": "2-test",
            "default_save_path": "/downloads",
            "category_paths": [],
        }

    def find(self, hashes):
        return self.tasks.get(HASH) if HASH in hashes else None

    def add(self, content, save_path, paused):
        self.tasks[HASH] = {
            "hash": HASH,
            "name": "smoke-test",
            "save_path": save_path,
            "state": "stoppedDL" if paused else "downloading",
            "progress": 0.5,
        }
        return "Ok."


main.MTeamClient = service.MTeamClient = SmokeSite
main.QbClient = service.QbClient = SmokeQb
app = main.create_app(
    data_dir=Path(tempfile.mkdtemp(prefix="mteam-smoke-")), admin_password=PASSWORD
)
