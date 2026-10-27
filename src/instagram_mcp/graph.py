"""The official Graph API layer. Both hosts, one function.

Instagram Login and Facebook Login are the same protocol against different
hosts with different reach, so they share this module and differ only in the
`host` on the Account.

Two details here are easy to get wrong and expensive to get wrong:

  1. DELETE is a real HTTP method. Several servers, ours included, branch on
     GET versus everything-else and quietly send a POST when they meant a
     delete. Graph answers 200 to that POST and changes nothing, so the tool
     reports success while doing nothing at all.

  2. Graph error bodies are far more useful than their status codes. A 400 that
     means "your token expired" and a 400 that means "you asked for a metric
     that no longer exists" need different reactions from the caller, so the
     error text says which it was.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

import httpx2

from .config import FACEBOOK_HOST, Account, Settings
from .errors import InstagramError

Method = Literal["GET", "POST", "DELETE"]

# Container processing. Reels and videos are transcoded asynchronously and
# publishing before the container reports FINISHED fails, so publishing polls.
_POLL_INTERVAL_SECONDS = 2.0
_POLL_MAX_ATTEMPTS = 40

# Resumable upload. Meta's content publishing guide sends a local video's bytes
# to this host, never to graph.facebook.com:
# https://developers.facebook.com/docs/instagram-platform/content-publishing/
RUPLOAD_HOST = "rupload.facebook.com"
_UPLOAD_CHUNK_BYTES = 1024 * 1024
_UPLOAD_ATTEMPTS = 3


class GraphError(InstagramError):
    """A Graph API call failed and the message says why in plain language."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        subcode: int | None = None,
        retriable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        # Only the upload sets this: a dropped connection or a fault Meta calls
        # retriable is worth sending again. A rejected request never is.
        self.retriable = retriable


def _explain(body: dict[str, Any], status: int, path: str) -> GraphError:
    """Turn a Graph error body into something a person can act on."""
    err = body.get("error") or {}
    message = err.get("message") or f"HTTP {status}"
    code = err.get("code")
    subcode = err.get("error_subcode")

    # The handful of failures that have a specific, actionable cause. Everything
    # else falls through with Meta's own wording, which is usually fine.
    hint = ""
    if code == 190:
        hint = (
            " Your access token is invalid or expired. Instagram Login tokens last 60 days: "
            "run `instagram-mcp refresh` before then, or re-mint with `instagram-mcp token`."
        )
    elif code == 10 or code == 200:
        hint = (
            " This is a permissions failure, not a bad request. The token is missing a scope, "
            "or the endpoint needs Advanced Access through Meta App Review."
        )
    elif code == 4 or code == 17 or code == 32:
        hint = " You are being rate limited by Meta. Wait, then retry."
    elif code == 100 and "metric" in message.lower():
        hint = (
            " Meta retires insight metrics between versions. Run `instagram-mcp doctor` "
            "to see which metrics this account still answers."
        )

    return GraphError(f"{path}: {message}{hint}", code=code, subcode=subcode)


def _upload_answer(response: httpx2.Response) -> dict[str, Any]:
    """Read rupload's answer, which is not a Graph error body.

    Success is `{"success": true, "message": "Upload successful."}`. A failure
    is `{"debug_info": {"retriable": ..., "type": ..., "message": ...}}`, and
    its message is sometimes JSON inside a string, which is passed on as it is.
    """
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:500]}
    if not isinstance(body, dict):
        body = {"data": body}

    info = body.get("debug_info")
    if response.status_code < 400 and not info and body.get("success") is not False:
        return body

    info = info if isinstance(info, dict) else {}
    kind = info.get("type")
    reason = info.get("message") or (body.get("error") or {}).get("message") or body.get("raw")
    hint = ""
    if response.status_code in (401, 403) or kind == "NotAuthorizedError":
        hint = (
            " Meta's resumable upload is only for apps that use Facebook Login for Business,"
            " with a token that has instagram_content_publish."
        )
    raise GraphError(
        f"upload {response.status_code}{f' {kind}' if kind else ''}: "
        f"{reason or 'no reason given'}.{hint}",
        retriable=info.get("retriable") is True or response.status_code >= 500,
    )


class GraphClient:
    """A shared async HTTP client. One per process, closed on shutdown."""

    def __init__(self, settings: Settings, *, client: httpx2.AsyncClient | None = None):
        self._settings = settings
        self._client = client or httpx2.AsyncClient(timeout=30.0)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _url(self, account: Account, path: str) -> str:
        return f"https://{account.host}/{self._settings.graph_version}{path}"

    async def call(
        self,
        account: Account,
        path: str,
        params: dict[str, Any] | None = None,
        method: Method = "GET",
    ) -> dict[str, Any]:
        """One Graph call. Empty and None values are dropped, never sent as ""."""
        payload = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        payload["access_token"] = account.token

        if method == "GET":
            request = self._client.build_request("GET", self._url(account, path), params=payload)
        elif method == "POST":
            request = self._client.build_request("POST", self._url(account, path), data=payload)
        else:
            # A real DELETE, with the token in the query string. Sending it as a
            # body on a DELETE is not reliably read by Graph.
            request = self._client.build_request(
                "DELETE", self._url(account, path), params=payload
            )

        try:
            response = await self._client.send(request)
        except httpx2.HTTPError as exc:
            raise GraphError(f"{path}: could not reach {account.host}: {exc}") from exc

        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:500]}
        if not isinstance(body, dict):
            body = {"data": body}

        if response.status_code >= 400:
            raise _explain(body, response.status_code, path)
        return body

    async def paginate(
        self,
        account: Account,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_items: int = 200,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Follow cursors until max_items or the data runs out.

        Guarded on both the cap and a missing cursor. A cursor that repeats or
        goes missing would otherwise spin until the client times out, which is
        how "list everything" tools hang.
        """
        out: list[dict[str, Any]] = []
        after: str | None = None
        seen_cursors: set[str] = set()

        while len(out) < max_items:
            page = await self.call(
                account,
                path,
                {**(params or {}), "limit": min(page_size, max_items - len(out)), "after": after},
            )
            rows = page.get("data") or []
            if not isinstance(rows, list) or not rows:
                break
            out.extend(rows)

            after = ((page.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or after in seen_cursors:
                break
            seen_cursors.add(after)

        return out[:max_items]

    async def wait_ready(self, account: Account, container_id: str) -> None:
        """Block until a media container finishes processing, or explain why not."""
        for _ in range(_POLL_MAX_ATTEMPTS):
            status = await self.call(
                account, f"/{container_id}", {"fields": "status_code,status"}
            )
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                detail = status.get("status") or ""
                raise GraphError(f"Container {container_id} came back {code}. {detail}".strip())
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        waited = int(_POLL_MAX_ATTEMPTS * _POLL_INTERVAL_SECONDS)
        raise GraphError(
            f"Container {container_id} was still processing after {waited}s. "
            "Large videos can take longer. The container is still valid for 24 hours, "
            "so check it with get_container_status and call publish_container with this "
            "id once it is FINISHED."
        )

    async def create_resumable(
        self, account: Account, fields: dict[str, Any]
    ) -> tuple[str, str]:
        """Open a resumable upload session: a container with no media in it yet.

        Meta answers with the container id and the rupload address its bytes go
        to. Only on graph.facebook.com: Meta lists rupload.facebook.com as a
        host for Facebook Login, never for Instagram Login.
        """
        body = await self.call(
            account, f"/{account.user_id}/media", {**fields, "upload_type": "resumable"}, "POST"
        )
        container = body.get("id")
        if not container:
            raise GraphError(f"Instagram opened no upload session: {str(body)[:300]}")
        container = str(container)
        return container, self._upload_url(container, body.get("uri"))

    def _upload_url(self, container_id: str, given: Any) -> str:
        """The address Meta returned, when it is on rupload; otherwise the documented one."""
        if isinstance(given, str):
            try:
                if httpx2.URL(given).host == RUPLOAD_HOST:
                    return given
            except Exception:  # noqa: BLE001 - not a URL, so use the documented address
                pass
        version = self._settings.graph_version
        return f"https://{RUPLOAD_HOST}/ig-api-upload/{version}/{container_id}"

    async def upload_file(
        self, account: Account, upload_url: str, path: Path, size: int
    ) -> dict[str, Any]:
        """Send a local video's bytes to rupload.facebook.com.

        Streamed from disk in chunks, so a 300 MB reel costs no more memory than
        a small one. Meta documents no way to ask an Instagram upload how far it
        got, so a broken connection starts again from the first byte rather than
        guessing an offset, up to _UPLOAD_ATTEMPTS sends in all.
        """
        for attempt in range(1, _UPLOAD_ATTEMPTS + 1):
            try:
                return await self._upload_once(account, upload_url, path, size)
            except GraphError as exc:
                if not exc.retriable or attempt == _UPLOAD_ATTEMPTS:
                    raise
        raise AssertionError("unreachable")

    async def _upload_once(
        self, account: Account, upload_url: str, path: Path, size: int
    ) -> dict[str, Any]:
        async def chunks():
            with path.open("rb") as handle:
                while chunk := handle.read(_UPLOAD_CHUNK_BYTES):
                    yield chunk

        request = self._client.build_request(
            "POST",
            upload_url,
            headers={
                "Authorization": f"OAuth {account.token}",
                "offset": "0",
                "file_size": str(size),
                "Content-Length": str(size),
            },
            content=chunks(),
            # The bytes may take minutes, and Meta's answer can take a while after them.
            timeout=httpx2.Timeout(30.0, read=300.0, write=300.0),
        )
        try:
            response = await self._client.send(request)
        except httpx2.HTTPError as exc:
            raise GraphError(
                f"upload: could not reach {RUPLOAD_HOST}: {exc}", retriable=True
            ) from exc
        return _upload_answer(response)

    async def refresh_token(self, account: Account) -> dict[str, Any]:
        """Extend a long-lived token by another 60 days.

        The two hosts do this differently and neither documents it next to the
        other, which is why every reference server skips it and dies at day 60.
        """
        if account.host == FACEBOOK_HOST:
            raise GraphError(
                "Facebook Page tokens do not expire and cannot be refreshed this way. "
                "If yours stopped working, the underlying user token was revoked: re-run "
                "`instagram-mcp token`."
            )
        try:
            response = await self._client.get(
                f"https://{account.host}/refresh_access_token",
                params={"grant_type": "ig_refresh_token", "access_token": account.token},
            )
        except httpx2.HTTPError as exc:
            raise GraphError(f"Could not reach {account.host}: {exc}") from exc

        body = response.json() if response.content else {}
        if response.status_code >= 400:
            raise _explain(body if isinstance(body, dict) else {}, response.status_code, "refresh")
        return body
