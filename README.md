<img src="https://cdn.navid.media/connectors/instagram-icon.png" alt="Instagram" width="88">

# Instagram MCP

[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)
[![YouTube](https://img.shields.io/badge/YouTube-@thenavidm-red?logo=youtube&logoColor=white)](https://youtube.com/@thenavidm?sub_confirmation=1)
[![X](https://img.shields.io/badge/X-@thenavidm-black?logo=x)](https://x.com/thenavidm)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-thenavidm-0A66C2?logo=linkedin&logoColor=white)](https://linkedin.com/in/thenavidm)

Instagram MCP server for Claude Code and AI agents. Read your own accounts, research any Business or Creator account on the platform, publish, and answer comments.

Three tiers of access behind one tool surface, and every answer tells you which tier it came from.

Built by [Navid Moazzez](https://navid.me?utm_source=github&utm_medium=readme&utm_campaign=instagram-mcp).

<img src="https://cdn.navid.media/repos/instagram-mcp.gif?v=1" alt="Claude Code using the Instagram MCP server" width="520">

## Contents

| | Section | |
|---|---|---|
| 1 | [What you can ask it](#1-what-you-can-ask-it-) | Real prompts, not features |
| 2 | [Quick install](#2-quick-install-) | The package, no account needed |
| 3 | [Create your Meta app](#3-create-your-meta-app-) | Every click. This is the part people give up on |
| 4 | [Get your token](#4-get-your-token-) | One command does the exchange |
| 5 | [Connect your client](#5-connect-your-client-) | Claude Code, Desktop, Cursor, the rest |
| 6 | [Check it worked](#6-check-it-worked-) | `doctor`, and the two things that actually fail |
| 7 | [Tools](#7-tools-) | All 46, by what they reach |
| 8 | [The three tiers](#8-the-three-tiers-) | What each one can see |
| 9 | [Multiple accounts](#9-multiple-accounts-) | One server, several logins |
| 10 | [Notes and gotchas](#10-notes-and-gotchas-) | Quotas, windows, and silent failures |
| 11 | [Troubleshooting](#11-troubleshooting-) | Symptom to cause |
| 12 | [FAQ](#12-faq-) | Including what an MCP server is |

## 1. What you can ask it 💬

- Which of my posts from the last month is still gaining likes?
- How many followers did I gain this week, and which post caused it?
- Compare @a, @b and @c. Who has the best engagement rate, and what do they post?
- What is working on #aitools right now?
- Read every comment from my last ten posts and tell me what people keep asking.
- Draft a reply to each comment that deserves one, in my voice. Do not post them.
- Stage this carousel as a container so I can look at it before it goes live.
- How many of today's 100 API posts have I used?

The first two are the point. Instagram will tell you your follower count right
now. It will not tell you what it was on Monday. This server remembers what it
reads, so it can answer the version of the question people actually have.

## 2. Quick install ⚡

> [!NOTE]
> **Not on PyPI yet.** Install it from a checkout until it is published. The
> command below is what it will become, and the rest of this README already
> works against a local install.

Python 3.11 or newer.

```bash
git clone https://github.com/thenavidm/instagram-mcp
uv tool install ./instagram-mcp
```

No `uv` yet? It is a Python package manager, one command to install:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Check it:

```bash
instagram-mcp --version
```

Once published:

```bash
uv tool install thenavidm-instagram-mcp
```

The package name and the command differ on purpose. The short name on PyPI
belongs to somebody else, so installing that would fetch code that is not this.
The installed command is `instagram-mcp` either way.

For the unofficial tier as well, read [section 8](#8-the-three-tiers-) first,
then add the extra:

```bash
uv tool install "./instagram-mcp[unofficial]"
```

## 3. Create your Meta app 🔑

> [!TIP]
> **One app covers Facebook, Instagram and Threads.**
>
> Use cases are ticked in a list, and you can tick several. If you plan to
> use more than one of these, do it now rather than making three apps and
> managing three sets of credentials.
>
> | Use case | For | Server |
> |---|---|---|
> | Manage everything on your Page | Facebook Pages | [facebook-mcp](https://github.com/thenavidm/facebook-mcp) |
> | Manage messaging and content on Instagram | Instagram | this one |
> | Access Threads API | Threads | [threads-mcp](https://github.com/thenavidm/threads-mcp) |
>
> Incompatible combinations grey out. If an option will not tick, it
> conflicts with something already selected.

Instagram's API does not hand out tokens directly. You create an app in Meta's
developer dashboard, and the app issues the token. It is free and takes about
ten minutes.

**Before you start:** your Instagram account must be a **Business** or
**Creator** account, not a personal one. Instagram's API does not work on
personal accounts at all, on any tier except the unofficial one. Switch in the
Instagram app under Settings, then Account type and tools.

### Step 1: create the app

1. Go to
   [developers.facebook.com/apps/creation](https://developers.facebook.com/apps/creation/)
   and log in.
2. **App details.** Enter your app's name and a contact email address, then
   **Next**. The name is not shown to anyone but you at this stage.
3. **Use cases.** This screen is a list of what the app is allowed to do.
   Tick **Manage messaging and content on Instagram**, then **Next**.

   That one name covers reading posts, insights, comments, DMs and publishing.
   There is no separate "Instagram API" option, which is what people look for
   and do not find.

   The other names on this screen belong to different products:

   | Use case | What it is for |
   |---|---|
   | **Manage messaging and content on Instagram** | this server, tick it |
   | **Access Threads API** | Threads, see below |
   | **Manage everything on your Page** | Facebook Pages |

   You can tick more than one. Incompatible combinations are greyed out, so if
   an option will not tick, it conflicts with something already selected.

> [!IMPORTANT]
> **Older guides tell you to pick "Other" and then choose an app type of
> "Business". Those options are not on this screen.**
>
> Meta reorganized app creation around use cases, so instead of picking a
> generic type and adding a product afterwards, you pick the thing you want to
> do and Meta adds the products for you. Almost every Instagram tutorial online
> still describes the old flow.
>
> If you are looking for "Other", you are on the right screen. Tick **Manage
> messaging and content on Instagram** instead. There is no app type step
> after it.

> [!TIP]
> **You do not need a callback or redirect URL for this server.**
>
> A redirect URL is for a hosted login flow, where other people sign into
> *your* app and get sent back to *your* website. That is not what is
> happening here. You are generating a token for your own account, from your
> own dashboard, and the server runs on your machine.
>
> The Facebook Login setup does not ask for one at all. The Instagram Login
> setup has a **Business login settings** section with OAuth redirect URIs,
> deauthorization and data deletion URLs. You can leave those alone unless you
> are building a website other people log into.
>
> If a guide tells you to host a callback URL to use an MCP server, it is
> describing a different job.
4. **Business.** Choose **A verified business portfolio**, **An unverified
   business portfolio**, or **I don't want to connect a business portfolio
   yet**. The last option is fine to start with.
5. **Requirements.** Review whatever it lists, then **Next**.
6. **Overview.** Check the details, then **Go to dashboard**.

### Step 2: add the permissions

1. Click **Dashboard** in the left menu.
2. Select the use case to customise.
3. Click **Add all required permissions**.

You can also reach these under **Permissions and features** in the left menu, to
add or remove individual ones.

### Step 3: pick your login path

Two setups, and they reach different things. This is the decision that matters
most, so read the table in [section 8](#8-the-three-tiers-) before choosing.

**Instagram Login** is the simpler one. In the left menu, **API setup with
Instagram login**. It reaches only accounts you own.

| Permission | For |
|---|---|
| `instagram_business_basic` | reading the account |
| `instagram_business_content_publish` | publishing |
| `instagram_business_manage_comments` | comments |
| `instagram_business_manage_messages` | DMs |

**Facebook Login** reaches more. It is the only path that gets
`discover_account` and the hashtag tools, and the Page tokens it returns do not
expire. It needs your Instagram account linked to a Facebook Page.

| Permission | For |
|---|---|
| `instagram_basic` | reading the account |
| `instagram_content_publish` | publishing |
| `instagram_manage_comments` | comments |
| `instagram_manage_insights` | reach, saves, impressions |
| `instagram_manage_messages` | DMs |
| `pages_show_list` | finding the Page |
| `pages_read_engagement` | reading the linked Page |
| `business_management` | required by Meta for the messaging and content setups |

### Step 4: generate a token

In the use case's **Generate access tokens** section, click **Add account** and
approve the dialog. Meta returns a short-lived token.

For the Facebook Login path you can also use
[Graph API Explorer](https://developers.facebook.com/tools/explorer): select
your app, add the permissions above, then generate.

Copy the token. It is short-lived, which is fine: section 4 trades it for a
long-lived one.

> [!TIP]
> **Your app does not need review to work.** App Review is only needed when
> people outside your app's Roles list will use it. For your own accounts, skip
> **Go to app review** entirely.

## 4. Get your token 🔑

One command does the exchange:

```bash
instagram-mcp token \
  --app-id YOUR_APP_ID \
  --app-secret YOUR_APP_SECRET \
  --short-token THE_SHORT_LIVED_TOKEN \
  --path facebook
```

It swaps the short-lived token for a long-lived one, finds every Page you manage
with a linked Instagram account, and prints a ready-to-paste `IG_ACCOUNTS_FILE`.

Underneath, the Instagram Login exchange is this:

```
GET https://graph.instagram.com/access_token
  ?grant_type=ig_exchange_token
  &client_secret=YOUR_APP_SECRET
  &access_token=THE_SHORT_LIVED_TOKEN
```

That returns a token good for **60 days**. The request carries your app secret,
so it belongs in server-side code only. Never in a browser, never in anything
shipped to a device, never committed.

Extend it before it expires:

```bash
instagram-mcp refresh
```

Facebook Login Page tokens do not expire, which is the other reason to prefer
that path.

### One app also covers Threads and Facebook

You do not create a second app for those. The same app carries several use
cases, with one app id, one secret and one Roles list, so a tester you add once
can use all of them.

What is not shared is the token. Each product mints its own against its own
host, so an Instagram token does not work on Threads.

If you want either, tick its use case back in step 1 and then follow that
server's own setup, which is written up in its repo rather than duplicated here:

| You also want | Tick | Then |
|---|---|---|
| Threads | **Access Threads API** | a Threads server is in progress, not published yet |
| Facebook ad research | nothing, the Ad Library is public and needs no app | [facebook-ad-library-mcp](https://github.com/thenavidm/facebook-ad-library-mcp) |

Adding a use case later is fine. It does not invalidate the token you already
have.

## 5. Connect your client 🔌

Writes are on. There is no flag to enable publishing or replying. The actions
that cannot be undone from a chat window ask the model to pass `confirm: true`
first, and `IG_READ_ONLY=1` removes every write tool if you want a reader.

### Claude Code

```bash
claude mcp add --transport stdio instagram \
  --env IG_ACCESS_TOKEN=your_token \
  --env IG_USER_ID=your_ig_user_id \
  -- instagram-mcp
```

By default this applies to the current project only. Add `--scope user` to use
it everywhere.

| Command | What it does |
|---|---|
| `claude mcp list` | List every configured server |
| `claude mcp get instagram` | Show this server's status |
| `/mcp` | Check the connection from inside a session |

### Claude Desktop

| Platform | Config path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "instagram": {
      "command": "/Users/you/.local/bin/instagram-mcp",
      "env": {
        "IG_ACCESS_TOKEN": "your_token",
        "IG_USER_ID": "your_ig_user_id"
      }
    }
  }
}
```

> [!TIP]
> The path must be absolute. Claude Desktop does not inherit your shell PATH, so
> a bare command name fails silently. Get it with `which instagram-mcp` on macOS
> or `where instagram-mcp` on Windows, and escape the backslashes on Windows.
>
> Then quit Claude Desktop completely and reopen it. Closing the window is not
> enough.

### Cursor

`.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "instagram": {
      "command": "instagram-mcp",
      "env": {
        "IG_ACCESS_TOKEN": "your_token",
        "IG_USER_ID": "your_ig_user_id"
      }
    }
  }
}
```

### claude.ai and other remote clients

Run it over HTTP:

```bash
instagram-mcp --http --host 0.0.0.0 --port 8000
```

Then add `https://your-host/mcp` as a custom connector.

Put it behind TLS and an authenticating proxy. This server holds tokens for your
Instagram accounts and has no authentication of its own.

### VS Code, Windsurf, Zed, Cline, Codex CLI, Gemini CLI

All of them take the same three things: the command `instagram-mcp`, any flags
as arguments, and the environment variables above. See
[docs/clients.md](docs/clients.md) for the exact file and key names.

## 6. Check it worked 🩺

```bash
instagram-mcp doctor
```

`doctor` is the whole troubleshooting story. It tests every token, names which
insight metrics your account still answers, and tells you whether
`business_discovery` works on your app. Run it before anything else.

Two things fail more than everything else combined:

**A personal account.** The API returns an empty or confusing error rather than
saying so. `doctor` says so plainly.

**A token generated without the scopes.** Adding scopes in Graph API Explorer
does not update a token you already copied. Generate a new one after adding
them.

## 7. Tools 🧰

46 tools. Every result carries a `source` field naming the tier that answered,
so a model can never present a scraped guess as an official metric.

### Your accounts

| Tool | What it does |
|---|---|
| `list_accounts` | Every connected account with live follower counts and which tier it reaches |
| `whoami` | Verify one token, return the live profile |
| `token_status` | When each token expires and whether it can be refreshed |
| `get_media` | Recent posts with permalinks and engagement |
| `list_all_media` | Every post, paging until the history is exhausted |
| `get_media_by_id` | One post, full fields |
| `list_tagged_media` | Posts by other accounts that tagged you |
| `list_stories` | Stories live right now |
| `get_media_insights` | Reach, saves, shares and interactions for one post |
| `get_account_insights` | Account-level reach and profile views over a period |
| `get_publishing_limit` | How many of the 100 daily API posts you have used |
| `growth_history` | Follower counts over time, from local readings |
| `post_movement` | Which posts are still gaining, and by how much |

`growth_history` and `post_movement` read the local store, not Instagram. They
are empty until the server has taken readings on more than one day, and they
cannot be back-filled. This is the reason to install it before you need it.

### Publishing

| Tool | Needs `confirm` |
|---|---|
| `create_container` | no, nothing goes public |
| `publish_container` | yes |
| `post` | yes |
| `post_carousel` | yes |
| `publish_reel` | yes |
| `publish_story` | yes |

`create_container` stages media without posting. Nothing appears in the app and
the container expires unused after 24 hours. It is the only draft-like state
Instagram has, so it is what to use when a person should approve something
before it goes live.

Instagram has no native scheduling. Anything that claims to schedule a post is
holding the job somewhere else and publishing it at the time.

### Comments and messages

| Tool | Needs `confirm` |
|---|---|
| `get_comments` | read |
| `get_comment_replies` | read |
| `read_all_comments` | read |
| `list_conversations` | read |
| `get_conversation` | read |
| `reply_to_comment` | no, a reply can be deleted |
| `hide_comment` | no, hiding is reversible |
| `delete_comment` | yes, it is permanent |
| `send_dm` | yes, a message cannot be unsent |
| `private_reply_to_comment` | yes, and only one is allowed per comment |

`read_all_comments` pulls comments across your recent posts in one call. It is
what comment triage and sentiment questions actually need, and doing it through
`get_comments` in a loop burns the context window before it answers anything.

### Research

| Tool | What it does |
|---|---|
| `discover_account` | Public profile and recent posts for any Business or Creator account |
| `compare_accounts` | Up to 10 accounts side by side with median engagement |
| `search_hashtag` | Resolve a hashtag to its id, remembering the result |
| `hashtag_top_media` | What is performing on a hashtag right now |
| `hashtag_recent_media` | What is being posted on a hashtag right now |

These need the Facebook Login path. `discover_account` is an official, free,
documented Graph endpoint. No scraping, no risk, no browser.

### Unofficial

Off unless you turn it on. See [section 8](#8-the-three-tiers-).

| Tool | What it does |
|---|---|
| `unofficial_status` | Whether the tier is on, and what is deliberately not implemented |
| `unofficial_profile` | Full public profile for any account, including personal ones |
| `unofficial_posts` | Recent posts by any public account |
| `unofficial_media` | One post's ordered assets; optional local download and source manifest |
| `unofficial_stories` | Stories live on any public account |
| `unofficial_followers` | Followers, newest first, capped at 200 |
| `unofficial_following` | Accounts an account follows, capped at 200 |
| `unofficial_search_accounts` | Search accounts by name or keyword |
| `unofficial_post_comments` | Comments on any public post |
| `unofficial_inbox` | Your real inbox, including message requests |
| `unofficial_thread` | Messages inside one inbox thread |
| `unofficial_send_dm` | A cold DM. Needs `confirm` |

`unofficial_media` accepts a numeric media id or an Instagram post or reel URL.
Its default returns metadata and the current CDN URLs. With `download=true`,
it saves every image or video in order, plus `manifest.json` containing the
source permalink, framed caption, paths, byte counts and SHA-256 hashes. Use
those paths with your client's image or video inspection tools.

Downloads live in a new directory under `IG_MCP_DATA_DIR/media` on each call.
They survive server restarts and expired CDN links. Delete a download's directory
to remove it. Downloads are limited to 20 assets, 100 MiB per asset and 200 MiB
per post. A failed download leaves no partial reference. They use the existing
saved session for metadata, make no Instagram writes, and remain available with
`IG_READ_ONLY=1`.

Three things are deliberately absent: `follow_user`, `unfollow_user` and
`bulk_like`. They are what every growth-hack tool ships and they are the three
actions that get accounts restricted fastest. `unofficial_status` reports them
as missing on purpose, so it is a decision on the record rather than an
oversight.

## 8. The three tiers 🧩

Instagram does not have one API. It has two, plus a private one, and they reach
completely different things.

| Tier | What it reaches | Runs where | Risk |
|---|---|---|---|
| **Instagram Login** | only accounts you own | anywhere | none |
| **Facebook Login** | the above, plus public data on any Business or Creator account, plus hashtags | anywhere | none |
| **Unofficial** | any public account, the real inbox, account search | a machine you control | your account can be restricted |

Most people want the middle one and do not know it exists.

> [!WARNING]
> The unofficial tier drives Instagram's private API through `instagrapi`. That
> is against Instagram's terms of service and it can get an account restricted
> or banned. Point it at a secondary account, never your main one.

It is not installed by default, it is off unless you set `IG_UNOFFICIAL=1` or
pass `--unofficial`, and it prints a warning on stderr when it starts.

It also paces itself: a random delay between calls and a local hourly ceiling
that is lower than Instagram's own. Instagram restricts accounts for
machine-speed access patterns more than for volume.

```bash
instagram-mcp login          # once, saves a session file
IG_UNOFFICIAL=1 instagram-mcp
```

## 9. Multiple accounts 👥

Point `IG_ACCOUNTS_FILE` at a JSON array instead of setting a single token.
Mixed tiers in one file are fine and expected.

```json
[
  {
    "account_name": "thenavidm",
    "user_id": "17841400000000000",
    "access_token": "IGQV...",
    "host": "graph.instagram.com"
  },
  {
    "account_name": "thenavidai",
    "user_id": "17841400000000001",
    "access_token": "EAAG...",
    "host": "graph.facebook.com"
  }
]
```

Every tool takes an optional `account` argument. When you leave it out, the
`IG_PREFERRED` order decides, and an exact name match beats a prefix match.

```bash
IG_PREFERRED=thenavidm,thenavidai
```

## 10. Notes and gotchas 🚧

- **Publishing needs public HTTPS URLs.** Meta fetches the media server-side, so
  a local file path will not work. Host the image or video somewhere reachable
  first.
- **100 posts per 24 hours**, per account, across every API client, not just this
  one. `get_publishing_limit` tells you where you are.
- **30 unique hashtags per rolling 7 days.** Resolved ids are cached locally so
  repeat lookups do not spend a slot, but the thirty-first new hashtag fails.
- **`business_discovery` sees Business and Creator accounts only.** Never
  personal, never private. The error does not say which of those it hit.
- **DMs need a 24-hour window.** Instagram allows a DM only inside a window the
  other person opened by messaging you, or as one private reply to a comment.
  There is no official way to message somebody cold.
- **Reading DMs needs Advanced Access** from Meta App Review on most apps.
  `list_conversations` returns empty rather than erroring until you have it.
- **Insight metrics change.** Meta deprecates and renames them regularly.
  `doctor` names the ones your account still answers, which is faster than
  reading the changelog.
- **Errors come back as a structured result**, not an exception, carrying the
  Graph error's `code`, `subcode` and `fbtrace_id`. That is Instagram's error,
  not a bug in this server.
- **Comments and DMs are written by strangers.** Every text field from another
  person comes back wrapped in an untrusted-content fence telling the model to
  report it, not obey it. This is the most injectable surface an agent gets
  handed, and "summarise my comments" is one of the first things anyone asks.
- **Every write is logged.** One JSON line per attempted write, allowed or
  refused, to `IG_AUDIT_LOG` or the data directory. The model has no tool to
  read or edit that file.

## 11. Troubleshooting 🔧

Run `instagram-mcp doctor` first. It diagnoses most of this.

| Symptom | Cause |
|---|---|
| Every tool returns nothing | Personal account. The API needs Business or Creator |
| `(#100) Tried accessing nonexisting field` | Token generated before you added the scopes. Generate a new one |
| `discover_account` fails | You are on the Instagram Login path. It needs Facebook Login |
| Hashtag tools stop working | 30 unique hashtags in a rolling 7 days |
| Server missing in Claude Desktop | Relative command path. Use the absolute one, and fully quit the app |
| `growth_history` is empty | It needs readings from more than one day. It cannot back-fill |
| Write tools are absent | `IG_READ_ONLY` is set |
| A write asks for confirmation | Working as intended. Call it again with `confirm: true` |
| Unofficial tools refuse | Tier is off. Set `IG_UNOFFICIAL=1` and run `instagram-mcp login` once |

## 12. FAQ ❓

<details>
<summary><strong>What is an MCP server?</strong></summary>

Model Context Protocol is a standard way to give an AI assistant access to a
tool or a data source. An MCP server exposes a set of functions, and a client
like Claude Code or Claude Desktop can call them during a conversation. This one
exposes Instagram.

You install it once, put your credentials in the client's config, and then ask
in plain language. You do not call the tools yourself.

</details>

<details>
<summary><strong>Do I need a Business account?</strong></summary>

You need one for everything except the unofficial tier. Instagram's API does not
work on personal accounts. Switching is free and reversible, in the Instagram app under
Settings, then Account type and tools.

</details>

<details>
<summary><strong>Why do I have to create a Meta app? That seems like a lot.</strong></summary>

Instagram does not issue API tokens to people, only to apps. Every tool that
touches Instagram programmatically works this way, including the ones that hide
it behind a signup form. Doing it yourself means the token is yours, it is not
held by a third party, and nobody can revoke your access by shutting down.

It is about ten minutes, once.

</details>

<details>
<summary><strong>Can it schedule posts?</strong></summary>

It cannot, and neither can anything else. Instagram has no scheduling API. Products
that offer scheduling hold the post on their own servers and publish it at the
time, which means handing them your content and your credentials.

`create_container` is the honest version: stage it now, publish it when you say.

</details>

<details>
<summary><strong>Can it read my DMs?</strong></summary>

It reads only the threads where the other person messaged you, only inside a
24-hour window, and only once Meta grants your app Advanced Access. The
unofficial tier reads the real inbox with no window, at the risk described in
section 8.

</details>

<details>
<summary><strong>Will this get my account banned?</strong></summary>

The two official tiers are Meta's own documented APIs. There is no more risk in
using them than in using the Instagram app.

The unofficial tier is a real risk. It drives the private API, which is against
Instagram's terms. Use a secondary account.

</details>

<details>
<summary><strong>Can it post without asking me?</strong></summary>

It can post when you ask it to. Publishing, deleting a comment and sending a DM
all require the model to pass `confirm: true`, which it does after reading a
description explaining what cannot be undone. That is a speed bump against a
careless call, not a lock.

If you want a server that cannot write at all, set `IG_READ_ONLY=1`. The write
tools are then not registered, so the model cannot see or call them.

</details>

<details>
<summary><strong>What data does it store, and where?</strong></summary>

It stores follower counts and post engagement readings in a SQLite file in your
data directory, so it can answer "what changed since Monday". An audit log of every
attempted write. A session file if you use the unofficial tier. Media files and
source manifests when you call `unofficial_media` with `download=true`.

All of it is local. Nothing is sent anywhere except to Instagram. Delete the
data directory and it is gone.

</details>

<details>
<summary><strong>Why is this Python when the other servers are TypeScript?</strong></summary>

The unofficial tier depends on `instagrapi`, which is Python only. Rewriting the
official tiers in TypeScript would split the project in two for no gain.

</details>

<details>
<summary><strong>Why is the package name not just "instagram-mcp"?</strong></summary>

That name on PyPI already belongs to somebody else. Installing it would fetch
code that is not this. The package is `thenavidm-instagram-mcp` and the command
it installs is `instagram-mcp`.

</details>

<details>
<summary><strong>Does it cost anything?</strong></summary>

It costs nothing. The server is MIT licensed and Meta's Graph API is free at
these volumes.
You are paying for your own AI client, not for this.

</details>

<details>
<summary><strong>Can I use it with several accounts?</strong></summary>

You can connect as many as you like, as section 9 covers. One server, one config
file, an `account` argument on every tool, and a preference order for when you
leave it out.

</details>

## Questions

Run into a problem or have a question? [Open an issue](https://github.com/thenavidm/instagram-mcp/issues) and I will help.

## About the author 👋

Navid Moazzez is a leading AI business strategist and the host of the AI Creator
Summit, watched by 100,000+ creators. He helps creators and founders master AI
and build their own AI Operating System (AI OS) to automate their business and
life. He creates useful free tools, MCP servers and CLIs that creators and founders can use in their own workflows.

**Links**

- Personal website: [navid.me](https://navid.me?utm_source=github&utm_medium=readme&utm_campaign=instagram-mcp)
- Navid Media: [navid.media](https://navid.media?utm_source=github&utm_medium=readme&utm_campaign=instagram-mcp)
- YouTube: [@thenavidm](https://youtube.com/@thenavidm?sub_confirmation=1) and [@thenavidai](https://youtube.com/@thenavidai?sub_confirmation=1)
- X: [@thenavidm](https://x.com/thenavidm)
- Instagram: [@thenavidm](https://instagram.com/thenavidm)
- LinkedIn: [thenavidm](https://linkedin.com/in/thenavidm)

If this is useful, star the repo and come say hi on [X](https://x.com/thenavidm).

## Dependencies

| Library | License | What it does |
|---|---|---|
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MIT | The MCP server, stdio and streamable HTTP |
| [httpx2](https://github.com/encode/httpx) | BSD-3 | The HTTP client, already an SDK dependency |
| [instagrapi](https://github.com/subzeroid/instagrapi) | MIT | The unofficial tier, optional extra |

## License

[MIT](./LICENSE). Free to use, modify, and share.

Not affiliated with, endorsed by, or sponsored by Meta Platforms, Inc.
Instagram and Facebook are trademarks of Meta Platforms, Inc. This project uses
Meta's public Graph API, and its optional unofficial tier is not sanctioned by
Meta.

---

© 2026 [Navid Media](https://navid.media?utm_source=github&utm_medium=referral&utm_campaign=instagram-mcp&utm_content=readme). Made with ❤️ by [Navid Moazzez](https://navid.me?utm_source=github&utm_medium=referral&utm_campaign=instagram-mcp&utm_content=readme).
