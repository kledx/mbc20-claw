#!/usr/bin/env python3
"""Safe MBC-20 mint post scheduler for Moltbook.

Compliant defaults:
- New agent (<24h): minimum 120 minutes between posts
- Established agent: minimum 30 minutes between posts
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

API_BASE = "https://www.moltbook.com/api/v1"
CREDENTIALS_PATH = Path.home() / ".config" / "moltbook" / "credentials.json"
MBC_LINK = "mbc20.xyz"


def load_api_key(path: Path) -> str:
    if not path.exists():
        raise RuntimeError(f"credentials not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    api_key = data.get("api_key", "").strip()
    if not api_key:
        raise RuntimeError("api_key missing in credentials file")
    return api_key


def parse_iso8601(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def api_request(
    method: str, endpoint: str, api_key: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    url = f"{API_BASE}{endpoint}"
    headers = {"Authorization": f"Bearer {api_key}"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"success": False, "error": raw}
        return exc.code, parsed


def get_me(api_key: str) -> dict[str, Any]:
    code, data = api_request("GET", "/agents/me", api_key)
    if code != 200 or not data.get("success"):
        raise RuntimeError(f"/agents/me failed ({code}): {data}")
    return data["agent"]


def platform_min_interval_minutes(created_at: str) -> int:
    age = datetime.now(timezone.utc) - parse_iso8601(created_at).astimezone(timezone.utc)
    return 120 if age.total_seconds() < 24 * 3600 else 30


def build_nonce() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def mint_content(tick: str, amt: str, add_nonce: bool = True) -> str:
    payload = {"p": "mbc-20", "op": "mint", "tick": tick, "amt": amt}
    base = f"{json.dumps(payload, separators=(',', ':'))} {MBC_LINK}"
    if not add_nonce:
        return base
    return f"{base}\n\nnonce:{build_nonce()}"


def submit_verification_if_needed(api_key: str, post_resp: dict[str, Any]) -> bool:
    if not post_resp.get("verification_required"):
        return True

    verify = post_resp.get("verification", {})
    code = verify.get("code")
    challenge = verify.get("challenge")
    if not code:
        print("verification required but code missing; cannot continue")
        return False

    print("\nVerification required by Moltbook.")
    if challenge:
        print(f"Challenge: {challenge}")
    answer = input("Enter verification answer (example 525.00): ").strip()
    if not answer:
        print("empty answer, stop")
        return False

    status, result = api_request(
        "POST",
        "/verify",
        api_key,
        {"verification_code": code, "answer": answer},
    )
    if 200 <= status < 300 and result.get("success"):
        print(f"verified: {result.get('message', 'ok')}")
        return True
    print(f"verify failed ({status}): {result}")
    return False


def post_once(
    api_key: str, submolt: str, title: str, tick: str, amt: str, add_nonce: bool
) -> bool:
    status, data = api_request(
        "POST",
        "/posts",
        api_key,
        {
            "submolt": submolt,
            "title": title,
            "content": mint_content(tick, amt, add_nonce=add_nonce),
        },
    )

    if status == 429:
        retry_m = data.get("retry_after_minutes")
        retry_s = data.get("retry_after_seconds")
        wait_s = int(retry_s or 0)
        if retry_m:
            wait_s = max(wait_s, int(retry_m) * 60)
        if wait_s <= 0:
            wait_s = 1800
        print(f"rate limited; sleeping {wait_s} seconds")
        time.sleep(wait_s)
        return False

    if status < 200 or status >= 300 or not data.get("success"):
        print(f"post failed ({status}): {data}")
        return False

    post = data.get("post", {})
    print(f"created post: {post.get('id')} https://www.moltbook.com{post.get('url', '')}")

    if not submit_verification_if_needed(api_key, data):
        return False

    return True


def run_scheduler(args: argparse.Namespace) -> int:
    try:
        api_key = load_api_key(Path(args.credentials))
        me = get_me(api_key)
    except Exception as exc:
        print(str(exc))
        return 1

    if not me.get("is_claimed"):
        print("agent is not claimed yet; cannot post")
        return 1

    min_interval = platform_min_interval_minutes(me["created_at"])
    chosen = int(args.interval_minutes)
    interval = max(chosen, min_interval)
    if chosen < min_interval:
        print(f"requested {chosen}m is too fast; enforced {interval}m to match platform rules")
    else:
        print(f"using interval {interval}m")

    sent = 0
    count = args.count
    title = args.title.replace("{tick}", args.tick)

    while True:
        ok = post_once(
            api_key=api_key,
            submolt=args.submolt,
            title=title,
            tick=args.tick,
            amt=str(args.amt),
            add_nonce=not args.no_nonce,
        )
        if ok:
            sent += 1
            print(f"posted count={sent}")

        if count > 0 and sent >= count:
            print("done")
            return 0

        sleep_seconds = interval * 60
        print(f"sleeping {sleep_seconds} seconds...")
        time.sleep(sleep_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe Moltbook mint scheduler")
    parser.add_argument("--tick", required=True, help="ticker, e.g. CLAW")
    parser.add_argument("--amt", required=True, type=int, help="mint amount, e.g. 100")
    parser.add_argument("--submolt", default="general", help="target submolt")
    parser.add_argument("--title", default="mint ${tick}", help="post title; supports {tick}")
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=30,
        help="desired interval; script enforces platform minimum",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="how many successful posts to send (0 = run forever)",
    )
    parser.add_argument(
        "--credentials",
        default=str(CREDENTIALS_PATH),
        help="path to moltbook credentials json",
    )
    parser.add_argument(
        "--no-nonce",
        action="store_true",
        help="disable default nonce suffix in content (not recommended)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.amt <= 0:
        print("amt must be > 0")
        return 1
    if args.interval_minutes <= 0:
        print("interval-minutes must be > 0")
        return 1
    if args.count < 0:
        print("count must be >= 0")
        return 1
    return run_scheduler(args)


if __name__ == "__main__":
    sys.exit(main())
