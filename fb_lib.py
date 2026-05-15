"""Facebook Graph API helper library (skeleton).

Pure functions, typed errors, and HTTP wrappers. The CLI lives in
``fb.py`` and only orchestrates these helpers; tests live in
``tests/test_fb_lib.py`` and exercise this module directly.

Many helpers below are STUBS to be filled in during phase 2+ of the
plan in ``docs/plans/2026-05-16-fb-cli-design.md``.
"""

from __future__ import annotations

import os
import re
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

SCOPES: tuple[str, ...] = (
    "public_profile",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "pages_manage_engagement",
    "pages_read_user_content",
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
                k = stripped.split("=", 1)[0].strip()
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


# --- The following OAuth helpers are STUBS for phase 2. ---
#     exchange_code_for_short_token(cfg, code) -> dict
#     exchange_to_long_lived(cfg, short_token) -> dict
#     refresh_long_lived(cfg, user_token) -> dict
#     fetch_page_token(user_token, page_id) -> dict  (calls /me/accounts and picks page)
#     persist_token(cfg, ...)


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
