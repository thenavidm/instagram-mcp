"""Ordered media details and complete local copies of one unofficial post."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2

from .errors import InstagramError

MAX_ASSETS = 20
MAX_ASSET_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
_NUMERIC_ID = re.compile(r"[0-9]{1,30}(?:_[0-9]{1,30})?")
_CODE = re.compile(r"[A-Za-z0-9_-]{1,100}")
_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
}


def media_locator(value: str) -> tuple[str, str]:
    """Accept a numeric id or an Instagram permalink, never an arbitrary URL."""
    value = value.strip()
    if _NUMERIC_ID.fullmatch(value):
        return "id", value.split("_")[0]
    try:
        url = urlsplit(value)
        parts = url.path.strip("/").split("/")
        if (
            url.scheme == "https"
            and url.hostname in {"instagram.com", "www.instagram.com"}
            and url.port in {None, 443}
            and url.username is None
            and url.password is None
            and len(parts) == 2
            and parts[0] in {"p", "reel", "tv"}
            and _CODE.fullmatch(parts[1])
        ):
            return "url", f"https://www.instagram.com/{parts[0]}/{parts[1]}/"
    except ValueError:
        pass
    raise InstagramError("Pass a numeric media id or an https://www.instagram.com/p/... permalink.")


def describe_media(media: dict[str, Any]) -> dict[str, Any]:
    """Keep carousel order and distinguish video bytes from their thumbnails."""
    media_id = str(media.get("pk") or media.get("id") or "")
    code = str(media.get("code") or "")
    if not _NUMERIC_ID.fullmatch(media_id) or not _CODE.fullmatch(code):
        raise InstagramError("Instagram returned incomplete media details. Fetch the post again.")
    kind = media.get("media_type")
    rows = media.get("resources") if kind == 8 else [media]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ASSETS:
        raise InstagramError(f"Expected between 1 and {MAX_ASSETS} media assets.")
    assets = []
    for index, row in enumerate(rows, 1):
        if row.get("media_type") not in {1, 2}:
            raise InstagramError("This post contains an unsupported media type.")
        video = row["media_type"] == 2
        url = row.get("video_url") if video else row.get("thumbnail_url")
        if not url:
            raise InstagramError(f"Instagram did not return a download URL for asset {index}.")
        assets.append({
            "index": index,
            "media_id": str(row.get("pk") or row.get("id") or ""),
            "kind": "video" if video else "image",
            "url": str(url),
            "thumbnail_url": str(row.get("thumbnail_url") or ""),
            "width": row.get("original_width"),
            "height": row.get("original_height"),
        })
    surface = "reel" if media.get("product_type") == "clips" else "p"
    return {
        "media_id": media_id.split("_")[0],
        "permalink": f"https://www.instagram.com/{surface}/{code}/",
        "author": (media.get("user") or {}).get("username"),
        "caption": media.get("caption_text") or "",
        "taken_at": media.get("taken_at"),
        "media_type": kind,
        "assets": assets,
    }


def _cdn_url(value: str) -> str:
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if (
            url.scheme == "https"
            and url.port in {None, 443}
            and url.username is None
            and url.password is None
            and not url.fragment
            and any(host == suffix or host.endswith("." + suffix)
                    for suffix in ("cdninstagram.com", "fbcdn.net"))
        ):
            return value
    except ValueError:
        pass
    raise InstagramError("Instagram returned a media URL outside its HTTPS media CDN.")


async def download_media(
    media: dict[str, Any], data_dir: Path, *, client: httpx2.AsyncClient | None = None
) -> dict[str, Any]:
    """Commit a manifest only after every asset has been saved successfully.

    A separate HTTP client prevents session credentials from reaching a CDN.
    Paths are generated here, not accepted from a tool call or provider text.
    """
    urls = [_cdn_url(asset["url"]) for asset in media["assets"]]
    if client is None:
        async with httpx2.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as http:
            return await download_media(media, data_dir, client=http)

    stage: Path | None = None
    try:
        root = data_dir.resolve() / "media"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".partial-", dir=root))
        destination = root / uuid4().hex
        saved = {
            **media,
            "source": "unofficial",
            "downloaded_at": datetime.now(UTC).isoformat(),
            "manifest_path": str(destination / "manifest.json"),
            "assets": [],
        }
        total = 0
        for asset, url in zip(media["assets"], urls, strict=True):
            async with client.stream("GET", url, follow_redirects=False) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].lower()
                extension = _CONTENT_TYPES.get(content_type)
                if extension is None or content_type.split("/")[0] != asset["kind"]:
                    raise InstagramError("Instagram returned an unsupported media content type.")
                filename = f"{asset['index']:02d}{extension}"
                size = 0
                digest = hashlib.sha256()
                fd = os.open(stage / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        if size > MAX_ASSET_BYTES or total > MAX_TOTAL_BYTES:
                            raise InstagramError(
                                "Media download exceeds the 100 MiB per asset "
                                "or 200 MiB post limit."
                            )
                        handle.write(chunk)
                        digest.update(chunk)
                    if not size:
                        raise InstagramError("Instagram returned an empty media download.")
                    handle.flush()
                    os.fsync(handle.fileno())
                saved["assets"].append({
                    **asset,
                    "path": str(destination / filename),
                    "content_type": content_type,
                    "bytes": size,
                    "sha256": digest.hexdigest(),
                })
        fd = os.open(stage / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(saved, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        stage.rename(destination)
        return saved
    except httpx2.HTTPError as exc:
        raise InstagramError(
            "Media download failed. Fetch the post again if its CDN URLs have expired."
        ) from exc
    except OSError as exc:
        raise InstagramError(
            "Could not save media. Check disk space and permissions for IG_MCP_DATA_DIR."
        ) from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
