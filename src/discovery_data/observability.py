"""Opt-in Sentry for the discovery.v1 API.

Off unless ``SENTRY_DSN`` is set and ``sentry_sdk`` is importable (``sentry`` extra).
``do_GET`` fails closed: a lake error becomes a 503 ``unavailable`` envelope with no
details, so the exception is reported here instead. No request bodies, payloads or
personal data are attached (``send_default_pii=False``).
"""

from __future__ import annotations

import os
from importlib import import_module
from types import ModuleType

SERVICE = "book-discovery-data-api"

_sdk: ModuleType | None = None


def init_sentry() -> ModuleType | None:
    """Initialise Sentry for this process; return the SDK, or None when it is off."""
    global _sdk
    _sdk = None
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return None
    try:
        sdk = import_module("sentry_sdk")
    except ImportError:
        return None
    sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "local"),
        send_default_pii=False,
        traces_sample_rate=0.0,
    )
    sdk.set_tag("service", SERVICE)
    _sdk = sdk
    return sdk


def report_exception(error: BaseException) -> None:
    """Send one exception event when Sentry is on; never raises."""
    sdk = _sdk
    if sdk is None:
        return
    try:
        sdk.capture_exception(error)
        sdk.flush(timeout=5)
    except Exception:  # reporting must never break the fail-closed response
        return
