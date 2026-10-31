"""Bounded retries for deployment reads, never ambiguous mutations."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import requests
from urllib3.util.retry import Retry
from urllib3.exceptions import InvalidHeader

READ_ATTEMPTS = 6
TRANSIENT_READ_STATUSES = {408, 429, 500, 502, 503, 504}


def request_with_read_retry(
    method: str, url: str, *, send: Callable[..., requests.Response] | None = None,
    retry_http: bool = True, retry_throttled_writes: bool = False, **kwargs: Any,
) -> requests.Response:
    method = method.upper()
    sender = send or requests.request
    safe_read = method in {"GET", "HEAD"}
    for attempt in range(READ_ATTEMPTS):
        delay = min(2 ** attempt, 15)
        try:
            response = sender(method, url, **kwargs)
        except requests.exceptions.SSLError:
            raise
        except (requests.ConnectionError, requests.Timeout) as error:
            if not safe_read or attempt == READ_ATTEMPTS - 1:
                raise
            reason = type(error).__name__
        else:
            retry = (safe_read and retry_http and response.status_code in TRANSIENT_READ_STATUSES
                     or retry_throttled_writes and response.status_code == 429)
            if not retry or attempt == READ_ATTEMPTS - 1:
                return response
            reason = f"HTTP {response.status_code}"
            header = response.headers.get("Retry-After")
            if header:
                try:
                    delay = Retry().parse_retry_after(header)
                except InvalidHeader:
                    print("Invalid Retry-After header; using bounded backoff.", flush=True)
            elif response.status_code == 429:
                delay = 10
            if delay > 120:
                print("Retry-After exceeds the 120-second retry bound; read remains unverified.", flush=True)
                return response
        print(
            f"{method} to {urlparse(url).hostname} interrupted ({reason}); "
            f"retry {attempt + 1}/{READ_ATTEMPTS - 1} in {delay}s.",
            flush=True,
        )
        time.sleep(delay)
    raise AssertionError("Unreachable HTTP retry state.")


def get_with_read_retry(url: str, **kwargs: Any) -> requests.Response:
    return request_with_read_retry(
        "GET", url, send=lambda _method, target, **options: requests.get(target, **options), **kwargs,
    )
