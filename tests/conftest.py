"""Shared fixtures. Nothing here touches the network or a real session file."""

from __future__ import annotations

import httpx2
import pytest
from mcp.server import MCPServer

from instagram_mcp.config import Account, Settings
from instagram_mcp.graph import GraphClient
from instagram_mcp.runtime import Runtime
from instagram_mcp.store import Store
from instagram_mcp.tools import register_all
from instagram_mcp.unofficial import Unofficial


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        accounts=[
            Account(name="acmem", user_id="1", token="t1"),
            Account(name="acmeai", user_id="2", token="t2"),
        ],
        preferred=["acmem"],
        data_dir=tmp_path,
    )


@pytest.fixture
def recorder():
    """Captures every outbound request so a test can assert on the method."""
    return []


@pytest.fixture
def make_server(settings, recorder):
    """Build a fully registered server over a scripted transport."""

    def build(routes: dict[str, object], *, read_only: bool = False, unofficial: bool = False):
        # Set before register_all: read-only removes the write tools at
        # registration, so flipping it afterwards would change nothing.
        settings.read_only = read_only
        settings.unofficial = unofficial

        def handler(request: httpx2.Request) -> httpx2.Response:
            recorder.append(request)
            for suffix, body in routes.items():
                if request.url.path.endswith(suffix):
                    if isinstance(body, int):
                        return httpx2.Response(
                            body, json={"error": {"message": "no", "code": body}}
                        )
                    return httpx2.Response(200, json=body)
            return httpx2.Response(200, json={})

        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        runtime = Runtime(
            settings=settings,
            graph=GraphClient(settings, client=client),
            store=Store(settings.db_path),
            unofficial=Unofficial(settings),
        )
        server = MCPServer(name="test", version="0")
        register_all(server, runtime)
        return server, runtime

    return build
