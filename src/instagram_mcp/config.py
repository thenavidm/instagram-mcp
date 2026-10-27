"""Runtime settings, account resolution, and the tier model.

Instagram has three ways in and they are not variants of each other. Everything
in this package is organised around that fact, so it is stated once, here.

    Tier.INSTAGRAM_LOGIN   graph.instagram.com, Instagram Login.
                           Reaches only accounts you own. Zero ban risk.

    Tier.FACEBOOK_LOGIN    graph.facebook.com, a Page-linked token.
                           Everything above, plus public data about any other
                           Business or Creator account through business_discovery,
                           plus hashtag search. Still official, still zero risk,
                           still runs anywhere.

    Tier.UNOFFICIAL        a private-API session held by instagrapi.
                           Everything a logged-in phone can see. Stateful, so it
                           only runs on a machine you control, and it can get the
                           account restricted.

Every tool result carries the tier that answered it, so the model knows whether
it is reading official data or scraped data and the caller knows what risk was
taken to get it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .errors import InstagramError

# Newest version live on graph.facebook.com as of 2026-08-31. Probed, not assumed:
# v26.0 answers, v27.0 is read as a node id rather than a version.
DEFAULT_GRAPH_VERSION = "v26.0"

INSTAGRAM_HOST = "graph.instagram.com"
FACEBOOK_HOST = "graph.facebook.com"


class Tier(StrEnum):
    INSTAGRAM_LOGIN = "instagram_login"
    FACEBOOK_LOGIN = "facebook_login"
    UNOFFICIAL = "unofficial"


class ConfigError(InstagramError):
    """Something about the configuration is wrong and the fix is the user's."""


@dataclass(frozen=True)
class Account:
    """One connected Instagram account.

    `user_id` means different things per tier and that is Meta's doing, not ours.
    On Instagram Login it is the Instagram user id the token was minted for. On
    Facebook Login it is the Instagram Business Account id hanging off a Page.
    """

    name: str
    user_id: str
    token: str
    host: str = INSTAGRAM_HOST

    @property
    def tier(self) -> Tier:
        return Tier.FACEBOOK_LOGIN if self.host == FACEBOOK_HOST else Tier.INSTAGRAM_LOGIN

    def redacted(self) -> dict[str, str]:
        return {"account": self.name, "id": self.user_id, "tier": self.tier.value}


@dataclass
class Settings:
    accounts: list[Account] = field(default_factory=list)
    preferred: list[str] = field(default_factory=list)
    read_only: bool = False
    unofficial: bool = False
    audit_log: Path | None = None
    graph_version: str = DEFAULT_GRAPH_VERSION
    data_dir: Path = field(default_factory=lambda: _default_data_dir())
    # Pacing for the unofficial tier only. The official tiers are rate limited by
    # Meta and need no help from us.
    pace_seconds: tuple[float, float] = (2.0, 5.0)
    hourly_cap: int = 120

    @property
    def session_path(self) -> Path:
        return self.data_dir / "session.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "instagram-mcp.db"

    @property
    def audit_path(self) -> Path:
        return self.audit_log or (self.data_dir / "audit.log")

    def has_facebook_login(self) -> bool:
        return any(a.host == FACEBOOK_HOST for a in self.accounts)


def _default_data_dir() -> Path:
    """Where the session, the local store and the audit log live.

    XDG on Linux, Application Support on macOS, LOCALAPPDATA on Windows. Never
    the working directory: an MCP client starts the server from wherever it
    happens to be, and dropping a session file there would scatter credentials
    across a user's disk.
    """
    if override := os.environ.get("IG_MCP_DATA_DIR"):
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or "~/AppData/Local"
        return Path(base).expanduser() / "instagram-mcp"
    if os.uname().sysname == "Darwin":
        return Path("~/Library/Application Support/instagram-mcp").expanduser()
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "instagram-mcp"


def _accounts_from_file(path: Path) -> list[Account]:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"IG_ACCOUNTS_FILE points at {path}, which does not exist.") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, list):
        raise ConfigError(f"{path} must contain a JSON array of accounts.")

    out: list[Account] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path} entry {i} is not an object.")
        missing = [k for k in ("user_id", "access_token") if not entry.get(k)]
        if missing:
            raise ConfigError(f"{path} entry {i} is missing {', '.join(missing)}.")
        host = entry.get("host") or INSTAGRAM_HOST
        if host not in (INSTAGRAM_HOST, FACEBOOK_HOST):
            raise ConfigError(f"{path} entry {i} has host {host!r}, which is not a Graph host.")
        out.append(
            Account(
                name=entry.get("account_name") or entry.get("username") or f"account-{i + 1}",
                user_id=str(entry["user_id"]),
                token=str(entry["access_token"]),
                host=host,
            )
        )
    return out


def _flag(env: dict[str, str], name: str) -> bool:
    """Read a boolean env var. Present and not an explicit off value means on."""
    raw = (env.get(name) or "").strip().lower()
    return raw not in ("", "0", "false", "no", "off")


def load_settings(
    *,
    unofficial: bool = False,
    env: dict[str, str] | None = None,
) -> Settings:
    """Build settings from the environment.

    Writes are on. IG_READ_ONLY=1 takes them away, and it does so by removing
    the tools from the list rather than by failing the call, because a model
    cannot misuse a tool it cannot see.

    Two shapes are accepted. A single account through IG_ACCESS_TOKEN and
    IG_USER_ID, which is what the reference servers do and what most people
    want, or IG_ACCOUNTS_FILE pointing at a JSON array for multi-account.
    """
    env = dict(os.environ if env is None else env)

    accounts: list[Account] = []
    if path := env.get("IG_ACCOUNTS_FILE"):
        accounts = _accounts_from_file(Path(path).expanduser())
    elif token := env.get("IG_ACCESS_TOKEN"):
        host = env.get("IG_HOST") or INSTAGRAM_HOST
        if host not in (INSTAGRAM_HOST, FACEBOOK_HOST):
            raise ConfigError(
                f"IG_HOST is {host!r}. It must be {INSTAGRAM_HOST} or {FACEBOOK_HOST}."
            )
        user_id = env.get("IG_USER_ID") or "me"
        accounts = [
            Account(
                name=env.get("IG_ACCOUNT_NAME") or "default",
                user_id=user_id,
                token=token,
                host=host,
            )
        ]

    preferred = [p.strip().lstrip("@").lower() for p in (env.get("IG_PREFERRED") or "").split(",")]

    settings = Settings(
        accounts=accounts,
        preferred=[p for p in preferred if p],
        read_only=_flag(env, "IG_READ_ONLY"),
        unofficial=unofficial or _flag(env, "IG_UNOFFICIAL"),
        audit_log=Path(p).expanduser() if (p := env.get("IG_AUDIT_LOG")) else None,
        graph_version=env.get("IG_GRAPH_VERSION") or DEFAULT_GRAPH_VERSION,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def pick(settings: Settings, name: str | None = None, *, host: str | None = None) -> Account:
    """Resolve which account a call acts on.

    Ported from the HQ connector, including the reason it looks odd. When the
    caller names nobody, the preference list decides, and an exact match beats a
    prefix match. Without that ordering a name like "Acme Media" wins the prefix
    race against "acme m" and an unnamed post lands on the wrong account.
    """
    pool = [a for a in settings.accounts if host is None or a.host == host]
    if not pool:
        if host and settings.accounts:
            raise ConfigError(
                "No account is connected on the "
                f"{'Facebook Login' if host == FACEBOOK_HOST else 'Instagram Login'} path. "
                "This tool needs one. See the Tiers section of the README."
            )
        raise ConfigError(
            "No Instagram account configured. Set IG_ACCESS_TOKEN and IG_USER_ID, "
            "or IG_ACCOUNTS_FILE. Run `instagram-mcp doctor` to check."
        )

    if name:
        want = name.strip().lstrip("@").lower()
        for account in pool:
            if account.name.lower() == want:
                return account
        known = ", ".join(a.name for a in pool) or "none"
        raise ConfigError(f'No connected account matches "{name}". Connected: {known}')

    for want in settings.preferred:
        for account in pool:
            if account.name.lower() == want:
                return account
        for account in pool:
            if account.name.lower().startswith(want):
                return account
    return pool[0]
