# Working on instagram-mcp

This is the one document for agents. `CLAUDE.md` points here so the two cannot drift.

For agents editing this repository. Not for users. Users read the README.

## What this is

An MCP server for Instagram with three tiers of access behind one tool surface.
The tier model is defined once, in `config.py`, and everything else follows from
it. Read that docstring before changing anything.

## Non-negotiables

**Every anticipated failure inherits from `InstagramError` in `errors.py`.**
The MCP SDK only forwards the message of an exception subclassing its
`ToolError`. Raise a plain `ValueError` or `RuntimeError` from a tool and the
model sees "Error executing tool <name>" with your explanation withheld. This is
silent and easy to reintroduce, so `ruff` will not catch it and a test will.

**Every result goes through `runtime.result()`** so it carries the `source`
field. A model that cannot tell official data from scraped data will present a
private-API guess as an official metric.

**Every field written by another person goes through `safety.frame_rows`.**
Comments, captions, biographies, DMs. No exceptions.

**Writes are on by default.** `IG_READ_ONLY` is the opt-out, and it works by not
registering the write tools at all, through `safety.write_gate`, rather than by
refusing at call time. A model cannot call a tool it cannot see, and cannot
argue with a refusal it never receives. Do not turn this back into a flag that
enables writes: a permission everyone passes permanently is not a permission.

**Writes call `safety.require_write` first**, then `safety.require_confirm` on
the irreversible ones, both before argument validation, so a refusal costs no
network call.

**Local files go through `graph.create_resumable` and `graph.upload_file`.**
Meta's resumable upload sends the bytes to rupload.facebook.com with an
`Authorization: OAuth` header, and documents it for Facebook Login only and for
video only (https://developers.facebook.com/docs/instagram-platform/content-publishing/).
`publish.local_video` and `_needs_facebook_login` refuse the rest before any
call. Do not add an image upload: Meta documents none, and an image container
takes `image_url` alone.

**Never log a Graph URL.** The token travels in its query string. `graph.py`
sets the `httpx2` logger to WARNING and filters tokens out of its records,
because the MCP SDK turns INFO on for the root logger and clients keep server
stderr in log files. A test fails if a token reaches the log at DEBUG.

**Ask each login path only for fields it has.** `account_type` exists on
Instagram Login only; on Facebook Login, Graph refuses the whole call. Use
`own.profile_fields(account)`.

**`instagram-mcp call` goes through the server.** `cli.py` builds the same
server and calls the tool with `server.call_tool`, so the write gate, the
confirmation and the audit log are the ones MCP uses. `--confirm` is the only
way to confirm there. The exit codes are the house contract; `cli.exit_code`
maps each failure, unwrapping only the SDK's own plain `ToolError`, because
`InstagramError` is a `ToolError` too.

**`confirm` goes on irreversible tools only.** Publishing, deleting a comment,
sending a DM. Not on replies, hides, or anything else that is one click to undo.
Confirming everything trains the reflex that makes the confirmation on a real
deletion worthless, which is the failure this is designed to avoid.

**Do not add `follow_user`, `unfollow_user` or `bulk_like`.** They are listed in
`safety.NOT_IMPLEMENTED` with the reason and reported by `unofficial_status`.
This is a positioning decision. If someone opens an issue, point them there.

**No password ever reaches a config file.** `instagram-mcp login` is interactive
and writes a session file with `0o600`. Do not add an `IG_PASSWORD` variable.

## Before changing anything about the SDK surface

The Python MCP SDK went to 2.x and renamed `FastMCP` to `MCPServer`. Do not
write SDK code from memory. Check the installed version:

```bash
uv run python -c "from mcp.server import MCPServer; import inspect; print(inspect.signature(MCPServer.tool))"
```

Structured output needs a concrete return annotation. `-> dict[str, Any]` works,
bare `-> dict` raises at registration time.

## Graph API versions

`DEFAULT_GRAPH_VERSION` in `config.py`. Probe rather than assume, because Meta
does not announce loudly:

```bash
curl -s "https://graph.facebook.com/v27.0/me" | head -c 200
```

A version that does not exist yet returns "Unknown path components: /me". A
version that does returns a token error.

## Tests

```bash
uv sync --all-extras
uv run pytest -q
uv run ruff check .
```

Tests run against `httpx2.MockTransport`. Nothing touches the network, a real
token, or a session file. Keep it that way: a test that needs credentials is a
test nobody will run.

If you add a tool, the count assertion in `tests/test_tools.py` and the count in
the README both need updating. That is deliberate, so the README cannot drift.

## Writing

No em dashes. Short paragraphs. Comments explain why, not what. The About block
in the README is copied verbatim and is never rewritten or paraphrased.
