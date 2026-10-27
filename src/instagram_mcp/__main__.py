"""The command line. One entry point, eight subcommands.

`call` runs any tool as a command, for a script or a scheduler, and `tools`
lists them; both live in cli.py.

`doctor` is the one to care about. Instagram integrations fail for about six
reasons and all of them look identical from inside an MCP client, which reports
"the tool errored" and nothing else. doctor names the actual reason.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from typing import Any

import httpx2

from . import __version__
from .config import (
    DEFAULT_GRAPH_VERSION,
    FACEBOOK_HOST,
    INSTAGRAM_HOST,
    ConfigError,
    load_settings,
)
from .graph import GraphClient, GraphError
from .server import build_server
from .tools.own import account_type, profile_fields


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instagram-mcp",
        description="Instagram for AI agents, over the Model Context Protocol.",
    )
    parser.add_argument("--version", action="version", version=f"instagram-mcp {__version__}")

    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run the MCP server. This is the default.")
    _add_run_flags(run)
    _add_run_flags(parser)  # so `instagram-mcp --unofficial` works with no subcommand

    login = sub.add_parser(
        "login", help="Log in once for the unofficial tier and save a session file."
    )
    login.add_argument("--username", help="Instagram username. Prompted for if omitted.")
    login.add_argument("--code", help="Six-digit two-factor code, if the account has 2FA on.")

    call = sub.add_parser(
        "call",
        help="Run one tool and print its JSON result. Tool arguments go as --name value.",
        allow_abbrev=False,
        description=(
            "Run one tool, through the same server and the same confirmation as MCP. "
            "Arguments go as --name value flags, read against the tool's schema, or as a "
            "JSON object with --args. Exit codes: 0 ok, 1 unexpected, 2 usage or a refused "
            "write, 3 not found, 4 auth, 5 API, 7 rate limited, 10 nothing configured."
        ),
    )
    call.add_argument("tool", help="The tool's name, with dashes or underscores.")
    call.add_argument("--account", help="Which connected account acts, by name.")
    call.add_argument("--args", dest="args_json", help="The tool's arguments as a JSON object.")
    call.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm a publish, delete or DM. Only when that exact action was asked for.",
    )
    call.add_argument(
        "--agent", action="store_true", help="Compact JSON on one line. Never confirms."
    )
    call.add_argument("--unofficial", action="store_true", help="Enable the private-API tier.")

    tools = sub.add_parser("tools", help="List every tool: risk, confirmation, arguments.")
    tools.add_argument("--agent", action="store_true", help="JSON instead of a table.")
    tools.add_argument("--unofficial", action="store_true", help="Include the unofficial tier.")

    sub.add_parser("doctor", help="Check the configuration and say what is broken.")
    sub.add_parser("refresh", help="Extend Instagram Login tokens by another 60 days.")

    token = sub.add_parser("token", help="Mint a long-lived token and find your account ids.")
    token.add_argument("--app-id", required=True, help="Meta app id.")
    token.add_argument("--app-secret", required=True, help="Meta app secret.")
    token.add_argument(
        "--short-token", required=True, help="Short-lived user token from Graph API Explorer."
    )
    token.add_argument(
        "--path",
        choices=["facebook", "instagram"],
        default="facebook",
        help="facebook unlocks discover_account and hashtags. instagram is your own accounts only.",
    )
    return parser


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--unofficial",
        action="store_true",
        help="Enable the private-API tier. Same as IG_UNOFFICIAL=1. Read the README first.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve over streamable HTTP instead of stdio, for claude.ai and other remote clients.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address.")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port.")


# ── run ───────────────────────────────────────────────────────────────────────


def _run(args: argparse.Namespace) -> int:
    settings = load_settings(unofficial=args.unofficial)

    if settings.unofficial:
        # Said once, on stderr, where it does not corrupt the stdio protocol.
        print(
            "instagram-mcp: unofficial tier ON. This is against Instagram's terms and can get "
            "an account restricted. Use a secondary account.",
            file=sys.stderr,
        )
    if settings.read_only:
        print("instagram-mcp: IG_READ_ONLY set, write tools are not registered.", file=sys.stderr)

    server, runtime = build_server(settings)
    try:
        if args.http:
            asyncio.run(server.run_streamable_http_async(host=args.host, port=args.port))
        else:
            asyncio.run(server.run_stdio_async())
    except KeyboardInterrupt:
        pass
    finally:
        asyncio.run(runtime.aclose())
    return 0


# ── login ─────────────────────────────────────────────────────────────────────


def _login(args: argparse.Namespace) -> int:
    from .unofficial import login_interactive

    settings = load_settings()
    username = args.username or input("Instagram username: ").strip()
    if not username:
        print("A username is needed.", file=sys.stderr)
        return 2

    # getpass, never an argument. A password passed on the command line lands in
    # the shell history and in the process table.
    password = getpass.getpass(f"Password for {username} (not stored, not echoed): ")

    try:
        login_interactive(settings.session_path, username, password, args.code)
    except RuntimeError as exc:
        print(f"Login failed. {exc}", file=sys.stderr)
        return 1

    print(f"Logged in as {username}.")
    print(f"Session saved to {settings.session_path} with owner-only permissions.")
    print("Start the server with --unofficial to use it.")
    return 0


# ── doctor ────────────────────────────────────────────────────────────────────


async def _doctor_async() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration: FAIL\n  {exc}")
        return 1

    print(f"instagram-mcp {__version__}")
    print(f"Graph version: {settings.graph_version} (newest known: {DEFAULT_GRAPH_VERSION})")
    print(f"Data directory: {settings.data_dir}")
    print()

    if not settings.accounts:
        print("Accounts: NONE")
        print("  Set IG_ACCESS_TOKEN and IG_USER_ID, or IG_ACCOUNTS_FILE.")
        print("  Run `instagram-mcp token --help` to mint one.")
        return 1

    graph = GraphClient(settings)
    failures = 0
    try:
        for account in settings.accounts:
            label = f"{account.name} ({account.tier.value})"
            try:
                profile = await graph.call(
                    account, f"/{account.user_id}", {"fields": profile_fields(account)}
                )
                kind = account_type(account, profile) or "unknown type"
                print(
                    f"Token {label}: OK, @{profile.get('username')} ({kind}), "
                    f"{profile.get('followers_count')} followers, "
                    f"{profile.get('media_count')} posts"
                )
            except GraphError as exc:
                failures += 1
                print(f"Token {label}: FAIL\n  {exc}")
                continue

            await _check_insights(graph, account, label)
            if account.host == FACEBOOK_HOST:
                await _check_discovery(graph, account, label)

        if not settings.has_facebook_login():
            print()
            print("Tier 2 (discover_account, hashtags): UNAVAILABLE")
            print("  No Facebook Login account configured. This is the tier that reads public")
            print("  data about other accounts. Run `instagram-mcp token --path facebook`.")

        print()
        state = "session present" if settings.session_path.exists() else "no session"
        print(f"Unofficial tier: {state}")
        try:
            import instagrapi  # noqa: F401

            print("  instagrapi: installed")
        except ImportError:
            print('  instagrapi: not installed. `uv tool install "instagram-mcp[unofficial]"`')
    finally:
        await graph.aclose()

    return 1 if failures else 0


async def _check_insights(graph: GraphClient, account: Any, label: str) -> None:
    """Which insight metrics this account still answers.

    Meta retires these between versions without a loud announcement, and a
    retired metric surfaces as a generic 400 inside a tool call. Naming the
    working set here turns a mystery into a one-argument fix.
    """
    from .tools.own import DEFAULT_ACCOUNT_METRICS

    working, dead = [], []
    for metric in DEFAULT_ACCOUNT_METRICS.split(","):
        try:
            await graph.call(
                account, f"/{account.user_id}/insights", {"metric": metric, "period": "week"}
            )
            working.append(metric)
        except GraphError:
            dead.append(metric)

    print(f"  Account insights: {', '.join(working) or 'none working'}")
    if dead:
        print(f"    Not available: {', '.join(dead)}")
        print("    Pass metrics= to get_account_insights to override the default.")


async def _check_discovery(graph: GraphClient, account: Any, label: str) -> None:
    """The load-bearing check. Does business_discovery still answer?

    Everything Tier 2 promises rests on this one edge, and Meta has narrowed
    Instagram Graph access repeatedly. Better to learn it here than from a tool
    call three weeks in.
    """
    probe = "instagram"  # a Business account that will not disappear
    try:
        body = await graph.call(
            account,
            f"/{account.user_id}",
            {"fields": f"business_discovery.username({probe}){{followers_count}}"},
        )
    except GraphError as exc:
        print(f"  business_discovery: FAIL\n    {exc}")
        print("    Tier 2 is unavailable on this token. discover_account will not work.")
        return

    found = (body.get("business_discovery") or {}).get("followers_count")
    if found:
        print(f"  business_discovery: OK (probe returned {found} followers)")
    else:
        print("  business_discovery: answered but returned nothing. Check the token's scopes.")


# ── refresh ───────────────────────────────────────────────────────────────────


async def _refresh_async() -> int:
    settings = load_settings()
    if not settings.accounts:
        print("No accounts configured.", file=sys.stderr)
        return 1

    graph = GraphClient(settings)
    try:
        for account in settings.accounts:
            if account.host != INSTAGRAM_HOST:
                print(f"{account.name}: skipped, Facebook Page tokens do not expire.")
                continue
            try:
                body = await graph.refresh_token(account)
            except GraphError as exc:
                print(f"{account.name}: FAIL. {exc}")
                continue
            days = int(body.get("expires_in", 0)) // 86400
            print(f"{account.name}: new token valid {days} days.")
            print(f"  {body.get('access_token')}")
        print()
        print("These are new tokens. This command cannot write them back into your config,")
        print("so update IG_ACCESS_TOKEN or your IG_ACCOUNTS_FILE with them.")
    finally:
        await graph.aclose()
    return 0


# ── token ─────────────────────────────────────────────────────────────────────


async def _token_async(args: argparse.Namespace) -> int:
    """Short-lived user token to a long-lived one, then find the account ids.

    The single most user-hostile part of Instagram, and the reason most people
    give up before the first successful call. Worth automating for that alone.
    """
    host = FACEBOOK_HOST if args.path == "facebook" else INSTAGRAM_HOST

    async with httpx2.AsyncClient(timeout=30.0) as client:
        if args.path == "facebook":
            exchange = await client.get(
                f"https://{host}/{DEFAULT_GRAPH_VERSION}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": args.app_id,
                    "client_secret": args.app_secret,
                    "fb_exchange_token": args.short_token,
                },
            )
        else:
            exchange = await client.get(
                f"https://{host}/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": args.app_secret,
                    "access_token": args.short_token,
                },
            )

        body = exchange.json()
        if exchange.status_code >= 400:
            print(f"Exchange failed: {json.dumps(body, indent=2)}", file=sys.stderr)
            return 1

        long_lived = body["access_token"]
        print(f"Long-lived token:\n  {long_lived}\n")

        if args.path != "facebook":
            print("Set these:")
            print(f"  IG_ACCESS_TOKEN={long_lived}")
            print(f"  IG_HOST={INSTAGRAM_HOST}")
            print("  IG_USER_ID=me")
            return 0

        pages = await client.get(
            f"https://{host}/{DEFAULT_GRAPH_VERSION}/me/accounts",
            params={
                "fields": "name,access_token,instagram_business_account{id,username}",
                "access_token": long_lived,
            },
        )
        rows = (pages.json() or {}).get("data") or []
        linked = [r for r in rows if r.get("instagram_business_account")]

        if not linked:
            print("No Instagram Business account is linked to any Page on this login.")
            print("Link one in Meta Business Suite, then run this again.")
            return 1

        print("Add these to your IG_ACCOUNTS_FILE:\n")
        print(
            json.dumps(
                [
                    {
                        "account_name": r["instagram_business_account"].get("username")
                        or r.get("name"),
                        "user_id": r["instagram_business_account"]["id"],
                        # The Page token, not the user token. Page tokens do not
                        # expire, which is why this path never needs refreshing.
                        "access_token": r.get("access_token"),
                        "host": FACEBOOK_HOST,
                    }
                    for r in linked
                ],
                indent=2,
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args, extra = parser.parse_known_args(argv)
    command = args.command or "run"
    # Only `call` takes flags it cannot know in advance: the tool's own arguments.
    if extra and command != "call":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")

    try:
        if command == "call":
            from .cli import call_tool

            try:
                settings = load_settings(unofficial=args.unofficial)
            except ConfigError as exc:
                from .cli import _fail

                return _fail(exc, args.agent)
            return asyncio.run(
                call_tool(
                    settings,
                    args.tool,
                    extra,
                    args_json=args.args_json,
                    account=args.account,
                    confirm=args.confirm,
                    agent=args.agent,
                )
            )
        if command == "tools":
            from .cli import list_tools

            settings = load_settings(unofficial=args.unofficial)
            return asyncio.run(list_tools(settings, agent=args.agent))
        if command == "run":
            return _run(args)
        if command == "login":
            return _login(args)
        if command == "doctor":
            return asyncio.run(_doctor_async())
        if command == "refresh":
            return asyncio.run(_refresh_async())
        if command == "token":
            return asyncio.run(_token_async(args))
    except ConfigError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130

    print(f"Unknown command {command}.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
