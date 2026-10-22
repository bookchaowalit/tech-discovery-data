"""discovery.v1 checked offline against discovery-data-contract-v1.yaml.

The contract lives in the private architecture-docs repo
(``systems/architecture/discovery-data-contract-v1.yaml``, status active); CI cannot
read it, so the values below are copied (2026-10-22). Known gaps are pinned explicitly,
so closing one fails this test until the list is updated.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any

from discovery_data import api, config, store

CONTRACT: dict[str, Any] = {
    "source": "book-discovery-data",
    "domain": "discovery",
    "schema_version": "discovery.v1",
    "port": 8110,
    "datasets": ["technology_signals", "technology_signals_history"],
    "identity_fields": ["source", "signal_id"],
    "identity_separator": "|",
    "refresh_default_status": 403,
    "api_methods": ["GET"],
}
NORMALIZED_FIELDS = [
    "signal_id",
    "source",
    "signal_kind",
    "title",
    "canonical_url",
    "description",
    "publisher",
    "source_url",
    "observed_at",
    "captured_at",
    "capture_kind",
    "record_kind",
    "attribution_required",
    "source_columns",
]
QUALITY_REQUIRED = ["signal_id", "source", "title", "captured_at", "canonical_url"]
# Finding (2026-10-22): the producer (data-lake-pipelines ingest_discovery_captures.py)
# spreads the raw CSV columns into the payload instead of a `source_columns` field, so no
# item carries it. Owned by the producer/contract, not this read-only boundary.
KNOWN_MISSING_NORMALIZED = {"source_columns"}
# v2 envelope data_status values plus this API's two extensions (finding: not in the
# frozen v2 list): "unavailable" (lake down, 503) and "method_not_allowed" (405).
REGISTRY_DATA_STATUS = {"ok", "empty", "stale", "malformed", "not_found", "forbidden", "error", "accepted"}
API_EXTENSIONS = {"unavailable", "method_not_allowed"}

# Payload shaped like the producer's mapped row (raw CSV columns + normalized fields).
PRODUCER_PAYLOAD = {
    "name": "example/repo",
    "stars": "1200",
    "url": "https://github.com/example/repo",
    "id": "github_trending|example/repo",
    "signal_id": "example/repo",
    "source": "github_trending",
    "signal_kind": "repository",
    "title": "example/repo",
    "canonical_url": "https://github.com/example/repo",
    "publisher": "GitHub",
    "source_url": "https://api.github.com/search/repositories",
    "description": "An example",
    "observed_at": "2026-10-22T00:00:00Z",
    "captured_at": "2026-10-22T00:00:00Z",
    "capture_kind": "current_snapshot",
    "record_kind": "technology_signal",
    "attribution_required": True,
}


class DiscoveryContractTests(unittest.TestCase):
    def test_identity_port_schema_and_datasets(self) -> None:
        self.assertEqual(config.REPO_NAME, CONTRACT["source"])
        self.assertEqual(config.DOMAIN, CONTRACT["domain"])
        self.assertEqual(config.SCHEMA_VERSION, CONTRACT["schema_version"])
        self.assertEqual(config.API_PORT, CONTRACT["port"])
        self.assertEqual([config.LAKE_DATASET_SIGNALS, config.LAKE_DATASET_HISTORY], CONTRACT["datasets"])
        self.assertEqual(list(config.ID_FIELDS), CONTRACT["identity_fields"])
        self.assertEqual(config.ID_SEP, CONTRACT["identity_separator"])

    def test_consumer_routes_are_get_only_and_refresh_is_operator(self) -> None:
        for (method, route), level in api.ROUTE_ACCESS.items():
            if level == api.PUBLIC:
                self.assertIn(method, CONTRACT["api_methods"], route)
        self.assertEqual(api.ROUTE_ACCESS[("POST", "/v1/refresh")], api.OPERATOR)
        self.assertFalse(config.ALLOW_REFRESH, "refresh must default to 403")

    def test_data_status_literals(self) -> None:
        text = "".join(p.read_text(encoding="utf-8") for p in Path(api.__file__).parent.glob("*.py"))
        used = set(re.findall(r'_status_envelope\(\s*"([a-z_]+)"', text))
        self.assertTrue(used, "scan found nothing")
        self.assertLessEqual(used, REGISTRY_DATA_STATUS | API_EXTENSIONS)

    def test_item_carries_normalized_and_required_fields(self) -> None:
        row = {
            "event_time": "2026-10-22T00:00:00Z",
            "source_record_id": "github_trending|example/repo",
            "raw_object_key": "landing/source=book-discovery-data/x.csv",
            "payload_json": json.dumps(PRODUCER_PAYLOAD),
        }
        try:
            item = store.signal_item_from_bronze(row)
        except store.SharedRuntimeUnavailable:
            self.skipTest("shared data_lake runtime not installed (uv sync --all-extras)")
        missing = {field for field in NORMALIZED_FIELDS if field not in item}
        self.assertEqual(missing, KNOWN_MISSING_NORMALIZED)
        for field in QUALITY_REQUIRED:
            self.assertTrue(item[field], field)
        self.assertIs(item["attribution_required"], True)
        source_record_id = CONTRACT["identity_separator"].join(item[f] for f in CONTRACT["identity_fields"])
        self.assertEqual(source_record_id, row["source_record_id"])
        self.assertEqual(item["raw_object_key"], row["raw_object_key"])


if __name__ == "__main__":
    unittest.main()
