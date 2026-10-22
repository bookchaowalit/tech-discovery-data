"""Local read-only HTTP API for discovery.v1.

The API reads committed Bronze Parquet through the shared lake adapter. It
never imports a collector or contacts a provider during a GET request.
"""

from __future__ import annotations

import hmac
import json
import re
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import config
from .observability import init_sentry, report_exception
from .store import envelope, get_record, load_history, load_records, paginate, utc_now_iso

_LOCAL_ORIGIN_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", re.I)


def _allowed_cors_origin(origin: str | None) -> str | None:
    if not origin:
        return None
    origin = origin.strip()
    if _LOCAL_ORIGIN_RE.match(origin) or origin in config.CORS_ALLOWED_ORIGINS:
        return origin
    return None


def _apply_cors_headers(handler: BaseHTTPRequestHandler) -> None:
    origin = _allowed_cors_origin(handler.headers.get("Origin"))
    if not origin:
        return
    handler.send_header("Access-Control-Allow-Origin", origin)
    handler.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")
    handler.send_header("Access-Control-Max-Age", "600")
    handler.send_header("Vary", "Origin")


def _json_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    body: dict[str, Any],
    extra_headers: dict[str, str] | None = None,
) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    for name, value in (extra_headers or {}).items():
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store")
    _apply_cors_headers(handler)
    handler.end_headers()
    handler.wfile.write(payload)


def _status_envelope(data_status: str, error: str, **extra: Any) -> dict[str, Any]:
    """Build a no-data response envelope without touching the shared lake runtime."""
    body: dict[str, Any] = {
        "schema_version": config.SCHEMA_VERSION,
        "source": config.REPO_NAME,
        "retrieved_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "data_status": data_status,
        "items": [],
        "next_cursor": None,
        "error": error,
    }
    body.update(extra)
    return body


REFRESH_OPERATOR_COMMAND = "task scraping:discovery:ingest"

# Route access matrix: every (method, route) the API serves. "public" reads
# need nothing; "operator" needs ALLOW_REFRESH plus `Authorization: Bearer
# $REFRESH_TOKEN`. Handlers only run for listed pairs (unknown route -> 404,
# known route with another method -> 405), and tests/test_route_access.py pins
# the table, so a new route or a public write fails CI until classified.
PUBLIC = "public"
OPERATOR = "operator"
ROUTE_ACCESS: dict[tuple[str, str], str] = {
    ("GET", "/healthz"): PUBLIC,
    ("GET", "/v1/metadata"): PUBLIC,
    ("GET", "/v1/records"): PUBLIC,
    ("GET", "/v1/records/{id}"): PUBLIC,
    ("GET", "/v1/history"): PUBLIC,
    ("POST", "/v1/refresh"): OPERATOR,
}


def route_pattern(path: str) -> str | None:
    """The ROUTE_ACCESS route serving ``path`` (already stripped of a trailing slash)."""
    if any(path == route for _method, route in ROUTE_ACCESS if "{" not in route):
        return path
    if path.startswith("/v1/records/"):
        return "/v1/records/{id}"
    return None


def route_methods(pattern: str | None) -> list[str]:
    return sorted(method for method, route in ROUTE_ACCESS if route == pattern)


def _refresh_authorized(auth_header: str) -> bool:
    if not (config.ALLOW_REFRESH and config.REFRESH_TOKEN):
        return False
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return False
    return hmac.compare_digest(token.strip().encode("utf-8"), config.REFRESH_TOKEN.encode("utf-8"))


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(query.get(name, [str(default)])[0])
    except (TypeError, ValueError):
        return default


class DataProductHandler(BaseHTTPRequestHandler):
    server_version = "book-discovery-data-api/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def do_OPTIONS(self) -> None:  # noqa: N802
        origin = _allowed_cors_origin(self.headers.get("Origin"))
        if not origin:
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"error":"origin not allowed"}')
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._handle_get()
        except Exception as exc:  # fail closed without leaking lake paths or payloads
            sys.stderr.write(f"[{config.REPO_NAME}] data unavailable: {type(exc).__name__}\n")
            report_exception(exc)
            _json_response(self, 503, _status_envelope("unavailable", "data lake unavailable"))

    def _handle_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if not self._route_allowed("GET", path):
            return None

        if path == "/healthz":
            records = load_records()
            return _json_response(
                self,
                200,
                {
                    "status": "ok",
                    "repository": config.REPO_NAME,
                    "domain": config.DOMAIN,
                    "schema_version": config.SCHEMA_VERSION,
                    "data_status": records["data_status"],
                    "retrieved_at": utc_now_iso(),
                    "free_only": config.FREE_ONLY,
                    "allow_paid_providers": config.ALLOW_PAID_PROVIDERS,
                    "allow_external_writes": config.ALLOW_EXTERNAL_WRITES,
                    "allow_refresh": config.ALLOW_REFRESH,
                },
            )

        if path == "/v1/metadata":
            records = load_records()
            history = load_history()
            body = {
                "repository": config.REPO_NAME,
                "domain": config.DOMAIN,
                "schema_version": config.SCHEMA_VERSION,
                "record_count": len(records["items"]),
                "history_count": len(history["items"]),
                "records_path": records["path"],
                "history_path": history["path"],
                "records_source_kind": records.get("source_kind"),
                "history_source_kind": history.get("source_kind"),
                "api_bind": f"{config.API_HOST}:{config.API_PORT}",
                "free_only": config.FREE_ONLY,
                "allow_paid_providers": config.ALLOW_PAID_PROVIDERS,
                "allow_external_writes": config.ALLOW_EXTERNAL_WRITES,
                "allow_refresh": config.ALLOW_REFRESH,
                "lake_domain": config.LAKE_DOMAIN,
                "lake_source": config.LAKE_SOURCE,
                "lake_dataset_signals": config.LAKE_DATASET_SIGNALS,
                "lake_dataset_history": config.LAKE_DATASET_HISTORY,
                "attribution_required": True,
                "acquisition": "approved_public_capture_files_only",
                "read_mode": records.get("read_mode", config.LAKE_READ_MODE),
                "read_fallback": records.get("read_fallback", config.LAKE_READ_FALLBACK),
                "fallback_reason": records.get("fallback_reason"),
                "shared_adapter": "data_lake.product_adapter",
            }
            return _json_response(
                self,
                200,
                envelope(
                    items=[body],
                    data_status=records["data_status"],
                    retrieved_at=records["retrieved_at"],
                ),
            )

        if path == "/v1/records":
            records = load_records()
            page, next_cursor = paginate(
                records["items"],
                limit=_query_int(query, "limit", 50),
                cursor=query.get("cursor", [None])[0],
            )
            return _json_response(
                self,
                200,
                envelope(
                    items=page,
                    data_status=records["data_status"],
                    next_cursor=next_cursor,
                    retrieved_at=records["retrieved_at"],
                ),
            )

        if path.startswith("/v1/records/"):
            raw_record_id = path[len("/v1/records/") :]
            record_id = unquote(raw_record_id)
            if not record_id:
                return _json_response(self, 400, _status_envelope("error", "missing record_id"))
            # The shared store URL-decodes the id itself; decoding here too would
            # turn an id containing "%25" into a different id.
            item = get_record(raw_record_id)
            if item is None:
                return _json_response(self, 404, _status_envelope("not_found", "record not found", record_id=record_id))
            records = load_records()
            return _json_response(
                self,
                200,
                envelope(
                    items=[item],
                    data_status=records["data_status"],
                    retrieved_at=records["retrieved_at"],
                ),
            )

        if path == "/v1/history":
            history = load_history()
            page, next_cursor = paginate(
                history["items"],
                limit=_query_int(query, "limit", 50),
                cursor=query.get("cursor", [None])[0],
            )
            return _json_response(
                self,
                200,
                envelope(
                    items=page,
                    data_status=history["data_status"],
                    next_cursor=next_cursor,
                    retrieved_at=history["retrieved_at"],
                ),
            )

        return _json_response(self, 404, _status_envelope("not_found", "unknown endpoint"))

    def _route_allowed(self, method: str, path: str) -> bool:
        """Answer 404/405 for anything ROUTE_ACCESS does not list."""
        pattern = route_pattern(path)
        if (method, pattern) in ROUTE_ACCESS:
            return True
        methods = route_methods(pattern)
        if not methods:
            _json_response(self, 404, _status_envelope("not_found", "unknown endpoint"))
        else:
            _json_response(
                self,
                405,
                _status_envelope("method_not_allowed", "method not allowed"),
                extra_headers={"Allow": ", ".join(methods)},
            )
        return False

    def _reject_method(self) -> None:
        self._route_allowed(self.command, urlparse(self.path).path.rstrip("/") or "/")

    do_PUT = do_PATCH = do_DELETE = _reject_method  # noqa: N815

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._route_allowed("POST", path):
            return None
        if not _refresh_authorized(self.headers.get("Authorization", "")):
            return _json_response(self, 403, _status_envelope("forbidden", "refresh disabled"))
        # Collection is an explicit operator action (discovery contract:
        # refresh_default_status 403). An authorised request is acknowledged
        # only; say so instead of implying a job was started.
        body = _status_envelope(
            "accepted",
            "",
            items=[
                {
                    "accepted": True,
                    "action": "refresh_acknowledged",
                    "job_started": False,
                    "operator_command": REFRESH_OPERATOR_COMMAND,
                }
            ],
        )
        body.pop("error")
        return _json_response(self, 202, body)


def create_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    init_sentry()  # no-op without SENTRY_DSN
    return ThreadingHTTPServer(
        (host or config.API_HOST, port if port is not None else config.API_PORT),
        DataProductHandler,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Local read-only API for book-discovery-data")
    parser.add_argument("--host", default=config.API_HOST)
    parser.add_argument("--port", type=int, default=config.API_PORT)
    args = parser.parse_args(argv)
    server = create_server(args.host, args.port)
    print(f"[{config.REPO_NAME}] API listening on http://{args.host}:{args.port}")
    print("  GET /healthz /v1/metadata /v1/records /v1/records/{id} /v1/history")
    print("  POST /v1/refresh (403 by default)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
