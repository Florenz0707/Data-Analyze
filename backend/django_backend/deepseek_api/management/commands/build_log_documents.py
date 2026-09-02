"""Build an auditable cleaned log corpus without changing the live index."""

from __future__ import annotations

import json
from pathlib import Path

from data_pipeline import build_document_manifest, clean_data_sources, iter_document_chunks
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Clean CSV logs and optionally stream canonical document chunks to JSONL."

    def add_arguments(self, parser):
        parser.add_argument("--input", default="data/log", help="CSV file or directory")
        parser.add_argument("--quality-report", help="Write the redacted quality report to JSON")
        parser.add_argument("--documents", help="Write document chunks to JSONL")
        parser.add_argument("--manifest", help="Write the stable document manifest to JSON")
        parser.add_argument("--max-chars", type=int, default=200)

    def handle(self, *args, **options):
        max_chars = options["max_chars"]
        if max_chars <= 0:
            raise CommandError("--max-chars 必须为正数")
        result = clean_data_sources(options["input"])
        report = dict(result.quality_report)
        report["document_chunks"] = sum(
            1 for _ in iter_document_chunks(result.records, max_chars=max_chars)
        )

        quality_report = options.get("quality_report")
        if quality_report:
            report_path = Path(quality_report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

        documents_path = options.get("documents")
        if documents_path:
            document_path = Path(documents_path)
            document_path.parent.mkdir(parents=True, exist_ok=True)
            with document_path.open("w", encoding="utf-8") as handle:
                for chunk in iter_document_chunks(result.records, max_chars=max_chars):
                    handle.write(
                        json.dumps(
                            {
                                "id": chunk.chunk_id,
                                "text": chunk.text,
                                "metadata": chunk.metadata,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )

        manifest_path = options.get("manifest")
        if manifest_path:
            path = Path(manifest_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    build_document_manifest(result.records, max_chars=max_chars),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        self.stdout.write(
            json.dumps(
                {
                    "accepted_records": report["accepted_records"],
                    "quarantined_records": report["quarantined_records"],
                    "duplicate_rows": report["duplicate_rows"],
                    "document_chunks": report["document_chunks"],
                },
                ensure_ascii=False,
            )
        )
