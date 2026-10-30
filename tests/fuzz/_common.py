"""Shared setup for the atheris fuzz harnesses.

The harnesses only exercise pure parsing and validation code. This module
keeps them that way: it points HOME at a throwaway directory, clears ORB_*
environment variables, silences logging, and installs an audit hook that turns
any network access, subprocess or file write outside the temporary directory
into a hard failure instead of a silent side effect.

Run a harness directly, passing libFuzzer arguments, for example::

    python tests/fuzz/fuzz_config_parsing.py -max_total_time=60 \\
        -artifact_prefix=/tmp/crashes/ tests/fuzz/corpus/config_parsing
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Callable

# Per-input wall-clock limit (seconds) applied unless the caller passes -timeout.
DEFAULT_TIMEOUT_SECONDS = 5
# Memory limit (MB) applied unless the caller passes -rss_limit_mb.
DEFAULT_RSS_LIMIT_MB = 2048

_BLOCKED_EVENTS = frozenset(
    {
        "socket.connect",
        "socket.bind",
        "socket.sendto",
        "socket.sendmsg",
        "socket.getaddrinfo",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.spawn",
    }
)
# Filesystem-mutating audit events mapped to the positions of the path
# arguments that must lie inside the sandbox.  Source paths of copies are only
# read, so just the destination is checked; renames and removals also change
# their source.
_PATH_EVENTS: dict[str, tuple[int, ...]] = {
    "os.rename": (0, 1),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.mkdir": (0,),
    "os.symlink": (1,),
    "os.link": (1,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.truncate": (0,),
    "shutil.copyfile": (1,),
    "shutil.copymode": (1,),
    "shutil.copystat": (1,),
    "shutil.copytree": (1,),
    "shutil.move": (0, 1),
    "shutil.rmtree": (0,),
    "shutil.chown": (0,),
    "shutil.unpack_archive": (1,),
}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_ARMED = False
_SANDBOX = ""


class HermeticityViolation(RuntimeError):
    """The code under test reached outside the pure-function boundary."""


def _outside_sandbox(path: object) -> bool:
    """True when ``path`` resolves to somewhere other than inside the sandbox."""
    if not isinstance(path, (str, bytes, os.PathLike)):
        return False  # file descriptors and similar carry no path to check
    resolved = os.path.realpath(os.fsdecode(path))
    return resolved != _SANDBOX and not resolved.startswith(_SANDBOX + os.sep)


def _audit(event: str, args: tuple) -> None:
    if not _ARMED:
        return
    if event in _BLOCKED_EVENTS:
        raise HermeticityViolation(f"blocked {event}: {str(args)[:120]}")
    if event == "open":
        path, mode, flags = args
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS
        )
        if writes and _outside_sandbox(path):
            raise HermeticityViolation(f"blocked write outside sandbox: {path!r}")
    elif event in _PATH_EVENTS:
        for index in _PATH_EVENTS[event]:
            if index < len(args) and _outside_sandbox(args[index]):
                raise HermeticityViolation(f"blocked {event} outside sandbox: {args[index]!r}")


def _remove_sandbox(path: str) -> None:
    """Disarm the audit hook, then delete the sandbox directory.

    rmtree issues removals relative to directory descriptors, which the hook
    cannot map back to the sandbox, so enforcement is switched off first.
    """
    global _ARMED
    _ARMED = False
    shutil.rmtree(path, ignore_errors=True)


def prepare_environment() -> str:
    """Isolate the process. Call before importing the code under test."""
    global _SANDBOX
    sys.dont_write_bytecode = True
    _SANDBOX = os.path.realpath(tempfile.mkdtemp(prefix="orb-fuzz-"))
    atexit.register(_remove_sandbox, _SANDBOX)

    for name in [k for k in os.environ if k.startswith("ORB_")]:
        del os.environ[name]
    os.environ["HOME"] = _SANDBOX
    os.environ["USERPROFILE"] = _SANDBOX
    os.environ["TMPDIR"] = _SANDBOX
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        os.environ.pop(name, None)

    logging.disable(logging.CRITICAL)
    sys.addaudithook(_audit)
    return _SANDBOX


def arm() -> None:
    """Start enforcing hermeticity. Call after imports and before fuzzing."""
    global _ARMED
    _ARMED = True


def run(atheris_module, test_one_input: Callable[[bytes], None]) -> None:
    """Configure libFuzzer defaults and enter the fuzz loop."""
    argv = list(sys.argv)
    if not any(a.startswith("-timeout=") for a in argv):
        argv.append(f"-timeout={DEFAULT_TIMEOUT_SECONDS}")
    if not any(a.startswith("-rss_limit_mb=") for a in argv):
        argv.append(f"-rss_limit_mb={DEFAULT_RSS_LIMIT_MB}")
    arm()
    atheris_module.Setup(argv, test_one_input)
    atheris_module.Fuzz()


def self_check() -> None:
    """Assert that the audit hook rejects escapes and allows sandbox work."""
    global _ARMED, _SANDBOX
    saved = (_ARMED, _SANDBOX)
    _SANDBOX = os.path.realpath(tempfile.mkdtemp(prefix="orb-fuzz-check-"))
    _ARMED = True
    try:
        inside = os.path.join(_SANDBOX, "x")
        escape = os.path.join(_SANDBOX, "..", "x")
        sibling = _SANDBOX + "2/x"
        other = os.path.join(tempfile.gettempdir(), "orb-fuzz-outside")

        _audit("open", (inside, "w", 0))
        _audit("os.rename", (inside, inside + "2", None, None))
        _audit("shutil.copyfile", (other, inside))

        denied = [
            ("open", (escape, "w", 0)),
            ("open", (sibling, "w", 0)),
            ("open", (other, "r", os.O_WRONLY)),
            ("os.rename", (inside, other, None, None)),
            ("os.remove", (other, None)),
            ("os.rmdir", (other, None)),
            ("os.mkdir", (other, 0o777, None)),
            ("os.symlink", (inside, other, None)),
            ("os.link", (inside, other, None, None, True)),
            ("os.chmod", (other, 0o777, None)),
            ("os.truncate", (other, 0)),
            ("shutil.copyfile", (inside, other)),
            ("shutil.move", (inside, other)),
            ("shutil.rmtree", (other, None)),
            ("socket.sendto", (None, b"x", ("127.0.0.1", 9))),
            ("socket.sendmsg", (None, [b"x"])),
            ("socket.bind", (None, ("127.0.0.1", 0))),
        ]
        for event, args in denied:
            try:
                _audit(event, args)
            except HermeticityViolation:
                continue
            raise AssertionError(f"audit hook allowed {event} {args!r}")
    finally:
        shutil.rmtree(_SANDBOX, ignore_errors=True)
        _ARMED, _SANDBOX = saved


if __name__ == "__main__":
    self_check()
    print("audit hook self-check passed")
