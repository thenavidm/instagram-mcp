"""`instagram-mcp call` and `tools`: every tool as a command a scheduler can run.

Driven through the real server over a scripted transport, so a refusal, an
exit code and the printed JSON are what a script would see. Nothing here
touches the network.
"""

from __future__ import annotations

import json
import logging

import httpx2
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from instagram_mcp import cli
from instagram_mcp.__main__ import main
from instagram_mcp.config import FACEBOOK_HOST, INSTAGRAM_HOST, Account, ConfigError
from instagram_mcp.graph import GraphClient, GraphError
from instagram_mcp.runtime import Runtime
from instagram_mcp.safety import ConfirmationRequired, RateLimited, WriteDenied
from instagram_mcp.store import Store
from instagram_mcp.tools import register_all
from instagram_mcp.tools.own import profile_fields
from instagram_mcp.unofficial import Unofficial, UnofficialUnavailable

MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x01" * 2000


def meta(seen: list[httpx2.Request]):
    """Graph and rupload, answering the way the publishing guide's examples do."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        path = request.url.path
        if request.url.host == "rupload.facebook.com":
            return httpx2.Response(200, json={"success": True, "message": "Upload successful."})
        if request.method == "POST" and path.endswith("/media"):
            return httpx2.Response(
                200, json={"id": "c1", "uri": "https://rupload.facebook.com/ig-api-upload/v26.0/c1"}
            )
        if path.endswith("/media_publish"):
            return httpx2.Response(200, json={"id": "m1"})
        if "status_code" in request.url.params.get("fields", ""):
            return httpx2.Response(200, json={"id": "c1", "status_code": "FINISHED"})
        if path.endswith("/m1"):
            return httpx2.Response(200, json={"id": "m1", "permalink": "https://instagram.com/p/x"})
        if path.endswith("/content_publishing_limit"):
            return httpx2.Response(
                200, json={"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]}
            )
        return httpx2.Response(200, json={})

    return handler


@pytest.fixture
def seen() -> list[httpx2.Request]:
    return []


@pytest.fixture
def scripted(settings, seen, monkeypatch):
    """Make `call` build its server over the scripted transport."""
    settings.accounts.append(Account(name="page", user_id="9", token="fbtok", host=FACEBOOK_HOST))

    def build(s):
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(meta(seen)))
        runtime = Runtime(
            settings=s,
            graph=GraphClient(s, client=client),
            store=Store(s.db_path),
            unofficial=Unofficial(s),
        )
        server = MCPServer(name="test", version="0")
        register_all(server, runtime)
        return server, runtime

    monkeypatch.setattr(cli, "build_server", build)
    return settings


async def run(settings, tool, *tokens, **options):
    defaults = {"args_json": None, "account": None, "confirm": False, "agent": True}
    return await cli.call_tool(settings, tool, list(tokens), **{**defaults, **options})


async def test_publishes_a_reel_from_a_file_with_confirm(scripted, seen, tmp_path, capsys):
    video = tmp_path / "reel.mp4"
    video.write_bytes(MP4)
    code = await run(
        scripted, "publish-reel", "--file", str(video), "--caption", "Launch",
        account="page", confirm=True,
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    assert json.loads(out.out)["id"] == "m1"
    assert any(r.url.host == "rupload.facebook.com" for r in seen)


async def test_refuses_without_confirm_and_says_how(scripted, seen, tmp_path, capsys):
    video = tmp_path / "reel.mp4"
    video.write_bytes(MP4)
    code = await run(scripted, "publish_reel", "--file", str(video), account="page")
    error = json.loads(capsys.readouterr().err)
    assert code == 2
    assert error["code"] == "refused"
    assert error["error"].startswith("publish_reel publishes immediately and cannot be undone.")
    assert "--confirm" in error["error"]
    assert seen == []


async def test_confirm_inside_the_arguments_does_not_count(scripted, seen, capsys):
    code = await run(scripted, "publish_reel", args_json='{"video_url": "https://x/a.mp4", '
                     '"confirm": true}', account="page")
    assert code == 2
    assert "--confirm" in json.loads(capsys.readouterr().err)["error"]
    assert seen == []


async def test_a_read_ignores_confirm_so_a_scheduler_can_always_pass_it(scripted, capsys):
    code = await run(scripted, "get_publishing_limit", account="page", confirm=True)
    assert code == 0
    assert json.loads(capsys.readouterr().out)["data"][0]["quota_usage"] == 3


async def test_args_json_and_flags_merge_with_flags_winning(scripted, seen, capsys):
    code = await run(
        scripted, "publish_reel", "--caption", "from the flag",
        args_json='{"video_url": "https://example.com/a.mp4", "caption": "from json"}',
        account="page", confirm=True,
    )
    assert code == 0, capsys.readouterr().err
    created = dict(httpx2.QueryParams(seen[0].content.decode()))
    assert created["caption"] == "from the flag"
    assert created["video_url"] == "https://example.com/a.mp4"


async def test_an_unknown_account_is_not_found(scripted, capsys):
    assert await run(scripted, "get_publishing_limit", account="nobody") == 3
    assert json.loads(capsys.readouterr().err)["code"] == "not_found"


async def test_nothing_configured_exits_10(scripted, capsys):
    scripted.accounts.clear()
    assert await run(scripted, "get_publishing_limit") == 10


async def test_read_only_says_why_a_write_is_missing(scripted, capsys):
    scripted.read_only = True
    assert await run(scripted, "publish_reel", confirm=True) == 2
    assert "IG_READ_ONLY" in json.loads(capsys.readouterr().err)["error"]


async def test_usage_errors_exit_2_before_any_call(scripted, seen, capsys):
    assert await run(scripted, "no_such_tool") == 2
    assert await run(scripted, "publish_reel", "--nope", "1") == 2
    assert await run(scripted, "get_media_by_id") == 2  # media_id is required
    assert await run(scripted, "list_accounts", account="page") == 2  # takes no account
    assert await run(scripted, "get_media", "--limit", "many", account="page") == 2
    assert seen == []
    errors = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert errors[2]["error"] == "media_id: Field required"


def test_flags_are_read_against_the_schema():
    schema = {
        "properties": {
            "limit": {"type": "integer"},
            "share_to_feed": {"type": "boolean"},
            "items": {"anyOf": [{"type": "array"}, {"type": "null"}]},
            "caption": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        }
    }
    parsed = cli.parse_flags(
        "t",
        ["--limit", "5", "--share-to-feed", "false", "--items", "a", "--items=b",
         "--caption=hi --there"],
        schema,
    )
    assert parsed == {"limit": 5, "share_to_feed": False, "items": ["a", "b"],
                      "caption": "hi --there"}
    assert cli.parse_flags("t", ["--share-to-feed"], schema) == {"share_to_feed": True}
    assert cli.parse_flags("t", ["--items", '["a", "b"]'], schema) == {"items": ["a", "b"]}


def test_exit_codes_follow_the_house_contract():
    class Model(BaseModel):
        x: int

    try:
        Model(x="no")  # type: ignore[arg-type]
    except ValidationError as exc:
        invalid = exc

    def wrapped(cause: BaseException) -> ToolError:
        error = ToolError("Error executing tool t: x")
        error.__cause__ = cause
        return error

    cases = {
        2: [ConfirmationRequired("x"), WriteDenied("x"), GraphError("x", code=100), invalid],
        3: [ConfigError("No connected account matches \"a\"."), GraphError("x", status=404)],
        4: [GraphError("x", code=190), GraphError("x", code=200), GraphError("x", status=401)],
        5: [GraphError("x", status=500), GraphError("could not reach")],
        7: [GraphError("x", code=4), GraphError("x", code=32), RateLimited("x")],
        10: [ConfigError("No Instagram account configured."), UnofficialUnavailable("x")],
        1: [RuntimeError("bug")],
    }
    for code, errors in cases.items():
        for error in errors:
            assert cli.exit_code(wrapped(error))[0] == code, error
            assert cli.exit_code(error)[0] == code, error


def test_the_cli_entry_lists_every_tool(settings, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("IG_MCP_DATA_DIR", str(tmp_path))
    for name in ("IG_ACCESS_TOKEN", "IG_ACCOUNTS_FILE", "IG_READ_ONLY"):
        monkeypatch.delenv(name, raising=False)
    assert main(["tools", "--agent"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 47
    reel = next(t for t in listed["tools"] if t["tool"] == "publish_reel")
    assert reel["requires_confirm"] is True and "file" in reel["args"]
    assert main(["call", "get_publishing_limit", "--agent"]) == 10
    with pytest.raises(SystemExit) as exited:
        main(["doctor", "--bogus"])
    assert exited.value.code == 2


def test_profile_fields_fit_the_login_path():
    """account_type exists only on Instagram Login; asking Facebook Login for it fails."""
    fb = Account(name="p", user_id="9", token="t", host=FACEBOOK_HOST)
    ig = Account(name="i", user_id="1", token="t", host=INSTAGRAM_HOST)
    assert "account_type" not in profile_fields(fb)
    assert "account_type" in profile_fields(ig)
    assert "followers_count" in profile_fields(fb) and "media_count" in profile_fields(fb)


async def test_list_accounts_on_facebook_login_never_asks_for_account_type(settings, make_server,
                                                                          recorder):
    settings.accounts[:] = [Account(name="page", user_id="9", token="fb", host=FACEBOOK_HOST)]
    server, runtime = make_server({"/9": {"id": "9", "username": "page", "followers_count": 5}})
    result = await server.call_tool("list_accounts", {})
    row = result.structured_content["accounts"][0]
    assert row["status"] == "ok" and row["type"] == "BUSINESS_OR_CREATOR"
    assert all("account_type" not in r.url.params.get("fields", "") for r in recorder)
    await runtime.aclose()


async def test_no_token_reaches_the_log_even_at_debug(caplog):
    """httpx2 logs each URL, and a Graph URL carries the token in its query string."""
    caplog.set_level(logging.DEBUG, logger="httpx2")
    from instagram_mcp.config import Settings

    account = Account(name="a", user_id="1", token="SECRET-TOKEN-123", host=FACEBOOK_HOST)
    client = GraphClient(
        Settings(accounts=[account]),
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(
            lambda r: httpx2.Response(200, json={})
        )),
    )
    await client.call(account, "/1", {"fields": "id"})
    await client.aclose()
    assert "HTTP Request" in caplog.text
    assert "SECRET-TOKEN-123" not in caplog.text
