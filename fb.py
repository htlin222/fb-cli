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
    exchange_code_for_short_token,
    exchange_to_long_lived,
    fetch_pages,
    fmt_expiry,
    gget,
    persist_page,
    persist_user_token,
    poll_worker_for_code,
)

ENV_PATH = Path(__file__).parent / ".env"


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


# ---------- Publish (phase 4) ----------

def cmd_post(args: argparse.Namespace) -> None:
    _todo("cmd_post")


def cmd_post_link(args: argparse.Namespace) -> None:
    _todo("cmd_post_link")


def cmd_schedule(args: argparse.Namespace) -> None:
    _todo("cmd_schedule")


def cmd_edit(args: argparse.Namespace) -> None:
    _todo("cmd_edit")


def cmd_delete(args: argparse.Namespace) -> None:
    _todo("cmd_delete")


# ---------- Read (phase 3) ----------

def cmd_list(args: argparse.Namespace) -> None:
    _todo("cmd_list")


def cmd_get(args: argparse.Namespace) -> None:
    _todo("cmd_get")


def cmd_comments(args: argparse.Namespace) -> None:
    _todo("cmd_comments")


def cmd_conversation(args: argparse.Namespace) -> None:
    _todo("cmd_conversation")


def cmd_reactions(args: argparse.Namespace) -> None:
    _todo("cmd_reactions")


# ---------- Manage comments (phase 5) ----------

def cmd_reply(args: argparse.Namespace) -> None:
    _todo("cmd_reply")


def cmd_delete_comment(args: argparse.Namespace) -> None:
    _todo("cmd_delete_comment")


def cmd_hide(args: argparse.Namespace) -> None:
    _todo("cmd_hide")


def cmd_unhide(args: argparse.Namespace) -> None:
    _todo("cmd_unhide")


# ---------- Insights (phase 7) ----------

def cmd_insights(args: argparse.Namespace) -> None:
    _todo("cmd_insights")


def cmd_page_insights(args: argparse.Namespace) -> None:
    _todo("cmd_page_insights")


def cmd_limits(args: argparse.Namespace) -> None:
    _todo("cmd_limits")


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

    # Manage
    rp = sub.add_parser("reply"); rp.set_defaults(func=cmd_reply)
    rp.add_argument("to")
    rp.add_argument("message")

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

    sub.add_parser("page-insights").set_defaults(func=cmd_page_insights)

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
