import csv
from pathlib import Path

from django.test import SimpleTestCase

from data_pipeline import (
    CanonicalLogRecord,
    build_document_manifest,
    clean_data_sources,
    diff_document_manifests,
    iter_document_chunks,
    iter_llama_documents,
)


class LogDocumentPipelineTests(SimpleTestCase):
    def _write_csv(self, path: Path, headers: list[str], rows: list[list[str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)

    def test_source_parsers_normalize_and_dedupe_full_and_reduced_records(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "from_kaggle").mkdir()
            self._write_csv(
                root / "z-standard.csv",
                ["服务", "级别", "错误", "消息", "组件", "原因"],
                [["OrderService", "warn", "TIMEOUT", "订单创建超时", "OrderCreator", "下游超时"]],
            )
            self._write_csv(
                root / "from_kaggle" / "Computer_events.csv",
                ["级别", "来源", "事件标识", "事件来源名称", "描述1", "日期和时间"],
                [
                    [
                        "Informações",
                        "MSSQLSERVER",
                        "17890",
                        "MSSQL",
                        "memory paged out",
                        "6/28/2020 10:57:55 PM",
                    ]
                ],
            )
            nested = root / "from_kaggle" / "Computer_events_column_reduced.csv"
            self._write_csv(
                nested,
                ["级别", "来源", "任务类别", "日志名称", "描述1"],
                [["Informações", "MSSQLSERVER", "Server", "Application", "memory paged out"]],
            )

            result = clean_data_sources(root)

        self.assertEqual(result.quality_report["files_discovered"], 3)
        self.assertEqual(result.quality_report["duplicate_rows"], 1)
        self.assertEqual(result.quality_report["accepted_records"], 2)
        standard = next(record for record in result.records if record.service == "OrderService")
        self.assertEqual(standard.level, "WARNING")
        self.assertEqual(standard.source_file, "z-standard.csv")
        computer = next(record for record in result.records if record.service == "MSSQLSERVER")
        self.assertEqual(computer.timestamp, "2020-06-28T22:57:55")
        self.assertEqual(computer.error_code, "17890")
        self.assertEqual(computer.source_row, 1)

    def test_sensitive_rows_are_quarantined_and_not_emitted(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "logs.csv"
            self._write_csv(
                path,
                ["service", "level", "message", "email"],
                [
                    ["safe", "INFO", "healthy", ""],
                    ["unsafe", "ERROR", "api_key=sk-test-secret", "user@example.com"],
                ],
            )
            result = clean_data_sources(path)

        self.assertEqual(len(result.records), 1)
        self.assertEqual(len(result.quarantined), 1)
        self.assertEqual(
            set(result.quarantined[0]["reasons"]), {"email", "pii_field", "secret_value"}
        )
        self.assertEqual(result.quality_report["quarantine_reasons"]["email"], 1)

    def test_pii_columns_are_redacted_from_metadata_without_losing_event(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "windows_event_log.csv"
            self._write_csv(
                path,
                ["MachineName", "EntryType", "Message", "city", "country"],
                [["host-1", "Error", "service failed", "Tianjin", "China"]],
            )
            result = clean_data_sources(path)

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.quality_report["redacted_rows"], 1)
        self.assertNotIn("city", result.records[0].metadata)
        self.assertNotIn("country", result.records[0].metadata)
        self.assertEqual(result.records[0].level, "ERROR")

    def test_blank_source_headers_are_not_emitted_as_chroma_metadata_keys(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "logs.csv"
            self._write_csv(
                path,
                ["service", "level", "message", ""],
                [["worker", "ERROR", "failed", "unusable metadata"]],
            )
            result = clean_data_sources(path)

        self.assertEqual(len(result.records), 1)
        self.assertNotIn("", result.records[0].metadata)

    def test_strict_metadata_variants_receive_distinct_document_ids(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "windows_event_log.csv"
            self._write_csv(
                path,
                ["MachineName", "EntryType", "Message"],
                [
                    ["host-a", "Info", "service started"],
                    ["host-b", "Info", "service started"],
                ],
            )
            result = clean_data_sources(path)
            chunks = list(iter_document_chunks(result.records, max_chars=200))

        self.assertEqual(len(result.records), 2)
        self.assertEqual(len({record.document_id for record in result.records}), 2)
        self.assertEqual(len({chunk.chunk_id for chunk in chunks}), len(chunks))

    def test_document_ids_metadata_and_chunks_are_stable_and_bounded(self):
        record = CanonicalLogRecord(
            document_id="log-fixed",
            source_file="sample.csv",
            source_row=2,
            service="python",
            level="ERROR",
            error_code="E1",
            message="。".join(["a" * 30] * 8),
            component="Parser",
            cause="输入过长",
            timestamp=None,
            language="python",
            metadata={"task": "parse"},
        )

        chunks = list(iter_document_chunks([record], max_chars=80))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 80 for chunk in chunks))
        self.assertEqual([chunk.chunk_index for chunk in chunks], list(range(len(chunks))))
        self.assertEqual(chunks[0].metadata["source_file"], "sample.csv")
        self.assertEqual(chunks[0].metadata["document_id"], "log-fixed")
        self.assertEqual(
            [chunk.chunk_id for chunk in chunks],
            [f"log-fixed-chunk-{index:04d}" for index in range(len(chunks))],
        )
        self.assertEqual(
            [chunk.text for chunk in iter_document_chunks([record], max_chars=80)],
            [chunk.text for chunk in chunks],
        )

    def test_python_bug_fix_source_has_domain_content_and_streams_documents(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "python_bug_fix_pairs.csv"
            self._write_csv(
                path,
                ["id", "buggy_code", "fixed_code", "commit_message", "commit_url", "date"],
                [
                    [
                        "1",
                        "print(x)",
                        "print(x + 1)",
                        "fix arithmetic",
                        "https://example.test/1",
                        "2024-01-01",
                    ]
                ],
            )
            documents = list(iter_llama_documents(path, max_chars=500))

        self.assertEqual(len(documents), 1)
        self.assertIn("修复后代码", documents[0].text)
        self.assertEqual(documents[0].metadata["language"], "python")
        self.assertEqual(documents[0].id_, documents[0].metadata["chunk_id"])

    def test_manifest_reports_updates_and_deletes_by_stable_chunk_id(self):
        record = CanonicalLogRecord(
            document_id="log-one",
            source_file="sample.csv",
            source_row=2,
            service="svc",
            level="ERROR",
            error_code="E1",
            message="failure",
            component="worker",
            cause="timeout",
            timestamp=None,
            language="generic",
        )
        current = build_document_manifest([record])
        previous = {key: dict(value) for key, value in current.items()}
        previous["log-old"] = {"content_hash": "old"}
        current["log-one"]["content_hash"] = "changed"

        self.assertEqual(
            diff_document_manifests(previous, current),
            {"upsert": ["log-one"], "delete": ["log-old"]},
        )
