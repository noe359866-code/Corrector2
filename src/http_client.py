"""HTTP con reintentos, backoff, ritmo por host y circuit breaker.

Respeta los límites de las APIs gratuitas (AniList ~30-90/min, Jikan ~2.4/s).
"""
from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import httpx

log = logging.getLogger("http")

# pausa mínima entre llamadas por host (segundos)
_MIN_INTERVAL = {
    "graphql.anilist.co": 1.0,
    "api.jikan.moe": 0.45,
    "kitsu.io": 0.3,
    "www.omdbapi.com": 0.2,
    "api.themoviedb.org": 0.1,
    "v3.sg.media-imdb.com": 0.1,
    "api.tvmaze.com": 0.2,
}
_DEFAULT_INTERVAL = 0.05


class Http:
    """Wrapper fino de httpx con reintentos y cortocircuito por host."""

    def __init__(self, timeout: float = 20.0, retries: int = 3,
                 user_agent: str = "torrents-enricher/1.6") -> None:
        self.client = httpx.Client(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            http2=True,
        )
        self.retries = retries
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self.calls = 0

    def close(self) -> None:
        self.client.close()

    # -- ritmo + breaker ----------------------------------------------------
    def _wait(self, host: str) -> None:
        with self._lock:
            until = self._open_until.get(host, 0)
            if until > time.monotonic():
                raise httpx.ConnectError(f"circuit open for {host}")
            gap = _MIN_INTERVAL.get(host, _DEFAULT_INTERVAL)
            wait = gap - (time.monotonic() - self._last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)

    def _mark(self, host: str, ok: bool) -> None:
        with self._lock:
            self._last[host] = time.monotonic()
            if ok:
                self._fails[host] = 0
            else:
                self._fails[host] = self._fails.get(host, 0) + 1
                if self._fails[host] >= 5:
                    self._open_until[host] = time.monotonic() + 60.0
                    log.warning("circuit open: %s (60s)", host)

    # -- llamadas -----------------------------------------------------------
    def _request(self, method: str, url: str, **kw) -> httpx.Response | None:
        host = urlparse(url).hostname or ""
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                self._wait(host)
            except httpx.ConnectError:
                return None
            try:
                self.calls += 1
                r = self.client.request(method, url, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    self._mark(host, False)
                    last_err = httpx.HTTPStatusError(
                        f"HTTP {r.status_code}", request=r.request, response=r)
                else:
                    self._mark(host, True)
                    return r
            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.RemoteProtocolError) as e:
                self._mark(host, False)
                last_err = e
            time.sleep(0.5 * (2 ** attempt))
        log.debug("HTTP %s %s falló tras %d intentos: %s",
                  method, url, self.retries + 1, last_err)
        return None

    def get_json(self, url: str, **kw) -> dict | list | None:
        """GET -> json (None si falla). Acepta params/headers de httpx."""
        r = self._request("GET", url, **kw)
        if r is None or r.status_code >= 400:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def post_json(self, url: str, payload: dict, **kw) -> dict | list | None:
        """POST json -> json (None si falla)."""
        r = self._request("POST", url, json=payload, **kw)
        if r is None or r.status_code >= 400:
            return None
        try:
            return r.json()
        except ValueError:
            return None
