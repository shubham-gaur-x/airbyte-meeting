"""Shared utilities for the meeting-memory-graph transform service."""

from __future__ import annotations

import asyncio
import functools
import time
import uuid
from datetime import date, datetime
from typing import Any, Callable, Optional

import structlog
from slugify import slugify as _slugify

log = structlog.get_logger()

__all__ = [
    "with_retry",
    "slugify",
    "uuid5_id",
    "extract_domain",
    "safe_date",
]


def with_retry(max_attempts: int = 3, base_delay: float = 2.0) -> Callable:
    """Decorator that retries a function on exception with exponential back-off.

    Works on both sync and async functions.
    """
    def decorator(fn: Callable) -> Callable:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: Exception | None = None
                for attempt in range(max_attempts):
                    try:
                        return await fn(*args, **kwargs)
                    except Exception as exc:
                        last_exc = exc
                        delay = base_delay * (2 ** attempt)
                        log.warning(
                            "retry",
                            fn=fn.__name__,
                            attempt=attempt + 1,
                            max_attempts=max_attempts,
                            delay=delay,
                            error=str(exc),
                        )
                        await asyncio.sleep(delay)
                raise last_exc  # type: ignore[misc]
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: Exception | None = None
                for attempt in range(max_attempts):
                    try:
                        return fn(*args, **kwargs)
                    except Exception as exc:
                        last_exc = exc
                        delay = base_delay * (2 ** attempt)
                        log.warning(
                            "retry",
                            fn=fn.__name__,
                            attempt=attempt + 1,
                            max_attempts=max_attempts,
                            delay=delay,
                            error=str(exc),
                        )
                        time.sleep(delay)
                raise last_exc  # type: ignore[misc]
            return sync_wrapper
    return decorator


def slugify(text: str) -> str:
    """Lowercase slug, max 64 chars."""
    return _slugify(text, max_length=64)


def uuid5_id(namespace: str, value: str) -> str:
    """Deterministic UUID from namespace + value, returned as a lowercase hex string."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"{namespace}:{value}").hex


def extract_domain(email: str) -> str:
    """Extract domain from an email address. Returns '' on malformed input."""
    try:
        return email.split("@")[1].lower()
    except (IndexError, AttributeError):
        return ""


def safe_date(value: Any) -> Optional[date]:
    """Parse a date from str, datetime, or date. Returns None on failure."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%B %d %Y",
            "%b %d %Y",
            "%d %B %Y",
        ):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None
