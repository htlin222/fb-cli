# fb-cli Design Doc

**Date:** 2026-05-16
**Status:** Approved (brainstorming complete, ready to implement)
**Sibling:** [threads-cli](https://github.com/htlin222/threads-cli) — same architecture, different Meta product.

---

## 0. Decisions summary

| # | Question | Decision |
|---|----------|----------|
| 1 | Surface to operate on | **A** — Facebook Pages (admin) only. Personal-timeline publishing was removed from Graph API in v6.0 (2020); reads of own posts also gone. |
| 2 | Scope breadth | **A2** — threads-cli parity + FB-specific extras: `post-link`, `schedule`, `edit`, `reactions`. ~24 make targets total. |
| 3 | Cloudflare Worker | **W1** — new dedicated `fb-cb` worker. Direct copy of `threads-cb` (58 lines). Independent deploy, clean separation from threads-cli. |
| 4 | Multi-Page handling | **P1** — single Page. Two static values in `.env`: `FB_PAGE_ID` + `FB_PAGE_ACCESS_TOKEN`. |

---

## 1. Architecture

### Repo layout

```
~/fb-cli/
├── fb.py                       # Thin CLI dispatcher (argparse, cmd_*, main with auto-retry)
├── fb_lib.py                   # Helpers, typed errors (ApiError, AuthError, ValidationError), validators
├── tests/test_fb_lib.py        # pytest, offline, mock HTTP via `responses`
├── pytest.ini
├── Makefile
├── .env.example
├── .gitignore                  # Same patterns as threads-cli
├── README.md
├── TUTORIAL.md
├── CLAUDE.md
├── cloudflare-worker/
│   ├── src/index.js            # Verbatim copy of threads-cb worker (callback + poll)
│   └── wrangler.toml           # name = "fb-cb"
└── .claude/skills/onboarding/SKILL.md
```

### API & base URL

- **Graph API version:** `v21.0` (pin in `fb_lib.GRAPH_API_VERSION`)
- **Base:** `https://graph.facebook.com/v21.0/`
- Meta deprecates Graph API versions ~2 years after release; bump constant + re-test on deprecation notice.

### Token model

| Token | Lifetime | Used for |
|-------|----------|----------|
| User access token (long-lived) | 60 days, refreshable | one-time, to derive Page token; refreshing |
| Page access token | **Permanent** (until user revokes app / changes password) | All day-to-day post / read / manage / insights |

This differs meaningfully from threads-cli, where there is **one** token that must be refreshed every ≤60 days. In fb-cli the Page token is effectively forever, so the auto-refresh path on 401 is rarer but the recovery is a 2-step (refresh user token → re-fetch page token from `/me/accounts`).

### Auto-retry on 401

`fb.py:main()` catches `AuthError` from any subcommand and runs:

1. Refresh `FB_USER_ACCESS_TOKEN` via `fb_exchange_token` grant
2. Call `GET /me/accounts` with the refreshed user token
3. Extract Page token for `FB_PAGE_ID`, overwrite `.env`
4. Retry original command once

If step 1 or 2 also fails → tell user to run `make auth` (browser approval needed).

---

## 2. Command surface

24 make targets. Each is a thin wrapper over a `cmd_*` in `fb.py`, which calls a helper in `fb_lib.py`.

### Publish (6)

| Target | Endpoint | Notes |
|--------|----------|-------|
| `post MSG="X"` | `POST /{page-id}/feed` | text post |
| `post-image URL=<url> [MSG=...]` | `POST /{page-id}/photos` | single photo |
| `post-video URL=<url> [MSG=...]` | `POST /{page-id}/videos` | non-resumable upload by URL |
| `post-carousel URLS="u1,u2,..." [MSG=...]` | photos (`published=false`) → `/feed` with `attached_media` | 2-step |
| `post-link URL=<url> MSG="..."` ⭐ | `POST /{page-id}/feed` + `link=` | A2 extra; FB auto-generates OG preview |
| `schedule MSG="..." WHEN=<...>` ⭐ | `POST /{page-id}/feed` + `published=false` + `scheduled_publish_time` | A2 extra; see §4 for `WHEN` parser |

### Edit / Delete (2)

| Target | Endpoint | Notes |
|--------|----------|-------|
| `edit ID=<post-id> MSG="..."` ⭐ | `POST /{post-id}` | A2 extra; Threads has no edit. Only `message` can change — media is immutable. |
| `delete ID=<post-id>` | `DELETE /{post-id}` | |

### Read (5)

| Target | Endpoint |
|--------|----------|
| `list LIMIT=10` | `GET /{page-id}/posts` |
| `get ID=<post-id>` | `GET /{post-id}?fields=...` |
| `comments ID=<post-id>` | `GET /{post-id}/comments` |
| `conversation ID=<comment-id>` | `GET /{comment-id}/comments` (nested) |
| `reactions ID=<post-id>` ⭐ | `GET /{post-id}/reactions?summary=total_count` | A2 extra |

### Manage comments (4)

| Target | Endpoint |
|--------|----------|
| `reply TO=<comment-id> MSG="..."` | `POST /{comment-id}/comments` |
| `delete-comment ID=<comment-id>` | `DELETE /{comment-id}` |
| `hide ID=<comment-id>` | `POST /{comment-id}` (`is_hidden=true`) |
| `unhide ID=<comment-id>` | `POST /{comment-id}` (`is_hidden=false`) |

### Insights (2)

| Target | Endpoint |
|--------|----------|
| `insights ID=<post-id>` | `GET /{post-id}/insights` |
| `page-insights` | `GET /{page-id}/insights` |

### Auth / env / worker (5+)

`pages`, `whoami`, `refresh`, `auth`, `limits`, `env-check`, `smoke`, `worker-deploy`, `worker-tail`, `help`.

---

## 3. OAuth flow & permissions

### `make auth` (one-time, ~1 minute)

1. CLI generates random `state` (≥32 chars), opens browser to
   `https://www.facebook.com/v21.0/dialog/oauth?client_id=<app-id>&redirect_uri=<worker>/callback&scope=<scopes>&state=<state>&response_type=code`
2. User approves → Meta redirects to `https://fb-cb.<acct>.workers.dev/callback?code=&state=`
3. Worker stashes `code` in KV keyed by `state` (TTL 10 min)
4. CLI polls `/poll?state=` (1 s intervals, 5 min timeout) — gets `code`, KV deletes it
5. CLI exchanges `code` → short-lived user token (1 hour):
   `GET /v21.0/oauth/access_token?client_id&client_secret&redirect_uri&code`
6. CLI exchanges short-lived → long-lived user token (60 days):
   `GET /v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id&client_secret&fb_exchange_token=<short>`
7. CLI calls `GET /me/accounts?access_token=<long-lived>` → returns Pages list with per-Page `access_token`
8. P1: single Page → auto-pick. (If multiple, prompt.)
9. CLI writes `.env`:
   ```
   FB_USER_ACCESS_TOKEN=<long-lived user, 60d>
   FB_USER_TOKEN_EXPIRES_AT=<unix>
   FB_PAGE_ID=<page-id>
   FB_PAGE_NAME=<page-name>      # cosmetic, for `make whoami`
   FB_PAGE_ACCESS_TOKEN=<derived from user, permanent>
   ```

### Scopes (A2 minimum)

```
public_profile
pages_show_list
pages_read_engagement
pages_manage_posts
pages_manage_engagement
pages_read_user_content
read_insights
```

If we add a feature needing another scope: edit `SCOPES` in `fb.py`, tell user to `make auth` again.

### Static config in `.env`

```
FB_APP_ID                       # Meta dashboard → App ID
FB_APP_SECRET                   # never print; last 6 chars max
FB_REDIRECT_URI                 # https://fb-cb.<acct>.workers.dev/callback
FB_WORKER_BASE                  # https://fb-cb.<acct>.workers.dev
```

Plus the 5 dynamic fields from §3 step 9.

---

## 4. FB-specific extras

### `post-link`

```
make post-link URL=https://example.com MSG="check this"
```

`POST /{page-id}/feed` with `link=` and `message=`. FB auto-generates OG preview card. Distinct from `post` because link posts are a different object type, have different insights breakdowns, and rendering rules.

### `schedule`

```
make schedule MSG="..." WHEN="2026-05-17 10:00"
make schedule MSG="..." WHEN="+2h"
```

`WHEN=` parser in `fb_lib.parse_schedule_time()`:
- Absolute: `"YYYY-MM-DD HH:MM"` (local timezone, converted to UTC unix)
- Relative: `"+Nh"` / `"+Nd"` / `"+Nm"` (hours / days / minutes)

Meta constraint: must be **10 minutes to 6 months** from now. `fb_lib.validate_schedule_time()` raises `ValidationError` early — don't round-trip a doomed request.

POST body: `published=false`, `scheduled_publish_time=<unix>`, `message=<text>`.

After publish time, the post auto-publishes. `make list` shows scheduled posts when `LIMIT` is large enough; filter via `is_published` field.

### `edit`

```
make edit ID=<post-id> MSG="corrected text"
```

`POST /{post-id}` with `message=<new>`. Only text content changes; media is immutable. Validates that `ID` resolves to a post owned by `FB_PAGE_ID` (defense in depth — Meta also enforces).

### `reactions`

```
make reactions ID=<post-id>
```

`GET /{post-id}/reactions?summary=total_count&fields=type` returns a per-type breakdown (LIKE / LOVE / WOW / HAHA / SAD / ANGRY) plus total. More granular than Threads' single likes count.

### `limits` (FB rate limit model)

FB does **not** use the quota counter model that Threads uses (250/24h + 1000/24h). Instead, every API response carries headers:

| Header | Meaning |
|--------|---------|
| `X-App-Usage` | App-level: `call_count` / `total_cputime` / `total_time` (each as % of limit) |
| `X-Page-Usage` | Per-Page: same shape |
| `X-Business-Use-Case-Usage` | BUC score 0-100; ≥ 100 → throttled |

`make limits` calls `GET /me` (cheap), parses these headers, prints them. Updates roughly in real time (no minute-lag like Threads).

---

## 5. Code reuse from threads-cli

Mechanically copy-paste then `sed`:

- `threads.py` → `fb.py` (rename CLI binary; rewrite endpoint URLs; new SCOPES)
- `threads_lib.py` → `fb_lib.py` (replace Threads API specifics; reuse OAuth helpers, validators, HTTP wrapper, typed errors verbatim)
- `cloudflare-worker/` → copy verbatim, change `wrangler.toml` `name = "fb-cb"`
- `tests/test_threads_lib.py` → `tests/test_fb_lib.py` (port test scaffolding; new mock responses)
- `Makefile`, `pytest.ini`, `.gitignore`, `.env.example` → copy + adapt vars
- `CLAUDE.md` → start from threads-cli's structure; rewrite the command map for fb-cli targets

**Anti-pattern to avoid:** building a shared `meta_common` library. The two products' OAuth flows differ (page token derivation, refresh semantics, scope model) enough that abstracting prematurely creates more lock-in than copy-paste. Revisit only if a 3rd Meta product appears.

---

## 6. Out of scope (explicit non-goals)

- Personal-timeline publishing (Graph API doesn't allow it; see §0)
- Multi-Page management (P1 decision — single Page only)
- Messenger send / receive (would need `pages_messaging` App Review for any production use)
- Instagram Business posting (would be a separate `ig-cli` if ever wanted)
- Ads API (entirely different product; not relevant to a personal posting CLI)
- Webhooks / Realtime updates (could be added later; for now, poll-based)

---

## 7. Implementation order (suggested phasing)

1. **Skeleton** — repo init + copy worker + `wrangler.toml` rename + Cloudflare deploy. Confirm `fb-cb` returns "ok" on `/health`.
2. **OAuth** — port `make auth` / `make refresh` / `make whoami`. Get a real long-lived token + page token written to `.env`. Verify with `GET /me/accounts`.
3. **Read endpoints** — `list`, `get`, `comments`, `reactions`. Smallest blast radius if anything's wrong.
4. **Publish endpoints** — `post`, `post-image`, `post-video`, `post-link`. Test on a throwaway draft Page if possible.
5. **Manage** — `reply`, `delete`, `delete-comment`, `hide`, `unhide`, `edit`.
6. **Schedule** — `schedule` + WHEN parser tests.
7. **Insights** — `insights`, `page-insights`, `limits`.
8. **Carousel** — `post-carousel` (multi-step, more failure modes — save for last).
9. **Tests + docs** — port `tests/`, write `README.md` + `TUTORIAL.md`.
10. **Repo init** — `git init`, `gh repo create htlin222/fb-cli --private --source=. --push`.
