# fb-cli

Facebook Pages CLI via the official Graph API + a Cloudflare Worker as the
OAuth callback relay. Sibling of [threads-cli](https://github.com/htlin222/threads-cli);
same architecture, different Meta product.

**Status:** scaffolding (phase 1 of `docs/plans/2026-05-16-fb-cli-design.md`).
Most commands print `[TODO]` until phase 2+ ports the OAuth flow and
per-endpoint helpers.

## What you can do (when fully implemented)

Operate on **one Facebook Page that you admin**. Scope was chosen
deliberately:

- Personal-timeline publishing was removed from Graph API in v6.0 (2020).
- Personal profile reads are gutted (`me/posts` removed, `me/friends` only
  returns friends who also use your App = always empty).
- Pages are the one Graph API surface Meta still fully supports.

Roughly 24 `make` targets parallel to `threads-cli`, plus FB-specific
extras Threads doesn't have:

- `make edit ID=<post-id> MSG="..."` -- edit post text
- `make schedule MSG="..." WHEN="+2h"` -- scheduled posts
- `make post-link URL=<url> MSG="..."` -- link posts with OG preview
- `make reactions ID=<post-id>` -- per-type LIKE/LOVE/WOW/HAHA/SAD/ANGRY breakdown

See `make help` for the full target list.

## Project layout

```
fb.py                 Thin CLI dispatcher (argparse, cmd_*, main with auto-retry)
fb_lib.py             All helpers, typed errors (ApiError/AuthError/ValidationError)
tests/                pytest suite (offline, mocks via `responses`)
Makefile              `make <target>` wrappers
.env.example          Template; copy to .env and fill credentials
cloudflare-worker/    OAuth callback relay (verbatim copy of threads-cb)
docs/plans/           Design docs + implementation phases
```

## Setup (first time)

1. **Create a Meta app** (https://developers.facebook.com/apps/) -> add
   "Facebook Login for Business" product. Note the App ID + App Secret.
2. `cp .env.example .env` and fill `FB_APP_ID`, `FB_APP_SECRET`.
3. **Create a KV namespace and deploy the worker:**
   ```
   cd cloudflare-worker
   wrangler kv namespace create FB_AUTH
   # paste the returned id into wrangler.toml
   wrangler deploy
   ```
4. Set the resulting worker URL in `.env` as `FB_WORKER_BASE` and
   `FB_REDIRECT_URI=<worker>/callback`. Register that same redirect URI
   in the Meta App Dashboard.
5. `make auth` -- approves the app in your browser, captures the code via
   the worker, exchanges for long-lived user + page tokens, writes them
   to `.env`.
6. `make whoami` -- sanity check.

## Tests

```
make test
```

Offline pytest suite. Currently 13 tests on the skeleton; will grow as
helpers land.

## License

Personal use; no LICENSE file yet.
