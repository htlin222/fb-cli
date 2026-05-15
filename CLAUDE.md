# CLAUDE.md -- instructions for Claude Code in this repo

This repo is a CLI client for the **Facebook Graph API (Pages)** plus a
Cloudflare Worker as the OAuth callback relay. Sibling of `threads-cli`;
same pattern, different Meta product. **Currently in phase 1 scaffolding**
-- most commands print `[TODO]` until later phases land. See
`docs/plans/2026-05-16-fb-cli-design.md` for the implementation roadmap.

---

## TL;DR command map (target state)

| User says...                                                    | You run                                                |
| --------------------------------------------------------------- | ------------------------------------------------------ |
| "post X" / "發 X"                                                | `make post MSG="X"`                                    |
| "post link <url> X"                                             | `make post-link URL=<url> MSG="X"`                     |
| "post image <url>"                                              | `make post-image URL=<url> [MSG="..."]`                |
| "post video <url>"                                              | `make post-video URL=<url> [MSG="..."]`                |
| "carousel" + URLs                                               | `make post-carousel URLS="u1,u2,..." [MSG=...]`        |
| "schedule X in 2h / on date"                                    | `make schedule MSG="X" WHEN="+2h"` or `WHEN="YYYY-MM-DD HH:MM"` |
| "edit <post-id> X"                                              | `make edit ID=<post-id> MSG="X"`                       |
| "delete <post-id>"                                              | `make delete ID=<post-id>`                             |
| "list" / "最近的貼文"                                            | `make list LIMIT=10`                                   |
| "get <post-id>"                                                 | `make get ID=<post-id>`                                |
| "看 <post-id> 的 comments"                                       | `make comments ID=<post-id>`                           |
| "看 <comment-id> 下面的對話"                                      | `make conversation ID=<comment-id>`                    |
| "reactions <post-id>" / "看誰按讚"                                | `make reactions ID=<post-id>`                          |
| "reply <comment-id> X" / "回 <comment-id>"                       | `make reply TO=<comment-id> MSG="X"`                   |
| "刪 comment <id>"                                                | `make delete-comment ID=<id>`                          |
| "隱藏 comment <id>" / "hide"                                     | `make hide ID=<id>`                                    |
| "取消隱藏 <id>"                                                  | `make unhide ID=<id>`                                  |
| "insights <post-id>" / "看貼文數據"                               | `make insights ID=<post-id>`                           |
| "粉專 insights"                                                  | `make page-insights`                                   |
| "限制" / "limits" / "rate limit"                                  | `make limits`                                          |
| "pages" / "我管理哪些粉專"                                        | `make pages`                                           |
| "whoami" / "我是誰"                                              | `make whoami`                                          |
| "refresh token"                                                 | `make refresh`                                         |
| "re-auth" / "重新授權"                                            | `make auth`                                            |

`make help` always prints the up-to-date list. Run from the repo root.

## Handling "post X"

1. Don't ask clarifying questions unless genuinely ambiguous.
2. Run `make post MSG="<text>"` (escape quotes / `$` for shell).
3. On success, the CLI prints the new post id + permalink. Report just the permalink.
4. **Auto-retry on 401**: Page tokens are theoretically permanent but can be
   invalidated if the user revokes the app / changes password. `fb.py:main()`
   catches `AuthError`, refreshes the long-lived user token via
   `fb_exchange_token`, re-fetches the Page token from `/me/accounts`, and
   retries once. If even that fails, tell the user to run `make auth`.
5. Don't auto-delete posts.

## Token model (important difference from threads-cli)

| Token | Lifetime | Used for |
|-------|----------|----------|
| `FB_USER_ACCESS_TOKEN` | 60 days, refreshable via `fb_exchange_token` | One-time during `make auth` to derive Page token; refreshing |
| `FB_PAGE_ACCESS_TOKEN` | **Permanent** (until user revokes app) | All day-to-day post/read/manage/insights |

Day-to-day commands use the **Page** token, not the user token. The user
token only exists so `make auth` can call `/me/accounts` and so `make refresh`
can be issued before the 60-day clock runs out.

## Scopes requested by `make auth`

```
public_profile, pages_show_list, pages_read_engagement,
pages_manage_posts, pages_manage_engagement, pages_read_user_content,
read_insights
```

If you add a feature needing another scope, edit `SCOPES` in `fb_lib.py`
and tell the user to re-run `make auth`.

## Endpoints potentially gated behind App Review

These work in **dev mode** for the Page admin (the user) but need App
Review for production / publication:

| Make target | Failure to expect | Required |
|-------------|-------------------|----------|
| `make page-insights`, `make insights` | code 10 if app not approved | App Review for `read_insights` (dev mode usually ok for own page) |
| Messenger endpoints (not in fb-cli scope) | code 10 | App Review for `pages_messaging` |

For a personal one-user CLI staying in dev mode, the A2 scope list works
out of the box for posting / reading / managing comments. Insights for own
pages usually also works.

## Token state in `.env`

```
FB_APP_ID, FB_APP_SECRET                  # static; from Meta dashboard
FB_REDIRECT_URI, FB_WORKER_BASE           # static; the Cloudflare worker URL
FB_USER_ACCESS_TOKEN                      # long-lived (60d), refreshable
FB_USER_TOKEN_EXPIRES_AT                  # unix timestamp
FB_PAGE_ID                                # the single Page we operate on
FB_PAGE_NAME                              # cosmetic, for whoami output
FB_PAGE_ACCESS_TOKEN                      # permanent; used by every command
```

**Never** print the full app secret or full access token in chat. Last 6-8
chars only when you need to show progress.

## Cloudflare Worker

- Deployed URL is in `$FB_WORKER_BASE` in `.env` (gitignored). Don't paste the literal URL here.
- Source: `cloudflare-worker/src/index.js` (verbatim copy of threads-cb).
- Config: `cloudflare-worker/wrangler.toml`, name `fb-cb`.
- **Don't redeploy** unless source/config changed. If needed: `make worker-deploy`.
- KV namespace `THREADS_AUTH` (binding name kept for source verbatim-ness; the namespace is a separate FB-only one) stores OAuth codes keyed by `state` (TTL 10min).
- Logs: `make worker-tail`.

## Recovery / debug ladder

1. `make env-check` -- required keys present?
2. `make whoami` -- user + page token still valid? (also prints expiry)
3. `make refresh` -- extends long-lived user token + re-fetches page token; retry the call.
4. `make auth` -- full OAuth (browser approval).
5. `make worker-tail` then re-run `make auth` -- verify worker route + Meta-side
   redirect URI in `.env` still match.

## Rate-limit model (different from threads-cli)

FB does **not** use a 24h quota counter like Threads (250/24h). It uses
rolling 5-min windows + per-app/per-page/per-BUC scores in response
headers:

- `X-App-Usage` -- app-level usage
- `X-Page-Usage` -- per-Page usage
- `X-Business-Use-Case-Usage` -- BUC score 0-100; >= 100 throttled

`make limits` reads these from a cheap `GET /me` call and prints them.

## Code layout

```
fb.py                 Thin CLI dispatcher; argparse + cmd_* + main()
fb_lib.py             All helpers, typed errors, validators, OAuth flow,
                      HTTP wrappers. Pure functions, testable.
tests/
  test_fb_lib.py      pytest; runs via `make test` (no network)
docs/plans/           Design docs + implementation phases
```

When adding a new endpoint:
1. Put the helper in `fb_lib.py` (raises `ApiError` on failure, no `sys.exit`).
2. Add a thin `cmd_*` in `fb.py` that calls it.
3. Add an argparse subparser + Make target.
4. Add a pytest test for the helper (use `responses` to mock HTTP).

## Anti-patterns

- Creating new Python scripts. `fb.py` is the single CLI entry; add a subcommand.
- Pre-emptively spawning OAuth. Just call the API; recover from 401.
- Committing `.env`. It's in `.gitignore`.
- Printing full secrets/tokens.
- Trying to edit media on a photo/video post (FB only allows editing `message`; media is immutable).
- Re-deploying the worker for trivial reasons.
- Building a shared `meta_common` library with threads-cli. The OAuth
  flows differ enough that copy-paste is cheaper than premature abstraction.

## Long-form context

- `docs/plans/2026-05-16-fb-cli-design.md` -- full design doc with 7
  sections + implementation phasing.
- `README.md` -- user-facing intro.

If this file and the design doc drift, the design doc is canonical for
target behavior; this file documents the runtime behavior at the current
implementation phase.
