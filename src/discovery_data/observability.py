"""Opt-in Sentry for the discovery.v1 API.

Off unless ``SENTRY_DSN`` is set and ``sentry_sdk`` is importable (``sentry`` extra).
``do_GET`` fails closed: a lake error becomes a 503 ``unavailable`` envelope with no
details, so the exception is reported here instead. Events carry the exception type and
stack only: no PII, no frame locals, no request bodies, and ``scrub_event`` drops request
data, log text (``logentry``, ``message``) and exception messages (which can hold lake
paths or tokens). Log records never become events or breadcrumbs: ``LoggingIntegration`` is
switched off. The API never calls ``capture_message``, so any ``message`` is free log text.
"""

from __future__ import annotations

import os
from importlib import import_module
from types import ModuleType
from typing import Any

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
        logging_integration = import_module("sentry_sdk.integrations.logging").LoggingIntegration
    except ImportError:
        return None
    sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "local"),
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=scrub_event,
        # No log-to-event and no log breadcrumbs: log lines (ours or urllib3's) hold free text.
        integrations=[logging_integration(level=None, event_level=None, sentry_logs_level=None)],
    )
    sdk.set_tag("service", SERVICE)
    _sdk = sdk
    return sdk


def scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Drop request data, log text, frame locals and exception messages; keep the type and stack."""
    for key in ("request", "extra", "logentry", "message"):
        event.pop(key, None)
    for value in (event.get("exception") or {}).get("values") or []:
        if "value" in value:
            value["value"] = ""
        for frame in (value.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    return event


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
