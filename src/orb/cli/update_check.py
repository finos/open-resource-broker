"""Opt-in update-availability notice for the CLI.

Disabled unless ``cli.update_check`` is true (env: ``ORB_CLI_UPDATE_CHECK``).
When enabled, the latest release is looked up on PyPI at most once every 24
hours and a single line is written to stderr after an interactive command.
Every failure is swallowed: the check never changes a command's result.
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
_TIMEOUT_SECONDS = 2.0
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_RELEASE_RE = re.compile(r"^\s*v?(\d+(?:\.\d+)*)")


def parse_release(version: Any) -> Optional[tuple[int, ...]]:
    """Return the numeric release segment of a version, or None if unparsable.

    Pre-release, dev and post suffixes are ignored.
    """
    if not isinstance(version, str):
        return None
    match = _RELEASE_RE.match(version)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(latest: Any, current: Any) -> bool:
    """Return True only when both versions parse and latest is greater."""
    new, old = parse_release(latest), parse_release(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


class _SamePypiRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to https://pypi.org."""

    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Optional[urllib.request.Request]:
        target = urlparse(newurl)
        if target.scheme != "https" or target.hostname != _PYPI_HOST:
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


def _fetch_bounded(logger: LoggingPort) -> Optional[str]:
    """Run the lookup in a daemon thread so a stalled DNS lookup cannot hold the CLI."""
    result: list[Optional[str]] = []
    worker = threading.Thread(target=lambda: result.append(_fetch_latest(logger)), daemon=True)
    worker.start()
    worker.join(_TIMEOUT_SECONDS + 0.5)
    return result[0] if result else None


def _read_cache(path: str, now: float, logger: LoggingPort) -> Optional[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        checked_at = data["checked_at"]
        if isinstance(checked_at, (int, float)) and 0 <= now - checked_at < _CACHE_TTL_SECONDS:
            return {"latest": data.get("latest")}
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


def latest_version(cache_dir: str, logger: LoggingPort) -> Optional[str]:
    """Return the latest version, using a 24 hour cache (failures are cached too)."""
    path = os.path.join(cache_dir, _CACHE_FILE)
    now = time.time()
    cached = _read_cache(path, now, logger)
    if cached is not None:
        return cached["latest"] if isinstance(cached["latest"], str) else None
    latest = _fetch_bounded(logger)
    _write_cache(path, latest, now, logger)
    return latest


def _interactive(args: Any) -> bool:
    if getattr(args, "quiet", False) or getattr(args, "format", "json") in ("json", "yaml"):
        return False
    return sys.stdout.isatty() and sys.stderr.isatty()


def maybe_notify(args: Any, config: Any, current_version: str, logger: LoggingPort) -> None:
    """Print one stderr line when enabled, interactive and a newer release exists."""
    try:
        if config.get_configuration_value("cli.update_check", False) is not True:
            return
        if not _interactive(args):
            return
        latest = latest_version(config.get_cache_dir(), logger)
        if latest and is_newer(latest, current_version):
            print(
                f"A newer orb-py release is available: {latest} (installed {current_version}). "
                "Upgrade with: pip install --upgrade orb-py",
                file=sys.stderr,
            )
    except Exception as exc:
        logger.debug("Update check skipped: %s", type(exc).__name__)
