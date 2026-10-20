"""One place that turns an exception into the right status code and message.

Every endpoint used to carry its own ``except Exception`` that raised
``HTTPException(500, f"Failed to ...: {exc}")``. Nine copies of that block had
three problems:

* **Every failure looked the same.** An unreachable database, a malformed row
  and a genuine bug all arrived at the browser as ``500``, so the status code
  carried no information and the partner was told "Internal Server Error" for a
  condition that was neither internal nor an error in the code.
* **Nothing was logged.** The exception was formatted into a string and the
  traceback - the only part that says *where* it broke - was discarded. A 500
  in the terminal could not be traced back to a line.
* **A broken database answered 500**, which says "this server is faulty". It is
  not faulty; it is missing a dependency, which is ``503`` plus a
  ``Retry-After``. Monitoring, browsers and the UI all treat the two
  differently, and the UI can only say "the database is down, start it" if the
  server says so.

:func:`operation` replaces all nine. It maps the infrastructure failures that
are not the caller's fault, logs everything with its traceback, and leaves
domain errors to the endpoint that understands them.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from db import DatabaseUnavailable

logger = logging.getLogger("fairpay.api")

#: Sent to the browser when Postgres cannot be reached. It names the port and
#: the command, because the one reading it is the operator, and "Internal
#: Server Error" cost real debugging time.
DATABASE_UNAVAILABLE_DETAIL = (
    "The FairPay database is not reachable. Start it with "
    "`docker compose up -d db` and check that port 54322 is listening, then "
    "try again."
)

#: ``Retry-After``, in seconds, on a 503. The database is typically a container
#: that is starting, so retrying shortly is the right advice.
RETRY_AFTER_SECONDS = "10"


def is_database_unavailable(exc: BaseException) -> bool:
    """True when this exception means "the database is not there".

    ``PoolTimeout`` is the pool giving up waiting for a connection, and
    ``OperationalError`` covers the connection itself failing (refused, timed
    out, authentication rejected, server restarting). Both mean the dependency
    is unavailable rather than the request being wrong, so both are a 503.

    Checked recursively through ``__cause__``: a repository function that wraps
    a connection failure in its own exception must still answer 503, or the
    mapping would depend on how deeply the failure happened to be nested.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (PoolTimeout, psycopg.OperationalError, DatabaseUnavailable)):
            return True
        current = current.__cause__ or current.__context__
    return False


def database_unavailable() -> HTTPException:
    """The 503 for an unreachable database, with its ``Retry-After``."""
    return HTTPException(
        status_code=503,
        detail=DATABASE_UNAVAILABLE_DETAIL,
        headers={"Retry-After": RETRY_AFTER_SECONDS},
    )


@contextmanager
def operation(
    description: str,
    *,
    not_found: tuple[type[BaseException], ...] = (),
) -> Iterator[None]:
    """Run a block and map anything it raises to the right HTTP status.

    ``description`` completes the sentence "Failed to ...", so it is a verb
    phrase in lower case: ``operation("load the earnings baseline")``.

    ``not_found`` names the exceptions this particular operation uses to mean
    "the thing asked for does not exist", which is a 404 rather than a server
    error. It is per-call because the same type means different things in
    different places: ``load_partner_history_from_db`` raises ``ValueError``
    for "this partner has no transactions" (404), while a ``ValueError`` from
    the calculation layer is a bug (500).

    The mapping, in order:

    * ``HTTPException`` passes through untouched - the endpoint already decided.
    * An unreachable database becomes **503** with a message that says how to
      start it. This is the one that used to surface as a bare 500.
    * Anything in ``not_found`` becomes **404** carrying its own message.
    * Everything else becomes **500**, named by ``description`` and by the
      exception's type, and logged with its full traceback.
    """
    try:
        yield
    except HTTPException:
        # Already a decision about status and wording; re-raising unchanged is
        # what lets an endpoint raise 404/410/422 from inside the block.
        raise
    except not_found as exc:  # type: ignore[misc]  # () is a valid empty except
        logger.info("Not found while trying to %s: %s", description, exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        if is_database_unavailable(exc):
            # logger.warning, not .exception: the traceback of a connection
            # timeout is noise, and the cause is already in the message.
            logger.warning("Database unavailable while trying to %s: %s", description, exc)
            raise database_unavailable() from exc
        # The traceback is the point. Without it a 500 names the endpoint but
        # never the line, which is what made these errors silent.
        logger.exception("Failed to %s", description)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to {description} ({type(exc).__name__}: {exc})",
        ) from exc


def register_exception_handlers(app: FastAPI) -> None:
    """Catch database failures that reached the app without an :func:`operation`.

    A safety net, not the main path: a new endpoint that forgets to wrap its
    database call still answers 503 instead of 500, so the worst case of
    forgetting is a missing log line rather than a misleading status code.
    """

    @app.exception_handler(PoolTimeout)
    @app.exception_handler(psycopg.OperationalError)
    async def _database_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("Database unavailable serving %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=503,
            content={"detail": DATABASE_UNAVAILABLE_DETAIL},
            headers={"Retry-After": RETRY_AFTER_SECONDS},
        )
