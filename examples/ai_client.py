"""Small API client for external automation. Does not contain any LLM integration.

export MTD_URL=http://127.0.0.1:8088
export MTD_TOKEN=mtd_...
python examples/ai_client.py search '流浪地球'
python examples/ai_client.py locations
python examples/ai_client.py add mteam:123456 --location default --key my-action-0001
python examples/ai_client.py status JOB_ID
"""

import argparse
import json
import os
import sys

import httpx


def main():
    parser = argparse.ArgumentParser(description="M-Team Downloader API client")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search")
    search.add_argument("keyword")
    search.add_argument("--page", type=int, default=1)
    search.add_argument("--free-only", action="store_true")
    commands.add_parser("locations")
    add = commands.add_parser("add")
    add.add_argument("torrent_id")
    add.add_argument("--location", default="default")
    add.add_argument("--paused", action="store_true")
    add.add_argument("--key", required=True, help="8–128 位幂等键；同一次逻辑操作重试必须复用")
    status = commands.add_parser("status")
    status.add_argument("job_id")
    status.add_argument("--local-only", action="store_true")
    args = parser.parse_args()
    base = os.environ.get("MTD_URL", "http://127.0.0.1:8088").rstrip("/") + "/api/v1"
    token = os.environ.get("MTD_TOKEN")
    if not token:
        parser.error("请在 MTD_TOKEN 环境变量中提供专用 API 令牌")
    try:
        with httpx.Client(
            base_url=base + "/",
            headers={"Authorization": "Bearer " + token},
            timeout=90,
            trust_env=False,
        ) as client:
            if args.command == "search":
                response = client.get(
                    "torrents/search",
                    params={
                        "keyword": args.keyword,
                        "page": args.page,
                        "free_only": args.free_only,
                    },
                )
            elif args.command == "locations":
                response = client.get("download-locations")
            elif args.command == "add":
                response = client.post(
                    "downloads",
                    headers={"Idempotency-Key": args.key},
                    json={
                        "torrent_id": args.torrent_id,
                        "location_id": args.location,
                        "start_paused": args.paused,
                    },
                )
            else:
                response = client.get(
                    "downloads/" + args.job_id, params={"include_live": not args.local_only}
                )
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))
            return 0 if response.is_success else 1
    except (httpx.HTTPError, ValueError):
        # Avoid dumping URLs/headers from arbitrary transport exceptions.
        print("调用失败。若为添加操作，请用相同 --key 和参数重试，勿生成新请求。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
