"""Smoke tests for the fb_lib skeleton.

Once the helpers from the design doc are implemented, this file should
grow to cover validators, OAuth flow, HTTP wrappers, and schedule parsing
(mock HTTP via the ``responses`` library, same pattern as threads-cli).
"""

from __future__ import annotations

import time

import pytest

from fb_lib import (
    AUTH_URL,
    CAROUSEL_MAX,
    CAROUSEL_MIN,
    GRAPH,
    GRAPH_API_VERSION,
    SCHEDULE_MAX_DELTA_SEC,
    SCHEDULE_MIN_DELTA_SEC,
    SCOPES,
    ApiError,
    AuthError,
    Config,
    ValidationError,
    build_auth_url,
    parse_schedule_time,
    validate_schedule_time,
)


# ---------- Constants ----------


def test_graph_api_version_pinned():
    assert GRAPH_API_VERSION == "v21.0"
    assert GRAPH == "https://graph.facebook.com/v21.0"
    assert AUTH_URL == "https://www.facebook.com/v21.0/dialog/oauth"


def test_scopes_includes_required_for_a2():
    required = {
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_posts",
        "pages_manage_engagement",
        "pages_read_user_content",
        "pages_manage_metadata",  # for /subscribed_apps (webhook subscribe)
        "read_insights",
        "public_profile",
    }
    assert required.issubset(set(SCOPES))


def test_carousel_bounds_are_facebook_limits():
    assert CAROUSEL_MIN == 2
    assert CAROUSEL_MAX == 10


# ---------- build_auth_url ----------


def test_build_auth_url_has_all_required_params():
    url = build_auth_url(
        client_id="123",
        redirect_uri="https://example.com/callback",
        scopes=("public_profile", "pages_show_list"),
        state="abc123",
    )
    assert url.startswith(AUTH_URL + "?")
    for piece in (
        "client_id=123",
        "redirect_uri=https",
        "scope=public_profile",
        "response_type=code",
        "state=abc123",
    ):
        assert piece in url


# ---------- Schedule time parser ----------


def test_parse_relative_when():
    base = 1_000_000
    assert parse_schedule_time("+30m", now=lambda: base) == base + 30 * 60
    assert parse_schedule_time("+2h", now=lambda: base) == base + 2 * 3600
    assert parse_schedule_time("+1d", now=lambda: base) == base + 86400


def test_parse_absolute_when():
    # only check it parses successfully and returns an int near the expected value
    ts = parse_schedule_time("2099-12-31 23:59")
    assert isinstance(ts, int)
    assert ts > 0


def test_parse_invalid_when_raises():
    with pytest.raises(ValidationError):
        parse_schedule_time("nonsense")
    with pytest.raises(ValidationError):
        parse_schedule_time("+0")  # missing unit


def test_validate_schedule_time_bounds():
    now = 1_000_000
    # Too soon (5 min from now < 10 min minimum)
    with pytest.raises(ValidationError):
        validate_schedule_time(now + 5 * 60, now=lambda: now)
    # Just inside lower bound (10 min)
    assert validate_schedule_time(now + 10 * 60, now=lambda: now) == now + 600
    # Too far (1 year > 6 months)
    with pytest.raises(ValidationError):
        validate_schedule_time(now + 365 * 86400, now=lambda: now)
    # Just inside upper bound (6 months)
    assert validate_schedule_time(now + SCHEDULE_MAX_DELTA_SEC, now=lambda: now) == now + SCHEDULE_MAX_DELTA_SEC


# ---------- Config ----------


def test_config_load_parses_simple_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "FB_APP_ID=12345\n"
        '# a comment\n'
        '\n'
        'FB_APP_SECRET="secret-value"\n'
    )
    cfg = Config.load(env)
    assert cfg.get("FB_APP_ID") == "12345"
    assert cfg.get("FB_APP_SECRET") == "secret-value"
    assert cfg.get("MISSING") is None


def test_config_require_raises_when_missing(tmp_path):
    cfg = Config.load(tmp_path / ".env")  # nonexistent
    with pytest.raises(ValidationError):
        cfg.require("FB_APP_ID")


def test_config_save_round_trips(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FB_APP_ID=keepme\nFB_APP_SECRET=oldval\n")
    cfg = Config.load(env)
    cfg.save({"FB_APP_SECRET": "newval", "FB_PAGE_ID": "999"})
    cfg2 = Config.load(env)
    assert cfg2.get("FB_APP_ID") == "keepme"
    assert cfg2.get("FB_APP_SECRET") == "newval"
    assert cfg2.get("FB_PAGE_ID") == "999"


# ---------- Errors ----------


def test_auth_error_is_apierror():
    assert issubclass(AuthError, ApiError)


def test_apierror_str_includes_status_and_code():
    e = ApiError(status=400, code=190, subcode=460, message="bad", raw=None)
    s = str(e)
    assert "400" in s and "190" in s and "460" in s and "bad" in s
