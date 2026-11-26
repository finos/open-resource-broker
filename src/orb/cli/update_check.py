"""Opt-in update-availability notice for the CLI.

Disabled unless ``cli.update_check`` is true (env: ``ORB_CLI_UPDATE_CHECK``).
When enabled, the lookup starts in a daemon thread before the command runs.
After the command output, a cached or already-finished result is used (waiting
at most a fraction of a second) and one line is written to stderr if a newer
release exists. A slow lookup shows nothing and fills the cache for the next
run. Every failure is swallowed: the check never changes a command's result.
"""

import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.request
from typing import Any, Optional
from urllib.parse import urlparse

from orb.domain.base.ports.logging_port import LoggingPort

PYPI_URL = "https://pypi.org/pypi/orb-py/json"
_PYPI_HOST = "pypi.org"
_CACHE_FILE = "update_check.json"
_CACHE_TTL_SECONDS = 24 * 60 * 60
_FAILURE_TTL_SECONDS = 60 * 60
_TIMEOUT_SECONDS = 2.0
_FINISH_WAIT_SECONDS = 0.2
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_MAX_VERSION_LENGTH = 64
_MAX_VERSION_SEGMENTS = 6
_RELEASE_RE = re.compile(r"^v?([0-9]+(?:\.[0-9]+)*)", re.ASCII)
_SKIPPED_RESOURCES = frozenset({"server", "mcp"})


def parse_release(version: Any) -> Optional[tuple[int, ...]]:
    """Return the numeric release segment of a version, or None if unparsable.

    Pre-release, dev and post suffixes are ignored. Only ASCII digits are
    accepted and length is bounded.
    """
    if not isinstance(version, str) or len(version) > _MAX_VERSION_LENGTH:
        return None
    match = _RELEASE_RE.match(version)
    if match is None:
        return None
    try:
        parts = tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return None
    return parts if len(parts) <= _MAX_VERSION_SEGMENTS else None


def format_release(parts: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in parts)


def _newer(new: tuple[int, ...], old: tuple[int, ...]) -> bool:
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def is_newer(latest: Any, current: Any) -> bool:
    """Return True only when both versions parse and latest is greater."""
    new, old = parse_release(latest), parse_release(current)
    return new is not None and old is not None and _newer(new, old)


class _SamePypiRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to https://pypi.org."""

    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Optional[urllib.request.Request]:
        target = urlparse(newurl)
        if target.scheme != "https" or target.netloc.lower() != _PYPI_HOST or "\\" in newurl:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_latest(logger: LoggingPort) -> Optional[str]:
    """Return the latest released version from PyPI, or None on any failure."""
    try:
        opener = urllib.request.build_opener(_SamePypiRedirect)
        request = urllib.request.Request(PYPI_URL, headers={"Accept": "application/json"})
        with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            if urlparse(response.geturl()).scheme != "https":
                return None
            payload = json.loads(response.read(_MAX_RESPONSE_BYTES))
        latest = payload["info"]["version"]
        return latest if isinstance(latest, str) else None
    except Exception as exc:
        logger.debug("Update check request failed: %s", type(exc).__name__)
        return None


def _read_cache(path: str, now: float, logger: LoggingPort) -> Optional[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        checked_at = data["checked_at"]
        if isinstance(checked_at, (int, float)) and 0 <= now - checked_at < _CACHE_TTL_SECONDS:
            latest = data.get("latest")
            return {"latest": latest if isinstance(latest, str) else None}
    except Exception as exc:
        logger.debug("Update check cache not usable: %s", type(exc).__name__)
    return None


def _write_cache(path: str, latest: Optional[str], now: float, logger: LoggingPort) -> None:
    tmp_path = None
    try:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".update_check.")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"checked_at": now, "latest": latest}, handle)
        os.replace(tmp_path, path)
    except Exception as exc:
        logger.debug("Update check cache write failed: %s", type(exc).__name__)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _interactive(args: Any) -> bool:
    if getattr(args, "quiet", False) or getattr(args, "format", "json") in ("json", "yaml"):
        return False
    return sys.stdout.isatty() and sys.stderr.isatty()


class UpdateCheck:
    """A started check; call ``finish`` after the command output."""

    def __init__(self, current: tuple[int, ...], logger: LoggingPort) -> None:
        self._current = current
        self._logger = logger
        self._result: list[Optional[str]] = []
        self._thread: Optional[threading.Thread] = None

    def _run(self, path: str) -> None:
        latest = _fetch_latest(self._logger)
        if latest:
            _write_cache(path, latest, time.time(), self._logger)
        self._result.append(latest)

    def finish(self) -> None:
        """Print the notice if a newer release is known; never raises or blocks long."""
        try:
            if self._thread is not None:
                self._thread.join(_FINISH_WAIT_SECONDS)
            latest = parse_release(self._result[0]) if self._result else None
            if latest is not None and _newer(latest, self._current):
                print(
                    f"A newer orb-py release is available: {format_release(latest)} "
                    f"(installed {format_release(self._current)}). "
                    "Upgrade with: pip install --upgrade orb-py",
                    file=sys.stderr,
                )
        except Exception as exc:
            self._logger.debug("Update notice skipped: %s", type(exc).__name__)


def start(
    args: Any, config: Any, current_version: str, logger: LoggingPort
) -> Optional[UpdateCheck]:
    """Begin the check when enabled and interactive; return None otherwise."""
    try:
        if config.get_configuration_value("cli.update_check", False) is not True:
            return None
        if getattr(args, "resource", None) in _SKIPPED_RESOURCES or not _interactive(args):
            return None
        current = parse_release(current_version)
        if current is None:
            return None
        check = UpdateCheck(current, logger)
        path = os.path.join(config.get_cache_dir(), _CACHE_FILE)
        cached = _read_cache(path, time.time(), logger)
        if cached is not None:
            check._result.append(cached["latest"])
        else:
            # Record a pending attempt first so a slow or failing lookup is not retried
            # by every command: it expires after the failure TTL, success overwrites it.
            stamp = time.time() - (_CACHE_TTL_SECONDS - _FAILURE_TTL_SECONDS)
            _write_cache(path, None, stamp, logger)
            check._thread = threading.Thread(target=check._run, args=(path,), daemon=True)
            check._thread.start()
        return check
    except Exception as exc:
        logger.debug("Update check skipped: %s", type(exc).__name__)
        return None
