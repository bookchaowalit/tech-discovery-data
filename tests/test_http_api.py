"""Offline HTTP tests for the read-only API.

The shared Solo Empire lake runtime is replaced with in-memory stubs, so these
tests run in a standalone checkout and in CI. Only a loopback socket is used.
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
from urllib.parse import unquote
from urllib.request import Request, urlopen

from discovery_data import api, config, store

ITEMS = [{"record_id": f"github_trending|repo-{index}", "title": f"Repo {index}"} for index in range(3)]
ITEMS.append({"record_id": "hn|100%25-literal", "title": "Percent id"})


def fake_records(**_kwargs):
    return {
        "items": list(ITEMS),
        "data_status": "fresh",
        "retrieved_at": "2026-09-12T00:00:00Z",
        "path": "/private/lake/path",
        "source_kind": "parquet",
    }


def fake_paginate(items, *, limit=50, cursor=None):
    start = int(cursor or 0)
    limit = max(1, min(limit, 500))
    end = start + limit
    return items[start:end], (str(end) if end < len(items) else None)


def fake_envelope(*, items, data_status, next_cursor=None, retrieved_at=None, extra=None):
    body = {
        "schema_version": config.SCHEMA_VERSION,
        "source": config.REPO_NAME,
        "retrieved_at": retrieved_at,
        "data_status": data_status,
        "items": items,
        "next_cursor": next_cursor,
    }
    body.update(extra or {})
    return body


def fake_get_record(record_id, **_kwargs):
    # Mirrors data_lake.product_store.get_record_from_payload, which URL-decodes once.
    record_id = unquote(record_id)
    return next((item for item in ITEMS if item["record_id"] == record_id), None)


class HttpApiTests(unittest.TestCase):
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
            patch.object(sys, "stderr", new=io.StringIO()),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def call(self, method, path, headers=None):
        request = Request(self.base + path, method=method, headers=headers or {})
        try:
            with urlopen(request, timeout=3) as response:
                raw = response.read()
                return response.status, dict(response.headers), json.loads(raw) if raw else None
        except HTTPError as error:
            raw = error.read()
            return error.code, dict(error.headers), json.loads(raw) if raw else None

    def test_records_are_paginated_with_cursor(self):
        status, headers, body = self.call("GET", "/v1/records?limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual([item["title"] for item in body["items"]], ["Repo 0", "Repo 1"])
        self.assertEqual(body["next_cursor"], "2")
        status, _, body = self.call("GET", "/v1/records?limit=2&cursor=2")
        self.assertEqual([item["title"] for item in body["items"]], ["Repo 2", "Percent id"])
        self.assertIsNone(body["next_cursor"])

    def test_invalid_limit_falls_back_to_default(self):
        status, _, body = self.call("GET", "/v1/records?limit=abc")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["items"]), len(ITEMS))

    def test_single_record_and_missing_record(self):
        status, _, body = self.call("GET", "/v1/records/github_trending%7Crepo-1")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"][0]["title"], "Repo 1")
        status, _, body = self.call("GET", "/v1/records/hn%7C100%2525-literal")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"][0]["title"], "Percent id")
        status, _, body = self.call("GET", "/v1/records/unknown")
        self.assertEqual(status, 404)
        self.assertEqual(body["data_status"], "not_found")
        self.assertEqual(body["items"], [])

    def test_metadata_reports_read_only_policy(self):
        status, _, body = self.call("GET", "/v1/metadata")
        self.assertEqual(status, 200)
        meta = body["items"][0]
        self.assertEqual(meta["record_count"], len(ITEMS))
        self.assertTrue(meta["attribution_required"])
        self.assertFalse(meta["allow_refresh"])

    def test_unknown_endpoint_is_404_json(self):
        status, _, body = self.call("GET", "/v1/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["schema_version"], "discovery.v1")
        self.assertEqual(body["error"], "unknown endpoint")

    def test_refresh_forbidden_by_default(self):
        status, _, body = self.call("POST", "/v1/refresh", {"Authorization": "Bearer anything"})
        self.assertEqual(status, 403)
        self.assertEqual(body["data_status"], "forbidden")

    def test_refresh_requires_matching_bearer_token(self):
        with patch.object(config, "ALLOW_REFRESH", True), patch.object(config, "REFRESH_TOKEN", "local-test-value"):
            self.assertEqual(self.call("POST", "/v1/refresh", {"Authorization": "Bearer wrong"})[0], 403)
            self.assertEqual(self.call("POST", "/v1/refresh", {"Authorization": "Basic local-test-value"})[0], 403)
            self.assertEqual(self.call("POST", "/v1/refresh")[0], 403)
            status, _, body = self.call("POST", "/v1/refresh", {"Authorization": "bearer local-test-value"})
        self.assertEqual(status, 202)
        self.assertEqual(body["data_status"], "accepted")
        self.assertNotIn("error", body)
        # Acknowledge-only: the response must not imply a collection job ran.
        self.assertEqual(body["items"][0]["job_started"], False)
        self.assertEqual(body["items"][0]["operator_command"], api.REFRESH_OPERATOR_COMMAND)

    def test_empty_refresh_token_never_authorizes(self):
        with patch.object(config, "ALLOW_REFRESH", True), patch.object(config, "REFRESH_TOKEN", ""):
            self.assertEqual(self.call("POST", "/v1/refresh", {"Authorization": "Bearer "})[0], 403)

    def test_cors_allows_only_loopback_or_configured_origins(self):
        status, headers, _ = self.call("OPTIONS", "/v1/records", {"Origin": "http://localhost:3000"})
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:3000")
        status, headers, _ = self.call("OPTIONS", "/v1/records", {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, headers, _ = self.call("GET", "/healthz", {"Origin": "https://evil.example"})
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_lake_failure_returns_503_without_leaking_details(self):
        def unavailable(**_kwargs):
            raise store.SharedRuntimeUnavailable("/secret/local/path is missing")

        with patch.object(api, "load_records", unavailable):
            status, _, body = self.call("GET", "/healthz")
        self.assertEqual(status, 503)
        self.assertEqual(body["data_status"], "unavailable")
        self.assertNotIn("/secret/local/path", json.dumps(body))


class RuntimeDiscoveryTests(unittest.TestCase):
    def test_missing_runtime_raises_clear_error(self):
        with patch.object(store, "_PRODUCT_STORE", None), patch.dict(sys.modules, {"data_lake": None}):
            with self.assertRaises(store.SharedRuntimeUnavailable):
                store._ps()

    def test_bad_data_lake_root_raises_clear_error(self):
        with (
            patch.object(store, "_PRODUCT_STORE", None),
            patch.object(config, "SOLO_DATA_LAKE_ROOT", "/nonexistent/solo-data-lake"),
            patch("importlib.util.find_spec", return_value=None),
        ):
            with self.assertRaises(store.SharedRuntimeUnavailable):
                store._ps()

    def test_env_bool_parsing(self):
        with patch.dict("os.environ", {"FLAG_A": " Yes ", "FLAG_B": "off"}):
            self.assertTrue(config.env_bool("FLAG_A", False))
            self.assertFalse(config.env_bool("FLAG_B", True))
            self.assertTrue(config.env_bool("FLAG_MISSING", True))


if __name__ == "__main__":
    unittest.main()
