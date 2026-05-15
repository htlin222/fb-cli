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
import sys
from pathlib import Path

from fb_lib import (
    ApiError,
    AuthError,
    Config,
    SCOPES,
    ValidationError,
)

ENV_PATH = Path(__file__).parent / ".env"


def pp(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _todo(name: str) -> None:
    print(f"[TODO] {name} not implemented yet -- see docs/plans/2026-05-16-fb-cli-design.md",
          file=sys.stderr)
    sys.exit(64)


# ---------- Auth / token (phase 2) ----------

def cmd_auth(args: argparse.Namespace) -> None:
    _todo("cmd_auth")


def cmd_refresh(args: argparse.Namespace) -> None:
    _todo("cmd_refresh")


def cmd_whoami(args: argparse.Namespace) -> None:
    _todo("cmd_whoami")


def cmd_pages(args: argparse.Namespace) -> None:
    _todo("cmd_pages")


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
        # Phase 2 will wire up auto-retry via refresh_long_lived(); for now
        # we just surface the error so the user knows to re-auth.
        print(f"[!] auth error: {e}", file=sys.stderr)
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
