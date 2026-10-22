"""The API reports swallowed lake failures to Sentry when SENTRY_DSN is set.

``do_GET`` fails closed: any lake error becomes a 503 ``unavailable`` envelope with no
details, which hides a broken lake from the operator. With a DSN, that exception must
reach Sentry (without leaking into the response). No network beyond a loopback socket;
``sentry_sdk`` is a fake module in ``sys.modules``.
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import types
import unittest
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from test_http_api import fake_envelope, fake_paginate, fake_records

from discovery_data import api


class FakeSentry(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("sentry_sdk")
        self.init_kwargs: dict[str, Any] | None = None
        self.exceptions: list[BaseException] = []
        self.tags: dict[str, str] = {}

    def init(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs

    def set_tag(self, key: str, value: str) -> None:
        self.tags[key] = value

    def capture_exception(self, error: BaseException) -> None:
        self.exceptions.append(error)

    def flush(self, timeout: Any = None) -> None:
        return None


def broken_lake(**_kwargs: Any) -> dict[str, Any]:
    raise OSError("/private/lake/path is unreadable")


class ApiSentryTests(unittest.TestCase):
    def start(self, dsn: str) -> tuple[FakeSentry, str]:
        sentry = FakeSentry()
        env = {"SENTRY_DSN": dsn} if dsn else {}
        for item in (
            patch.dict(sys.modules, {"sentry_sdk": sentry}),
            patch.dict(os.environ, env, clear=True),
            patch.object(api, "load_records", fake_records),
            patch.object(api, "load_history", fake_records),
            patch.object(api, "paginate", fake_paginate),
            patch.object(api, "envelope", fake_envelope),
            patch.object(sys, "stderr", new=io.StringIO()),
        ):
            item.start()
            self.addCleanup(item.stop)
        server = api.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.addCleanup(stop)
        return sentry, f"http://127.0.0.1:{server.server_address[1]}"

    def get(self, url: str) -> tuple[int, dict[str, Any]]:
        try:
            with urlopen(url, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_lake_failure_reaches_sentry_but_not_the_client(self) -> None:
        sentry, base = self.start("https://key@example.invalid/1")
        with patch.object(api, "load_records", broken_lake):
            status, body = self.get(base + "/v1/records")
        self.assertEqual(status, 503)
        self.assertEqual(body["data_status"], "unavailable")
        self.assertNotIn("/private/lake", json.dumps(body))
        self.assertEqual(len(sentry.exceptions), 1)
        self.assertIsInstance(sentry.exceptions[0], OSError)
        self.assertEqual(sentry.tags.get("service"), "book-discovery-data-api")
        self.assertIs((sentry.init_kwargs or {}).get("send_default_pii"), False)

    def test_init_scrubs_locals_request_bodies_and_messages(self) -> None:
        sentry, _base = self.start("https://key@example.invalid/1")
        kwargs: dict[str, Any] = sentry.init_kwargs or {}
        self.assertIs(kwargs.get("include_local_variables"), False)
        self.assertEqual(kwargs.get("max_request_body_size"), "never")
        before_send = kwargs.get("before_send")
        self.assertTrue(callable(before_send))
        event = {
            "request": {"headers": {"Authorization": "Bearer secret-token"}, "data": "body"},
            "exception": {
                "values": [
                    {
                        "type": "OSError",
                        "value": "/private/lake/path token=secret-token",
                        "stacktrace": {"frames": [{"function": "f", "vars": {"token": "secret-token"}}]},
                    }
                ]
            },
        }
        scrubbed = before_send(event, {})
        self.assertNotIn("secret-token", json.dumps(scrubbed))
        self.assertNotIn("/private/lake", json.dumps(scrubbed))
        self.assertNotIn("request", scrubbed)
        value = scrubbed["exception"]["values"][0]
        self.assertEqual(value["type"], "OSError")
        self.assertNotIn("vars", value["stacktrace"]["frames"][0])

    def test_healthy_request_sends_nothing(self) -> None:
        sentry, base = self.start("https://key@example.invalid/1")
        status, _body = self.get(base + "/v1/records")
        self.assertEqual(status, 200)
        self.assertEqual(sentry.exceptions, [])

    def test_without_dsn_sentry_stays_off(self) -> None:
        sentry, base = self.start("")
        with patch.object(api, "load_records", broken_lake):
            status, _body = self.get(base + "/v1/records")
        self.assertEqual(status, 503)
        self.assertIsNone(sentry.init_kwargs)
        self.assertEqual(sentry.exceptions, [])


if __name__ == "__main__":
    unittest.main()
