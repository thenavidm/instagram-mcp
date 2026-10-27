# Instagram MCP changelog

| Component | Version | Last Updated |
|-----------|---------|--------------|
| thenavidm-instagram-mcp | 0.5.0 | 2026-10-27 |
| Graph API | v26.0 | 2026-10-27 |

---

## 0.5.0, 2026-10-27

Songs from Instagram's own library on a reel. 47 tools, from 46. From Meta's [Audio API](https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/), read on 2026-10-27, and checked against real accounts with unpublished containers only.

- **`search_audio`** searches the licensed library through `GET /ig_audio`: with no query it returns what is trending now (25 tracks), with one any song by name; `audio_type: original_sound` searches the sounds people made on their own reels. Each track comes with its `audio_id`, title, artist, length and a link to hear it on Instagram. Facebook Login only: Meta does not answer the Audio API on Instagram Login.
- **`audio_id` on `publish_reel` and `create_container`** attaches the track as `audio_configuration`, with `audio_volume` and `video_volume` from 1 to 100. A reel container with a trending track and one with an original sound both came back `FINISHED`. Instagram attaches the song itself, so the reel names it and counts toward it, as one added in the app does.
- **Reels only.** A carousel or a photo container refuses it in Meta's words: "audio_configuration is not a valid parameter for the media type, CAROUSEL_V2". The tools refuse it there first.

## 0.4.0, 2026-10-27

Every tool as a command, and the Facebook Login path working end to end. Checked against five real Facebook Login accounts with reads only: `doctor` passes for all five, and `list_accounts`, `get_media` and `get_publishing_limit` answer for each.

- **`instagram-mcp call <tool>`** runs any of the 46 tools from a shell, through the same server, the same write gate, confirmation and audit log as MCP, and prints the tool's JSON result. Arguments are `--name value` flags read against the tool's schema, or one JSON object with `--args`, the flags winning. `--confirm` is the only way to confirm: `confirm` inside the arguments is refused and `--agent` never adds it, and a tool with nothing to confirm ignores it, so a scheduler can pass `--confirm --agent` to every command. Exit codes follow the house contract: 0 ok, 1 unexpected, 2 usage or a refused write, 3 not found, 4 auth, 5 API, 7 rate limited, 10 nothing configured; Meta's own error code decides before the HTTP status, since it answers most failures with a 400. A failure is one JSON object on stderr. `instagram-mcp tools` lists every tool with its risk, whether it needs `--confirm`, and its arguments, or as JSON with `--agent`.
- **`doctor`, `list_accounts` and `whoami` work on Facebook Login.** They asked every account for `account_type`, which exists only on Instagram Login, and Graph refused the whole call on the Facebook Login path with "(#100) Tried accessing nonexisting field (account_type)", so every Facebook Login account failed. Each path is now asked only for the fields it has, and a Facebook Login account reports as a Business or Creator account, the only kind that path reaches. `doctor` also prints each account's post count.
- **Tokens no longer reach the log.** httpx2 logged every request URL at INFO, the MCP SDK turns INFO on, and a Graph URL carries the access token in its query string, so a running server wrote each token it used to stderr, which MCP clients keep in their log files. Those lines are off, and a filter strips tokens from them even at DEBUG. If you ran 0.3.0 or earlier as an MCP server, the tokens it used may be in your client's MCP logs: delete those logs, or rotate the tokens.

### Upgrading

Nothing changes over MCP. `list_accounts` reports `type: BUSINESS_OR_CREATOR` for a Facebook Login account, where it failed before.

## 0.3.0, 2026-10-27

Publishing from a file on this machine. 46 tools, from 45. Every Meta rule below is from the [content publishing guide](https://developers.facebook.com/docs/instagram-platform/content-publishing/) and the [IG User Media reference](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media), read on 2026-10-27.

- **`file` on every publishing tool.** `post`, `create_container`, `publish_reel` and `publish_story` take the path of a video on this machine, next to `image_url` and `video_url`. It goes up with Meta's resumable upload: a container opened with `upload_type=resumable`, then the bytes POSTed to `rupload.facebook.com` with `Authorization: OAuth`, `offset` and `file_size` headers, streamed from disk. A dropped connection is sent again from the first byte, up to three sends, since Meta documents no way to ask an Instagram upload how far it got. A rejected upload says Meta's `debug_info` type and message.
- **`post_carousel` takes `items`**, image and video URLs and video paths in order, and up to 20 of them, Meta's current limit, where it took 2 to 10 images. Video children are waited on before the carousel is made. `image_urls` still works.
- **What Meta does not allow is refused before any call, with the reason.** An image file: Meta's resumable upload takes video only and an image container takes `image_url` alone, so host the JPEG. A file on an Instagram Login account: Meta lists the upload host for Facebook Login only, "Only for apps that have implemented Facebook Login for Business". A reel over 300 MB or a story video over 100 MB. A carousel checks every file before it creates its first container, so a bad item leaves nothing behind.
- **`get_container_status`** says where a staged container stands, `IN_PROGRESS`, `FINISHED`, `PUBLISHED`, `ERROR` or `EXPIRED`, in Meta's own words, and whether it can be published. `create_container` now returns `expires_at`, 24 hours on, and the media type it staged; a video file stages as a reel unless `media_type` is `STORIES` or `is_carousel_item` is set.
- **`--version` tells the truth.** It said 0.1.0 while the package was 0.2.0; a test now keeps the two equal.
- **Docs.** The README covers publishing a file, the two refusals and Meta's limits with a link for each: 100 API-published posts per 24 hours on the rate limit section and 50 on the carousel section of the same page, with `get_publishing_limit` reading the one Meta applies; containers expiring after 24 hours and 400 a day; carousels up to 20; the reel, story and image specs. Section 10 no longer says a local file cannot work. `SKILL.md` says there is no scheduling and how to stage and publish inside the 24 hours.

Instagram still has no scheduling, and this server still does not pretend to: publish now, or stage and publish within 24 hours.

Not checked against a real account yet: the resumable session, the upload and the carousel of video children, which the tests drive against a scripted Meta built from the guide's examples.

### Upgrading

`post`, `create_container` and `publish_story` take exactly one of `image_url`, `video_url` and `file`, where passing both URLs was let through to Meta. `publish_reel`'s `video_url` is optional, with `file` as the alternative. A carousel of 11 to 20 is now sent to Meta rather than refused here.

## 0.2.0

Breaking, twice.

**Writes are on by default.** The `--allow-write` flag is gone. A permission that
has to be passed on every install is one an operator sets permanently and stops
reading, which is worse than none because it looks like protection. The
irreversible actions take `confirm=true` instead: `post`, `post_carousel`,
`publish_reel`, `publish_story`, `publish_container`, `delete_comment`,
`send_dm`, `private_reply_to_comment`, `unofficial_send_dm`. Replies and hides do
not, because confirming everything is what makes a real confirmation worthless.

`IG_READ_ONLY=1` replaces the flag and works by not registering the write tools
at all, so a model cannot see them. `IG_UNOFFICIAL` and `IG_AUDIT_LOG` move to
the environment for the same reason: a client config is already a JSON env block.

**Renamed to `thenavidm-instagram-mcp`.** The short name on PyPI belongs to
somebody else, so the previous install line fetched a stranger's code. The
installed command is still `instagram-mcp`.

Also: `SKILL.md` ships inside the wheel, `SECURITY.md`, a digest-pinned
Dockerfile and a systemd install script. `AGENTS.md` is now the agent document
and `CLAUDE.md` points at it. README rebuilt so install comes before the section
that hands you the credential it needs.

## 0.1.0

First release.

### What it does

Gives any AI agent access to Instagram across three tiers of access, behind one
tool surface, with every result labelled by the tier that answered it.

45 tools. stdio and streamable HTTP, so it works in Claude Code, Claude Desktop,
Cursor, VS Code, Windsurf, Zed, Cline, Codex CLI, Gemini CLI, and over HTTP from
claude.ai and ChatGPT.

### The three tiers

| Tier | Reaches | Risk |
|---|---|---|
| Instagram Login | accounts you own | none |
| Facebook Login | the above, plus public data on any Business or Creator account, plus hashtags | none |
| Unofficial | any public account, the real inbox, search | account restriction |

The Facebook Login tier is the one worth knowing about. `discover_account`
and `compare_accounts` answer competitor questions officially, for free, with no
scraping.

### Safety

- Read-only unless started with `--allow-write`.
- The unofficial tier needs `--unofficial`, and its library is not installed by
  default, so a plain install cannot reach it at all.
- Comments, captions, biographies and DMs are wrapped in an untrusted-content
  fence before they reach the model.
- Every write appends to an audit log the model cannot read or edit.
- The unofficial tier paces itself and stops at a local hourly ceiling.
- `follow_user`, `unfollow_user` and `bulk_like` are deliberately absent.
- `instagram-mcp login` never stores a password, and no password ever goes into
  an MCP client config.

### Notable

- Multiple accounts on one server, with a preference order where an exact name
  match beats a prefix match.
- Automatic token refresh, so the integration does not die at day 60.
- A local SQLite store, so `growth_history` and `post_movement` can answer what
  changed rather than only what is true right now.
- `instagram-mcp doctor` tests every token, names which insight metrics your
  account still answers, and checks whether `business_discovery` works on your
  app.
- `instagram-mcp token` does the short-lived to long-lived exchange and prints a
  ready-to-paste accounts file.

### Verified at build time

- Graph API v26.0 is the newest live version. v27.0 does not resolve.
- The Python MCP SDK is 2.1.1, where `FastMCP` was renamed to `MCPServer` and
  `mcp.server.fastmcp` no longer exists.
- Only exceptions subclassing the SDK's `ToolError` have their message
  forwarded to the model. Everything else is replaced with a generic string, so
  every anticipated failure here inherits from it.
- instagrapi 2.18.18 is current and actively maintained.

### Not verified

- `business_discovery` was not tested against a live Page-linked token. The code
  path is written and `instagram-mcp doctor` tests it in one command. Run that
  before relying on the Facebook Login tier.
- Insight metric names change between Graph versions. `doctor` reports the
  working set per account, and every insights tool takes a `metrics` override.
