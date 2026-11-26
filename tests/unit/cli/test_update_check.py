"""Opt-in update notice: gating, caching, comparison and failure handling."""

import contextlib
import io
import json
import socket
import time
import urllib.request
from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.cli import update_check
from orb.cli.update_check import is_newer, maybe_notify, parse_release


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

    def run(self, args, config, version):
        # Streams are swapped only for the call: pytest re-installs its own between phases.
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            maybe_notify(args, config, version, MagicMock())

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
def test_request_error_is_silent_and_cached(tmp_path, tty, monkeypatch, error):
    opener = MagicMock()
    opener.open.side_effect = error
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: opener)

    tty.run(_args(), _Config(tmp_path), "1.0.0")
    tty.run(_args(), _Config(tmp_path), "1.0.0")

    assert tty.read() == ("", "")
    assert opener.open.call_count == 1


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
        ],
    )
    def test_other_targets_refused(self, url):
        assert self._follow(url) is None


def test_main_hook_swallows_failures(monkeypatch):
    from orb.cli import main as cli_main

    monkeypatch.setattr(cli_main, "get_container", MagicMock(side_effect=RuntimeError("boom")))
    cli_main._notify_update(_args())
