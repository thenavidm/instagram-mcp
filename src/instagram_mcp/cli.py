"""Every tool as a shell command: `instagram-mcp call <tool>`.

A scheduler, a script or cron runs a tool the way an MCP client would, through
the same server, the same tools and the same confirmation, and branches on the
exit code rather than reading the message:

    0 ok, 1 unexpected, 2 usage or a refused write, 3 not found, 4 auth,
    5 API, 7 rate limited, 10 nothing configured.

Arguments come as `--name value` flags, read against the tool's own schema, or
as one JSON object with `--args`, or both, the flags winning. `--confirm` is
the only way to confirm from here: `confirm` inside the arguments is refused,
and `--agent` never adds it, because the agent that sets `--agent` is exactly
the caller a confirmation exists for.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from .config import ConfigError, Settings
from .errors import InstagramError
from .graph import GraphError
from .safety import ConfirmationRequired, RateLimited, WriteDenied
from .server import build_server
from .unofficial import UnofficialUnavailable

OK, UNEXPECTED, USAGE, NOT_FOUND, AUTH, API, RATE_LIMITED, NOT_CONFIGURED = 0, 1, 2, 3, 4, 5, 7, 10

_EXIT_WORDS = {
    UNEXPECTED: "unexpected",
    USAGE: "usage",
    NOT_FOUND: "not_found",
    AUTH: "auth",
    API: "api",
    RATE_LIMITED: "rate_limited",
    NOT_CONFIGURED: "not_configured",
}

_SLOW_DOWN = {4, 17, 32, 613}


class UsageError(InstagramError):
    """The command line itself is wrong: an unknown tool, flag or value."""


def _unwrap(error: BaseException) -> BaseException:
    """The real failure inside the SDK's wrapper. InstagramError is a ToolError too, so
    only the SDK's own, plain ToolError is unwrapped."""
    if type(error) is ToolError and error.__cause__ is not None:
        return error.__cause__
    return error


def exit_code(error: BaseException) -> tuple[int, str]:
    """The exit code for a failure, and the word for it in the JSON error.

    The MCP SDK wraps every tool failure in a ToolError with the real one as its
    cause, so the cause decides. Meta answers most failures with a 400, so its
    own error code is read before the HTTP status.
    """
    cause = _unwrap(error)
    if isinstance(cause, (ConfirmationRequired, WriteDenied)):
        return USAGE, "refused"
    if isinstance(cause, RateLimited):
        return RATE_LIMITED, _EXIT_WORDS[RATE_LIMITED]
    if isinstance(cause, ConfigError):
        message = str(cause)
        if message.startswith("No connected account matches"):
            return NOT_FOUND, _EXIT_WORDS[NOT_FOUND]
        if message.startswith(("No Instagram account configured", "No account is connected")):
            return NOT_CONFIGURED, _EXIT_WORDS[NOT_CONFIGURED]
        if "does not exist" in message:
            return NOT_CONFIGURED, _EXIT_WORDS[NOT_CONFIGURED]
        return USAGE, _EXIT_WORDS[USAGE]
    if isinstance(cause, UnofficialUnavailable):
        if str(cause).startswith("Instagram refused"):
            return API, _EXIT_WORDS[API]
        return NOT_CONFIGURED, _EXIT_WORDS[NOT_CONFIGURED]
    if isinstance(cause, GraphError):
        if cause.code in _SLOW_DOWN or cause.status == 429:
            return RATE_LIMITED, _EXIT_WORDS[RATE_LIMITED]
        if cause.code in (190, 10, 200) or cause.status in (401, 403):
            return AUTH, _EXIT_WORDS[AUTH]
        if cause.code == 100 or cause.status in (400, 422):
            return USAGE, _EXIT_WORDS[USAGE]
        if cause.status in (404, 410):
            return NOT_FOUND, _EXIT_WORDS[NOT_FOUND]
        return API, _EXIT_WORDS[API]
    if isinstance(cause, (ValidationError, InstagramError)):
        return USAGE, _EXIT_WORDS[USAGE]
    return UNEXPECTED, _EXIT_WORDS[UNEXPECTED]


def _clean(error: BaseException) -> str:
    """The message without the SDK's wrapper, in the words a command line uses."""
    cause = _unwrap(error)
    if isinstance(cause, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or 'arguments'}: {issue['msg']}"
            for issue in cause.errors()
        )
    message = str(error)
    if message.startswith("Error executing tool ") and ": " in message:
        message = message.split(": ", 1)[1]
    if isinstance(cause, ConfirmationRequired):
        # The tool speaks to a model; here a person or a scheduler types a flag.
        message = message.replace("with confirm=true", "with --confirm")
    return message.strip()


def _type_of(prop: dict[str, Any]) -> str:
    if "type" in prop:
        return str(prop["type"])
    for option in prop.get("anyOf") or []:
        if option.get("type") and option["type"] != "null":
            return str(option["type"])
    return "string"


def _coerce(tool: str, name: str, kind: str, raw: str) -> Any:
    try:
        if kind == "integer":
            return int(raw)
        if kind == "number":
            return float(raw)
        if kind == "boolean":
            lowered = raw.strip().lower()
            if lowered in ("true", "1", "yes"):
                return True
            if lowered in ("false", "0", "no"):
                return False
            raise ValueError(raw)
        if kind == "array":
            if raw.lstrip().startswith("["):
                value = json.loads(raw)
                if not isinstance(value, list):
                    raise ValueError(raw)
                return value
            return [raw]
        if kind == "object":
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError(raw)
            return value
    except ValueError as exc:
        article = "an" if kind[0] in "aeiou" else "a"
        raise UsageError(
            f"{tool} --{name} takes {article} {kind}, and {raw!r} is not one."
        ) from exc
    return raw


def parse_flags(tool: str, tokens: list[str], schema: dict[str, Any]) -> dict[str, Any]:
    """Read `--name value` flags against a tool's input schema.

    Dashes and underscores are the same. A boolean flag alone means true. An
    array takes a JSON list, or the flag repeated once per item.
    """
    props: dict[str, Any] = schema.get("properties") or {}
    out: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--") or token == "--":
            raise UsageError(
                f"Unexpected {token!r}. Arguments are --name value, or --args with a JSON object."
            )
        flag, has_value, value = token[2:].partition("=")
        key = flag.replace("-", "_")
        if key not in props:
            takes = ", ".join(f"--{p.replace('_', '-')}" for p in props) or "nothing"
            raise UsageError(f"{tool} has no --{flag}. It takes: {takes}.")
        kind = _type_of(props[key])
        if kind == "boolean" and not has_value:
            following = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
            if following in ("true", "false"):
                out[key] = following == "true"
                i += 2
            else:
                out[key] = True
                i += 1
            continue
        if not has_value:
            if i + 1 >= len(tokens):
                raise UsageError(f"--{flag} needs a value.")
            value = tokens[i + 1]
            i += 1
        parsed = _coerce(tool, flag, kind, value)
        if kind == "array" and key in out:
            out[key] = [*out[key], *parsed]
        else:
            out[key] = parsed
        i += 1
    return out


def _emit(data: Any, agent: bool) -> None:
    text = json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")) if agent else (
        json.dumps(data, ensure_ascii=False, default=str, indent=2)
    )
    sys.stdout.write(text + "\n")


def _fail(error: BaseException, agent: bool) -> int:
    code, word = exit_code(error)
    payload = {"error": _clean(error), "code": word}
    text = json.dumps(payload, ensure_ascii=False) if agent else json.dumps(
        payload, ensure_ascii=False, indent=2
    )
    sys.stderr.write(text + "\n")
    return code


def _risk(tool: Any) -> str:
    hints = tool.annotations
    read_only = getattr(hints, "read_only_hint", None) or getattr(hints, "readOnlyHint", None)
    return "read" if read_only else "write"


async def list_tools(settings: Settings, *, agent: bool) -> int:
    """Every tool this server would list, with its risk, confirmation and arguments."""
    server, runtime = build_server(settings)
    try:
        tools = await server.list_tools()
    finally:
        await runtime.aclose()

    rows = []
    for tool in tools:
        props = list((tool.input_schema or {}).get("properties") or {})
        rows.append(
            {
                "tool": tool.name,
                "risk": _risk(tool),
                "requires_confirm": "confirm" in props,
                "args": [p for p in props if p != "confirm"],
                "description": (tool.description or "").split(". ")[0].rstrip("."),
            }
        )
    if agent:
        _emit({"count": len(rows), "tools": rows}, agent=True)
        return OK

    width = max(len(r["tool"]) for r in rows)
    lines = [f"instagram-mcp {len(rows)} tools. Run one with `instagram-mcp call <tool>`.", ""]
    for r in rows:
        mark = "!" if r["requires_confirm"] else "*" if r["risk"] == "write" else " "
        lines.append(f"  {mark} {r['tool'].ljust(width)}  {r['description']}")
    lines += ["", "  * writes    ! needs --confirm", ""]
    sys.stdout.write("\n".join(lines))
    return OK


async def call_tool(
    settings: Settings,
    tool: str,
    tokens: list[str],
    *,
    args_json: str | None,
    account: str | None,
    confirm: bool,
    agent: bool,
) -> int:
    """Run one tool and print its JSON result, or a JSON error, with the house exit code."""
    name = tool.replace("-", "_")
    server, runtime = build_server(settings)
    try:
        tools = {t.name: t for t in await server.list_tools()}
        if name not in tools:
            hint = (
                " Write tools are not registered while IG_READ_ONLY is set."
                if settings.read_only
                else " Run `instagram-mcp tools` for the list."
            )
            raise UsageError(f"No tool named {tool}.{hint}")
        schema = tools[name].input_schema or {}
        props = schema.get("properties") or {}

        arguments: dict[str, Any] = {}
        if args_json:
            try:
                arguments = json.loads(args_json)
            except json.JSONDecodeError as exc:
                raise UsageError(f"--args is not valid JSON: {exc}") from exc
            if not isinstance(arguments, dict):
                raise UsageError("--args must be a JSON object of argument names to values.")
        arguments.update(parse_flags(name, tokens, schema))

        if "confirm" in arguments:
            raise UsageError(
                "Confirm with --confirm, not with confirm in the arguments, and only when "
                "this exact action was asked for."
            )
        if account:
            if "account" not in props:
                raise UsageError(f"{name} does not take an account.")
            arguments["account"] = account
        # Tools with nothing to confirm ignore --confirm, so a scheduler can pass it
        # to every command the same way.
        if confirm and "confirm" in props:
            arguments["confirm"] = True

        result = await server.call_tool(name, arguments)
        data = getattr(result, "structured_content", None)
        if data is None:
            content = getattr(result, "content", None) or []
            data = {"content": [getattr(block, "text", str(block)) for block in content]}
        _emit(data, agent)
        return OK
    except Exception as error:  # noqa: BLE001 - every failure becomes an exit code
        return _fail(error, agent)
    finally:
        await runtime.aclose()
