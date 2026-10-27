"""Tier 1: your own accounts. Reads only.

This is the surface every Instagram MCP server has. Two things here that the
others do not: it works across several connected accounts, and it writes what it
reads to the local store so growth_history and post_movement have something to
answer with.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..config import INSTAGRAM_HOST, Account, Tier, pick
from ..errors import InstagramError
from ..runtime import Runtime, result
from ..safety import frame_rows

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

PROFILE_FIELDS = "id,username,media_count,followers_count,follows_count,profile_picture_url"


def profile_fields(account: Account) -> str:
    """The profile fields this account's host answers.

    `account_type` exists only on Instagram Login (graph.instagram.com). On the
    Facebook Login path Graph refuses the whole call with "(#100) Tried
    accessing nonexisting field (account_type)", and every account it can reach
    is a Business or Creator account anyway.
    """
    return PROFILE_FIELDS + (",account_type" if account.host == INSTAGRAM_HOST else "")


def account_type(account: Account, profile: dict[str, Any]) -> str | None:
    """Meta's account_type where it answers it; on Facebook Login, what that path implies."""
    if profile.get("account_type"):
        return str(profile["account_type"])
    return None if account.host == INSTAGRAM_HOST else "BUSINESS_OR_CREATOR"
MEDIA_FIELDS = "id,caption,media_type,permalink,timestamp,like_count,comments_count"

# Meta retires insight metrics between Graph versions and does not always say so
# before it happens. These are the defaults, and every insights tool takes an
# override so a retired metric is a one-argument workaround rather than a wait
# for a release.
DEFAULT_MEDIA_METRICS = "reach,saved,shares,total_interactions,likes,comments,views"
DEFAULT_ACCOUNT_METRICS = "reach,follower_count,profile_views"


def register(server: MCPServer, runtime: Runtime) -> None:
    settings = runtime.settings
    graph = runtime.graph
    store = runtime.store

    @server.tool(
        description=(
            "Every connected Instagram account with live follower counts, post counts and "
            "account type, and which tier each one can reach. Start here rather than calling "
            "whoami once per account."
        ),
        annotations=READ,
    )
    async def list_accounts(fast: bool = False) -> dict[str, Any]:
        if not settings.accounts:
            return {
                "count": 0,
                "accounts": [],
                "note": "No account configured. Run `instagram-mcp doctor` for the fix.",
            }
        if fast:
            return {
                "count": len(settings.accounts),
                "accounts": [a.redacted() for a in settings.accounts],
            }

        async def probe(account: Any) -> dict[str, Any]:
            # One dead token must not hide the six healthy accounts next to it,
            # so a failure is reported on its own row rather than raised.
            try:
                profile = await graph.call(
                    account, f"/{account.user_id}", {"fields": profile_fields(account)}
                )
            except Exception as exc:
                return {**account.redacted(), "status": "error", "detail": str(exc)[:200]}
            await store.record_profile(account.name, profile)
            return {
                **account.redacted(),
                "status": "ok",
                "username": profile.get("username"),
                "type": account_type(account, profile),
                "followers": profile.get("followers_count"),
                "following": profile.get("follows_count"),
                "posts": profile.get("media_count"),
            }

        rows = await asyncio.gather(*(probe(a) for a in settings.accounts))
        healthy = [r for r in rows if r.get("status") == "ok"]
        return {
            "count": len(rows),
            "healthy": len(healthy),
            "total_followers": sum(int(r.get("followers") or 0) for r in healthy),
            "accounts": sorted(rows, key=lambda r: int(r.get("followers") or 0), reverse=True),
        }

    @server.tool(
        description="Verify one account's token and return its live profile.",
        annotations=READ,
    )
    async def whoami(account: str | None = None) -> dict[str, Any]:
        target = pick(settings, account)
        profile = await graph.call(
            target, f"/{target.user_id}", {"fields": profile_fields(target)}
        )
        await store.record_profile(target.name, profile)
        # A biography is written by the account holder, but on a Business account
        # it can be edited by anyone with access, so it is framed like any other
        # free text that did not come from the operator of this server.
        return result(target.tier, account=target.name, **frame_rows([profile])[0])

    @server.tool(
        description=(
            "When each connected token expires, and whether it can be refreshed. "
            "Instagram Login tokens die after 60 days of no refresh, which is how most "
            "Instagram integrations break."
        ),
        annotations=READ,
    )
    async def token_status() -> dict[str, Any]:
        async def probe(account: Any) -> dict[str, Any]:
            try:
                # debug_token is the only edge that reports expiry without
                # spending the refresh. It exists on both hosts.
                body = await graph.call(
                    account, "/debug_token", {"input_token": account.token}
                )
                data = body.get("data") or {}
                return {
                    **account.redacted(),
                    "valid": data.get("is_valid"),
                    "expires_at": data.get("expires_at"),
                    "scopes": data.get("scopes"),
                    "refreshable": account.tier is Tier.INSTAGRAM_LOGIN,
                }
            except Exception as exc:
                return {**account.redacted(), "status": "error", "detail": str(exc)[:200]}

        return {"accounts": list(await asyncio.gather(*(probe(a) for a in settings.accounts)))}

    @server.tool(description="Recent posts with permalinks and engagement.", annotations=READ)
    async def get_media(limit: int = 25, account: str | None = None) -> dict[str, Any]:
        target = pick(settings, account)
        body = await graph.call(
            target, f"/{target.user_id}/media", {"fields": MEDIA_FIELDS, "limit": limit}
        )
        rows = body.get("data") or []
        await store.record_media(target.name, rows)
        return result(target.tier, account=target.name, count=len(rows), media=frame_rows(rows))

    @server.tool(
        description=(
            "Every post, paging until the account history is exhausted or max is reached. "
            "Use this for analysis over a whole account, not get_media in a loop."
        ),
        annotations=READ,
    )
    async def list_all_media(max: int = 200, account: str | None = None) -> dict[str, Any]:
        target = pick(settings, account)
        rows = await graph.paginate(
            target, f"/{target.user_id}/media", {"fields": MEDIA_FIELDS}, max_items=max
        )
        await store.record_media(target.name, rows)
        return result(target.tier, account=target.name, count=len(rows), media=frame_rows(rows))

    @server.tool(description="One post by its media id, with full fields.", annotations=READ)
    async def get_media_by_id(media_id: str, account: str | None = None) -> dict[str, Any]:
        target = pick(settings, account)
        body = await graph.call(
            target,
            f"/{media_id}",
            {
                "fields": "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
                "like_count,comments_count,is_shared_to_feed"
            },
        )
        return result(target.tier, **frame_rows([body])[0])

    @server.tool(description="Posts by other accounts that tagged you.", annotations=READ)
    async def list_tagged_media(limit: int = 25, account: str | None = None) -> dict[str, Any]:
        target = pick(settings, account)
        body = await graph.call(
            target,
            f"/{target.user_id}/tags",
            {"fields": "id,caption,media_type,permalink,timestamp,username", "limit": limit},
        )
        rows = body.get("data") or []
        return result(target.tier, count=len(rows), media=frame_rows(rows))

    @server.tool(
        description=(
            "Stories currently live on the account. Stories vanish after 24 hours, so this "
            "is empty most of the time and that is not an error."
        ),
        annotations=READ,
    )
    async def list_stories(account: str | None = None) -> dict[str, Any]:
        target = pick(settings, account)
        body = await graph.call(
            target,
            f"/{target.user_id}/stories",
            {"fields": "id,media_type,media_url,permalink,timestamp"},
        )
        rows = body.get("data") or []
        return result(target.tier, count=len(rows), stories=rows)

    @server.tool(
        description=(
            "Reach, saves, shares and interactions for one post. Pass metrics to override "
            "the defaults if Meta has retired one in your Graph version."
        ),
        annotations=READ,
    )
    async def get_media_insights(
        media_id: str, metrics: str | None = None, account: str | None = None
    ) -> dict[str, Any]:
        target = pick(settings, account)
        body = await graph.call(
            target, f"/{media_id}/insights", {"metric": metrics or DEFAULT_MEDIA_METRICS}
        )
        return result(target.tier, media_id=media_id, insights=body.get("data") or [])

    @server.tool(
        description=(
            "Account-level reach, follower count and profile views over a period. Pass "
            "metrics to override the defaults."
        ),
        annotations=READ,
    )
    async def get_account_insights(
        period: str = "week", metrics: str | None = None, account: str | None = None
    ) -> dict[str, Any]:
        if period not in ("day", "week", "days_28"):
            raise InstagramError("period must be day, week or days_28.")
        target = pick(settings, account)
        body = await graph.call(
            target,
            f"/{target.user_id}/insights",
            {"metric": metrics or DEFAULT_ACCOUNT_METRICS, "period": period},
        )
        return result(target.tier, period=period, insights=body.get("data") or [])

    @server.tool(
        description=(
            "How many of the 100 API-published posts per rolling 24 hours this account "
            "has used. Check before a batch publish."
        ),
        annotations=READ,
    )
    async def get_publishing_limit(account: str | None = None) -> dict[str, Any]:
        target = pick(settings, account)
        body = await graph.call(
            target,
            f"/{target.user_id}/content_publishing_limit",
            {"fields": "config,quota_usage"},
        )
        return result(target.tier, **body)

    # ── store-backed. A live Graph call cannot answer either of these ─────────

    @server.tool(
        description=(
            "Follower and post counts over time, from readings this server has already "
            "taken. Answers 'how much did I grow this week', which no live Instagram call "
            "can. Empty until list_accounts or whoami has run at least twice on "
            "different days."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def growth_history(account: str | None = None, limit: int = 30) -> dict[str, Any]:
        target = pick(settings, account)
        rows = await store.profile_history(target.name, limit)
        return {
            "account": target.name,
            "readings": len(rows),
            "history": rows,
            "note": "Local readings, not an Instagram metric. Oldest reading bounds the window.",
        }

    @server.tool(
        description=(
            "Which posts gained the most likes and comments between the earliest and latest "
            "readings this server holds. This is the 'what is still growing' question that a "
            "single live call cannot answer."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def post_movement(account: str | None = None, limit: int = 25) -> dict[str, Any]:
        target = pick(settings, account)
        rows = await store.media_movement(target.name, limit)
        return {
            "account": target.name,
            "posts": rows,
            "note": "Deltas between local readings. Run get_media regularly to build history.",
        }
