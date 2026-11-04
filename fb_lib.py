"""Facebook Graph API helper library (skeleton).

Pure functions, typed errors, and HTTP wrappers. The CLI lives in
``fb.py`` and only orchestrates these helpers; tests live in
``tests/test_fb_lib.py`` and exercise this module directly.

Many helpers below are STUBS to be filled in during phase 2+ of the
plan in ``docs/plans/2026-05-16-fb-cli-design.md``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import requests

# ---------- Constants ----------

GRAPH_API_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
AUTH_URL = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"
# Reels use a separate resumable-upload host (not graph.facebook.com).
RUPLOAD = f"https://rupload.facebook.com/video-upload/{GRAPH_API_VERSION}"

SCOPES: tuple[str, ...] = (
    "public_profile",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "pages_manage_engagement",
    "pages_read_user_content",
    "pages_manage_metadata",  # required for /subscribed_apps (webhook subscribe)
    "read_insights",
)

# Schedule bounds enforced by Meta (see design doc §4).
SCHEDULE_MIN_DELTA_SEC = 10 * 60
SCHEDULE_MAX_DELTA_SEC = 180 * 24 * 3600  # 6 months

# Carousel bounds (FB's published_via API supports 2-10 children).
CAROUSEL_MIN = 2
CAROUSEL_MAX = 10

# Patterns
RELATIVE_WHEN_RE = re.compile(r"^\+(\d+)([mhd])$")  # +30m / +2h / +1d
ABSOLUTE_WHEN_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$")

# Facebook post permalink: https://www.facebook.com/<owner>/posts/<short>[?...]
PERMALINK_RE = re.compile(
    r"^https?://(?:www\.|m\.|web\.|mbasic\.)?facebook\.com/"
    r"(?P<owner>[^/?#]+)/posts/(?P<short>[^/?#]+)"
)


# ---------- Typed errors ----------


class FbError(Exception):
    """Base class for everything raised by fb_lib."""


class ValidationError(FbError):
    """A caller violated a precondition before we hit the API."""


@dataclass
class ApiError(FbError):
    """HTTP / API call failed. Carries enough structure for callers to react."""

    status: int
    code: int | None
    subcode: int | None
    message: str
    raw: dict | None = None

    def __str__(self) -> str:
        bits = [f"HTTP {self.status}"]
        if self.code is not None:
            bits.append(f"code {self.code}")
        if self.subcode is not None:
            bits.append(f"subcode {self.subcode}")
        return f"[{' / '.join(bits)}] {self.message}"


class AuthError(ApiError):
    """401 / OAuth error. Caller can catch this specifically to attempt refresh."""


def _parse_error(r: requests.Response) -> ApiError:
    try:
        body = r.json()
    except ValueError:
        body = None
    err = body.get("error", {}) if isinstance(body, dict) else {}
    code = err.get("code")
    subcode = err.get("error_subcode")
    message = err.get("message") or (r.text[:500] if r.text else f"HTTP {r.status_code}")
    cls = AuthError if r.status_code == 401 or code == 190 else ApiError
    return cls(status=r.status_code, code=code, subcode=subcode, message=message, raw=body)


# ---------- Config ----------


def _op_write(ref: str, value: str) -> None:
    """Write `value` into the 1Password field named by `op://vault/item/[section/]field`.

    The secret goes to `op item edit` as a JSON item on stdin, never in argv.
    """
    parts = ref[len("op://"):].split("/")
    if len(parts) not in (3, 4):
        raise ValidationError(f"unsupported 1Password reference: {ref}")
    vault, item = parts[0], parts[1]
    section, fld = (parts[2], parts[3]) if len(parts) == 4 else (None, parts[2])
    got = subprocess.run(
        ["op", "item", "get", item, "--vault", vault, "--format", "json"],
        capture_output=True, text=True, timeout=120,
    )
    if got.returncode != 0:
        raise ValidationError(f"op item get failed for {ref}: {got.stderr.strip()}")
    data = json.loads(got.stdout)

    def match(f: dict) -> bool:
        if fld not in (f.get("label"), f.get("id")):
            return False
        sec = f.get("section") or {}
        return section is None or section in (sec.get("label"), sec.get("id"))

    hits = [f for f in data.get("fields", []) if match(f)]
    if len(hits) != 1:
        raise ValidationError(f"{ref}: expected exactly one field, found {len(hits)}")
    hits[0]["value"] = value
    r = subprocess.run(
        ["op", "item", "edit", item, "--vault", vault],
        input=json.dumps(data), capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise ValidationError(f"op item edit failed for {ref}: {r.stderr.strip()}")


@dataclass
class Config:
    """Read/write/merge .env. Falls back to env vars prefixed FB_."""

    env_path: Path
    data: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, env_path: Path) -> "Config":
        data: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"').strip("'")
        for k, v in os.environ.items():
            if k.startswith("FB_"):
                data[k] = v
        return cls(env_path=env_path, data=data)

    def save(self, updates: dict[str, str]) -> None:
        existing = (
            self.env_path.read_text().splitlines() if self.env_path.exists() else []
        )
        seen: set[str] = set()
        out: list[str] = []
        for line in existing:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, cur = (x.strip() for x in stripped.split("=", 1))
                if k in updates and cur.strip('"').strip("'").startswith("op://"):
                    # Secret lives in 1Password: update it there, keep the ref line.
                    _op_write(cur.strip('"').strip("'"), updates[k])
                    seen.add(k)
                    out.append(line)
                    continue
                if k in updates:
                    out.append(f"{k}={updates[k]}")
                    seen.add(k)
                    continue
            out.append(line)
        for k, v in updates.items():
            if k not in seen:
                out.append(f"{k}={v}")
        self.env_path.write_text("\n".join(out).rstrip() + "\n")
        self.data.update(updates)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.data.get(key, default)

    def require(self, *keys: str) -> tuple[str, ...]:
        missing = [k for k in keys if not self.data.get(k)]
        if missing:
            raise ValidationError(f"missing in .env: {', '.join(missing)}")
        return tuple(self.data[k] for k in keys)


# ---------- HTTP wrappers ----------


def gget(path: str, token: str | None, *, timeout: int = 30, **params) -> dict:
    if token is not None:
        params["access_token"] = token
    r = requests.get(f"{GRAPH}{path}", params=params, timeout=timeout)
    if r.status_code != 200:
        raise _parse_error(r)
    return r.json()


def gpost(path: str, token: str, *, timeout: int = 30, **params) -> dict:
    params["access_token"] = token
    r = requests.post(f"{GRAPH}{path}", data=params, timeout=timeout)
    if r.status_code != 200:
        raise _parse_error(r)
    return r.json()


def gdelete(path: str, token: str, *, timeout: int = 30) -> dict:
    r = requests.delete(
        f"{GRAPH}{path}", params={"access_token": token}, timeout=timeout
    )
    if r.status_code != 200:
        raise _parse_error(r)
    return r.json()


# ---------- Reels (resumable upload) ----------
#
# Reels publishing is a three-phase flow (see Meta's video-api reels guide):
#   1. start   POST graph.facebook.com/<page>/video_reels  -> video_id
#   2. upload  POST rupload.facebook.com/.../<video_id>     (bytes or file_url)
#   3. finish  POST graph.facebook.com/<page>/video_reels   (video_state=PUBLISHED)
# followed by a status poll on GET graph.facebook.com/<video_id>?fields=status.


def reel_start(page_id: str, token: str, *, timeout: int = 30) -> str:
    """Phase 1: open an upload session and return its ``video_id``."""
    out = gpost(f"/{page_id}/video_reels", token, timeout=timeout, upload_phase="start")
    vid = out.get("video_id")
    if not vid:
        raise ApiError(
            status=502, code=None, subcode=None,
            message=f"video_reels start returned no video_id: {out}", raw=out,
        )
    return str(vid)


def reel_upload(
    video_id: str,
    token: str,
    *,
    data: bytes | None = None,
    file_url: str | None = None,
    timeout: int = 300,
) -> dict:
    """Phase 2: upload the video, either as raw ``data`` bytes or by handing
    Facebook a hosted ``file_url`` for it to fetch. Exactly one is required.
    """
    if (data is None) == (file_url is None):
        raise ValidationError(
            "reel_upload needs exactly one of data= or file_url="
        )
    url = f"{RUPLOAD}/{video_id}"
    headers = {"Authorization": f"OAuth {token}"}
    if file_url is not None:
        headers["file_url"] = file_url
        r = requests.post(url, headers=headers, timeout=timeout)
    else:
        headers["offset"] = "0"
        headers["file_size"] = str(len(data))
        r = requests.post(url, headers=headers, data=data, timeout=timeout)
    if r.status_code != 200:
        raise _parse_error(r)
    return r.json()


def reel_finish(
    page_id: str,
    token: str,
    video_id: str,
    *,
    description: str | None = None,
    video_state: str = "PUBLISHED",
    timeout: int = 30,
) -> dict:
    """Phase 3: publish (or draft/schedule) the uploaded reel."""
    params: dict[str, str] = {
        "upload_phase": "finish",
        "video_id": video_id,
        "video_state": video_state,
    }
    if description:
        params["description"] = description
    return gpost(f"/{page_id}/video_reels", token, timeout=timeout, **params)


def reel_status(video_id: str, token: str, *, timeout: int = 30) -> dict:
    """Read the reel's processing/publishing status."""
    return gget(f"/{video_id}", token, timeout=timeout, fields="status")


def resolve_video_bytes(
    video: str, *, get=requests.get, timeout: int = 300
) -> bytes:
    """Return raw video bytes from a local path or an ``http(s)`` URL.

    A URL is downloaded here (rather than handed to Facebook as ``file_url``)
    so the byte-upload path works uniformly for local files, CDN links, and
    redirecting hosts like GitHub release assets.
    """
    if re.match(r"^https?://", video, re.IGNORECASE):
        r = get(video, timeout=timeout)
        if r.status_code != 200:
            raise ApiError(
                status=r.status_code, code=None, subcode=None,
                message=f"could not download video from {video}", raw=None,
            )
        return r.content
    p = Path(video).expanduser()
    if not p.is_file():
        raise ValidationError(f"video not found: {video}")
    return p.read_bytes()


def poll_reel_status(
    video_id: str,
    token: str,
    *,
    interval: float = 3.0,
    timeout: float = 300.0,
    now=time.monotonic,
    sleep=time.sleep,
) -> dict:
    """Poll ``reel_status`` until processing completes, errors, or times out.

    ``now`` and ``sleep`` are injectable for tests.
    """
    deadline = now() + timeout
    while now() < deadline:
        st = reel_status(video_id, token)
        phases = st.get("status", {}) if isinstance(st, dict) else {}
        proc = phases.get("processing_phase", {}).get("status")
        vstatus = phases.get("video_status")
        if proc in ("complete", "completed") or vstatus == "ready":
            return st
        if proc == "error" or vstatus == "error":
            raise ApiError(
                status=502, code=None, subcode=None,
                message=f"reel processing failed: {phases}", raw=st,
            )
        sleep(interval)
    raise ApiError(
        status=408, code=None, subcode=None,
        message=f"timed out waiting for reel {video_id} to process after {timeout:.0f}s",
    )


def fb_post_reel(
    page_id: str,
    token: str,
    video: str,
    *,
    description: str | None = None,
    poll: bool = True,
    poll_interval: float = 3.0,
    poll_timeout: float = 300.0,
    now=time.monotonic,
    sleep=time.sleep,
) -> dict:
    """Publish a Reel end-to-end: resolve bytes -> start -> upload -> finish
    -> (optionally) poll until processing completes.

    ``video`` is a local path or an ``http(s)`` URL. Returns
    ``{"video_id": ..., "status": <last status dict or None>}``.
    """
    data = resolve_video_bytes(video)
    video_id = reel_start(page_id, token)
    reel_upload(video_id, token, data=data)
    reel_finish(page_id, token, video_id, description=description)
    status = None
    if poll:
        status = poll_reel_status(
            video_id, token,
            interval=poll_interval, timeout=poll_timeout, now=now, sleep=sleep,
        )
    return {"video_id": video_id, "status": status}


# ---------- OAuth (skeleton) ----------


def build_auth_url(
    client_id: str,
    redirect_uri: str,
    scopes: Iterable[str] | str,
    state: str,
) -> str:
    scope_str = scopes if isinstance(scopes, str) else ",".join(scopes)
    return f"{AUTH_URL}?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope_str,
        "response_type": "code",
        "state": state,
    })


def poll_worker_for_code(
    worker_base: str,
    state: str,
    *,
    interval: float = 2.0,
    timeout: float = 300.0,
    now=time.monotonic,
    sleep=time.sleep,
) -> str:
    """Poll the Cloudflare Worker's /poll endpoint until the code appears.

    ``now`` and ``sleep`` are injectable for tests.
    """
    poll_url = f"{worker_base.rstrip('/')}/poll"
    deadline = now() + timeout
    while now() < deadline:
        try:
            r = requests.get(poll_url, params={"state": state}, timeout=10)
        except requests.RequestException:
            sleep(interval)
            continue
        if r.status_code == 200 and r.text:
            return r.text.strip().split("#", 1)[0]
        sleep(interval)
    raise ApiError(
        status=408, code=None, subcode=None,
        message=f"timed out waiting for OAuth code after {timeout:.0f}s",
    )


def exchange_code_for_short_token(cfg: Config, code: str) -> dict:
    """Exchange the OAuth ?code= for a short-lived (1h) user access token.

    Facebook's endpoint is GET (not POST like Threads).
    """
    app_id, app_secret, redirect_uri = cfg.require(
        "FB_APP_ID", "FB_APP_SECRET", "FB_REDIRECT_URI"
    )
    r = requests.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "client_id": app_id,
            "client_secret": app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise _parse_error(r)
    return r.json()


def exchange_to_long_lived(cfg: Config, short_token: str) -> dict:
    """Exchange short-lived (or expiring long-lived) token for a 60-day token.

    Same endpoint is used to *refresh* an existing long-lived token --
    just call it again with the current token as ``fb_exchange_token``.
    """
    app_id, app_secret = cfg.require("FB_APP_ID", "FB_APP_SECRET")
    r = requests.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise _parse_error(r)
    return r.json()


# Refresh is identical to long-lived exchange; alias for readability.
refresh_long_lived = exchange_to_long_lived


def fetch_pages(cfg: Config, user_token: str) -> list[dict]:
    """Return list of pages this user token can operate on, with per-page tokens.

    Handles two OAuth flows:

    - **Classic FB Login**: ``/me/accounts`` returns pages with their access tokens.
    - **FB Login for Business** (granular scopes): ``/me/accounts`` returns empty;
      page IDs live in the user token's ``granular_scopes.target_ids``. We fetch
      each via ``/<page-id>?fields=id,name,access_token,category,tasks``.

    We try classic first; if empty, fall back to the granular-scopes path. Both
    return the same dict shape so callers don't care which flow was used.
    """
    out = gget("/me/accounts", user_token, fields="id,name,access_token,category,tasks")
    pages = out.get("data", [])
    if pages:
        return pages

    # Fall back: granular_scopes from debug_token. This needs the app token.
    app_id, app_secret = cfg.require("FB_APP_ID", "FB_APP_SECRET")
    app_token = f"{app_id}|{app_secret}"
    r = requests.get(
        f"{GRAPH}/debug_token",
        params={"input_token": user_token, "access_token": app_token},
        timeout=30,
    )
    if r.status_code != 200:
        raise _parse_error(r)
    granular = r.json().get("data", {}).get("granular_scopes", [])
    page_ids: list[str] = []
    seen: set[str] = set()
    for entry in granular:
        if not entry.get("scope", "").startswith("pages_"):
            continue
        for tid in entry.get("target_ids") or []:
            sid = str(tid)
            if sid not in seen:
                seen.add(sid)
                page_ids.append(sid)

    pages = []
    for pid in page_ids:
        try:
            # Note: `tasks` exists on /me/accounts response items but NOT on
            # the Page node itself, so it's omitted here.
            page = gget(
                f"/{pid}", user_token,
                fields="id,name,access_token,category",
            )
            pages.append(page)
        except ApiError:
            # Skip pages we can't read for whatever reason; surface via cmd_pages
            # but don't break auth flow over a single inaccessible page.
            continue
    return pages


def persist_user_token(cfg: Config, token: str, expires_in: int | None) -> None:
    updates = {"FB_USER_ACCESS_TOKEN": token}
    if expires_in is not None:
        updates["FB_USER_TOKEN_EXPIRES_AT"] = str(int(time.time()) + int(expires_in))
    cfg.save(updates)


def persist_page(cfg: Config, page: dict) -> None:
    cfg.save({
        "FB_PAGE_ID": str(page["id"]),
        "FB_PAGE_NAME": str(page.get("name", "")),
        "FB_PAGE_ACCESS_TOKEN": str(page["access_token"]),
    })


def configure_app_webhook(
    cfg: "Config",
    worker_base: str,
    verify_token: str,
    fields: str = "mention,feed",
) -> dict:
    """Register (or update) the app-level webhook subscription via API.

    Bypasses the Meta App Dashboard's Webhooks product (which doesn't show
    up in the sidebar for apps using FB Login for Business). Uses the App
    Access Token (``<app-id>|<app-secret>``, no user OAuth needed). Meta
    silently does the verification handshake against ``<worker>/webhook``.

    Returns the API response dict (``{"success": true}`` on success).
    """
    app_id, app_secret = cfg.require("FB_APP_ID", "FB_APP_SECRET")
    app_token = f"{app_id}|{app_secret}"
    r = requests.post(
        f"{GRAPH}/{app_id}/subscriptions",
        data={
            "object": "page",
            "callback_url": f"{worker_base.rstrip('/')}/webhook",
            "fields": fields,
            "verify_token": verify_token,
            "access_token": app_token,
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise _parse_error(r)
    return r.json()


def list_app_subscriptions(cfg: "Config") -> dict:
    """GET /<app-id>/subscriptions to inspect current app-level subscriptions."""
    app_id, app_secret = cfg.require("FB_APP_ID", "FB_APP_SECRET")
    app_token = f"{app_id}|{app_secret}"
    r = requests.get(
        f"{GRAPH}/{app_id}/subscriptions",
        params={"access_token": app_token},
        timeout=30,
    )
    if r.status_code != 200:
        raise _parse_error(r)
    return r.json()


def fetch_mentions(worker_base: str, verify_token: str, since_ms: int = 0) -> dict:
    """Poll the Cloudflare Worker's /mentions endpoint.

    Returns ``{"events": [...], "count": N}``. ``verify_token`` is the same
    shared secret used in the Meta webhook handshake; it doubles as the
    bearer for this read endpoint.
    """
    r = requests.get(
        f"{worker_base.rstrip('/')}/mentions",
        params={"token": verify_token, "since": since_ms},
        timeout=30,
    )
    if r.status_code != 200:
        raise ApiError(
            status=r.status_code, code=None, subcode=None,
            message=f"worker /mentions: {r.text[:200]}",
        )
    return r.json()


def fmt_expiry(expires_at_unix: str | None) -> str:
    """Format a unix-timestamp string as 'Xd Yh remaining' (or 'EXPIRED')."""
    if not expires_at_unix:
        return "unknown"
    try:
        ts = int(expires_at_unix)
    except (TypeError, ValueError):
        return f"unparseable: {expires_at_unix!r}"
    remaining = ts - int(time.time())
    if remaining <= 0:
        return "EXPIRED"
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    return f"{days}d {hours}h remaining"


# ---------- Schedule time parser ----------


def parse_schedule_time(when: str, *, now=time.time) -> int:
    """Parse a WHEN= argument into a unix timestamp.

    Accepts either:
      - Absolute: "YYYY-MM-DD HH:MM" (local timezone)
      - Relative: "+30m", "+2h", "+1d"
    """
    m = RELATIVE_WHEN_RE.match(when.strip())
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        seconds = {"m": 60, "h": 3600, "d": 86400}[unit] * n
        return int(now()) + seconds

    m = ABSOLUTE_WHEN_RE.match(when.strip())
    if m:
        import datetime as _dt
        y, mo, d, h, mi = map(int, m.groups())
        try:
            dt = _dt.datetime(y, mo, d, h, mi)  # local time
        except ValueError as e:
            raise ValidationError(f"invalid date/time {when!r}: {e}") from e
        return int(dt.timestamp())

    raise ValidationError(
        f"WHEN={when!r} not recognized. "
        "Use '+30m' / '+2h' / '+1d' or 'YYYY-MM-DD HH:MM'."
    )


def parse_fb_permalink(url: str) -> tuple[str, str]:
    """Parse a Facebook post permalink into ``(owner, short_id)``.

    Handles:
      - https://www.facebook.com/<owner-id-or-username>/posts/<short>[?...]
      - https://m.facebook.com/...                                 (mobile)
      - https://www.facebook.com/permalink.php?story_fbid=<s>&id=<o>
      - https://www.facebook.com/story.php?story_fbid=<s>&id=<o>

    The Graph post id is conventionally ``<owner>_<short>``, but the
    permalink's owner segment may differ from ``$FB_PAGE_ID`` (Meta uses
    a user-facing redirect alias). Callers should try the parsed owner
    first, then fall back to ``$FB_PAGE_ID``.
    """
    url = url.strip()
    m = PERMALINK_RE.match(url)
    if m:
        return m.group("owner"), m.group("short")
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("facebook.com") and parsed.path in ("/permalink.php", "/story.php"):
        qs = urllib.parse.parse_qs(parsed.query)
        owner = (qs.get("id") or [None])[0]
        short = (qs.get("story_fbid") or [None])[0]
        if owner and short:
            return owner, short
    raise ValidationError(f"unrecognized FB permalink: {url!r}")


def validate_schedule_time(ts: int, *, now=time.time) -> int:
    """Raise ValidationError if scheduled time is outside Meta's 10min-6mo bounds."""
    delta = ts - int(now())
    if delta < SCHEDULE_MIN_DELTA_SEC:
        raise ValidationError(
            f"scheduled_publish_time must be >= 10 min from now (got {delta}s)"
        )
    if delta > SCHEDULE_MAX_DELTA_SEC:
        raise ValidationError(
            f"scheduled_publish_time must be <= 6 months from now (got {delta}s)"
        )
    return ts
