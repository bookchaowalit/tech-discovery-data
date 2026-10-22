"""Route access matrix for the discovery API.

ROUTE_ACCESS gates every handler. These tests pin it, forbid public writes,
and call each route with every method as an anonymous caller, with a wrong
token and with the refresh token (lake reads stubbed, loopback only).
"""

from __future__ import annotations

import io
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_http_api import fake_envelope, fake_get_record, fake_paginate, fake_records

from discovery_data import api, config

EXPECTED = {
    ("GET", "/healthz"): "public",
    ("GET", "/v1/metadata"): "public",
    ("GET", "/v1/records"): "public",
    ("GET", "/v1/records/{id}"): "public",
    ("GET", "/v1/history"): "public",
    ("POST", "/v1/refresh"): "operator",
}
SAMPLE_PATHS = {
    "/healthz": "/healthz",
    "/v1/metadata": "/v1/metadata",
    "/v1/records": "/v1/records",
    "/v1/records/{id}": "/v1/records/github_trending%7Crepo-0",
    "/v1/history": "/v1/history",
    "/v1/refresh": "/v1/refresh",
}
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
TOKEN = "matrix-refresh-token"


class RouteAccessTableTests(unittest.TestCase):
    def test_table_is_pinned(self):
        self.assertEqual(api.ROUTE_ACCESS, EXPECTED)

    def test_no_public_write(self):
        for (method, route), level in api.ROUTE_ACCESS.items():
            if method not in {"GET", "HEAD"}:
                self.assertNotEqual(level, api.PUBLIC, f"{method} {route} must not be public")


class RouteAccessMatrixTests(unittest.TestCase):
    server: ThreadingHTTPServer
    thread: threading.Thread
    base: str

    @classmethod
    def setUpClass(cls):
        cls.server = api.create_server("127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        patches: list[Any] = [
            patch.object(api, "load_records", fake_records),
            patch.object(api, "load_history", fake_records),
            patch.object(api, "paginate", fake_paginate),
            patch.object(api, "envelope", fake_envelope),
            patch.object(api, "get_record", fake_get_record),
            patch.object(api, "utc_now_iso", lambda: "2026-09-12T00:00:00Z"),
            patch.object(config, "ALLOW_REFRESH", True),
            patch.object(config, "REFRESH_TOKEN", TOKEN),
            patch.object(sys, "stderr", new=io.StringIO()),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def status(self, method, path, headers=None):
        data = b"" if method in {"POST", "PUT", "PATCH"} else None
        request = Request(self.base + path, method=method, headers=headers or {}, data=data)
        try:
            with urlopen(request, timeout=3) as response:
                response.read()
                return response.status, dict(response.headers)
        except HTTPError as error:
            body = error.read()
            if body:
                json.loads(body)  # every rejection is JSON, never the stdlib HTML page
            return error.code, dict(error.headers)

    def test_every_route_method_and_caller(self):
        callers = {
            "anonymous": {},
            "wrong-token": {"Authorization": "Bearer wrong"},
            "operator": {"Authorization": f"Bearer {TOKEN}"},
        }
        routes = {route for _method, route in api.ROUTE_ACCESS}
        for route in sorted(routes):
            for method in METHODS:
                level = api.ROUTE_ACCESS.get((method, route))
                for caller, headers in callers.items():
                    with self.subTest(route=route, method=method, caller=caller):
                        status, response_headers = self.status(method, SAMPLE_PATHS[route], headers)
                        if level is None:
                            self.assertEqual(status, 405)
                            self.assertEqual(response_headers.get("Allow"), ", ".join(api.route_methods(route)))
                        elif level == api.PUBLIC:
                            self.assertEqual(status, 200)
                        elif caller == "operator":
                            self.assertEqual(status, 202)
                        else:
                            self.assertEqual(status, 403)

    def test_unlisted_paths_are_404_for_every_method(self):
        for path in ("/", "/v1", "/admin", "/healthz/extra", "/v1/refresh/now"):
            for method in METHODS:
                with self.subTest(path=path, method=method):
                    self.assertEqual(self.status(method, path)[0], 404)


if __name__ == "__main__":
    unittest.main()
