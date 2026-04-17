"""Shared HTTP session factory with retry + exponential backoff."""

from __future__ import annotations

import requests
from urllib3.util.retry import Retry

MAX_RETRIES = 3
BACKOFF_FACTOR = 5  # exponential backoff: 5s, 10s, 20s
RETRY_STATUS_CODES = [429, 500, 502, 503, 504]


def build_session(
    max_retries: int = MAX_RETRIES,
    backoff_factor: float = BACKOFF_FACTOR,
    allowed_methods: list[str] | None = None,
) -> requests.Session:
    """Build a requests Session with automatic retry + exponential backoff."""
    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=allowed_methods or ["GET"],
        raise_on_status=False,
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
