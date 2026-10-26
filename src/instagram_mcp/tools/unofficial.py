"""Tier 3: the private API. Registered always, usable only with --unofficial.

The tools are always registered so that an agent asking for something official
Instagram cannot do gets a clear explanation instead of "no such tool". Every
one of them fails closed with the reason and the flag needed.

What is deliberately absent, and why, is in safety.NOT_IMPLEMENTED and is
reported by unofficial_status. Follow, unfollow and bulk like are the three
actions that get accounts restricted fastest, and they are what every
growth-hack tool ships. Leaving them out is the positioning, not an oversight.

Writes on this tier need the tier turned on and confirm=true on the call. A
message sent from a private-API session is both the most useful and the most
dangerous thing here.
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..config import Tier
from ..media import describe_media, download_media, media_locator
from ..runtime import Runtime, result
from ..safety import NOT_IMPLEMENTED, audit, frame_rows, require_confirm, require_write
from ..unofficial import simplify

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

# Capped well below what instagrapi will happily fetch. A request for 5,000
# followers is thousands of paged private-API calls and is the single most
# reliable way to get an account restricted.
MAX_PEOPLE = 200


def register(server: MCPServer, runtime: Runtime) -> None:
    settings = runtime.settings
    unofficial = runtime.unofficial

    def ok(**payload: Any) -> dict[str, Any]:
        return result(Tier.UNOFFICIAL, **payload)

    async def _user_id(username: str) -> str:
        user = await unofficial.run("user_info_by_username", username.strip().lstrip("@"))
        return str(user.pk)

    @server.tool(
        description=(
            "Whether the unofficial tier is on, whether a session is loaded, how much of the "
            "local hourly ceiling is used, and which tools this server deliberately does not "
            "implement. Call this first if an unofficial tool fails."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def unofficial_status() -> dict[str, Any]:
        session = settings.session_path
        return {
            "enabled": settings.unofficial,
            "session_file_present": session.exists(),
            "writes_allowed": not settings.read_only,
            "pacing": unofficial.pacer_status(),
            "deliberately_not_implemented": NOT_IMPLEMENTED,
            "note": (
                "This tier is unofficial and against Instagram's terms. It can get an account "
                "restricted. Point it at a secondary account."
            ),
        }

    @server.tool(
        description=(
            "Full public profile for ANY Instagram account by username, including personal "
            "accounts that business_discovery cannot see. Prefer discover_account when the "
            "target is a Business or Creator account: it is official and carries no risk."
        ),
        annotations=READ,
    )
    async def unofficial_profile(username: str) -> dict[str, Any]:
        user = await unofficial.run("user_info_by_username", username.strip().lstrip("@"))
        data = simplify(user)
        return ok(profile=frame_rows([data])[0])

    @server.tool(
        description=(
            "Recent posts by ANY public account, with engagement. This reaches accounts and "
            "history depth no official endpoint returns."
        ),
        annotations=READ,
    )
    async def unofficial_posts(username: str, amount: int = 20) -> dict[str, Any]:
        amount = max(1, min(amount, 100))
        media = await unofficial.run("user_medias", await _user_id(username), amount)
        rows = [simplify(m) for m in media]
        return ok(username=username.lstrip("@"), count=len(rows), media=frame_rows(rows))

    @server.tool(
        description=(
            "One Instagram post by numeric media id or permalink, with every carousel asset "
            "in order. Set download=true to save images and videos plus a source manifest "
            "under IG_MCP_DATA_DIR/media. Returns local paths and SHA-256 hashes for later "
            "inspection. Never publishes. CDN URLs expire; saved files persist."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, openWorldHint=True
        ),
    )
    async def unofficial_media(media: str, download: bool = False) -> dict[str, Any]:
        kind, locator = media_locator(media)
        media_id = (
            await unofficial.run("media_pk_from_url", locator) if kind == "url" else locator
        )
        post = await unofficial.run("media_info", str(media_id))
        details = frame_rows([describe_media(simplify(post))])[0]
        if download:
            details = await download_media(details, settings.data_dir)
        return ok(media=details)

    @server.tool(
        description=(
            "Stories currently live on ANY public account. There is no official way to read "
            "somebody else's stories."
        ),
        annotations=READ,
    )
    async def unofficial_stories(username: str, amount: int = 20) -> dict[str, Any]:
        stories = await unofficial.run("user_stories", await _user_id(username), amount)
        rows = [simplify(s) for s in stories]
        return ok(username=username.lstrip("@"), count=len(rows), stories=rows)

    @server.tool(
        description=(
            f"Followers of an account, newest first, capped at {MAX_PEOPLE}. Each page is a "
            "separate private-API call, so a large request is both slow and the fastest way "
            "to get restricted. Ask for the smallest number that answers the question."
        ),
        annotations=READ,
    )
    async def unofficial_followers(username: str, amount: int = 50) -> dict[str, Any]:
        amount = max(1, min(amount, MAX_PEOPLE))
        people = await unofficial.run("user_followers", await _user_id(username), amount=amount)
        rows = [simplify(p) for p in (people.values() if isinstance(people, dict) else people)]
        return ok(username=username.lstrip("@"), count=len(rows), followers=rows)

    @server.tool(
        description=(
            f"Accounts an account follows, capped at {MAX_PEOPLE}. "
            "Same cost warning as followers."
        ),
        annotations=READ,
    )
    async def unofficial_following(username: str, amount: int = 50) -> dict[str, Any]:
        amount = max(1, min(amount, MAX_PEOPLE))
        people = await unofficial.run("user_following", await _user_id(username), amount=amount)
        rows = [simplify(p) for p in (people.values() if isinstance(people, dict) else people)]
        return ok(username=username.lstrip("@"), count=len(rows), following=rows)

    @server.tool(
        description=(
            "Search Instagram accounts by name or keyword. There is no official account search."
        ),
        annotations=READ,
    )
    async def unofficial_search_accounts(query: str) -> dict[str, Any]:
        users = await unofficial.run("search_users", query)
        rows = [simplify(u) for u in users]
        return ok(query=query, count=len(rows), accounts=frame_rows(rows))

    @server.tool(
        description=(
            "Comments on ANY public post, by its URL or shortcode. The official API only "
            "returns comments on your own posts."
        ),
        annotations=READ,
    )
    async def unofficial_post_comments(url: str, amount: int = 30) -> dict[str, Any]:
        amount = max(1, min(amount, 200))
        media_pk = await unofficial.run("media_pk_from_url", url)
        comments = await unofficial.run("media_comments", str(media_pk), amount)
        rows = [simplify(c) for c in comments]
        return ok(url=url, count=len(rows), comments=frame_rows(rows))

    @server.tool(
        description=(
            "Your real Instagram inbox, including message requests and threads older than the "
            "24-hour window the official API confines you to."
        ),
        annotations=READ,
    )
    async def unofficial_inbox(amount: int = 20) -> dict[str, Any]:
        amount = max(1, min(amount, 100))
        threads = await unofficial.run("direct_threads", amount)
        rows = [simplify(t) for t in threads]
        return ok(count=len(rows), threads=frame_rows(rows))

    @server.tool(description="Messages inside one inbox thread.", annotations=READ)
    async def unofficial_thread(thread_id: str, amount: int = 25) -> dict[str, Any]:
        amount = max(1, min(amount, 100))
        messages = await unofficial.run("direct_messages", int(thread_id), amount)
        rows = [simplify(m) for m in messages]
        return ok(thread_id=thread_id, count=len(rows), messages=frame_rows(rows))

    @server.tool(
        description=(
            "Send a direct message with no 24-hour window and no prior contact. This is the "
            "thing the official API cannot do, and it is also the thing most likely to get an "
            "account restricted. Needs the unofficial tier on, and confirm=true."
        ),
        annotations=WRITE,
    )
    async def unofficial_send_dm(
        username: str, message: str, confirm: bool = False
    ) -> dict[str, Any]:
        require_write(settings, "unofficial_send_dm")
        require_confirm(
            confirm,
            "unofficial_send_dm",
            "sends a cold DM from a private-API session. It cannot be unsent and it is the "
            "single most likely action here to get the account restricted.",
        )
        user_id = await _user_id(username)
        sent = await unofficial.run("direct_send", message, user_ids=[int(user_id)])
        audit(
            settings,
            "unofficial_send_dm",
            {"tier": "unofficial", "to": username.lstrip("@"), "message": message},
        )
        return ok(to=username.lstrip("@"), thread=simplify(sent))
