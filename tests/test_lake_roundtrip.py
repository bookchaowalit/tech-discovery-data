"""End-to-end Bronze round trip through the shared lake runtime.

Runs when the ``[lake]`` extra (``solo-empire-data-lake`` + pyarrow/duckdb)
is installed; otherwise skips with a reason. Everything is written to a
temporary lake directory, so no network and no real lake are touched.
"""

from __future__ import annotations

import json
import tempfile
import unittest

from discovery_data import config, store

ROWS = [
    {
        "id": "github_trending|example/repo",
        "event_time": "2026-09-12T00:00:00Z",
        "signal_id": "example/repo",
        "source": "github_trending",
        "title": "example/repo",
        "canonical_url": "https://github.com/example/repo",
        "publisher": "GitHub",
        "attribution_required": True,
    },
    {
        "id": "hacker_news|42",
        "event_time": "2026-09-12T01:00:00Z",
        "signal_id": "42",
        "source": "hacker_news",
        "title": "Show HN: 100% offline tool",
        "canonical_url": "https://news.ycombinator.com/item?id=42",
        "publisher": "Hacker News",
        "attribution_required": True,
    },
]


@unittest.skipUnless(
    store.shared_runtime_available(),
    "shared data_lake runtime not installed (pip install -e '.[lake]')",
)
class LakeRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="discovery-lake-")
        self.lake_uri = self._tmp.name
        from data_lake.product_adapter import ingest_to_lake

        for dataset in (config.LAKE_DATASET_SIGNALS, config.LAKE_DATASET_HISTORY):
            # Batch ids derive from the landed bytes, so each dataset lands its own batch.
            raw = json.dumps({"dataset": dataset, "rows": ROWS}).encode("utf-8")
            ingest_to_lake(
                store._contract(),
                raw=raw,
                records=[dict(row) for row in ROWS],
                dataset=dataset,
                data_lake_uri=self.lake_uri,
            )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_load_records_builds_attributed_items_with_lineage(self) -> None:
        payload = store.load_records(data_lake_uri=self.lake_uri)
        items = {item["signal_id"]: item for item in payload["items"]}
        self.assertEqual(set(items), {"example/repo", "42"})
        repo = items["example/repo"]
        self.assertEqual(repo["canonical_url"], "https://github.com/example/repo")
        self.assertTrue(repo["attribution_required"])
        self.assertTrue(repo["ingest_run_id"])
        self.assertTrue(repo["raw_object_key"].startswith("landing/"))
        # Ids containing "/" are hashed so they stay a single URL path segment.
        self.assertTrue(repo["record_id"].startswith("hash:"))
        self.assertEqual(items["42"]["record_id"], "hacker_news|42")

    def test_get_record_resolves_ids_and_misses_unknown(self) -> None:
        record = store.get_record("hacker_news|42", data_lake_uri=self.lake_uri)
        assert record is not None
        self.assertEqual(record["title"], "Show HN: 100% offline tool")
        self.assertIsNone(store.get_record("hacker_news|missing", data_lake_uri=self.lake_uri))

    def test_history_items_are_suffixed(self) -> None:
        payload = store.load_history(data_lake_uri=self.lake_uri)
        self.assertEqual(len(payload["items"]), len(ROWS))
        for item in payload["items"]:
            self.assertRegex(item["record_id"], r"#h\d+$")


if __name__ == "__main__":
    unittest.main()
