#!/usr/bin/env python3
"""Export synthetic M0 model answers and gold annotations for independent review."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path

from validate_dataset import load_cases

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "backend" / "django_backend" / "db.sqlite3"
DEFAULT_OUTPUT = Path(__file__).with_name("evidence") / "human_review_packet.csv"
DEFAULT_DATASET = Path(__file__).with_name("gold_queries.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--username", default="m0_evaluation_full")
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as database:
        answers = {
            session_id: response or ""
            for session_id, response in database.execute(
                "SELECT session_id, response FROM deepseek_api_history WHERE user = ? ORDER BY id",
                (args.username,),
            )
        }
    fieldnames = [
        "case_id",
        "category",
        "is_negative",
        "query",
        "relevant_log_ids",
        "expected_causes",
        "expected_steps",
        "forbidden_advice",
        "model_answer",
        "reviewer",
        "cause_score_1_to_5",
        "evidence_score_1_to_5",
        "steps_score_1_to_5",
        "forbidden_advice_hit_yes_no",
        "negative_refusal_yes_no_na",
        "notes",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for case in cases:
            writer.writerow(
                {
                    "case_id": case["case_id"],
                    "category": case["category"],
                    "is_negative": str(case["is_negative"]).lower(),
                    "query": case["query"],
                    "relevant_log_ids": json.dumps(case["relevant_log_ids"], ensure_ascii=False),
                    "expected_causes": json.dumps(case["expected_causes"], ensure_ascii=False),
                    "expected_steps": json.dumps(case["expected_steps"], ensure_ascii=False),
                    "forbidden_advice": json.dumps(case["forbidden_advice"], ensure_ascii=False),
                    "model_answer": answers.get(f"m0_{case['case_id'].lower()}", ""),
                }
            )
    missing = sum(not answers.get(f"m0_{case['case_id'].lower()}") for case in cases)
    print(
        json.dumps(
            {"output": str(args.output), "rows": len(cases), "missing_answers": missing}, indent=2
        )
    )
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
