"""Tier 1: publishing. These tools work by default and disappear under IG_READ_ONLY.

The ones that put something public take confirm=True, because a post cannot be
unpublished from a chat window. create_container does not: staging is the safe
half of the pair and confirming it would train the reflex that makes the
confirmation on publish worthless.

Instagram publishing is two calls, always. You create a media container, then
you publish it. The container never appears in the app and is discarded unused
after 24 hours, which makes it the closest thing Instagram has to a draft and
the only way to stage something for review before it goes live.

Media reaches a container one of two ways. A public URL, which Meta fetches
itself. Or, for video, a file on this machine sent with Meta's resumable upload
to rupload.facebook.com, which Meta documents only for Facebook Login:
https://developers.facebook.com/docs/instagram-platform/content-publishing/
Meta documents no upload for an image file. An image comes from a public URL.

There is no native scheduling. Anything that claims to schedule an Instagram
post is holding the job somewhere else and publishing it at the time. This
server does not pretend otherwise.

Music from Instagram's own licensed library attaches to a reel through Meta's
Audio API: search_audio finds a track (trending when no query is given) and
audio_id puts it on the reel. Meta supports it on Facebook Login only, and on
reels only: a carousel or image container refuses audio_configuration with
"not a valid parameter for the media type".
https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..config import FACEBOOK_HOST, Account, pick
from ..errors import InstagramError
from ..runtime import Runtime, result
from ..safety import audit, require_confirm, require_write, write_gate

WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

PUBLISHING_DOCS = "https://developers.facebook.com/docs/instagram-platform/content-publishing/"

# Meta's carousel limit, from the content publishing guide: "Carousels are
# limited to 20 images, videos, or a mix of the two."
CAROUSEL_MAX = 20

# File size limits from the IG User Media reference: reels 300 MB, story video
# 100 MB. Read as MiB, so a file Meta might take is never refused here.
_MAX_BYTES = {"REELS": 300 * 1024 * 1024, "STORIES": 100 * 1024 * 1024}

# How a public URL is told apart from a path, and a video URL from an image one.
_VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v")

# HEIF and AVIF are images in an MP4-shaped box, so the brand decides.
_IMAGE_BRANDS = {
    b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"avis",
}

# What each status_code means, in the words of Meta's troubleshooting section.
CONTAINER_STATUS = {
    "EXPIRED": "The container was not published within 24 hours and has expired.",
    "ERROR": "The container failed to complete the publishing process.",
    "FINISHED": "The container and its media object are ready to be published.",
    "IN_PROGRESS": "The container is still in the publishing process.",
    "PUBLISHED": "The container's media object has been published.",
}


@dataclass(frozen=True)
class LocalVideo:
    path: Path
    size: int


def _is_url(value: str) -> bool:
    return value.lower().startswith(("https://", "http://"))


def _sniff(path: Path) -> str:
    """What a file holds, from its first bytes rather than its name."""
    with path.open("rb") as handle:
        head = handle.read(16)
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG"):
        return "image"
    if head.startswith(b"GIF8") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    if head[4:8] == b"ftyp":
        return "image" if head[8:12] in _IMAGE_BRANDS else "video"
    return "unknown"


def local_video(file: str) -> LocalVideo:
    """Check a local file before anything is sent: there, not empty, and a video.

    An image is refused with the reason, because Meta's resumable upload takes
    video only and an image container takes image_url and nothing else.
    """
    path = Path(file).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    if not path.exists():
        raise InstagramError(f"No file at {path}. Pass an absolute path to the video.")
    if not path.is_file():
        raise InstagramError(f"{path} is not a file.")
    size = path.stat().st_size
    if size == 0:
        raise InstagramError(f"{path} is empty.")
    kind = _sniff(path)
    if kind == "image":
        raise InstagramError(
            f"{path} is an image. Meta's resumable upload takes video only: reels, video "
            "stories and video carousel items. Instagram takes an image only from a public "
            f"URL that it fetches itself, so host it and pass image_url. {PUBLISHING_DOCS}"
        )
    if kind != "video":
        raise InstagramError(f"{path} is not an MP4 or MOV video, which is what Instagram takes.")
    return LocalVideo(path=path, size=size)


def _check_size(video: LocalVideo, media_type: str) -> None:
    limit = _MAX_BYTES.get(media_type)
    if limit and video.size > limit:
        what = "A reel" if media_type == "REELS" else "A story video"
        raise InstagramError(
            f"{what} can be at most {limit // (1024 * 1024)} MB and {video.path.name} is "
            f"{video.size / (1024 * 1024):.0f} MB."
        )


def _needs_facebook_login(target: Account) -> None:
    """Meta lists rupload.facebook.com for Facebook Login only."""
    if target.host != FACEBOOK_HOST:
        raise InstagramError(
            f"A file on this machine uploads only on the Facebook Login path, and {target.name} "
            "uses Instagram Login (graph.instagram.com). Meta's resumable upload is \"Only for "
            "apps that have implemented Facebook Login for Business\". Pass a public video_url "
            "instead, or connect the account with `instagram-mcp token --path facebook` and "
            f"name it with account=. {PUBLISHING_DOCS}"
        )


def _one_source(**sources: str | None) -> None:
    given = [name for name, value in sources.items() if value]
    if len(given) != 1:
        names = ", ".join(sources)
        raise InstagramError(
            f"Pass exactly one of {names}. A URL is fetched by Meta; file is a video on this "
            "machine."
        )


AUDIO_DOCS = "https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/"


def _audio(
    target: Account, audio_id: str | None, audio_volume: int | None, video_volume: int | None
) -> str | None:
    """The audio_configuration field for a reel, as the JSON string Meta reads.

    Meta takes volumes from 1 to 100 and the Audio API on Facebook Login only.
    """
    if not audio_id:
        if audio_volume is not None or video_volume is not None:
            raise InstagramError("audio_volume and video_volume need an audio_id.")
        return None
    if target.host != FACEBOOK_HOST:
        raise InstagramError(
            f"Music from Instagram's library attaches on the Facebook Login path only, and "
            f"{target.name} uses Instagram Login (graph.instagram.com). {AUDIO_DOCS}"
        )
    config: dict[str, Any] = {"audio_id": str(audio_id)}
    for name, value in (("audio_volume", audio_volume), ("video_volume", video_volume)):
        if value is not None:
            if not 1 <= value <= 100:
                raise InstagramError(f"{name} runs from 1 to 100. You passed {value}.")
            config[name] = value
    return json.dumps(config)


def _expires_at() -> str:
    return (datetime.now(UTC) + timedelta(hours=24)).isoformat(timespec="seconds")


def register(server: MCPServer, runtime: Runtime) -> None:
    settings = runtime.settings
    write = write_gate(server, settings)
    graph = runtime.graph

    async def _create(target: Any, fields: dict[str, Any]) -> str:
        body = await graph.call(target, f"/{target.user_id}/media", fields, "POST")
        container = body.get("id")
        if not container:
            raise InstagramError(
                f"Instagram accepted the container call but returned no id: {body}"
            )
        return str(container)

    async def _create_from_file(target: Account, video: LocalVideo, fields: dict[str, Any]) -> str:
        """Open a resumable session, send the file, and return the container id."""
        container, upload_url = await graph.create_resumable(target, fields)
        await graph.upload_file(target, upload_url, video.path, video.size)
        audit(
            settings,
            "upload",
            {"account": target.name, "container_id": container, "file": str(video.path),
             "bytes": video.size},
        )
        return container

    async def _publish(target: Any, container_id: str) -> dict[str, Any]:
        await graph.wait_ready(target, container_id)
        body = await graph.call(
            target, f"/{target.user_id}/media_publish", {"creation_id": container_id}, "POST"
        )
        media_id = str(body.get("id"))
        detail = await graph.call(
            target, f"/{media_id}", {"fields": "id,permalink,timestamp,media_type"}
        )
        audit(settings, "publish", {"account": target.name, "media_id": media_id})
        return detail

    @server.tool(
        description=(
            "Search Instagram's licensed music library for a reel's sound, through Meta's Audio "
            "API. With no query it returns what is trending now. Each track has an audio_id to "
            "pass to publish_reel or create_container, its title, artist, length and a link to "
            "hear it on Instagram. Facebook Login accounts only. audio_type music is the "
            "licensed catalogue; original_sound is people's own reel audio."
        ),
        annotations=READ,
    )
    async def search_audio(
        query: str | None = None,
        audio_type: str = "music",
        account: str | None = None,
    ) -> dict[str, Any]:
        if audio_type not in ("music", "original_sound"):
            raise InstagramError("audio_type must be music or original_sound.")
        target = pick(settings, account)
        if target.host != FACEBOOK_HOST:
            raise InstagramError(
                f"Meta's Audio API answers on the Facebook Login path only, and {target.name} "
                f"uses Instagram Login (graph.instagram.com). {AUDIO_DOCS}"
            )
        body = await graph.call(
            target,
            "/ig_audio",
            {"audio_type": audio_type, "user_id": target.user_id, "search_query": query},
        )
        tracks = [
            {
                "audio_id": str(t.get("audio_id")),
                "title": t.get("title"),
                "artist": t.get("display_artist") or t.get("ig_username"),
                "seconds": round((t.get("duration_in_ms") or 0) / 1000),
                "listen": t.get("on_platform_audio_preview_link"),
            }
            for t in body.get("audio", [])
        ]
        return result(
            target.tier, query=query, trending=not query, count=len(tracks), tracks=tracks
        )

    @write(
        description=(
            "Stage media without posting it. Nothing appears in the app and the container "
            "expires unused after 24 hours. This is the only draft-like state Instagram has, "
            "so use it when a human should see something before it goes live. Pass one of "
            "image_url or video_url, which Meta fetches, or file, the path of a video on this "
            "machine, which is uploaded now with Meta's resumable upload (Facebook Login "
            "accounts only; images need image_url). A video file stages as a reel unless "
            "media_type is STORIES or is_carousel_item is set. A reel takes audio_id from "
            "search_audio for a song from Instagram's library. Check it with "
            "get_container_status, then publish_container."
        ),
        annotations=WRITE,
    )
    async def create_container(
        caption: str | None = None,
        image_url: str | None = None,
        video_url: str | None = None,
        media_type: str = "IMAGE",
        cover_url: str | None = None,
        location_id: str | None = None,
        is_carousel_item: bool = False,
        account: str | None = None,
        file: str | None = None,
        audio_id: str | None = None,
        audio_volume: int | None = None,
        video_volume: int | None = None,
    ) -> dict[str, Any]:
        require_write(settings, "create_container")
        if media_type not in ("IMAGE", "REELS", "STORIES"):
            raise InstagramError("media_type must be IMAGE, REELS or STORIES.")
        _one_source(image_url=image_url, video_url=video_url, file=file)

        if file:
            video = local_video(file)
            if is_carousel_item:
                kind = "VIDEO"
            else:
                kind = "REELS" if media_type == "IMAGE" else media_type
            _check_size(video, kind)
            target = pick(settings, account)
            _needs_facebook_login(target)
            if audio_id and kind != "REELS":
                raise InstagramError("Instagram attaches a song to a reel only.")
            container = await _create_from_file(
                target,
                video,
                {
                    "media_type": kind,
                    "caption": caption,
                    "cover_url": cover_url,
                    "location_id": location_id,
                    "is_carousel_item": "true" if is_carousel_item else None,
                    "audio_configuration": _audio(target, audio_id, audio_volume, video_volume),
                },
            )
            staged: dict[str, Any] = {"media_type": kind, "uploaded_bytes": video.size}
        else:
            target = pick(settings, account)
            if audio_id and media_type != "REELS":
                raise InstagramError("Instagram attaches a song to a reel only.")
            container = await _create(
                target,
                {
                    "caption": caption,
                    "image_url": image_url,
                    "video_url": video_url,
                    # IMAGE is the default and Graph rejects it as an explicit value.
                    "media_type": None if media_type == "IMAGE" else media_type,
                    "cover_url": cover_url,
                    "location_id": location_id,
                    "is_carousel_item": "true" if is_carousel_item else None,
                    "audio_configuration": _audio(target, audio_id, audio_volume, video_volume),
                },
            )
            staged = {"media_type": media_type}
        if audio_id:
            staged["audio_id"] = str(audio_id)
        audit(settings, "create_container", {"account": target.name, "container_id": container})
        return result(
            target.tier,
            container_id=container,
            published=False,
            expires_at=_expires_at(),
            **staged,
            note=(
                "Not live. Meta discards it unused after 24 hours. A video processes first: "
                "get_container_status says when it is FINISHED, and publish_container posts it."
            ),
        )

    @server.tool(
        description=(
            "Where a staged container stands: IN_PROGRESS while a video processes, FINISHED "
            "when it can be published, PUBLISHED, ERROR, or EXPIRED after 24 hours unused. "
            "Meta suggests checking once a minute for no more than 5 minutes."
        ),
        annotations=READ,
    )
    async def get_container_status(
        container_id: str, account: str | None = None
    ) -> dict[str, Any]:
        target = pick(settings, account)
        body = await graph.call(target, f"/{container_id}", {"fields": "id,status_code,status"})
        code = body.get("status_code")
        return result(
            target.tier,
            container_id=str(body.get("id") or container_id),
            status_code=code,
            meaning=CONTAINER_STATUS.get(
                str(code), "Meta returned a status this server does not know."
            ),
            publishable=code == "FINISHED",
            **({"status": body["status"]} if body.get("status") else {}),
        )

    @write(
        description="Publish a staged container. This makes it live and cannot be undone.",
        annotations=WRITE,
    )
    async def publish_container(
        container_id: str, account: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        require_write(settings, "publish_container")
        require_confirm(
            confirm, "publish_container", "posts publicly to the account and cannot be undone."
        )
        target = pick(settings, account)
        return result(target.tier, **await _publish(target, container_id))

    @write(
        description=(
            "Publish in one step: stage, wait for processing, publish. Pass one of image_url or "
            "video_url, which Meta fetches, or file, the path of a video on this machine, "
            "uploaded with Meta's resumable upload (Facebook Login accounts only). A video "
            "file posts as a reel unless media_type is STORIES. Use create_container instead "
            "when a person should approve the post first."
        ),
        annotations=WRITE,
    )
    async def post(
        caption: str | None = None,
        image_url: str | None = None,
        video_url: str | None = None,
        media_type: str = "IMAGE",
        account: str | None = None,
        confirm: bool = False,
        file: str | None = None,
    ) -> dict[str, Any]:
        require_write(settings, "post")
        require_confirm(confirm, "post", "publishes immediately and cannot be undone.")
        if media_type not in ("IMAGE", "REELS", "STORIES"):
            raise InstagramError("media_type must be IMAGE, REELS or STORIES.")
        _one_source(image_url=image_url, video_url=video_url, file=file)

        if file:
            video = local_video(file)
            kind = "REELS" if media_type == "IMAGE" else media_type
            _check_size(video, kind)
            target = pick(settings, account)
            _needs_facebook_login(target)
            container = await _create_from_file(
                target, video, {"media_type": kind, "caption": caption}
            )
        else:
            target = pick(settings, account)
            container = await _create(
                target,
                {
                    "caption": caption,
                    "image_url": image_url,
                    "video_url": video_url,
                    "media_type": None if media_type == "IMAGE" else media_type,
                },
            )
        return result(target.tier, **await _publish(target, container))

    @write(
        description=(
            "Publish a carousel of 2 to 20 images and videos. items is the list in order: "
            "each a public image or video URL (a URL ending .mp4, .mov or .m4v is a video), "
            "or the path of a video on this machine (Facebook Login accounts only). "
            "image_urls still works for a list of image URLs."
        ),
        annotations=WRITE,
    )
    async def post_carousel(
        image_urls: list[str] | None = None,
        caption: str | None = None,
        account: str | None = None,
        confirm: bool = False,
        items: list[str] | None = None,
    ) -> dict[str, Any]:
        require_write(settings, "post_carousel")
        require_confirm(confirm, "post_carousel", "publishes immediately and cannot be undone.")
        if image_urls and items:
            raise InstagramError("Pass items or image_urls, not both.")
        entries = items or image_urls or []
        if not 2 <= len(entries) <= CAROUSEL_MAX:
            raise InstagramError(
                f"A carousel takes 2 to {CAROUSEL_MAX} items. You passed {len(entries)}."
            )

        # Every local file is checked before the first container is created, so a
        # bad tenth item does not leave nine orphaned containers behind.
        plan: list[tuple[str, Any]] = []
        for entry in entries:
            if _is_url(entry):
                is_video = entry.lower().split("?", 1)[0].endswith(_VIDEO_SUFFIXES)
                plan.append(("video_url" if is_video else "image_url", entry))
            elif image_urls:
                raise InstagramError(
                    f"{entry} is not a URL. image_urls takes public URLs; use items for a file."
                )
            else:
                plan.append(("file", local_video(entry)))

        target = pick(settings, account)
        if any(kind == "file" for kind, _ in plan):
            _needs_facebook_login(target)

        # Children are created in sequence on purpose. Meta rate limits container
        # creation per account, and firing ten at once earns a 4 where ten in a
        # row does not.
        children: list[str] = []
        videos: list[str] = []
        for kind, value in plan:
            if kind == "image_url":
                children.append(
                    await _create(target, {"image_url": value, "is_carousel_item": "true"})
                )
                continue
            if kind == "video_url":
                child = await _create(
                    target,
                    {"media_type": "VIDEO", "video_url": value, "is_carousel_item": "true"},
                )
            else:
                child = await _create_from_file(
                    target, value, {"media_type": "VIDEO", "is_carousel_item": "true"}
                )
            children.append(child)
            videos.append(child)

        # A video child has to finish processing before the carousel can hold it.
        for child in videos:
            await graph.wait_ready(target, child)

        parent = await _create(
            target,
            {"media_type": "CAROUSEL", "caption": caption, "children": ",".join(children)},
        )
        detail = await _publish(target, parent)
        return result(target.tier, items=len(children), **detail)

    @write(
        description=(
            "Publish a reel, with an optional cover image URL. Pass video_url, which Meta "
            "fetches, or file, the path of a video on this machine, uploaded with Meta's "
            "resumable upload (Facebook Login accounts only). Meta takes MP4 or MOV, 3 seconds "
            "to 15 minutes, up to 300 MB; 9:16 avoids cropping. audio_id, from search_audio, "
            "puts a song from Instagram's library on it; audio_volume and video_volume (1 to "
            "100) set the song against the clip's own sound."
        ),
        annotations=WRITE,
    )
    async def publish_reel(
        video_url: str | None = None,
        caption: str | None = None,
        cover_url: str | None = None,
        share_to_feed: bool = True,
        account: str | None = None,
        confirm: bool = False,
        file: str | None = None,
        audio_id: str | None = None,
        audio_volume: int | None = None,
        video_volume: int | None = None,
    ) -> dict[str, Any]:
        require_write(settings, "publish_reel")
        require_confirm(confirm, "publish_reel", "publishes immediately and cannot be undone.")
        _one_source(video_url=video_url, file=file)
        target = pick(settings, account)
        fields = {
            "media_type": "REELS",
            "caption": caption,
            "cover_url": cover_url,
            "share_to_feed": "true" if share_to_feed else "false",
            "audio_configuration": _audio(target, audio_id, audio_volume, video_volume),
        }
        if file:
            video = local_video(file)
            _check_size(video, "REELS")
            _needs_facebook_login(target)
            container = await _create_from_file(target, video, fields)
        else:
            container = await _create(target, {**fields, "video_url": video_url})
        detail = await _publish(target, container)
        if audio_id:
            detail["audio_id"] = str(audio_id)
        return result(target.tier, **detail)

    @write(
        description=(
            "Publish an image or video story. It disappears after 24 hours. Pass image_url or "
            "video_url, which Meta fetches, or file, the path of a video on this machine "
            "(Facebook Login accounts only; up to 60 seconds and 100 MB)."
        ),
        annotations=WRITE,
    )
    async def publish_story(
        image_url: str | None = None,
        video_url: str | None = None,
        account: str | None = None,
        confirm: bool = False,
        file: str | None = None,
    ) -> dict[str, Any]:
        require_write(settings, "publish_story")
        require_confirm(confirm, "publish_story", "posts a story visible for 24 hours.")
        _one_source(image_url=image_url, video_url=video_url, file=file)
        if file:
            video = local_video(file)
            _check_size(video, "STORIES")
            target = pick(settings, account)
            _needs_facebook_login(target)
            container = await _create_from_file(target, video, {"media_type": "STORIES"})
        else:
            target = pick(settings, account)
            container = await _create(
                target, {"media_type": "STORIES", "image_url": image_url, "video_url": video_url}
            )
        detail = await _publish(target, container)
        return result(target.tier, expires_in_hours=24, **detail)
