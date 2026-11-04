.DEFAULT_GOAL := help
SHELL := /bin/bash

# Prefer uv (pulls deps from PEP-723 header on first run); fall back to python3.
RUN := $(shell command -v uv >/dev/null 2>&1 && echo "uv run" || echo "python3")
# .env holds 1Password refs (op://...); `op run` injects the real values (FB_* env wins over .env).
RUN := op run --env-file=$(CURDIR)/.env -- $(RUN)

.PHONY: help \
  post post-image post-video post-reel post-carousel post-link schedule \
  edit delete \
  list get comments conversation reactions latest summary \
  reply reply-url delete-comment hide unhide \
  insights page-insights \
  pages whoami refresh auth limits \
  mentions subscribe-page webhook-setup \
  smoke worker-deploy worker-tail worker-delete env-check test \
  get-latest comments-latest reactions-latest delete-latest

help: ## show this help
	@echo "fb-cli -- Facebook Pages CLI ('make <target>' wraps 'uv run fb.py <subcmd>')"
	@echo ""
	@echo "Publish:"
	@echo "  make post MSG=\"...\"                           text post"
	@echo "  make post-image URL=<img> [MSG=...]            image post"
	@echo "  make post-video URL=<video> [MSG=...]          video post"
	@echo "  make post-reel VIDEO=<path|url> [MSG=...]      publish a Reel"
	@echo "  make post-carousel URLS=\"u1,u2,...\" [MSG=...]  2-10 photo carousel"
	@echo "  make post-link URL=<url> MSG=\"...\"             link post with OG preview"
	@echo "  make schedule MSG=\"...\" WHEN=\"+2h\"             scheduled post (10min-6mo)"
	@echo ""
	@echo "Edit / delete:"
	@echo "  make edit ID=<post-id> MSG=\"...\"               edit post text (media immutable)"
	@echo "  make delete ID=<post-id>                       delete a post"
	@echo ""
	@echo "Read:"
	@echo "  make list [LIMIT=10]                          recent posts"
	@echo "  make get ID=<post-id>                         full post details"
	@echo "  make comments ID=<post-id>                    comments on a post"
	@echo "  make conversation ID=<comment-id>             nested replies under a comment"
	@echo "  make reactions ID=<post-id>                   per-type reaction breakdown"
	@echo ""
	@echo "Shortcuts on the latest post:"
	@echo "  make latest                                   print latest post id (one line)"
	@echo "  make summary                                  latest post + reactions + comment count"
	@echo "  make get-latest                               make get on the latest post"
	@echo "  make comments-latest                          make comments on the latest post"
	@echo "  make reactions-latest                         make reactions on the latest post"
	@echo "  make delete-latest                            make delete on the latest post (with confirm)"
	@echo ""
	@echo "Manage comments:"
	@echo "  make reply TO=<id> MSG=\"...\"                  reply to a post or comment"
	@echo "  make reply-url URL=<permalink> MSG=\"...\"      reply by FB permalink (manual mention)"
	@echo "  make delete-comment ID=<comment-id>            delete a comment"
	@echo "  make hide ID=<comment-id>                      hide a comment"
	@echo "  make unhide ID=<comment-id>                    unhide a comment"
	@echo ""
	@echo "Insights:"
	@echo "  make insights ID=<post-id> [METRIC=...]"
	@echo "  make page-insights"
	@echo "  make limits                                    rate-limit headers"
	@echo ""
	@echo "Auth / token:"
	@echo "  make auth                                      OAuth -> long-lived user + page token"
	@echo "  make refresh                                   refresh long-lived user token"
	@echo "  make whoami                                    /me + page id + token expiry"
	@echo "  make pages                                     list pages I admin"
	@echo "  make env-check                                 sanity-check .env"
	@echo ""
	@echo "Mentions / webhook:"
	@echo "  make webhook-setup                             register app-level subscription (one-time)"
	@echo "  make subscribe-page                            tell Meta to push events for Dr.蜥蜴"
	@echo "  make mentions [SINCE=<unix_ms>]                poll worker for received events"
	@echo ""
	@echo "Worker:"
	@echo "  make worker-deploy                             redeploy fb-cb worker"
	@echo "  make worker-tail                               tail worker logs"
	@echo ""
	@echo "Demo:"
	@echo "  make smoke                                     full smoke test (write+delete)"
	@echo "  make test                                      pytest suite (offline)"

# ---- Publish ----------------------------------------------------------------

post: ## text post; usage: make post MSG="hello"
	@test -n "$(MSG)" || (echo "usage: make post MSG=\"hello\""; exit 2)
	$(RUN) fb.py post "$(MSG)" $(if $(LINK),--link "$(LINK)",)

post-image: ## image post; usage: make post-image URL=<img> [MSG=...]
	@test -n "$(URL)" || (echo "usage: make post-image URL=<image-url> [MSG=...]"; exit 2)
	$(RUN) fb.py post "$(MSG)" --image-url "$(URL)"

post-video: ## video post; usage: make post-video URL=<video> [MSG=...]
	@test -n "$(URL)" || (echo "usage: make post-video URL=<video-url> [MSG=...]"; exit 2)
	$(RUN) fb.py post "$(MSG)" --video-url "$(URL)"

post-reel: ## publish a Reel; usage: make post-reel VIDEO=<path|url> [MSG=...] [NO_WAIT=1]
	@test -n "$(VIDEO)" || (echo "usage: make post-reel VIDEO=<path-or-url> [MSG=...]"; exit 2)
	$(RUN) fb.py post-reel "$(VIDEO)" "$(MSG)" $(if $(NO_WAIT),--no-wait,)

post-carousel: ## carousel post; usage: make post-carousel URLS="u1,u2" [MSG=...]
	@test -n "$(URLS)" || (echo "usage: make post-carousel URLS=\"u1,u2,...\" [MSG=...]"; exit 2)
	$(RUN) fb.py post "$(MSG)" --carousel-urls "$(URLS)"

post-link: ## link post with OG preview; usage: make post-link URL=<url> MSG="..."
	@test -n "$(URL)" -a -n "$(MSG)" || (echo "usage: make post-link URL=<url> MSG=\"...\""; exit 2)
	$(RUN) fb.py post-link "$(URL)" "$(MSG)"

schedule: ## scheduled post; usage: make schedule MSG="..." WHEN="+2h"
	@test -n "$(MSG)" -a -n "$(WHEN)" || (echo "usage: make schedule MSG=\"...\" WHEN=\"+2h | YYYY-MM-DD HH:MM\""; exit 2)
	$(RUN) fb.py schedule "$(MSG)" --when "$(WHEN)"

# ---- Edit / Delete ---------------------------------------------------------

edit: ## edit a post's text; usage: make edit ID=<post-id> MSG="..."
	@test -n "$(ID)" -a -n "$(MSG)" || (echo "usage: make edit ID=<post-id> MSG=\"...\""; exit 2)
	$(RUN) fb.py edit "$(ID)" "$(MSG)"

delete: ## delete a post
	@test -n "$(ID)" || (echo "usage: make delete ID=<post-id>"; exit 2)
	$(RUN) fb.py delete "$(ID)"

# ---- Read ------------------------------------------------------------------

list: ## list recent posts
	$(RUN) fb.py list $(if $(LIMIT),$(LIMIT),10)

get: ## full post details; usage: make get ID=<post-id>
	@test -n "$(ID)" || (echo "usage: make get ID=<post-id>"; exit 2)
	$(RUN) fb.py get "$(ID)"

comments: ## comments on a post; usage: make comments ID=<post-id>
	@test -n "$(ID)" || (echo "usage: make comments ID=<post-id>"; exit 2)
	$(RUN) fb.py comments "$(ID)"

conversation: ## nested replies under a comment
	@test -n "$(ID)" || (echo "usage: make conversation ID=<comment-id>"; exit 2)
	$(RUN) fb.py conversation "$(ID)"

reactions: ## per-type reaction breakdown
	@test -n "$(ID)" || (echo "usage: make reactions ID=<post-id>"; exit 2)
	$(RUN) fb.py reactions "$(ID)"

# ---- Shortcuts on the latest post -----------------------------------------
# These resolve "the latest post id" via `fb.py latest` so you don't have to
# copy-paste long IDs around.

latest: ## print latest post id, one line (scriptable)
	@$(RUN) fb.py latest

summary: ## latest post + reactions + comment count (one shot)
	$(RUN) fb.py summary

get-latest: ## make get on the latest post
	@ID=$$($(RUN) fb.py latest) && $(RUN) fb.py get "$$ID"

comments-latest: ## make comments on the latest post
	@ID=$$($(RUN) fb.py latest) && $(RUN) fb.py comments "$$ID"

reactions-latest: ## make reactions on the latest post
	@ID=$$($(RUN) fb.py latest) && $(RUN) fb.py reactions "$$ID"

delete-latest: ## make delete on the latest post (with confirmation)
	@ID=$$($(RUN) fb.py latest) && \
	read -p "delete post $$ID? [y/N] " a && [ "$$a" = "y" ] && \
	$(RUN) fb.py delete "$$ID"

# ---- Manage comments -------------------------------------------------------

reply: ## reply to a post or comment; usage: make reply TO=<id> MSG="..."
	@test -n "$(TO)" -a -n "$(MSG)" || (echo "usage: make reply TO=<post-or-comment-id> MSG=\"...\""; exit 2)
	$(RUN) fb.py reply "$(TO)" "$(MSG)"

reply-url: ## reply to a post by its FB permalink (manual mention workflow)
	@test -n "$(URL)" -a -n "$(MSG)" || (echo "usage: make reply-url URL=<fb-post-permalink> MSG=\"...\""; exit 2)
	$(RUN) fb.py reply-url "$(URL)" "$(MSG)"

delete-comment: ## delete a comment
	@test -n "$(ID)" || (echo "usage: make delete-comment ID=<comment-id>"; exit 2)
	$(RUN) fb.py delete-comment "$(ID)"

hide: ## hide a comment
	@test -n "$(ID)" || (echo "usage: make hide ID=<comment-id>"; exit 2)
	$(RUN) fb.py hide "$(ID)"

unhide: ## unhide a comment
	@test -n "$(ID)" || (echo "usage: make unhide ID=<comment-id>"; exit 2)
	$(RUN) fb.py unhide "$(ID)"

# ---- Insights --------------------------------------------------------------

insights: ## post-level insights
	@test -n "$(ID)" || (echo "usage: make insights ID=<post-id> [METRIC=...]"; exit 2)
	$(RUN) fb.py insights "$(ID)" $(if $(METRIC),--metric "$(METRIC)",)

page-insights: ## page-level insights
	$(RUN) fb.py page-insights

limits: ## current rate-limit header readout
	$(RUN) fb.py limits

# ---- Auth / token ----------------------------------------------------------

auth: ## OAuth -> long-lived user + page token
	$(RUN) fb.py auth

refresh: ## refresh long-lived user token
	$(RUN) fb.py refresh

whoami: ## /me + page id + token expiry
	$(RUN) fb.py whoami

pages: ## list pages I admin
	$(RUN) fb.py pages

# ---- Mentions / webhook ----------------------------------------------------

webhook-setup: ## register app-level webhook subscription (one-time, bypasses Meta Dashboard)
	$(RUN) fb.py webhook-setup $(if $(FIELDS),--fields "$(FIELDS)",)

subscribe-page: ## tell Meta to push webhook events for the configured Page
	$(RUN) fb.py subscribe-page $(if $(FIELDS),--fields "$(FIELDS)",)

mentions: ## poll the worker for received webhook events
	$(RUN) fb.py mentions $(if $(SINCE),--since $(SINCE),)

# ---- Demo / worker / env ---------------------------------------------------

smoke: ## smoke test (write + delete)
	$(RUN) fb.py smoke

test: ## run the pytest suite for fb_lib (no network)
	uv run --with pytest --with responses --with requests pytest tests/ -q

worker-deploy:
	cd cloudflare-worker && wrangler deploy

worker-tail:
	cd cloudflare-worker && wrangler tail

worker-delete:
	cd cloudflare-worker && wrangler delete

env-check: ## verify required keys are present in .env
	@test -f .env || (echo ".env missing -- copy .env.example and fill credentials"; exit 1)
	@for k in FB_APP_ID FB_APP_SECRET FB_REDIRECT_URI FB_WORKER_BASE; do \
		grep -q "^$$k=" .env || (echo "missing in .env: $$k"; exit 1); \
	done
	@echo ".env looks ok"
	@grep -q "^FB_PAGE_ACCESS_TOKEN=" .env && echo "(has page token)" || echo "(no page token -- run 'make auth')"
