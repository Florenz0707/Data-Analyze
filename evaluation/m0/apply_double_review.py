#!/usr/bin/env python3
"""Apply the recorded independent review metadata to selected gold cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_DATASET = Path(__file__).with_name("gold_queries.jsonl")
DEFAULT_RESULTS = Path(__file__).with_name("evidence") / "double_review_results.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()

    results = json.loads(args.results.read_text(encoding="utf-8"))
    scope = results["review_scope"]
    review_ids = set(scope["sample_ids"])
    cases = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    seen = {case["case_id"] for case in cases}
    missing = sorted(review_ids - seen)
    if missing:
        raise SystemExit(f"review cases missing from dataset: {missing}")

    for case in cases:
        if case["case_id"] not in review_ids:
            continue
        annotation = case["annotation"]
        annotation["reviewers"] = ["user", "codex-assisted"]
        annotation["reviewer_types"] = {
            "user": "human",
            "codex-assisted": "human"
            if scope.get("human_double_review_basis") == "user_explicit_designation"
            else "ai_assisted",
        }
        annotation["status"] = "conflict" if scope.get("conflict_detected") else "reviewed"

    args.dataset.write_text(
        "".join(
            json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n" for case in cases
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "updated_cases": len(review_ids),
                "human_reviewer": scope["human_reviewer"],
                "second_reviewer": scope["second_reviewer"],
                "human_double_review_complete": scope["human_double_review_complete"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
