#!/usr/bin/env python3
"""Moltbook binding + MBC-20 helper agent.

This follows https://www.moltbook.com/developers:
1) Bind your app key (moltdev_...)
2) Generate identity token with a bot API key
3) Verify identity token with your app key
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib import error, parse, request

CONFIG_PATH = Path(".moltbook-agent.json")
DEFAULT_API_BASE = "https://www.moltbook.com"
MBC_LINK = "mbc20.xyz"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def normalize_api_base(api_base: str) -> str:
    value = api_base.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise ValueError("api-base must start with http:// or https://")
    return value


def validate_app_key(app_key: str) -> str:
    value = app_key.strip()
    if not value.startswith("moltdev_"):
        raise ValueError("app key should start with moltdev_")
    return value


def validate_tick(tick: str) -> str:
    normalized = tick.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,10}", normalized):
        raise ValueError("tick must be 1-10 uppercase letters or digits")
    return normalized


def validate_amt(amt: str) -> str:
    raw = amt.strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError("amt must be a positive integer")
    return str(int(raw))


def post_json(url: str, headers: dict[str, str], payload: dict) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        return 0, f"network error: {exc.reason}"


def build_mint_post(tick: str, amt: str) -> str:
    payload = {"p": "mbc-20", "op": "mint", "tick": tick, "amt": amt}
    return f"{json.dumps(payload, separators=(',', ':'))}\n\n{MBC_LINK}"


def cmd_bind(args: argparse.Namespace) -> int:
    try:
        app_key = validate_app_key(args.app_key)
        api_base = normalize_api_base(args.api_base)
    except ValueError as exc:
        print(str(exc))
        return 1

    config = load_config()
    config["app_key"] = app_key
    config["api_base"] = api_base
    if args.bot_api_key:
        config["bot_api_key"] = args.bot_api_key.strip()
    save_config(config)

    print(f"bound Moltbook config in {CONFIG_PATH}")
    print(f"api_base={api_base}")
    print(f"app_key={app_key[:12]}...")
    return 0


def cmd_auth_url(args: argparse.Namespace) -> int:
    query = {
        "app": args.app_name.strip(),
        "endpoint": args.endpoint.strip(),
    }
    if args.header:
        query["header"] = args.header.strip()
    url = "https://moltbook.com/auth.md?" + parse.urlencode(query)
    print(url)
    return 0


def cmd_identity_token(args: argparse.Namespace) -> int:
    config = load_config()
    api_base = config.get("api_base", DEFAULT_API_BASE)
    try:
        api_base = normalize_api_base(api_base)
    except ValueError as exc:
        print(str(exc))
        return 1

    bot_api_key = args.bot_api_key or config.get("bot_api_key") or os.getenv("MOLTBOOK_API_KEY")
    if not bot_api_key:
        print("missing bot api key. set --bot-api-key, bind --bot-api-key, or MOLTBOOK_API_KEY")
        return 1

    status, body = post_json(
        f"{api_base}/api/v1/agents/me/identity-token",
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bot_api_key}",
        },
        {},
    )
    print(f"status={status}")
    print(body)
    return 0 if 200 <= status < 300 else 1


def cmd_verify_identity(args: argparse.Namespace) -> int:
    config = load_config()
    app_key = args.app_key or config.get("app_key") or os.getenv("MOLTBOOK_APP_KEY")
    api_base = config.get("api_base", DEFAULT_API_BASE)

    if not app_key:
        print("missing app key. run bind --app-key or pass --app-key")
        return 1
    try:
        app_key = validate_app_key(app_key)
        api_base = normalize_api_base(api_base)
    except ValueError as exc:
        print(str(exc))
        return 1

    status, body = post_json(
        f"{api_base}/api/v1/agents/verify-identity",
        {
            "Content-Type": "application/json",
            "X-Moltbook-App-Key": app_key,
        },
        {"token": args.token.strip()},
    )
    print(f"status={status}")
    print(body)
    return 0 if 200 <= status < 300 else 1


def cmd_mint(args: argparse.Namespace) -> int:
    try:
        tick = validate_tick(args.tick)
        amt = validate_amt(str(args.amt))
    except ValueError as exc:
        print(str(exc))
        return 1

    print(build_mint_post(tick, amt))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Moltbook + MBC-20 helper agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bind_parser = subparsers.add_parser("bind", help="bind Moltbook app config")
    bind_parser.add_argument("--app-key", required=True, help="your Moltbook app key (moltdev_...)")
    bind_parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="Moltbook API base URL")
    bind_parser.add_argument("--bot-api-key", help="optional bot API key for identity-token calls")
    bind_parser.set_defaults(func=cmd_bind)

    auth_url_parser = subparsers.add_parser("auth-url", help="generate hosted auth instructions URL")
    auth_url_parser.add_argument("--app-name", required=True, help="shown app name")
    auth_url_parser.add_argument("--endpoint", required=True, help="your API endpoint")
    auth_url_parser.add_argument("--header", help="custom header name (default X-Moltbook-Identity)")
    auth_url_parser.set_defaults(func=cmd_auth_url)

    token_parser = subparsers.add_parser(
        "identity-token", help="generate temporary identity token using bot API key"
    )
    token_parser.add_argument("--bot-api-key", help="bot API key; optional if bound/env exists")
    token_parser.set_defaults(func=cmd_identity_token)

    verify_parser = subparsers.add_parser("verify-identity", help="verify identity token with app key")
    verify_parser.add_argument("--token", required=True, help="identity token from bot")
    verify_parser.add_argument("--app-key", help="override bound app key")
    verify_parser.set_defaults(func=cmd_verify_identity)

    mint_parser = subparsers.add_parser("mint", help="generate MBC-20 mint post content")
    mint_parser.add_argument("--tick", required=True, help="token ticker, e.g. CLAW")
    mint_parser.add_argument("--amt", required=True, help="mint amount, e.g. 100")
    mint_parser.set_defaults(func=cmd_mint)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
