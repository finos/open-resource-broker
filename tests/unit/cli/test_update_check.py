"""Opt-in update notice: gating, caching, comparison and failure handling."""

import contextlib
import io
import json
import socket
import threading
import time
import urllib.request
from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.cli import update_check
from orb.cli.update_check import is_newer, parse_release, start


class _Config:
    def __init__(self, cache_dir, enabled=True):
        self._cache_dir = str(cache_dir)
        self._enabled = enabled

    def get_configuration_value(self, key, default=None):
        return self._enabled if key == "cli.update_check" else default

    def get_cache_dir(self):
        return self._cache_dir


class _Response:
    def __init__(self, body, url=update_check.PYPI_URL):
        self._body = body
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self):
        return self._url

    def read(self, n=-1):
        return self._body


def _pypi_body(version):
    return json.dumps({"info": {"version": version}}).encode()


class _Stream(io.StringIO):
    tty = True

    def isatty(self):
        return self.tty


_Captured = namedtuple("_Captured", "out err")


class _Streams:
    def __init__(self):
        self.out, self.err = _Stream(), _Stream()

    def run(self, args, config, version, wait=True):
        """Start the check, optionally let the lookup finish, then finish like main does."""
        # Streams are swapped only for the call: pytest re-installs its own between phases.
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            check = start(args, config, version, MagicMock())
            if check is not None:
                if wait and check._thread is not None:
                    check._thread.join(5)
                check.finish()
        return check

    def read(self):
        return _Captured(self.out.getvalue(), self.err.getvalue())


@pytest.fixture
def tty():
    return _Streams()


@pytest.fixture
def network(monkeypatch):
    """Replace the opener so no real request is made; records each call."""
    calls = MagicMock()
    opener = MagicMock()

    def _open(req, timeout=None):
        calls(req.full_url, timeout)
        return calls.response

    opener.open.side_effect = _open
    calls.response = _Response(_pypi_body("9.9.9"))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: opener)
    return calls


def _args(**kw):
    return SimpleNamespace(**{"quiet": False, "format": "table", **kw})


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("1.11.0", "1.10.0", True),
        ("1.10.0", "1.10.0", False),
        ("1.9.0", "1.10.0", False),
        ("1.10", "1.10.0", False),
        ("1.10.1", "1.10", True),
        ("2.0.0", "1.10.0rc1", True),
        ("1.10.0", "1.10.0.dev3", False),
        ("1.10.0", "0.1.0.dev0", True),
        ("garbage", "1.0.0", False),
        ("1.0.0", "garbage", False),
        ("", "1.0.0", False),
        (None, "1.0.0", False),
        (7, "1.0.0", False),
    ],
)
def test_is_newer(latest, current, expected):
    assert is_newer(latest, current) is expected


def test_parse_release_ignores_suffix():
    assert parse_release("v1.2.3.post1") == (1, 2, 3)
    assert parse_release("abc") is None


def test_disabled_makes_no_network_call(tmp_path, tty, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network used while update_check is off")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(urllib.request, "build_opener", _boom)

    tty.run(_args(), _Config(tmp_path, enabled=False), "1.0.0")

    assert tty.read().err == ""
    assert not list(tmp_path.iterdir())


def test_non_bool_setting_is_not_enabled(tmp_path, tty, network):
    tty.run(_args(), _Config(tmp_path, enabled="yes"), "1.0.0")
    network.assert_not_called()


def test_newer_release_prints_one_stderr_line(tmp_path, tty, network):
    tty.run(_args(), _Config(tmp_path), "1.0.0")

    out, err = tty.read()
    assert out == ""
    assert err.count("\n") == 1
    assert "9.9.9" in err and "1.0.0" in err
    network.assert_called_once_with(update_check.PYPI_URL, 2.0)


def test_equal_release_is_silent(tmp_path, tty, network):
    tty.run(_args(), _Config(tmp_path), "9.9.9")
    assert tty.read() == ("", "")


def test_cache_hit_skips_network(tmp_path, tty, network):
    (tmp_path / "update_check.json").write_text(
        json.dumps({"checked_at": time.time() - 60, "latest": "5.0.0"})
    )
    tty.run(_args(), _Config(tmp_path), "1.0.0")

    network.assert_not_called()
    assert "5.0.0" in tty.read().err


def test_expired_cache_refetches_and_rewrites(tmp_path, tty, network):
    cache = tmp_path / "update_check.json"
    cache.write_text(json.dumps({"checked_at": time.time() - 25 * 3600, "latest": "5.0.0"}))
    tty.run(_args(), _Config(tmp_path), "1.0.0")

    network.assert_called_once()
    assert json.loads(cache.read_text())["latest"] == "9.9.9"


@pytest.mark.parametrize("error", [TimeoutError("slow"), OSError("down"), ValueError("bad")])
def test_request_error_is_silent_and_negative_cached(tmp_path, tty, monkeypatch, error):
    opener = MagicMock()
    opener.open.side_effect = error
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: opener)

    tty.run(_args(), _Config(tmp_path), "1.0.0")
    tty.run(_args(), _Config(tmp_path), "1.0.0")

    assert tty.read() == ("", "")
    assert opener.open.call_count == 1
    assert json.loads((tmp_path / "update_check.json").read_text())["latest"] is None


@pytest.mark.parametrize(
    "body", [b"not json", b"{}", json.dumps({"info": {"version": 3}}).encode()]
)
def test_bad_payload_is_silent(tmp_path, tty, network, body):
    network.response = _Response(body)
    tty.run(_args(), _Config(tmp_path), "1.0.0")
    assert tty.read() == ("", "")


def test_corrupt_cache_is_ignored(tmp_path, tty, network):
    (tmp_path / "update_check.json").write_text("{{{")
    tty.run(_args(), _Config(tmp_path), "1.0.0")
    assert "9.9.9" in tty.read().err


def test_unwritable_cache_dir_is_silent(tmp_path, tty, network):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    tty.run(_args(), _Config(blocker / "sub"), "1.0.0")
    assert "9.9.9" in tty.read().err


@pytest.mark.parametrize("args", [_args(format="json"), _args(format="yaml"), _args(quiet=True)])
def test_machine_readable_or_quiet_is_silent(tmp_path, tty, network, args):
    tty.run(args, _Config(tmp_path), "1.0.0")
    network.assert_not_called()
    assert tty.read() == ("", "")


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_non_tty_is_silent(tmp_path, tty, network, stream):
    getattr(tty, "out" if stream == "stdout" else "err").tty = False
    tty.run(_args(), _Config(tmp_path), "1.0.0")
    network.assert_not_called()
    assert tty.read() == ("", "")


def test_broken_config_never_raises(tty):
    tty.run(_args(), SimpleNamespace(), "1.0.0")


class TestRedirectPolicy:
    handler = update_check._SamePypiRedirect()

    def _follow(self, url):
        req = urllib.request.Request(update_check.PYPI_URL)
        return self.handler.redirect_request(req, None, 302, "Found", {}, url)

    def test_same_host_https_followed(self):
        assert self._follow("https://pypi.org/pypi/orb-py/1.0/json") is not None

    @pytest.mark.parametrize(
        "url",
        [
            "http://pypi.org/pypi/orb-py/json",
            "https://example.com/pypi/orb-py/json",
            "https://pypi.org.example.com/json",
            "file:///etc/passwd",
            "https://evil@pypi.org/x",
            "https://pypi.org:8443/x",
            "https://evil.com\\@pypi.org/x",
        ],
    )
    def test_other_targets_refused(self, url):
        assert self._follow(url) is None


def test_main_hook_swallows_failures(monkeypatch):
    from orb.cli import main as cli_main

    monkeypatch.setattr(cli_main, "get_container", MagicMock(side_effect=RuntimeError("boom")))
    assert cli_main._start_update_check(_args()) is None


@pytest.mark.parametrize("resource", ["server", "mcp"])
def test_server_and_mcp_are_skipped(tmp_path, tty, network, resource):
    assert tty.run(_args(resource=resource), _Config(tmp_path), "1.0.0") is None
    network.assert_not_called()
    assert tty.read() == ("", "")


def test_slow_lookup_does_not_delay_and_fills_cache(tmp_path, tty, monkeypatch):
    release = threading.Event()
    opener = MagicMock()

    def _slow(req, timeout=None):
        release.wait(5)
        return _Response(_pypi_body("9.9.9"))

    opener.open.side_effect = _slow
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: opener)

    began = time.monotonic()
    check = tty.run(_args(), _Config(tmp_path), "1.0.0", wait=False)
    elapsed = time.monotonic() - began
    assert elapsed < 1.0
    assert tty.read() == ("", "")

    release.set()
    check._thread.join(5)
    assert json.loads((tmp_path / "update_check.json").read_text())["latest"] == "9.9.9"
    # Next run uses the cache and notifies.
    tty.run(_args(), _Config(tmp_path), "1.0.0")
    assert "9.9.9" in tty.read().err


@pytest.mark.parametrize(
    "version",
    ["1" * 5000, "9" * 65, "1.0.0" + "\x1b[31mred", "١.٢.٣", "1." + "1." * 10 + "1"],
)
def test_hostile_versions_are_never_printed_raw(tmp_path, tty, network, version):
    network.response = _Response(_pypi_body(version))
    tty.run(_args(), _Config(tmp_path), "0.0.1")
    assert "\x1b" not in tty.read().err


def test_escape_suffix_prints_normalised_release_only(tmp_path, tty, network):
    network.response = _Response(_pypi_body("2.1.0\x1b[31m\nforged"))
    tty.run(_args(), _Config(tmp_path), "1.0.0")
    err = tty.read().err
    assert "2.1.0 " in err and "\x1b" not in err and "forged" not in err
    assert err.count("\n") == 1


@pytest.mark.parametrize("value", ["1" * 5000, "١.٢.٣", "1.1.1.1.1.1.1", "\x1b[0m1.0", " 1.0"])
def test_parse_release_rejects(value):
    assert parse_release(value) is None


def test_hung_lookup_is_not_retried_by_next_run(tmp_path, tty, monkeypatch):
    release = threading.Event()
    opener = MagicMock()
    opener.open.side_effect = lambda req, timeout=None: release.wait(5) and None
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: opener)
    try:
        first = tty.run(_args(), _Config(tmp_path), "1.0.0", wait=False)
        assert first._thread is not None
        second = tty.run(_args(), _Config(tmp_path), "1.0.0", wait=False)
        assert second._thread is None
        assert opener.open.call_count == 1
    finally:
        release.set()
        first._thread.join(5)


def _cache_entry(tmp_path, latest, age_seconds, now):
    (tmp_path / "update_check.json").write_text(
        json.dumps({"checked_at": now - age_seconds, "latest": latest})
    )


@pytest.mark.parametrize(
    ("latest", "age", "expect_fetch"),
    [
        (None, 59 * 60, False),
        (None, 61 * 60, True),
        ("5.0.0", 23 * 3600, False),
        ("5.0.0", 24 * 3600 + 1, True),
    ],
)
def test_cache_expiry_with_frozen_time(
    tmp_path, tty, network, monkeypatch, latest, age, expect_fetch
):
    now = 1_800_000_000.0
    monkeypatch.setattr(update_check.time, "time", lambda: now)
    # A failure entry is written pre-aged so it expires one hour after the attempt.
    failure_age = age + (24 * 3600 - 3600) if latest is None else age
    _cache_entry(tmp_path, latest, failure_age, now)
    check = tty.run(_args(), _Config(tmp_path), "1.0.0")
    assert (check._thread is not None) is expect_fetch
    assert (network.call_count > 0) is expect_fetch


@pytest.mark.parametrize(("minutes", "expect_fetch"), [(59, False), (61, True)])
def test_failed_attempt_expires_after_an_hour(tmp_path, tty, monkeypatch, minutes, expect_fetch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(update_check.time, "time", lambda: clock[0])
    opener = MagicMock()
    opener.open.side_effect = OSError("down")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: opener)

    tty.run(_args(), _Config(tmp_path), "1.0.0")
    assert opener.open.call_count == 1
    clock[0] += minutes * 60
    tty.run(_args(), _Config(tmp_path), "1.0.0")
    assert (opener.open.call_count == 2) is expect_fetch


def test_temp_file_cleanup_failure_is_logged_not_raised(tmp_path, monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(update_check.os, "replace", MagicMock(side_effect=OSError("x")))
    monkeypatch.setattr(update_check.os, "unlink", MagicMock(side_effect=OSError("y")))
    update_check._write_cache(str(tmp_path / "update_check.json"), "1.0.0", 0.0, log)
    assert [c.args[0] for c in log.debug.call_args_list] == [
        "Update check cache write failed: %s",
        "Update check temp file cleanup failed: %s",
    ]
