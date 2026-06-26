"""Unit + integration tests for the ``orb server`` daemon primitives.

Coverage:
  - PID lock: ``_acquire_pid_lock`` rejects a second acquirer
  - ``_pid_is_alive`` / ``_read_pid`` edge cases
  - ``status``: PID file missing, PID stale, PID alive (mock), health probe ok/fail
  - ``reload``: signals only when alive
  - ``stop``: SIGTERM path, SIGKILL fallback, not-running fast path
  - ``start --foreground``: runs the runtime in-process, cleans up PID file
  - Live ``start`` → ``stop`` round trip with a trivial sleeping runtime
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

import pytest

from orb.interface import server_daemon as daemon


# ── Path helpers ────────────────────────────────────────────────────────────


def _pid_path(tmp_path: Path) -> Path:
    return tmp_path / "orb-server.pid"


def _log_path(tmp_path: Path) -> Path:
    return tmp_path / "orb-server.log"


# ── PID lock ────────────────────────────────────────────────────────────────


def test_acquire_pid_lock_writes_parent_dir_and_returns_fd(tmp_path: Path) -> None:
    pf = _pid_path(tmp_path / "nested" / "dir")
    fd = daemon._acquire_pid_lock(pf)
    try:
        assert pf.exists()
        assert pf.parent.exists()
    finally:
        os.close(fd)


def test_acquire_pid_lock_rejects_other_process(tmp_path: Path) -> None:
    """fcntl.lockf locks are per-PROCESS, not per-fd, so we have to spawn a
    real child to exercise contention (a daemon would always be a separate
    process from any rival ``start`` invocation)."""
    pf = _pid_path(tmp_path)
    fd = daemon._acquire_pid_lock(pf)
    try:
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:  # child
            os.close(r)
            try:
                daemon._acquire_pid_lock(pf)
                os.write(w, b"ok")
            except RuntimeError as exc:
                os.write(w, f"err:{exc}".encode())
            except Exception as exc:  # pragma: no cover
                os.write(w, f"unexpected:{exc!r}".encode())
            os._exit(0)
        # parent
        os.close(w)
        os.waitpid(pid, 0)
        with os.fdopen(r, "rb") as rfd:
            payload = rfd.read().decode("utf-8", errors="replace")
        assert payload.startswith("err:"), f"child should have been rejected, got: {payload!r}"
        assert "running" in payload
    finally:
        os.close(fd)


def test_acquire_pid_lock_releases_on_close(tmp_path: Path) -> None:
    pf = _pid_path(tmp_path)
    fd = daemon._acquire_pid_lock(pf)
    os.close(fd)
    fd2 = daemon._acquire_pid_lock(pf)
    os.close(fd2)


# ── PID liveness + parsing ──────────────────────────────────────────────────


def test_pid_is_alive_self_returns_true() -> None:
    assert daemon._pid_is_alive(os.getpid())


def test_pid_is_alive_zero_pid_returns_false() -> None:
    assert not daemon._pid_is_alive(0)
    assert not daemon._pid_is_alive(-1)


def test_pid_is_alive_unused_pid_returns_false() -> None:
    # Reasonably high pid that is almost certainly not alive in a test env.
    assert not daemon._pid_is_alive(2**31 - 2)


def test_read_pid_missing_file(tmp_path: Path) -> None:
    assert daemon._read_pid(_pid_path(tmp_path)) is None


def test_read_pid_garbage_returns_none(tmp_path: Path) -> None:
    pf = _pid_path(tmp_path)
    pf.write_text("not-an-int\n")
    assert daemon._read_pid(pf) is None


def test_read_pid_valid(tmp_path: Path) -> None:
    pf = _pid_path(tmp_path)
    pf.write_text("12345\n")
    assert daemon._read_pid(pf) == 12345


# ── status ──────────────────────────────────────────────────────────────────


def test_status_no_pid_file(tmp_path: Path) -> None:
    pf = _pid_path(tmp_path)
    out = daemon.status(pid_file=pf)
    assert out == {"pid": None, "running": False, "pid_file": str(pf)}


def test_status_stale_pid_file(tmp_path: Path) -> None:
    pf = _pid_path(tmp_path)
    pf.write_text(f"{2**31 - 2}\n")
    out = daemon.status(pid_file=pf)
    assert out["running"] is False
    assert out["pid"] == 2**31 - 2


def test_status_live_pid_without_health(tmp_path: Path) -> None:
    pf = _pid_path(tmp_path)
    pf.write_text(f"{os.getpid()}\n")
    out = daemon.status(pid_file=pf)
    assert out["pid"] == os.getpid()
    assert out["running"] is True
    assert "health_status" not in out


def test_status_live_pid_with_unreachable_health(tmp_path: Path) -> None:
    pf = _pid_path(tmp_path)
    pf.write_text(f"{os.getpid()}\n")
    # 127.0.0.1:1 — almost certainly nothing listening.
    out = daemon.status(pid_file=pf, health_url="http://127.0.0.1:1/health")
    assert out["running"] is True
    assert out["health_ok"] is False
    assert "health_error" in out


# ── reload ──────────────────────────────────────────────────────────────────


def test_reload_no_pid_file(tmp_path: Path) -> None:
    out = daemon.reload(pid_file=_pid_path(tmp_path))
    assert out["status"] == "not_running"


def test_reload_signals_alive_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pf = _pid_path(tmp_path)
    pf.write_text(f"{os.getpid()}\n")
    sent: list[tuple[int, int]] = []
    real_kill = os.kill

    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0:
            # ``_pid_is_alive`` probe — delegate to the real kill so the
            # liveness check still works.
            real_kill(pid, sig)
            return
        sent.append((pid, sig))

    monkeypatch.setattr(daemon.os, "kill", fake_kill)
    out = daemon.reload(pid_file=pf)
    assert out["status"] == "signalled"
    assert sent == [(os.getpid(), signal.SIGHUP)]


# ── stop ────────────────────────────────────────────────────────────────────


def test_stop_no_pid_file(tmp_path: Path) -> None:
    out = daemon.stop(pid_file=_pid_path(tmp_path), timeout=0.1)
    assert out["status"] == "not_running"


def test_stop_stale_pid_cleans_up_pid_file(tmp_path: Path) -> None:
    pf = _pid_path(tmp_path)
    pf.write_text(f"{2**31 - 2}\n")
    out = daemon.stop(pid_file=pf, timeout=0.1)
    assert out["status"] == "not_running"
    assert not pf.exists()


def test_stop_sigterm_fallback_to_sigkill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If SIGTERM doesn't make the pid disappear before the deadline we escalate.

    The child here is the test process itself — we mock killpg so nothing
    actually dies and force ``_pid_is_alive`` to flip True → False only
    after SIGKILL is observed.
    """
    pf = _pid_path(tmp_path)
    pf.write_text(f"{os.getpid()}\n")

    signals_sent: list[int] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        signals_sent.append(sig)

    monkeypatch.setattr(daemon.os, "killpg", fake_killpg)
    monkeypatch.setattr(daemon.os, "getpgid", lambda pid: pid)
    # Alive until SIGKILL observed, so SIGTERM doesn't kill it and we
    # exhaust the timeout, escalating to SIGKILL.
    monkeypatch.setattr(
        daemon, "_pid_is_alive", lambda pid: signal.SIGKILL not in signals_sent
    )

    out = daemon.stop(pid_file=pf, timeout=0.4)
    assert out["status"] == "killed"
    assert signals_sent[0] == signal.SIGTERM
    assert signal.SIGKILL in signals_sent
    assert not pf.exists()


def test_stop_succeeds_on_sigterm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pf = _pid_path(tmp_path)
    pf.write_text(f"{os.getpid()}\n")
    sent: list[int] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        sent.append(sig)

    monkeypatch.setattr(daemon.os, "killpg", fake_killpg)
    monkeypatch.setattr(daemon.os, "getpgid", lambda pid: pid)
    # Alive until SIGTERM observed, then drop.
    monkeypatch.setattr(
        daemon, "_pid_is_alive", lambda pid: signal.SIGTERM not in sent
    )

    out = daemon.stop(pid_file=pf, timeout=0.4)
    assert out["status"] == "stopped"
    assert sent == [signal.SIGTERM]
    assert not pf.exists()


# ── tail_log ────────────────────────────────────────────────────────────────


def test_tail_log_returns_empty_for_missing(tmp_path: Path) -> None:
    assert daemon.tail_log(log_file=_log_path(tmp_path)) == ""


def test_tail_log_returns_last_n_lines(tmp_path: Path) -> None:
    lp = _log_path(tmp_path)
    lp.write_text("\n".join(f"line-{i}" for i in range(20)) + "\n")
    tail = daemon.tail_log(log_file=lp, lines=3)
    assert tail.splitlines() == ["line-17", "line-18", "line-19"]


# ── foreground start + lock cleanup ────────────────────────────────────────


def test_start_foreground_runs_runtime_and_cleans_pid_file(tmp_path: Path) -> None:
    pf = _pid_path(tmp_path)
    lf = _log_path(tmp_path)
    ran = []

    async def runtime() -> dict[str, object]:
        ran.append(os.getpid())
        return {"message": "ok"}

    out = daemon.start(
        pid_file=pf,
        log_file=lf,
        working_dir=tmp_path,
        runtime=runtime,
        foreground=True,
    )
    assert out["pid"] == os.getpid()
    assert out["status"] == "exited"
    assert ran == [os.getpid()]
    assert not pf.exists(), "foreground start must remove its pid file on exit"


def test_start_foreground_refuses_when_other_process_holds_lock(tmp_path: Path) -> None:
    """A rival process holding the lock should make ``start`` raise."""
    pf = _pid_path(tmp_path)
    fd = daemon._acquire_pid_lock(pf)
    try:
        r, w = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(r)
            try:

                async def runtime() -> dict[str, object]:
                    return {}

                daemon.start(
                    pid_file=pf,
                    log_file=_log_path(tmp_path),
                    working_dir=tmp_path,
                    runtime=runtime,
                    foreground=True,
                )
                os.write(w, b"ok")
            except RuntimeError as exc:
                os.write(w, f"err:{exc}".encode())
            os._exit(0)
        os.close(w)
        os.waitpid(child, 0)
        with os.fdopen(r, "rb") as rfd:
            payload = rfd.read().decode("utf-8", errors="replace")
        assert payload.startswith("err:"), f"child should have been rejected, got: {payload!r}"
        assert "running" in payload
    finally:
        os.close(fd)


# ── live daemon round trip ──────────────────────────────────────────────────


@pytest.mark.usefixtures("capfd")
def test_daemon_round_trip_start_stop(tmp_path: Path, capfd) -> None:
    # capfd uses file-descriptor capture (capsys would mock sys.stdin with
    # a non-fileno proxy and ``os.dup2`` blows up). Even with capfd,
    # pytest sometimes replaces stdin with a non-fileno proxy; if so
    # ``_redirect_stdio`` will skip it via the fallback.
    _ = capfd  # silence unused

    """Full happy path: daemonise a tiny runtime, status it, stop it."""
    pf = _pid_path(tmp_path)
    lf = _log_path(tmp_path)

    async def runtime() -> dict[str, object]:
        # Stay alive long enough for the test to see it running and stop it.
        while True:
            await asyncio.sleep(0.1)

    res = daemon.start(
        pid_file=pf,
        log_file=lf,
        working_dir=tmp_path,
        runtime=runtime,
        foreground=False,
    )
    assert res["status"] == "started"
    pid = int(res["pid"])  # type: ignore[arg-type]

    try:
        # Give the grandchild a beat to write its pid file.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            st = daemon.status(pid_file=pf)
            if st["running"]:
                break
            time.sleep(0.05)
        else:
            pytest.fail("daemon did not become live in time")
        assert st["pid"] == pid
    finally:
        out = daemon.stop(pid_file=pf, timeout=3.0)
        assert out["status"] in {"stopped", "killed"}
        assert not pf.exists()
