"""Publishing from a file on this machine, through the real MCP server.

Meta's resumable upload, as the content publishing guide describes it: a
container opened with upload_type=resumable on graph.facebook.com, the bytes
POSTed to rupload.facebook.com with `Authorization: OAuth`, `offset` and
`file_size` headers, then the usual wait and media_publish. Nothing here
touches the network.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import httpx2
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from instagram_mcp import __version__
from instagram_mcp.config import FACEBOOK_HOST, Account
from instagram_mcp.graph import GraphClient
from instagram_mcp.runtime import Runtime
from instagram_mcp.store import Store
from instagram_mcp.tools import register_all
from instagram_mcp.tools.publish import local_video
from instagram_mcp.unofficial import Unofficial

MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x01" * 4000
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x02" * 500
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x03" * 500


@pytest.fixture
def files(tmp_path) -> dict[str, Path]:
    out = {}
    for name, data in {"reel.mp4": MP4, "photo.jpg": JPEG, "photo.heic": HEIC}.items():
        path = tmp_path / name
        path.write_bytes(data)
        out[name] = path
    (tmp_path / "empty.mp4").write_bytes(b"")
    out["empty.mp4"] = tmp_path / "empty.mp4"
    return out


@pytest.fixture
def fb(settings):
    """A Facebook Login account, the only path Meta's resumable upload is documented for."""
    settings.accounts.append(Account(name="page", user_id="9", token="fbtok", host=FACEBOOK_HOST))
    return "page"


class FakeMeta:
    """Answers Graph and rupload the way the guide's examples do, and records every request."""

    def __init__(self, *, drop_uploads: int = 0, upload_reply: tuple[int, dict] | None = None):
        self.seen: list[httpx2.Request] = []
        self.containers = 0
        self.drop_uploads = drop_uploads
        self.upload_reply = upload_reply

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        path = request.url.path
        if request.url.host == "rupload.facebook.com":
            if self.drop_uploads:
                self.drop_uploads -= 1
                raise httpx2.ConnectError("connection reset", request=request)
            if self.upload_reply:
                status, body = self.upload_reply
                return httpx2.Response(status, json=body)
            return httpx2.Response(200, json={"success": True, "message": "Upload successful."})
        if request.method == "POST" and path.endswith("/media"):
            self.containers += 1
            cid = f"c{self.containers}"
            body = {"id": cid}
            if b"upload_type=resumable" in request.content:
                body["uri"] = f"https://rupload.facebook.com/ig-api-upload/v26.0/{cid}"
            return httpx2.Response(200, json=body)
        if path.endswith("/media_publish"):
            return httpx2.Response(200, json={"id": "m1"})
        if request.method == "GET" and "status_code" in request.url.params.get("fields", ""):
            container = path.rsplit("/", 1)[-1]
            return httpx2.Response(200, json={"id": container, "status_code": "FINISHED"})
        if path.endswith("/m1"):
            return httpx2.Response(200, json={"id": "m1", "permalink": "https://instagram.com/p/x"})
        return httpx2.Response(200, json={})

    def uploads(self) -> list[httpx2.Request]:
        return [r for r in self.seen if r.url.host == "rupload.facebook.com"]

    def created(self) -> list[dict[str, str]]:
        """The form fields of every container creation, in order."""
        out = []
        for r in self.seen:
            if r.method == "POST" and r.url.path.endswith("/media"):
                out.append(dict(httpx2.QueryParams(r.content.decode())))
        return out


@pytest.fixture
def build(settings):
    made: list[Runtime] = []

    def make(meta: FakeMeta) -> MCPServer:
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(meta))
        runtime = Runtime(
            settings=settings,
            graph=GraphClient(settings, client=client),
            store=Store(settings.db_path),
            unofficial=Unofficial(settings),
        )
        made.append(runtime)
        server = MCPServer(name="test", version="0")
        register_all(server, runtime)
        return server

    yield make


async def test_a_reel_from_a_file_goes_up_through_rupload(build, fb, files):
    meta = FakeMeta()
    server = build(meta)
    result = await server.call_tool(
        "publish_reel",
        {"file": str(files["reel.mp4"]), "caption": "Launch", "account": fb, "confirm": True},
    )
    assert result.structured_content["id"] == "m1"

    created = meta.created()
    assert created[0]["upload_type"] == "resumable"
    assert created[0]["media_type"] == "REELS"
    assert created[0]["caption"] == "Launch"
    assert "video_url" not in created[0]
    assert meta.seen[0].url.host == "graph.facebook.com"

    [upload] = meta.uploads()
    assert str(upload.url) == "https://rupload.facebook.com/ig-api-upload/v26.0/c1"
    assert upload.headers["authorization"] == "OAuth fbtok"
    assert upload.headers["offset"] == "0"
    assert upload.headers["file_size"] == str(len(MP4))
    assert upload.content == MP4

    publish = [r for r in meta.seen if r.url.path.endswith("/media_publish")]
    assert b"creation_id=c1" in publish[0].content


async def test_an_image_file_is_refused_with_the_reason_before_any_call(build, fb, files):
    meta = FakeMeta()
    server = build(meta)
    for name in ("photo.jpg", "photo.heic"):
        with pytest.raises(ToolError, match="image_url"):
            await server.call_tool(
                "post", {"file": str(files[name]), "account": fb, "confirm": True}
            )
    assert meta.seen == []


async def test_a_file_needs_the_facebook_login_path(build, files):
    """The default accounts here are Instagram Login, which Meta gives no rupload host."""
    meta = FakeMeta()
    server = build(meta)
    with pytest.raises(ToolError, match="Facebook Login"):
        await server.call_tool("publish_reel", {"file": str(files["reel.mp4"]), "confirm": True})
    assert meta.seen == []


async def test_publishing_from_a_file_still_needs_confirm(build, fb, files):
    meta = FakeMeta()
    server = build(meta)
    with pytest.raises(ToolError, match="confirm=true"):
        await server.call_tool("publish_reel", {"file": str(files["reel.mp4"]), "account": fb})
    assert meta.seen == []


async def test_a_dropped_upload_is_sent_again_from_the_first_byte(build, fb, files):
    meta = FakeMeta(drop_uploads=1)
    server = build(meta)
    await server.call_tool(
        "post", {"file": str(files["reel.mp4"]), "account": fb, "confirm": True}
    )
    uploads = meta.uploads()
    assert len(uploads) == 2
    assert [u.headers["offset"] for u in uploads] == ["0", "0"]
    assert uploads[1].content == MP4


async def test_a_rejected_upload_says_why_and_is_not_retried(build, fb, files):
    meta = FakeMeta(
        upload_reply=(
            400,
            {"debug_info": {"retriable": False, "type": "ProcessingFailedError",
                            "message": "unauthorized user request"}},
        )
    )
    server = build(meta)
    with pytest.raises(ToolError, match="ProcessingFailedError"):
        await server.call_tool(
            "publish_story", {"file": str(files["reel.mp4"]), "account": fb, "confirm": True}
        )
    assert len(meta.uploads()) == 1


async def test_staging_a_file_uploads_it_and_publishes_nothing(build, fb, files):
    meta = FakeMeta()
    server = build(meta)
    result = await server.call_tool(
        "create_container", {"file": str(files["reel.mp4"]), "caption": "Later", "account": fb}
    )
    payload = result.structured_content
    assert payload["container_id"] == "c1"
    assert payload["published"] is False
    assert payload["media_type"] == "REELS"
    assert payload["uploaded_bytes"] == len(MP4)
    assert "expires_at" in payload
    assert not any(r.url.path.endswith("/media_publish") for r in meta.seen)


async def test_a_story_file_stages_as_a_story(build, fb, files):
    meta = FakeMeta()
    server = build(meta)
    await server.call_tool(
        "create_container",
        {"file": str(files["reel.mp4"]), "media_type": "STORIES", "account": fb},
    )
    assert meta.created()[0]["media_type"] == "STORIES"


async def test_a_carousel_mixes_urls_and_files_in_order(build, fb, files):
    meta = FakeMeta()
    server = build(meta)
    result = await server.call_tool(
        "post_carousel",
        {
            "items": [
                "https://example.com/a.jpg",
                "https://example.com/b.mp4",
                str(files["reel.mp4"]),
            ],
            "caption": "Three",
            "account": fb,
            "confirm": True,
        },
    )
    assert result.structured_content["items"] == 3
    image, video, upload, parent = meta.created()
    assert image == {"image_url": "https://example.com/a.jpg", "is_carousel_item": "true",
                     "access_token": "fbtok"}
    assert video["media_type"] == "VIDEO" and video["video_url"] == "https://example.com/b.mp4"
    assert upload["media_type"] == "VIDEO" and upload["upload_type"] == "resumable"
    assert upload["is_carousel_item"] == "true"
    assert parent["media_type"] == "CAROUSEL" and parent["children"] == "c1,c2,c3"
    # The two video children are waited on before the carousel is made.
    checked = [r.url.path for r in meta.seen if r.method == "GET"]
    assert any(p.endswith("/c2") for p in checked) and any(p.endswith("/c3") for p in checked)


async def test_a_carousel_checks_every_file_before_creating_anything(build, fb, files, tmp_path):
    meta = FakeMeta()
    server = build(meta)
    with pytest.raises(ToolError, match="No file at"):
        await server.call_tool(
            "post_carousel",
            {
                "items": ["https://example.com/a.jpg", str(tmp_path / "missing.mp4")],
                "account": fb,
                "confirm": True,
            },
        )
    assert meta.seen == []


async def test_a_carousel_takes_up_to_twenty(build, fb):
    meta = FakeMeta()
    server = build(meta)
    urls = [f"https://example.com/{i}.jpg" for i in range(21)]
    with pytest.raises(ToolError, match="2 to 20"):
        await server.call_tool("post_carousel", {"items": urls, "confirm": True})
    assert meta.seen == []


async def test_a_story_video_over_100_mb_is_refused_before_uploading(build, fb, tmp_path):
    big = tmp_path / "big.mp4"
    with big.open("wb") as handle:
        handle.write(MP4[:32])
        handle.truncate(101 * 1024 * 1024)  # sparse: no real 101 MB on disk
    meta = FakeMeta()
    server = build(meta)
    with pytest.raises(ToolError, match="100 MB"):
        await server.call_tool(
            "publish_story", {"file": str(big), "account": fb, "confirm": True}
        )
    assert meta.seen == []


async def test_container_status_says_what_the_code_means(settings, build):
    meta = FakeMeta()

    def expired(request):
        meta.seen.append(request)
        return httpx2.Response(200, json={"id": "c7", "status_code": "EXPIRED"})

    server = build(expired)  # type: ignore[arg-type]
    result = await server.call_tool("get_container_status", {"container_id": "c7"})
    payload = result.structured_content
    assert payload["status_code"] == "EXPIRED"
    assert payload["publishable"] is False
    assert "24 hours" in payload["meaning"]


def test_local_video_refuses_what_it_cannot_send(files, tmp_path):
    with pytest.raises(ToolError, match="is empty"):
        local_video(str(files["empty.mp4"]))
    with pytest.raises(ToolError, match="No file at"):
        local_video(str(tmp_path / "nope.mp4"))
    (tmp_path / "notes.txt").write_text("hello there")
    with pytest.raises(ToolError, match="MP4 or MOV"):
        local_video(str(tmp_path / "notes.txt"))
    assert local_video(str(files["reel.mp4"])).size == len(MP4)


def test_the_version_the_cli_reports_is_the_package_version():
    pyproject = tomllib.loads(
        (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert __version__ == pyproject["project"]["version"]
