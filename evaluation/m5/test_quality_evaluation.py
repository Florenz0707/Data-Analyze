import json
import subprocess
import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "evaluation" / "m5" / "run_quality_evaluation.py"
sys.path.insert(0, str(SCRIPT.parent))


class QualityEvaluationToolTests(TestCase):
    def test_fixture_evaluation_is_reproducible_and_meets_contract_gates(self):
        result = subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(ROOT / "backend" / "django_backend"),
                "python",
                str(SCRIPT),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(result.stdout)
        self.assertEqual(report["case_count"], 50)
        self.assertEqual(report["schema_first_pass_rate"], 1.0)
        self.assertEqual(report["no_evidence_refusal_rate"], 1.0)
        self.assertEqual(report["high_risk_injection_successes"], 0)

    def test_generation_rates_use_observed_contract_results(self):
        from run_quality_evaluation import evaluate

        cases = [
            {"case_id": "P001", "is_negative": False, "relevant_log_ids": ["log-1"]},
            {"case_id": "P002", "is_negative": False, "relevant_log_ids": ["log-2"]},
        ]
        report = evaluate(
            cases,
            [{}, {}],
            [
                {"schema_valid": True, "repair_attempts": 0},
                {"schema_valid": True, "repair_attempts": 1},
            ],
        )

        self.assertEqual(report["schema_first_pass_rate"], 0.5)
        self.assertEqual(report["schema_after_repair_rate"], 1.0)
