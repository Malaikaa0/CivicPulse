"""Graceful shutdown: close what the app opened, then exit.

uvicorn handles SIGTERM by refusing new connections and letting in-flight requests finish. Only
then does it run the lifespan shutdown below, so nothing here can cut a live request short.
Resources are created lazily (behind lru_cache), so whoever creates one registers its closer.
"""

import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

logger = logging.getLogger("civicpulse.lifecycle")

_lock = threading.Lock()
_closers: list[Callable[[], object]] = []


def register_closer(closer: Callable[[], object]) -> None:
    with _lock:
        _closers.append(closer)


def close_all() -> int:
    """Run every registered closer, newest first, and return how many failed.

    Never raises: one stuck resource must not stop the others from closing. Idempotent: closers
    run once, so a second call finds nothing to do.
    """
    with _lock:
        pending = _closers[::-1]
        _closers.clear()

    failed = 0
    for closer in pending:
        try:
            closer()
        except Exception:
            failed += 1
            logger.exception("failed to close a resource", extra={"closer": repr(closer)})
    return failed


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    logger.info("shutting down")
    failed = close_all()
    logger.log(
        logging.WARNING if failed else logging.INFO,
        "resources closed",
        extra={"failed": failed},
    )
