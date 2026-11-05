"""Process-wide SQLAlchemy engines, one per configured database.

Every SQL storage consumer (unit of work, repositories, health probes) that
names the same database gets the same engine and therefore the same connection
pool, instead of each opening its own. The registry owns the engines: consumers
never dispose them, ``dispose_engines`` does at shutdown.

Fork safety: a child process must not use connections it inherited from its
parent. The registry forgets the inherited engines in the child, releasing them
without closing the parent's sockets, so the child builds its own on first use.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import threading
from typing import Any, Callable, Union

from sqlalchemy import Engine
from sqlalchemy.engine import URL, make_url

from orb.infrastructure.logging.logger import get_logger

_lock = threading.RLock()
_engines: dict[str, Engine] = {}

UrlLike = Union[str, URL]


def engine_key(url: UrlLike) -> str:
    """Identity of the database a URL names.

    The key contains the password, so it stays in memory and is never logged.
    SQLite file databases are keyed by their real path so two spellings of one
    file share an engine.
    """
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        database = parsed.database or ""
        if database and database != ":memory:" and not database.startswith("file:"):
            parsed = parsed.set(database=os.path.realpath(database))
    return parsed.render_as_string(hide_password=False)


def get_or_create_engine(url: UrlLike, factory: Callable[[URL], Engine]) -> Engine:
    """The process's engine for ``url``, built with ``factory`` on first use."""
    parsed = make_url(url)
    key = engine_key(parsed)
    with _lock:
        engine = _engines.get(key)
        if engine is None:
            engine = factory(parsed)
            _engines[key] = engine
            get_logger(__name__).debug(
                "Created shared %s engine (%s registered)", parsed.get_backend_name(), len(_engines)
            )
        return engine


def registered_engines() -> list[Engine]:
    """The engines currently held (a snapshot)."""
    with _lock:
        return list(_engines.values())


def dispose_engines() -> None:
    """Dispose every shared engine and forget it; later requests build new ones."""
    with _lock:
        engines = list(_engines.values())
        _engines.clear()
    for engine in engines:
        try:
            engine.dispose()
        except Exception as exc:  # shutdown must not fail on one broken pool
            get_logger(__name__).warning(
                "Failed to dispose a shared engine: %s", type(exc).__name__
            )


def _forget_inherited_engines() -> None:
    """Child side of a fork: drop the parent's engines without closing its sockets."""
    engines = list(_engines.values())
    _engines.clear()
    for engine in engines:
        # Nothing useful to do about a failure in a fresh child
        with contextlib.suppress(Exception):
            engine.dispose(close=False)


def _release_after_fork_in_child() -> None:
    try:
        _forget_inherited_engines()
    finally:
        _lock.release()


def _install_hooks() -> None:
    atexit.register(dispose_engines)
    register: Any = getattr(os, "register_at_fork", None)
    if register is not None:
        # The lock is held across the fork so the child never inherits it taken
        # by a thread that does not exist there.
        register(
            before=_lock.acquire,
            after_in_parent=_lock.release,
            after_in_child=_release_after_fork_in_child,
        )


_install_hooks()
