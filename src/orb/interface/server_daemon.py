"""Process-lifecycle primitives for ``orb server`` commands.

Posix-only. Implements start/stop/status/restart for the foreground
``server_runtime`` entrypoints. Responsibilities here:

  - Double-fork + ``setsid`` so the daemon detaches from the controlling
    terminal and survives shell exit
  - Redirect stdio to a rotating log file
  - Write a PID file guarded by ``fcntl.lockf`` so two starts can't race
  - Stop via SIGTERM → wait → SIGKILL fallback, killing the whole
    process group so the Reflex tree (Node included) goes down with us

The actual server work — uvicorn or ``reflex run`` — is delegated to
``server_runtime`` via a thin ``_run_in_loop`` helper. The daemon module
doesn't import uvicorn or Reflex at module scope.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, NoReturn

from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path))).resolve()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM  # alive but not ours to signal
    return True


def _read_pid(pid_file: Path) -> int | None:
    try:
        raw = pid_file.read_text().strip()
    except FileNotFoundError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _acquire_pid_lock(pid_file: Path) -> int:
    """Open + lock the pid file; return the file descriptor.

    Raises ``RuntimeError`` if another daemon already holds the lock.
    Caller owns closing the fd (which releases the lock).
    """
    _ensure_parent(pid_file)
    fd = os.open(str(pid_file), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EAGAIN, errno.EACCES):
            existing = _read_pid(pid_file)
            raise RuntimeError(
                f"Another orb server appears to be running (pid={existing}). "
                f"Use 'orb server stop' first, or delete {pid_file} if stale."
            ) from None
        raise
    return fd


def _write_pid(fd: int, pid: int) -> None:
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, f"{pid}\n".encode("ascii"))
    os.fsync(fd)


def _redirect_stdio(log_file: Path) -> None:
    _ensure_parent(log_file)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception as exc:
        # Flush failure during daemonisation is non-fatal — the stdio
        # handoff continues with whatever buffered output is on the wire.
        logger.debug("stdio flush failed during daemon handoff: %s", exc)
    log_fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        # ``sys.stdout/.stderr`` may be replaced with non-fileno wrappers
        # by some runners (pytest's capsys). Fall back to the canonical
        # FDs in that case; the real daemon always has real underlying
        # fds anyway.
        for stream, default_fd in ((sys.stdout, 1), (sys.stderr, 2)):
            try:
                target_fd = stream.fileno()
            except (AttributeError, OSError, ValueError):
                target_fd = default_fd
            try:
                os.dup2(log_fd, target_fd)
            except OSError as exc:
                # Best-effort: some unusual runtimes refuse dup2 on a
                # particular fd; keep going so we still daemonise.
                logger.debug("dup2 failed on fd %d: %s", target_fd, exc)
        try:
            stdin_fd = sys.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            stdin_fd = 0
        try:
            devnull = os.open(os.devnull, os.O_RDONLY)
            os.dup2(devnull, stdin_fd)
            os.close(devnull)
        except OSError as exc:
            # Best-effort stdin /dev/null redirect; daemon proceeds even
            # if the host filesystem hides /dev/null (containers etc.).
            logger.debug("stdin /dev/null redirect failed: %s", exc)
    finally:
        os.close(log_fd)


def _spawn_runtime(coro_factory: Callable[[], Coroutine[Any, Any, Any]]) -> int:
    """Run the async runtime to completion; return its exit code."""
    try:
        result = asyncio.run(coro_factory())
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pragma: no cover — logged for ops
        logger = get_logger(__name__)
        logger.error("daemon runtime crashed: %s", exc, exc_info=True)
        return 1

    if isinstance(result, dict):
        return int(result.get("exit_code", 0))
    return 0


def _run_daemon_grandchild(
    write_fd: int,
    pid_path: Path,
    log_path: Path,
    wd_path: Path,
    runtime: Callable[[], Coroutine[Any, Any, Any]],
) -> NoReturn:
    """Grandchild daemon body. Always terminates the process via os._exit."""
    try:
        os.umask(0o027)
        os.chdir(str(wd_path))
        _redirect_stdio(log_path)
        daemon_fd = _acquire_pid_lock(pid_path)
        _write_pid(daemon_fd, os.getpid())
    except Exception as exc:
        try:
            with os.fdopen(write_fd, "wb") as w:
                w.write(f"err:{exc}".encode())
        except Exception as report_exc:
            logger.debug("daemon child failed to report start error: %s", report_exc)
        os._exit(1)

    try:
        with os.fdopen(write_fd, "wb") as w:
            w.write(f"ok:{os.getpid()}".encode())
    except Exception as exc:
        logger.debug("daemon child readiness pipe write failed: %s", exc)

    try:
        rc = _spawn_runtime(runtime)
    finally:
        pid_path.unlink(missing_ok=True)
        try:
            os.close(daemon_fd)
        except OSError as exc:
            logger.debug("daemon lock fd already closed: %s", exc)

    os._exit(rc)


def start(
    *,
    pid_file: str | os.PathLike[str],
    log_file: str | os.PathLike[str],
    working_dir: str | os.PathLike[str],
    runtime: Callable[[], Coroutine[Any, Any, Any]],
    foreground: bool = False,
) -> dict[str, Any]:
    """Start the server, either daemonized or in the foreground.

    Args:
        pid_file:     Where to write the PID file (advisory lock target).
        log_file:     stdout/stderr redirect target (daemon mode).
        working_dir:  ``chdir`` target (daemon mode).
        runtime:      Zero-arg coroutine factory that runs the server.
        foreground:   When True, skip fork/setsid/redirect and just run the
                      runtime in this process (still writes pid file).

    Returns ``{"pid": int, "status": "started"|"running_foreground"}``.
    Raises ``RuntimeError`` if a daemon is already running.
    """
    pid_path = _expand(str(pid_file))
    log_path = _expand(str(log_file))
    wd_path = _expand(str(working_dir))
    wd_path.mkdir(parents=True, exist_ok=True)

    lock_fd = _acquire_pid_lock(pid_path)

    if foreground:
        _write_pid(lock_fd, os.getpid())
        try:
            rc = _spawn_runtime(runtime)
        finally:
            pid_path.unlink(missing_ok=True)
            os.close(lock_fd)
        return {"pid": os.getpid(), "status": "exited", "exit_code": rc}

    os.close(lock_fd)
    read_fd, write_fd = os.pipe()

    intermediate = os.fork()
    if intermediate > 0:
        os.close(write_fd)
        os.waitpid(intermediate, 0)
        with os.fdopen(read_fd, "rb") as r:
            payload = r.read().decode("utf-8", errors="replace").strip()
        if payload.startswith("ok:"):
            return {"pid": int(payload[3:]), "status": "started"}
        raise RuntimeError(
            payload[4:] if payload.startswith("err:") else payload or "daemon failed"
        )

    os.close(read_fd)
    os.setsid()
    grandchild = os.fork()
    if grandchild > 0:
        # Intermediate fork exits without running atexit / finally clauses;
        # the grandchild owns the daemon lifecycle from here on.
        os._exit(0)
    _run_daemon_grandchild(write_fd, pid_path, log_path, wd_path, runtime)
    raise AssertionError("unreachable: _run_daemon_grandchild is NoReturn")


def stop(
    *,
    pid_file: str | os.PathLike[str],
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Stop the daemon. SIGTERM → wait → SIGKILL fallback.

    Returns ``{"pid": int|None, "status": "stopped"|"not_running"|"killed"}``.
    """
    pid_path = _expand(str(pid_file))
    pid = _read_pid(pid_path)
    if pid is None or not _pid_is_alive(pid):
        pid_path.unlink(missing_ok=True)
        return {"pid": pid, "status": "not_running"}

    # Kill the whole group so the Reflex subtree dies too.
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        pgid = pid

    def _signal_group(sig: int) -> None:
        # killpg races with the process group exiting on its own; gate
        # on a liveness check so the call only fires when there is
        # something to signal. Exceptions still possible if the group
        # dies between the check and the call, but the window is
        # narrow and the daemon stop path doesn't depend on the result.
        if _pid_is_alive(pid):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError as exc:
                logger.debug("killpg target %s exited mid-call: %s", pgid, exc)

    _signal_group(signal.SIGTERM)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            pid_path.unlink(missing_ok=True)
            return {"pid": pid, "status": "stopped"}
        time.sleep(0.2)

    _signal_group(signal.SIGKILL)
    # Final check
    time.sleep(0.2)
    pid_path.unlink(missing_ok=True)
    return {"pid": pid, "status": "killed" if not _pid_is_alive(pid) else "still_running"}


def status(
    *,
    pid_file: str | os.PathLike[str],
    health_url: str | None = None,
) -> dict[str, Any]:
    """Return a structured status snapshot.

    ``health_url`` is probed best-effort with a short timeout; failures
    don't mask the local-process info.
    """
    pid_path = _expand(str(pid_file))
    pid = _read_pid(pid_path)
    if pid is None:
        return {"pid": None, "running": False, "pid_file": str(pid_path)}
    alive = _pid_is_alive(pid)
    out: dict[str, Any] = {
        "pid": pid,
        "running": alive,
        "pid_file": str(pid_path),
    }
    if not alive:
        return out

    # Health probe.
    if health_url:
        try:
            import requests

            # health_url is composed by the CLI from operator-controlled
            # ServerConfig (host/port). ``requests`` is bound to http(s)://
            # only — no file:// fallback if the URL is misconfigured.
            resp = requests.get(health_url, timeout=1.5)
            out["health_status"] = resp.status_code
            out["health_ok"] = 200 <= resp.status_code < 300
        except Exception as exc:
            out["health_status"] = None
            out["health_ok"] = False
            out["health_error"] = str(exc)
    return out


def reload(*, pid_file: str | os.PathLike[str]) -> dict[str, Any]:
    """Send SIGHUP to the daemon. Handler is registered server-side."""
    pid_path = _expand(str(pid_file))
    pid = _read_pid(pid_path)
    if pid is None or not _pid_is_alive(pid):
        return {"pid": pid, "status": "not_running"}
    try:
        os.kill(pid, signal.SIGHUP)
    except ProcessLookupError:
        return {"pid": pid, "status": "not_running"}
    return {"pid": pid, "status": "signalled"}


def tail_log(*, log_file: str | os.PathLike[str], lines: int = 50) -> str:
    """Return the last *lines* of the log file (best-effort, no follow)."""
    log_path = _expand(str(log_file))
    if not log_path.exists():
        return ""
    # Cheap implementation: read whole file. The daemon log file is
    # rotated by RotatingFileHandler so this stays bounded.
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        return "".join(fh.readlines()[-lines:])
