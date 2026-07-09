"""Tests for the Reels resumable-upload helpers in ``fb_lib``.

Reels use a distinct three-phase flow (start / upload / finish) plus a
status poll, spanning two hosts (``graph.facebook.com`` for start/finish/
status, ``rupload.facebook.com`` for the byte upload). HTTP is mocked with
``responses``; the poll clock is injected so tests never really sleep.
"""

from __future__ import annotations

import pytest
import responses

from fb_lib import (
    GRAPH,
    RUPLOAD,
    ApiError,
    ValidationError,
    fb_post_reel,
    poll_reel_status,
    reel_finish,
    reel_start,
    reel_status,
    reel_upload,
    resolve_video_bytes,
)

PAGE = "111"
TOKEN = "PAGETOKEN"
VID = "999888777"


# ---------- reel_start ----------


@responses.activate
def test_reel_start_returns_video_id():
    responses.add(
        responses.POST,
        f"{GRAPH}/{PAGE}/video_reels",
        json={"video_id": VID, "upload_url": "https://rupload.facebook.com/x"},
        status=200,
    )
    assert reel_start(PAGE, TOKEN) == VID
    sent = responses.calls[0].request.body
    assert "upload_phase=start" in sent


@responses.activate
def test_reel_start_raises_when_no_video_id():
    responses.add(
        responses.POST, f"{GRAPH}/{PAGE}/video_reels", json={}, status=200
    )
    with pytest.raises(ApiError):
        reel_start(PAGE, TOKEN)


# ---------- reel_upload ----------


@responses.activate
def test_reel_upload_bytes_sets_offset_and_filesize():
    responses.add(
        responses.POST, f"{RUPLOAD}/{VID}", json={"success": True}, status=200
    )
    data = b"\x00\x01\x02\x03\x04"
    out = reel_upload(VID, TOKEN, data=data)
    assert out == {"success": True}
    req = responses.calls[0].request
    assert req.headers["Authorization"] == f"OAuth {TOKEN}"
    assert req.headers["offset"] == "0"
    assert req.headers["file_size"] == str(len(data))
    assert req.body == data


@responses.activate
def test_reel_upload_hosted_url_sets_file_url_header_and_no_body():
    responses.add(
        responses.POST, f"{RUPLOAD}/{VID}", json={"success": True}, status=200
    )
    out = reel_upload(VID, TOKEN, file_url="https://cdn.example/v.mp4")
    assert out == {"success": True}
    req = responses.calls[0].request
    assert req.headers["file_url"] == "https://cdn.example/v.mp4"
    assert req.headers["Authorization"] == f"OAuth {TOKEN}"
    assert not req.body


def test_reel_upload_requires_exactly_one_source():
    with pytest.raises(ValidationError):
        reel_upload(VID, TOKEN)  # neither
    with pytest.raises(ValidationError):
        reel_upload(VID, TOKEN, data=b"x", file_url="https://x")  # both


# ---------- reel_finish ----------


@responses.activate
def test_reel_finish_posts_finish_phase_with_description():
    responses.add(
        responses.POST, f"{GRAPH}/{PAGE}/video_reels", json={"success": True}, status=200
    )
    reel_finish(PAGE, TOKEN, VID, description="hello #reel")
    body = responses.calls[0].request.body
    assert "upload_phase=finish" in body
    assert f"video_id={VID}" in body
    assert "video_state=PUBLISHED" in body
    assert "description=hello" in body


# ---------- reel_status ----------


@responses.activate
def test_reel_status_reads_status_field():
    responses.add(
        responses.GET,
        f"{GRAPH}/{VID}",
        json={"status": {"video_status": "ready"}},
        status=200,
    )
    st = reel_status(VID, TOKEN)
    assert st["status"]["video_status"] == "ready"


# ---------- resolve_video_bytes ----------


def test_resolve_video_bytes_reads_local_file(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"MP4DATA")
    assert resolve_video_bytes(str(p)) == b"MP4DATA"


def test_resolve_video_bytes_missing_local_file_raises(tmp_path):
    with pytest.raises(ValidationError):
        resolve_video_bytes(str(tmp_path / "nope.mp4"))


@responses.activate
def test_resolve_video_bytes_downloads_http_url():
    responses.add(
        responses.GET, "https://cdn.example/v.mp4", body=b"REMOTEBYTES", status=200
    )
    assert resolve_video_bytes("https://cdn.example/v.mp4") == b"REMOTEBYTES"


@responses.activate
def test_resolve_video_bytes_download_failure_raises():
    responses.add(responses.GET, "https://cdn.example/v.mp4", status=404)
    with pytest.raises(ApiError):
        resolve_video_bytes("https://cdn.example/v.mp4")


# ---------- poll_reel_status ----------


class _Clock:
    """Deterministic monotonic clock + sleep for poll tests."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


@responses.activate
def test_poll_returns_when_processing_complete():
    responses.add(
        responses.GET,
        f"{GRAPH}/{VID}",
        json={"status": {"processing_phase": {"status": "in_progress"}}},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{GRAPH}/{VID}",
        json={"status": {"processing_phase": {"status": "complete"}}},
        status=200,
    )
    clk = _Clock()
    st = poll_reel_status(VID, TOKEN, interval=3.0, timeout=60.0, now=clk.now, sleep=clk.sleep)
    assert st["status"]["processing_phase"]["status"] == "complete"


@responses.activate
def test_poll_raises_on_processing_error():
    responses.add(
        responses.GET,
        f"{GRAPH}/{VID}",
        json={"status": {"processing_phase": {"status": "error"}}},
        status=200,
    )
    clk = _Clock()
    with pytest.raises(ApiError):
        poll_reel_status(VID, TOKEN, interval=3.0, timeout=60.0, now=clk.now, sleep=clk.sleep)


@responses.activate
def test_poll_times_out():
    responses.add(
        responses.GET,
        f"{GRAPH}/{VID}",
        json={"status": {"processing_phase": {"status": "in_progress"}}},
        status=200,
    )
    clk = _Clock()
    with pytest.raises(ApiError) as ei:
        poll_reel_status(VID, TOKEN, interval=10.0, timeout=20.0, now=clk.now, sleep=clk.sleep)
    assert ei.value.status == 408


# ---------- fb_post_reel (orchestration) ----------


@responses.activate
def test_fb_post_reel_full_flow_local_file(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"BYTES")
    # start
    responses.add(
        responses.POST, f"{GRAPH}/{PAGE}/video_reels", json={"video_id": VID}, status=200
    )
    # upload
    responses.add(responses.POST, f"{RUPLOAD}/{VID}", json={"success": True}, status=200)
    # finish
    responses.add(
        responses.POST, f"{GRAPH}/{PAGE}/video_reels", json={"success": True}, status=200
    )
    # status poll -> ready
    responses.add(
        responses.GET,
        f"{GRAPH}/{VID}",
        json={"status": {"video_status": "ready"}},
        status=200,
    )
    clk = _Clock()
    out = fb_post_reel(
        PAGE, TOKEN, str(p), description="cap",
        poll=True, poll_interval=1.0, poll_timeout=30.0, now=clk.now, sleep=clk.sleep,
    )
    assert out["video_id"] == VID
    assert out["status"]["status"]["video_status"] == "ready"


@responses.activate
def test_fb_post_reel_skip_poll(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"BYTES")
    responses.add(
        responses.POST, f"{GRAPH}/{PAGE}/video_reels", json={"video_id": VID}, status=200
    )
    responses.add(responses.POST, f"{RUPLOAD}/{VID}", json={"success": True}, status=200)
    responses.add(
        responses.POST, f"{GRAPH}/{PAGE}/video_reels", json={"success": True}, status=200
    )
    out = fb_post_reel(PAGE, TOKEN, str(p), description="cap", poll=False)
    assert out["video_id"] == VID
    assert out["status"] is None
