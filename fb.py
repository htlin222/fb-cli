# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.32"]
# ///
"""Facebook Pages CLI (thin dispatcher) -- skeleton.

All helpers, validators, and typed errors live in ``fb_lib.py``.
This module only handles:

- argparse subcommands
- pretty-printing
- ``main()``: auto-retry on AuthError

Most ``cmd_*`` functions are stubs that print "TODO" until phase 2+
of ``docs/plans/2026-05-16-fb-cli-design.md`` lands.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import webbrowser
from pathlib import Path

from fb_lib import (
    ApiError,
    AuthError,
    Config,
    SCOPES,
    ValidationError,
    build_auth_url,
    configure_app_webhook,
    exchange_code_for_short_token,
    exchange_to_long_lived,
    fetch_mentions,
    fetch_pages,
    fmt_expiry,
    gdelete,
    gget,
    gpost,
    list_app_subscriptions,
    parse_fb_permalink,
    parse_schedule_time,
    persist_page,
    persist_user_token,
    poll_worker_for_code,
    validate_schedule_time,
)

# Resolve .env relative to the current working directory so that a
# uvx-installed `fb-cli` binary finds the user's local .env (the package
# dir is read-only inside ~/.cache/uv). Falls back to the script's own
# directory when running locally via `uv run fb.py`.
def _resolve_env_path() -> Path:
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env
    sibling = Path(__file__).resolve().parent / ".env"
    if sibling.exists():
        return sibling
    # Neither exists yet -- prefer cwd for first-run setup.
    return cwd_env


ENV_PATH = _resolve_env_path()


def pp(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _todo(name: str) -> None:
    print(f"[TODO] {name} not implemented yet -- see docs/plans/2026-05-16-fb-cli-design.md",
          file=sys.stderr)
    sys.exit(64)


# ---------- Auth / token ----------

def cmd_auth(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    app_id, redirect_uri, worker_base = cfg.require(
        "FB_APP_ID", "FB_REDIRECT_URI", "FB_WORKER_BASE"
    )
    scopes = args.scopes or ",".join(SCOPES)
    state = secrets.token_urlsafe(32)
    url = build_auth_url(app_id, redirect_uri, scopes, state)
    print("\n=== AUTHORIZE ===")
    print(f"Opening this URL in your browser (approve to continue):\n\n  {url}\n")
    print(f"scopes requested: {scopes}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    code = poll_worker_for_code(worker_base, state)
    print("[ok] worker captured code, exchanging for short-lived user token...")
    short = exchange_code_for_short_token(cfg, code)
    short_token = short["access_token"]
    print(f"[ok] short-lived token acquired (expires in {short.get('expires_in', '?')}s)")
    print("[..] exchanging short -> long-lived user token (60d)...")
    longl = exchange_to_long_lived(cfg, short_token)
    long_token = longl["access_token"]
    persist_user_token(cfg, long_token, longl.get("expires_in"))
    days = int(longl.get("expires_in", 0)) // 86400
    print(f"[ok] long-lived user token saved (valid {days}d)")
    print("[..] fetching granted pages...")
    pages = fetch_pages(cfg, long_token)
    if not pages:
        print("[!] you don't admin any pages. fb-cli needs at least one.",
              file=sys.stderr)
        print("    Create a Facebook Page at https://www.facebook.com/pages/create",
              file=sys.stderr)
        sys.exit(1)
    if len(pages) == 1:
        page = pages[0]
        print(f"[ok] auto-selected single page: {page['name']} (id={page['id']})")
    else:
        print(f"[!] you admin {len(pages)} pages:")
        for i, p in enumerate(pages):
            print(f"    [{i}] {p['name']} (id={p['id']})")
        page = pages[0]
        print(f"    auto-selecting [0]; edit FB_PAGE_ID + FB_PAGE_ACCESS_TOKEN in .env to switch")
    cfg = Config.load(ENV_PATH)  # reload after persist_user_token
    persist_page(cfg, page)
    print(f"[ok] page saved: {page['name']}")
    print(f"\nDone. Try: make whoami")


def cmd_refresh(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    user_token = cfg.require("FB_USER_ACCESS_TOKEN")[0]
    print(f"[..] current user token: {fmt_expiry(cfg.get('FB_USER_TOKEN_EXPIRES_AT'))}")
    longl = exchange_to_long_lived(cfg, user_token)
    persist_user_token(cfg, longl["access_token"], longl.get("expires_in"))
    cfg = Config.load(ENV_PATH)
    page_id = cfg.get("FB_PAGE_ID")
    if page_id:
        pages = fetch_pages(cfg, longl["access_token"])
        target = next((p for p in pages if p["id"] == page_id), None)
        if target:
            persist_page(cfg, target)
            print(f"[ok] page token refreshed for {target.get('name')}")
        else:
            print(f"[!] page {page_id} no longer in /me/accounts -- you may have lost admin",
                  file=sys.stderr)
    cfg = Config.load(ENV_PATH)
    print(f"[ok] new user token expiry: {fmt_expiry(cfg.get('FB_USER_TOKEN_EXPIRES_AT'))}")


def cmd_whoami(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    user_token = cfg.require("FB_USER_ACCESS_TOKEN")[0]
    me = gget("/me", user_token, fields="id,name")
    out = {"user": me}
    page_id = cfg.get("FB_PAGE_ID")
    page_token = cfg.get("FB_PAGE_ACCESS_TOKEN")
    if page_id and page_token:
        page = gget(
            f"/{page_id}", page_token,
            fields="id,name,category,fan_count,followers_count,verification_status",
        )
        out["page"] = page
    pp(out)
    print(f"\nuser token: {fmt_expiry(cfg.get('FB_USER_TOKEN_EXPIRES_AT'))}", file=sys.stderr)
    print("page token: permanent (until app revoked or password change)", file=sys.stderr)


def cmd_pages(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    user_token = cfg.require("FB_USER_ACCESS_TOKEN")[0]
    pages = fetch_pages(cfg, user_token)
    # Strip per-page access_token before printing -- it's a secret.
    safe = [
        {k: p.get(k) for k in ("id", "name", "category", "tasks")} for p in pages
    ]
    pp(safe)


def cmd_subscribe_page(args: argparse.Namespace) -> None:
    """POST /<page-id>/subscribed_apps with subscribed_fields=<comma-sep>.

    Tells Meta: "for this Page, push events on these fields to my app's
    configured webhook URL." Required after configuring the webhook in the
    Dashboard, otherwise Meta knows the URL but never sends events for this
    specific Page.
    """
    cfg = Config.load(ENV_PATH)
    page_id, page_token = cfg.require("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")
    # Meta uses singular `mention` (per /subscribed_apps schema).
    fields = args.fields or "mention,feed"
    out = gpost(f"/{page_id}/subscribed_apps", page_token, subscribed_fields=fields)
    pp({"subscribed_fields": fields, "response": out})


def cmd_mentions(args: argparse.Namespace) -> None:
    """Poll the Cloudflare Worker for stored mention events.

    The worker received them via POST /webhook (HMAC-verified). They live
    in KV for 7 days. Pass --since=<unix_ms> to filter out already-seen.
    """
    cfg = Config.load(ENV_PATH)
    worker_base, verify_token = cfg.require(
        "FB_WORKER_BASE", "FB_WEBHOOK_VERIFY_TOKEN"
    )
    body = fetch_mentions(worker_base, verify_token, since_ms=args.since)
    pp(body)


def cmd_webhook_setup(args: argparse.Namespace) -> None:
    """Register/update the app-level webhook subscription via API.

    Bypasses the Meta App Dashboard's Webhooks product UI -- needed because
    apps using FB Login for Business no longer expose that UI in the sidebar.
    Idempotent: re-running just updates the existing subscription.
    """
    cfg = Config.load(ENV_PATH)
    worker_base, verify_token = cfg.require(
        "FB_WORKER_BASE", "FB_WEBHOOK_VERIFY_TOKEN"
    )
    fields = args.fields or "mention,feed"
    print(f"[..] registering {worker_base}/webhook for object=page, fields={fields}")
    res = configure_app_webhook(cfg, worker_base, verify_token, fields=fields)
    print(f"[ok] subscription: {res}")
    print("\n[..] current subscriptions:")
    pp(list_app_subscriptions(cfg))


# ---------- Publish ----------

def cmd_post(args: argparse.Namespace) -> None:
    """Post to /<page-id>/feed (text + optional link) or /<page-id>/photos
    (when ``--image-url`` is set). Video / carousel still deferred.
    """
    cfg = Config.load(ENV_PATH)
    page_id, page_token = cfg.require("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")

    if args.video_url or args.carousel_urls:
        flag = "video-url" if args.video_url else "carousel-urls"
        print(f"[TODO] --{flag} not wired up yet (separate phase)", file=sys.stderr)
        sys.exit(64)

    if args.image_url:
        # Photo post: POST /<page-id>/photos with url + caption.
        # FB returns {"id": "<photo-id>", "post_id": "<page-id>_<post-id>"}.
        params = {"url": args.image_url}
        if args.message:
            params["caption"] = args.message
        out = gpost(f"/{page_id}/photos", page_token, **params)
        pp(out)
        post_id = out.get("post_id") or out.get("id")
    else:
        if not args.message and not args.link:
            raise ValidationError("either MSG=... or --link=... is required for a post")
        params: dict[str, str] = {}
        if args.message:
            params["message"] = args.message
        if args.link:
            params["link"] = args.link
        out = gpost(f"/{page_id}/feed", page_token, **params)
        pp(out)
        post_id = out.get("id")

    if post_id:
        try:
            extra = gget(f"/{post_id}", page_token, fields="permalink_url,created_time")
            print(f"\npermalink: {extra.get('permalink_url', '(unknown)')}", file=sys.stderr)
        except ApiError:
            pass


def cmd_post_link(args: argparse.Namespace) -> None:
    """Dedicated link post: POST /<page-id>/feed with both `link` and `message`.

    Distinct from `make post MSG="..." LINK="..."` in semantics: the link
    field makes FB render this as a Link post (with OG preview card) rather
    than a Text post that happens to contain a URL.
    """
    cfg = Config.load(ENV_PATH)
    page_id, page_token = cfg.require("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")
    out = gpost(
        f"/{page_id}/feed", page_token,
        link=args.url,
        message=args.message,
    )
    pp(out)
    post_id = out.get("id")
    if post_id:
        try:
            extra = gget(f"/{post_id}", page_token, fields="permalink_url")
            print(f"\npermalink: {extra.get('permalink_url', '(unknown)')}", file=sys.stderr)
        except ApiError:
            pass


def cmd_schedule(args: argparse.Namespace) -> None:
    """Schedule a text post for future publication.

    Uses parse_schedule_time() + validate_schedule_time() shipped in
    phase 1; the latter rejects anything outside Meta's 10-min-to-6-month
    window before round-tripping to the API.
    """
    cfg = Config.load(ENV_PATH)
    page_id, page_token = cfg.require("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")
    ts = validate_schedule_time(parse_schedule_time(args.when))
    print(f"[..] scheduled for unix={ts}", file=sys.stderr)
    out = gpost(
        f"/{page_id}/feed", page_token,
        message=args.message,
        published="false",
        scheduled_publish_time=ts,
    )
    pp(out)


def cmd_edit(args: argparse.Namespace) -> None:
    """POST /<post-id> with `message=<new>` to update the post text.

    FB only allows editing `message` -- media is immutable. Page admin
    can edit any post the Page owns; we fail-fast if the post id prefix
    doesn't match $FB_PAGE_ID (Meta also enforces, but cheap to check).
    """
    cfg = Config.load(ENV_PATH)
    page_id, page_token = cfg.require("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")
    # Graph post ids look like <owner>_<short>; fail-fast on cross-page edits
    if "_" in args.id and not args.id.startswith(f"{page_id}_"):
        raise ValidationError(
            f"post {args.id!r} doesn't belong to FB_PAGE_ID {page_id} -- can't edit"
        )
    out = gpost(f"/{args.id}", page_token, message=args.message)
    pp(out)


def cmd_delete(args: argparse.Namespace) -> None:
    """DELETE /<post-id>. Idempotent on Meta side: second call 400s."""
    cfg = Config.load(ENV_PATH)
    page_token = cfg.require("FB_PAGE_ACCESS_TOKEN")[0]
    out = gdelete(f"/{args.id}", page_token)
    pp(out)


# ---------- Read ----------

# Fields requested when listing or fetching a single post.
_POST_FIELDS = (
    "id,message,story,created_time,updated_time,permalink_url,"
    "status_type,is_published,shares,attachments{type,title,media,url}"
)

# Fields requested when listing comments.
_COMMENT_FIELDS = (
    "id,from{id,name},message,created_time,like_count,comment_count,can_hide"
)

REACTION_TYPES = ("LIKE", "LOVE", "WOW", "HAHA", "SAD", "ANGRY")


def cmd_list(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    page_id, page_token = cfg.require("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")
    out = gget(
        f"/{page_id}/posts", page_token,
        fields=_POST_FIELDS,
        limit=args.limit,
    )
    pp(out.get("data", []))


def cmd_get(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    page_token = cfg.require("FB_PAGE_ACCESS_TOKEN")[0]
    out = gget(f"/{args.id}", page_token, fields=_POST_FIELDS)
    pp(out)


def cmd_comments(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    page_token = cfg.require("FB_PAGE_ACCESS_TOKEN")[0]
    out = gget(
        f"/{args.id}/comments", page_token,
        fields=_COMMENT_FIELDS,
        order="reverse_chronological",
    )
    pp(out.get("data", []))


def cmd_conversation(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    page_token = cfg.require("FB_PAGE_ACCESS_TOKEN")[0]
    # Nested replies under a comment use the same /comments edge on the comment.
    out = gget(
        f"/{args.id}/comments", page_token,
        fields=_COMMENT_FIELDS,
    )
    pp(out.get("data", []))


def cmd_reactions(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    page_token = cfg.require("FB_PAGE_ACCESS_TOKEN")[0]
    pp(_reactions_for(args.id, page_token))


def _reactions_for(post_id: str, page_token: str) -> dict:
    """GET per-type reaction counts for a post via Graph field expansion.

    One round-trip thanks to aliased subqueries on the post node.
    """
    fields = ",".join(
        f"reactions.type({t}).summary(total_count).limit(0).as({t.lower()})"
        for t in REACTION_TYPES
    )
    out = gget(f"/{post_id}", page_token, fields=f"id,{fields}")
    breakdown = {
        t: out.get(t.lower(), {}).get("summary", {}).get("total_count", 0)
        for t in REACTION_TYPES
    }
    breakdown["TOTAL"] = sum(breakdown.values())
    return {"id": out["id"], "reactions": breakdown}


def cmd_latest(args: argparse.Namespace) -> None:
    """Print just the most recent post id, one line. Designed for piping.

    Usage:  ID=$(make -s latest) make get ID=$ID  /  make insights ID=$ID
    """
    cfg = Config.load(ENV_PATH)
    page_id, page_token = cfg.require("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")
    out = gget(f"/{page_id}/posts", page_token, fields="id", limit=1)
    data = out.get("data", [])
    if not data:
        print("(no posts on this page)", file=sys.stderr)
        sys.exit(1)
    print(data[0]["id"])


def cmd_summary(args: argparse.Namespace) -> None:
    """Latest post + reaction breakdown + comment count, in one shot.

    The day-to-day 'how's my latest post doing?' helper.
    """
    cfg = Config.load(ENV_PATH)
    page_id, page_token = cfg.require("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")
    out = gget(
        f"/{page_id}/posts", page_token,
        fields=_POST_FIELDS + ",comments.summary(total_count).limit(0)",
        limit=1,
    )
    posts = out.get("data", [])
    if not posts:
        print("(no posts on this page)", file=sys.stderr)
        sys.exit(1)
    post = posts[0]
    post_id = post["id"]
    comment_count = post.pop("comments", {}).get("summary", {}).get("total_count", 0)
    reactions = _reactions_for(post_id, page_token)["reactions"]
    pp({"post": post, "reactions": reactions, "comment_count": comment_count})


# ---------- Manage comments ----------

def cmd_reply(args: argparse.Namespace) -> None:
    """POST /<parent-id>/comments. The parent can be a post OR a comment.

    For Page-owned posts this just works. For non-Page posts (e.g., a user's
    own post that tags the Page), Meta may gate behind Page Public Content
    Access -- expect code 10 in that case.
    """
    cfg = Config.load(ENV_PATH)
    page_token = cfg.require("FB_PAGE_ACCESS_TOKEN")[0]
    out = gpost(f"/{args.to}/comments", page_token, message=args.message)
    pp(out)


def cmd_reply_url(args: argparse.Namespace) -> None:
    """Reply to a post identified by its FB permalink (semi-manual mention flow).

    Workflow: user sees a tag in FB notifications, copies the post permalink,
    runs ``make reply-url URL=<permalink> MSG="..."``. We parse the permalink
    into ``<owner>_<short>``, then POST /<id>/comments. On failure, falls
    back to ``$FB_PAGE_ID`` as the owner (handles Meta's user-facing alias
    that differs from the Graph-API id).
    """
    cfg = Config.load(ENV_PATH)
    page_token = cfg.require("FB_PAGE_ACCESS_TOKEN")[0]
    owner, short = parse_fb_permalink(args.url)
    post_id = f"{owner}_{short}"
    print(f"[..] permalink -> post id: {post_id}", file=sys.stderr)
    try:
        out = gpost(f"/{post_id}/comments", page_token, message=args.message)
    except ApiError as e:
        page_id = cfg.get("FB_PAGE_ID")
        if page_id and page_id != owner:
            fallback = f"{page_id}_{short}"
            print(f"[!] {e}; retrying with FB_PAGE_ID owner: {fallback}", file=sys.stderr)
            out = gpost(f"/{fallback}/comments", page_token, message=args.message)
        else:
            raise
    pp(out)


def cmd_delete_comment(args: argparse.Namespace) -> None:
    _todo("cmd_delete_comment")


def cmd_hide(args: argparse.Namespace) -> None:
    _todo("cmd_hide")


def cmd_unhide(args: argparse.Namespace) -> None:
    _todo("cmd_unhide")


# ---------- Insights ----------

# Per-post defaults. Probed against Graph v21.0 on 2026-05-17 -- many
# historical metric names were retired (post_impressions, post_engaged_users,
# post_activity, post_negative_feedback...). Keep this list to *_unique
# + clicks + reactions + video, which are the ones still accepted.
DEFAULT_POST_METRICS = (
    "post_impressions_unique,"
    "post_clicks,"
    "post_reactions_by_type_total,"
    "post_video_views"
)

# Page-level defaults. Same v21.0 probe: page_impressions, page_fans, and
# page_fan_adds are gone. _unique variant + post_engagements + views + total
# actions are what's left.
DEFAULT_PAGE_METRICS = (
    "page_impressions_unique,"
    "page_post_engagements,"
    "page_views_total,"
    "page_total_actions"
)


def cmd_insights(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    page_token = cfg.require("FB_PAGE_ACCESS_TOKEN")[0]
    metric = args.metric or DEFAULT_POST_METRICS
    out = gget(f"/{args.id}/insights", page_token, metric=metric)
    pp(out)


def cmd_page_insights(args: argparse.Namespace) -> None:
    cfg = Config.load(ENV_PATH)
    page_id, page_token = cfg.require("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")
    metric = args.metric or DEFAULT_PAGE_METRICS
    params: dict[str, str] = {"metric": metric}
    if args.since:
        params["since"] = args.since
    if args.until:
        params["until"] = args.until
    if args.period:
        params["period"] = args.period
    out = gget(f"/{page_id}/insights", page_token, **params)
    pp(out)


def cmd_limits(args: argparse.Namespace) -> None:
    """Probe rate-limit headers via a cheap GET /me call.

    FB doesn't expose a counter endpoint -- usage lives in response
    headers on every request. We poke /me and surface the three usage
    headers Meta documents.
    """
    cfg = Config.load(ENV_PATH)
    user_token = cfg.require("FB_USER_ACCESS_TOKEN")[0]
    import requests as _r  # local: keeps top-of-file imports clean
    r = _r.get(
        f"https://graph.facebook.com/v21.0/me",
        params={"access_token": user_token, "fields": "id"},
        timeout=15,
    )
    out = {
        "http_status": r.status_code,
        "x_app_usage": r.headers.get("x-app-usage"),
        "x_page_usage": r.headers.get("x-page-usage"),
        "x_business_use_case_usage": r.headers.get("x-business-use-case-usage"),
        "x_fb_api_caller_class": r.headers.get("x-fb-api-caller-class"),
    }
    pp(out)


# ---------- Smoke ----------

def cmd_smoke(args: argparse.Namespace) -> None:
    _todo("cmd_smoke")


# ---------- argparse ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fb", description="Facebook Pages CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    # Auth / env
    a = sub.add_parser("auth"); a.set_defaults(func=cmd_auth)
    a.add_argument("--scopes", help="override default scopes (comma-separated)")
    sub.add_parser("refresh").set_defaults(func=cmd_refresh)
    sub.add_parser("whoami").set_defaults(func=cmd_whoami)
    sub.add_parser("pages").set_defaults(func=cmd_pages)
    sub.add_parser("limits").set_defaults(func=cmd_limits)

    sp = sub.add_parser("subscribe-page"); sp.set_defaults(func=cmd_subscribe_page)
    sp.add_argument("--fields", help="comma-separated (default: mention,feed)")

    ws = sub.add_parser("webhook-setup"); ws.set_defaults(func=cmd_webhook_setup)
    ws.add_argument("--fields", help="comma-separated (default: mention,feed)")

    mn = sub.add_parser("mentions"); mn.set_defaults(func=cmd_mentions)
    mn.add_argument("--since", type=int, default=0, help="unix ms; show events newer than")

    # Publish
    pp_ = sub.add_parser("post"); pp_.set_defaults(func=cmd_post)
    pp_.add_argument("message", nargs="?", default="")
    pp_.add_argument("--image-url")
    pp_.add_argument("--video-url")
    pp_.add_argument("--carousel-urls")
    pp_.add_argument("--link")

    pl = sub.add_parser("post-link"); pl.set_defaults(func=cmd_post_link)
    pl.add_argument("url")
    pl.add_argument("message")

    sc = sub.add_parser("schedule"); sc.set_defaults(func=cmd_schedule)
    sc.add_argument("message")
    sc.add_argument("--when", required=True)

    ed = sub.add_parser("edit"); ed.set_defaults(func=cmd_edit)
    ed.add_argument("id")
    ed.add_argument("message")

    dl = sub.add_parser("delete"); dl.set_defaults(func=cmd_delete)
    dl.add_argument("id")

    # Read
    ls = sub.add_parser("list"); ls.set_defaults(func=cmd_list)
    ls.add_argument("limit", type=int, nargs="?", default=10)

    g = sub.add_parser("get"); g.set_defaults(func=cmd_get)
    g.add_argument("id")

    c = sub.add_parser("comments"); c.set_defaults(func=cmd_comments)
    c.add_argument("id")

    cv = sub.add_parser("conversation"); cv.set_defaults(func=cmd_conversation)
    cv.add_argument("id")

    rx = sub.add_parser("reactions"); rx.set_defaults(func=cmd_reactions)
    rx.add_argument("id")

    # DX helpers: refer to the latest post by 'shape' instead of a long id
    sub.add_parser("latest").set_defaults(func=cmd_latest)
    sub.add_parser("summary").set_defaults(func=cmd_summary)

    # Manage
    rp = sub.add_parser("reply"); rp.set_defaults(func=cmd_reply)
    rp.add_argument("to")
    rp.add_argument("message")

    ru = sub.add_parser("reply-url"); ru.set_defaults(func=cmd_reply_url)
    ru.add_argument("url")
    ru.add_argument("message")

    dc = sub.add_parser("delete-comment"); dc.set_defaults(func=cmd_delete_comment)
    dc.add_argument("id")

    h = sub.add_parser("hide"); h.set_defaults(func=cmd_hide)
    h.add_argument("id")

    uh = sub.add_parser("unhide"); uh.set_defaults(func=cmd_unhide)
    uh.add_argument("id")

    # Insights
    ins = sub.add_parser("insights"); ins.set_defaults(func=cmd_insights)
    ins.add_argument("id")
    ins.add_argument("--metric")

    pi = sub.add_parser("page-insights"); pi.set_defaults(func=cmd_page_insights)
    pi.add_argument("--metric")
    pi.add_argument("--since")
    pi.add_argument("--until")
    pi.add_argument("--period", help="day|week|days_28|month|lifetime (default: day)")

    # Smoke
    sub.add_parser("smoke").set_defaults(func=cmd_smoke)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except AuthError as e:
        if args.cmd in ("auth", "refresh"):
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[!] auth error: {e}", file=sys.stderr)
        cfg = Config.load(ENV_PATH)
        user_token = cfg.get("FB_USER_ACCESS_TOKEN")
        page_id = cfg.get("FB_PAGE_ID")
        if not user_token or not page_id:
            print("    no token/page in .env; run: make auth", file=sys.stderr)
            sys.exit(1)
        try:
            print("[..] refreshing user token + re-fetching page token...", file=sys.stderr)
            longl = exchange_to_long_lived(cfg, user_token)
            persist_user_token(cfg, longl["access_token"], longl.get("expires_in"))
            cfg = Config.load(ENV_PATH)
            pages = fetch_pages(cfg, longl["access_token"])
            target = next((p for p in pages if p["id"] == page_id), None)
            if not target:
                print(f"[!] page {page_id} no longer in /me/accounts; run: make auth",
                      file=sys.stderr)
                sys.exit(1)
            persist_page(cfg, target)
            print("[ok] refreshed; retrying...", file=sys.stderr)
            args.func(args)
        except ApiError as refresh_err:
            print(f"[!] refresh also failed: {refresh_err}", file=sys.stderr)
            print("    run: make auth", file=sys.stderr)
            sys.exit(1)
    except ValidationError as e:
        print(f"INPUT ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except ApiError as e:
        print(f"API ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
