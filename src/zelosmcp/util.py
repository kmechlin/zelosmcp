"""Cross-module helpers.

Currently exposes :func:`safe_record`, a decorator used by recorder /
telemetry helpers (``savings.py``, soon ``manager.py``) that want
"never raise — log and move on" semantics. Centralising the policy here
replaces ~20 ad-hoc ``try/except Exception``  blocks that all
implemented the same thing with slightly different logging styles.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Awaitable, Callable, TypeVar

_log = logging.getLogger(__name__)

T = TypeVar("T")


def safe_record(
    *,
    default: Any = None,
    logger: logging.Logger | None = None,
    log_level: int = logging.WARNING,
) -> Callable[[Callable[..., Awaitable[T] | T]], Callable[..., Awaitable[T] | T]]:
    """Wrap a function so any exception is logged and ``default`` is returned.

    Works for both sync and async functions. Intended for "best-effort"
    recorders — e.g. snapshotting savings telemetry into SQLite — where
    the caller doesn't want a recorder failure to bubble up and break
    the surrounding request.

    The wrapped function's exception type, message, and traceback are
    logged via :meth:`Logger.exception` (so the traceback is preserved
    at WARNING level), then ``default`` is returned in its place.
    """
    target_logger = logger or _log

    def decorator(
        func: Callable[..., Awaitable[T] | T],
    ) -> Callable[..., Awaitable[T] | T]:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    target_logger.log(
                        log_level,
                        "safe_record: %s raised; returning default",
                        func.__qualname__,
                        exc_info=True,
                    )
                    return default

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception:
                target_logger.log(
                    log_level,
                    "safe_record: %s raised; returning default",
                    func.__qualname__,
                    exc_info=True,
                )
                return default

        return sync_wrapper

    return decorator


__all__ = ["safe_record"]
