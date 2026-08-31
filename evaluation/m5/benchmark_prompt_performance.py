"""Measure the M4/M5 prompt assembly cost without invoking a model."""

from __future__ import annotations

import json
import sys
import timeit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "django_backend"))

from topklogsystem import TopKLogSystem  # noqa: E402


def main() -> int:
    system = TopKLogSystem.__new__(TopKLogSystem)
    system.system_prompt = "Role: analyst\nLog: {log_context}\nQuery: {query}"
    system.response_template = "legacy template"
    system.max_parts_num = 3
    system.max_part_length = 70
    system.prompt_version = "m5-v1"
    system.max_prompt_context_chars = 12000
    context = [
        {
            "document_id": f"log-{index}",
            "content": "timeout connection refused " * 30,
            "metadata": {"source_file": "sample.csv", "source_row": index},
        }
        for index in range(1, 11)
    ]
    query = "如何排查数据库连接超时？"
    prompt = system._build_prompt_text(query, context)
    evidence_start = prompt.find("<untrusted_evidence>")
    evidence_end = prompt.find("</untrusted_evidence>") + len("</untrusted_evidence>")
    evidence_block_chars = evidence_end - evidence_start
    result = {
        "context_items": len(context),
        "optimized_prompt_chars": len(prompt),
        "evidence_marker_count": prompt.count("<untrusted_evidence>"),
        "duplicate_evidence_chars_removed": evidence_block_chars,
        "optimized_assembly_p50_us": timeit.timeit(
            lambda: system._build_prompt_text(query, context), number=100
        )
        * 1_000_000
        / 100,
        "note": "duplicate_evidence_chars_removed is the former second evidence block estimate; no model/network was invoked",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
