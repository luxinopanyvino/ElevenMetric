"""Outbound HTTP for external sources: opt-in, throttled, cached, honest.

Four rules, all of them load-bearing:

1. **Nothing is fetched unless the operator turned fetching on.** The default is
   off, and a disabled fetch raises :class:`SourceUnavailable` naming the
   setting — it never silently returns empty.
2. **Only hosts on the allow-list are contacted.** A parser bug that produces a
   URL pointing somewhere else fails loudly rather than making the request.
3. **One request per second per host**, configurable. A club preview reads a
   roster page and then up to two dozen player pages; without throttling that
   is a burst nobody consented to.
4. **Every response is cached with the moment it was read.** A repeated preview
   costs nothing, and each imported row can honestly state when its data was
   retrieved rather than when it was written.

Built on the standard library on purpose: this must not add a runtime dependency
to a project whose stated constraint is to stay light.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings

from app.services.external.base import FetchError, SourceUnavailable

#: Last request time per host, so the throttle is per-host rather than global.
_last_request: dict[str, float] = {}
_lock = threading.Lock()


@dataclass
class CachedResponse:
    """A fetched body plus when it was read and whether it came from cache."""

    body: str
    url: str
    retrieved_at: str
    from_cache: bool


def _host_allowed(host: str) -> bool:
    host = host.lower()
    return any(host == h or host.endswith("." + h)
               for h in (s.lower() for s in settings.external_hosts))


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return Path(settings.external_cache_dir) / f"{digest}.json"


def _read_cache(url: str, ttl_hours: float) -> CachedResponse | None:
    path = _cache_path(url)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        stamped = datetime.fromisoformat(payload["retrieved_at"])
    except (OSError, ValueError, KeyError):
        # A corrupt cache entry is not worth an error; refetching is correct.
        return None
    age_h = (datetime.now(timezone.utc) - stamped).total_seconds() / 3600.0
    if ttl_hours >= 0 and age_h > ttl_hours:
        return None
    return CachedResponse(body=payload["body"], url=url,
                          retrieved_at=payload["retrieved_at"], from_cache=True)


def _write_cache(url: str, body: str, retrieved_at: str) -> None:
    path = _cache_path(url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"url": url, "body": body, "retrieved_at": retrieved_at}),
            encoding="utf-8",
        )
    except OSError:
        # An unwritable cache slows the next preview down; it must not fail
        # the import the user actually asked for.
        pass


def _throttle(host: str) -> None:
    interval = max(0.0, float(settings.external_rate_limit_s))
    if interval <= 0:
        return
    with _lock:
        previous = _last_request.get(host)
        now = time.monotonic()
        if previous is not None and (wait := interval - (now - previous)) > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def fetch(url: str, *, source: str, expected: str,
          ttl_hours: float | None = None) -> CachedResponse:
    """GET ``url``, honouring the allow-list, the throttle and the cache.

    Raises :class:`SourceUnavailable` when fetching is switched off or the host
    is not allowed, and :class:`FetchError` when the request itself fails. It
    never returns a partial or empty body dressed up as success.
    """
    ttl = settings.external_cache_ttl_hours if ttl_hours is None else ttl_hours
    if (hit := _read_cache(url, ttl)) is not None:
        return hit

    if not settings.external_fetch_enabled:
        raise SourceUnavailable(
            source,
            "outbound fetching is switched off and this page is not cached",
            "Set ELEVENMETRIC_EXTERNAL_FETCH_ENABLED=true to allow requests to "
            f"{', '.join(settings.external_hosts)}, or import from a saved file "
            "instead. You are responsible for complying with the source's terms.",
        )

    host = (urlparse(url).hostname or "").lower()
    if not _host_allowed(host):
        raise SourceUnavailable(
            source,
            f"host {host!r} is not on the allow-list",
            "Add it to ELEVENMETRIC_EXTERNAL_HOSTS if you intend to contact it.",
        )

    _throttle(host)
    request = urllib.request.Request(url, headers={
        "User-Agent": settings.external_user_agent,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en",
    })
    try:
        with urllib.request.urlopen(request, timeout=settings.external_timeout_s) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise FetchError(source, url, expected, f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(source, url, expected, f"network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise FetchError(source, url, expected,
                         f"timed out after {settings.external_timeout_s}s") from exc

    body = raw.decode(charset, errors="replace")
    if not body.strip():
        raise FetchError(source, url, expected, "the response was empty")

    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_cache(url, body, retrieved_at)
    return CachedResponse(body=body, url=url, retrieved_at=retrieved_at, from_cache=False)


def fetch_enabled() -> tuple[bool, str, str]:
    """Whether live fetching is on, and if not, why and what to do about it."""
    if settings.external_fetch_enabled:
        return True, "", ""
    return (
        False,
        "outbound fetching is switched off",
        "Set ELEVENMETRIC_EXTERNAL_FETCH_ENABLED=true to enable it. Imports from "
        "a saved file work either way.",
    )
