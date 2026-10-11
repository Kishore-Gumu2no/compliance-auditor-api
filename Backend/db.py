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


_pool: Optional[ConnectionPool] = None
_lock = threading.Lock()


def open_pool() -> ConnectionPool:
    """Create and open the pool, or return the one already open.

    Called from the FastAPI lifespan so the server fails at startup, not on the
    first request, when the database is unreachable. Scripts, the Telegram bot
    and the tests never call this: :func:`pool` opens one on first use.
    """
    global _pool
    with _lock:
        if _pool is not None:
            return _pool
        pool = ConnectionPool(
            _conninfo(),
            min_size=int(os.getenv("DB_POOL_MIN", "1")),
            max_size=int(os.getenv("DB_POOL_MAX", "10")),
            # Hand out only connections that are still alive, so a database
            # restart costs one retry instead of breaking every later request.
            check=ConnectionPool.check_connection,
            name="fairpay",
            # Opened explicitly below: passing open=True to the constructor is
            # deprecated in psycopg_pool 3.2+.
            open=False,
        )
        pool.open()
        _pool = pool
        return _pool


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
