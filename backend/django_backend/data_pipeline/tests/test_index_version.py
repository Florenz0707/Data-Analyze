import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from django.test import SimpleTestCase

from data_pipeline import (
    IndexStateStore,
    build_index_spec,
    cleanup_old_index_collections,
    compute_data_content_hash,
)


class IndexVersionTests(SimpleTestCase):
    def _write_csv(self, path: Path, message: str) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["service", "level", "message"])
            writer.writerow(["worker", "ERROR", message])

    def test_data_hash_and_index_identity_change_with_inputs(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_csv(root / "logs.csv", "timeout")
            first_hash = compute_data_content_hash(root)
            first = build_index_spec(
                root,
                logical_version="v1",
                embedding_provider="ollama",
                embedding_model="embed-a",
                embedding_dimensions=768,
                embedding_parameters={"normalize": True},
                retrieval_parameters={"min_score": 0.2},
            )
            self._write_csv(root / "logs.csv", "database timeout")
            second_hash = compute_data_content_hash(root)
            second = build_index_spec(
                root,
                logical_version="v1",
                embedding_provider="ollama",
                embedding_model="embed-a",
                embedding_dimensions=768,
                embedding_parameters={"normalize": True},
                retrieval_parameters={"min_score": 0.2},
            )

        self.assertNotEqual(first_hash, second_hash)
        self.assertNotEqual(first.version, second.version)
        self.assertLessEqual(len(first.collection_name("a/long collection name")), 63)
        self.assertEqual(first.payload["retrieval_parameters"], {"min_score": 0.2})

    def test_state_store_publishes_atomically_and_failure_keeps_current(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "logs.csv"
            self._write_csv(data, "timeout")
            spec = build_index_spec(
                root,
                logical_version="v1",
                embedding_provider="ollama",
                embedding_model="embed-a",
                embedding_dimensions=None,
            )
            store = IndexStateStore(root / "state.json")
            store.mark_building(spec, "logs__building")
            store.mark_ready(spec, "logs__ready", 2)
            store.mark_failed(spec, "logs__ready", "embedding unavailable")
            state = store.load()

        self.assertEqual(state["current_version"], spec.version)
        self.assertEqual(state["versions"][spec.version]["status"], "failed")
        self.assertNotIn("embedding unavailable", state["versions"][spec.version]["spec"])

    def test_cleanup_keeps_current_and_latest_versioned_collections(self):
        client = Mock()
        state = {
            "current_version": "v3",
            "versions": {
                "v1": {"status": "ready", "ready_at": "2026-01-01", "collection_name": "logs__v1"},
                "v2": {"status": "ready", "ready_at": "2026-02-01", "collection_name": "logs__v2"},
                "v3": {"status": "ready", "ready_at": "2026-03-01", "collection_name": "logs__v3"},
                "legacy": {"status": "ready", "ready_at": "2025-01-01", "collection_name": "logs"},
            },
        }

        removed = cleanup_old_index_collections(
            client,
            base_name="logs",
            state=state,
            keep_versions=2,
        )

        self.assertEqual(removed, ["logs__v1"])
        client.delete_collection.assert_called_once_with("logs__v1")
