#!/usr/bin/env python3
"""Validate the M0 JSONL dataset without requiring optional validation packages."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = Path(__file__).with_name("gold_queries.jsonl")
LOG_DIR = ROOT / "backend" / "django_backend" / "data" / "log"
REQUIRED_CATEGORIES = {"python", "java", "linux_command", "windows_event", "generic_service"}
REQUIRED_FIELDS = {
    "case_id",
    "category",
    "query",
    "is_negative",
    "relevant_log_ids",
    "expected_causes",
    "expected_steps",
    "forbidden_advice",
    "annotation",
}
SECRET_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            cases.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return cases


def inventory_log_ids() -> set[str]:
    inventory = set()
    for path in sorted(LOG_DIR.glob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row_number, _ in enumerate(csv.DictReader(handle), 1):
                inventory.add(f"{path.name}:{row_number:06d}")
    return inventory


def validate(cases: list[dict]) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    ids = [case.get("case_id") for case in cases]
    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    if duplicates:
        errors.append(f"duplicate case_id values: {duplicates}")

    known_log_ids = inventory_log_ids()
    reviewed_by_two = 0
    conflicts = []
    for index, case in enumerate(cases, 1):
        label = case.get("case_id") or f"line {index}"
        missing = sorted(REQUIRED_FIELDS - set(case))
        if missing:
            errors.append(f"{label}: missing fields {missing}")
            continue
        if case["category"] not in REQUIRED_CATEGORIES:
            errors.append(f"{label}: invalid category {case['category']!r}")
        if not isinstance(case["query"], str) or len(case["query"].strip()) < 8:
            errors.append(f"{label}: query must contain at least 8 characters")
        for field in ("relevant_log_ids", "expected_causes", "expected_steps", "forbidden_advice"):
            if not isinstance(case[field], list):
                errors.append(f"{label}: {field} must be a list")
        if (
            not case["expected_causes"]
            or not case["expected_steps"]
            or not case["forbidden_advice"]
        ):
            errors.append(f"{label}: expected and forbidden annotation lists cannot be empty")
        if case["is_negative"] and case["relevant_log_ids"]:
            errors.append(f"{label}: negative cases cannot declare relevant logs")
        if not case["is_negative"] and not case["relevant_log_ids"]:
            errors.append(f"{label}: positive cases require at least one relevant log")
        unknown = sorted(set(case["relevant_log_ids"]) - known_log_ids)
        if unknown:
            errors.append(f"{label}: unknown relevant_log_ids {unknown}")
        annotation = case["annotation"]
        if not isinstance(annotation, dict) or not {"author", "reviewers", "status"} <= set(
            annotation
        ):
            errors.append(f"{label}: incomplete annotation metadata")
            continue
        reviewers = annotation.get("reviewers") or []
        if len(set(reviewers)) >= 2 and annotation.get("status") in {"reviewed", "conflict"}:
            reviewed_by_two += 1
        if annotation.get("status") == "conflict":
            conflicts.append(label)
        serialized = json.dumps(case, ensure_ascii=False)
        if any(pattern.search(serialized) for pattern in SECRET_PATTERNS):
            errors.append(f"{label}: possible secret or private key detected")

    categories = Counter(case.get("category") for case in cases)
    missing_categories = sorted(REQUIRED_CATEGORIES - set(categories))
    if missing_categories:
        errors.append(f"missing required categories: {missing_categories}")
    if len(cases) < 50:
        errors.append(f"dataset has {len(cases)} cases; at least 50 are required")
    negative_count = sum(bool(case.get("is_negative")) for case in cases)
    negative_ratio = negative_count / len(cases) if cases else 0.0
    if negative_ratio < 0.20:
        errors.append(f"negative ratio {negative_ratio:.1%} is below 20%")
    review_ratio = reviewed_by_two / len(cases) if cases else 0.0
    if review_ratio < 0.20:
        warnings.append(f"dual-review coverage {review_ratio:.1%} is below the M0 target of 20%")

    return {
        "case_count": len(cases),
        "category_counts": dict(sorted(categories.items())),
        "negative_count": negative_count,
        "negative_ratio": round(negative_ratio, 4),
        "field_complete_count": len(cases) - sum("missing fields" in error for error in errors),
        "dual_reviewed_count": reviewed_by_two,
        "dual_review_ratio": round(review_ratio, 4),
        "conflicts": conflicts,
        "known_log_count": len(known_log_ids),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--strict-acceptance", action="store_true")
    args = parser.parse_args()
    result = validate(load_cases(args.dataset))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["errors"]:
        return 1
    if args.strict_acceptance and result["dual_review_ratio"] < 0.20:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
