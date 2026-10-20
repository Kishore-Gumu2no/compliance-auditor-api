"""The PostgreSQL connection pool for the whole process.

This replaces the single module-level connection that ``real_data.py`` used to
open at import time. That one connection was shared by every caller, including
the worker threads ``asyncio.to_thread`` starts, so two problems were possible:

* One failed statement left the shared connection in a failed transaction, and
  the next unrelated caller's query failed too until somebody rolled back.
* Two threads using one connection interleaved their transactions, so a commit
  by one could commit another's half-finished work.

A pool fixes both: every caller borrows its own connection and gets its own
transaction. ``psycopg_pool.ConnectionPool`` is thread-safe by design.

The pool is synchronous on purpose. Every query in this codebase is written
against a synchronous cursor and the audit path deliberately runs in a worker
thread (``asyncio.to_thread`` in ``api.py``), so ``AsyncConnectionPool`` would
mean rewriting every repository function and every caller above it as ``async``
for no gain. A synchronous pool hands each worker thread its own connection,
which is exactly what that design needs.

Repository functions take an optional ``conn`` and pass it to :func:`session`,
so a caller or a test can inject its own connection (and group several calls
into one transaction) instead of reaching for a module-level global.
"""

from __future__ import annotations

import atexit
import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

from dotenv import load_dotenv
from psycopg import Connection
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

load_dotenv()

# The defaults are the local values used when running directly on the host
# (Postgres published on localhost:54322); docker-compose overrides them with
# the `db` service. These are the same names and defaults the single global
# connection used, so nothing about how the app is configured changes.
def _conninfo() -> str:
    return make_conninfo(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "54322")),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres"),
    )


def describe_target() -> str:
    """``host:port/dbname``, for an error message.

    Built from the environment rather than from :func:`_conninfo`, whose string
    carries the password; this is written to logs and returned to callers.
    """
    return (
        f"{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '54322')}"
        f"/{os.getenv('DB_NAME', 'postgres')}"
    )


class DatabaseUnavailable(RuntimeError):
    """Postgres could not be reached. Raised by :func:`open_pool` on verify."""


_pool: Optional[ConnectionPool] = None
_lock = threading.Lock()


def open_pool(*, verify: bool = False, verify_timeout: Optional[float] = None) -> ConnectionPool:
    """Create and open the pool, or return the one already open.

    Scripts, the Telegram bot and the tests never call this: :func:`pool` opens
    one on first use.

    ``verify=True`` makes this actually reach the database before returning,
    and raise :class:`DatabaseUnavailable` when it cannot. **Opening a pool
    proves nothing on its own.** ``ConnectionPool.open()`` defaults to
    ``wait=False``: it starts background worker threads and returns
    immediately, successfully, even when there is no server at the address. The
    old call site here relied on it as a startup check and therefore had no
    startup check at all - the server came up looking healthy with no database,
    and every later request paid a full ``timeout`` of waiting before failing
    with a 500. ``pool.wait()`` is the call that blocks until ``min_size``
    connections are really established, and raises if they cannot be.

    The server passes ``verify=True`` from its lifespan so that a missing
    database is one loud failure at startup instead of a silent one per request.
    """
    global _pool
    with _lock:
        if _pool is not None:
            pool_ = _pool
        else:
            pool_ = ConnectionPool(
                _conninfo(),
                min_size=int(os.getenv("DB_POOL_MIN", "1")),
                max_size=int(os.getenv("DB_POOL_MAX", "10")),
                # How long a caller waits for a free connection before giving
                # up. psycopg_pool's own default is 30s, which is far longer
                # than any partner will hold a page open: the request is dead
                # either way, so fail in 10 and let the UI say so.
                timeout=float(os.getenv("DB_POOL_TIMEOUT", "10")),
                # Hand out only connections that are still alive, so a database
                # restart costs one retry instead of breaking every later request.
                check=ConnectionPool.check_connection,
                name="fairpay",
                # Opened explicitly below: passing open=True to the constructor is
                # deprecated in psycopg_pool 3.2+.
                open=False,
            )
            pool_.open()
            _pool = pool_

    if verify:
        # Outside the lock on purpose: this blocks for as long as the database
        # takes to answer, and holding `_lock` across it would stall every
        # other thread trying to borrow a connection.
        timeout = (
            verify_timeout
            if verify_timeout is not None
            else float(os.getenv("DB_STARTUP_TIMEOUT", "10"))
        )
        try:
            pool_.wait(timeout=timeout)
        except Exception as exc:
            raise DatabaseUnavailable(
                f"Could not reach Postgres at {describe_target()} within {timeout:g}s: {exc}. "
                "Start it with `docker compose up -d db`, or set DB_HOST/DB_PORT to "
                "point at your own Postgres."
            ) from exc
    return pool_


def close_pool() -> None:
    """Close the pool and every connection in it. Called from the lifespan."""
    global _pool
    with _lock:
        if _pool is None:
            return
        _pool.close()
        _pool = None


def pool() -> ConnectionPool:
    """The process pool, opened on first use.

    Opening lazily keeps the modules importable outside the server, where there
    is no lifespan to call :func:`open_pool` - the test suite, the Telegram bot
    and the ingest scripts all rely on this.
    """
    if _pool is None:
        return open_pool()
    return _pool


# The server closes the pool in its lifespan, which is deterministic. Scripts,
# the Telegram bot and the tests have no lifespan, so without this their pool
# would be collected during interpreter finalization, where its worker threads
# can no longer be joined ("cannot join thread at interpreter shutdown").
# Closing an already-closed pool is a no-op.
atexit.register(close_pool)


@contextmanager
def session(conn: Optional[Connection] = None) -> Iterator[Connection]:
    """Yield the caller's connection, or borrow one from the pool.

    This is the injection point. Passing ``conn`` hands control of the
    transaction to the caller, which is what lets several repository calls share
    one transaction and what lets a test pass a connection it can roll back.
    Passing nothing borrows a connection for the duration of the block; the
    pool commits it on a clean exit and rolls it back if the block raises.
    """
    if conn is not None:
        yield conn
        return
    with pool().connection() as borrowed:
        yield borrowed
