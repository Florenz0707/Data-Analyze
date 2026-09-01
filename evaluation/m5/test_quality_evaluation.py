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

    def test_quality_matching_accepts_paraphrases_and_split_step_fields(self):
        from run_quality_evaluation import evaluate

        cases = [
            {
                "case_id": "P001",
                "is_negative": False,
                "relevant_log_ids": ["log-1"],
                "expected_causes": ["代码存在拼写、标点或语法结构错误"],
                "expected_steps": ["根据 traceback 定位首个语法错误行"],
            }
        ]
        payload = {
            "diagnosis": ["脚本解析失败"],
            "possible_causes": [
                {
                    "cause": "代码中存在无效语法，例如拼写错误、缺少标点或结构不正确",
                    "confidence": "high",
                    "evidence_ids": ["log-1"],
                }
            ],
            "investigation_steps": [
                {
                    "step": "使用编译器查看具体错误位置",
                    "expected": "定位到具体语法错误行",
                    "risk": "只读操作",
                    "evidence_ids": ["log-1"],
                }
            ],
            "mitigations": [],
            "final_fixes": [],
            "citations": [{"evidence_id": "log-1", "quote": "syntax error"}],
            "confidence": "high",
            "confidence_reason": "日志有明确错误信息",
            "need_more_information": False,
            "follow_up_questions": [],
        }

        report = evaluate(cases, [payload], evidence_ids=[["log-1"]])

        self.assertEqual(report["cause_match_rate"], 1.0)
        self.assertEqual(report["step_match_rate"], 1.0)
        self.assertEqual(
            report["records"][0]["review_excerpt"]["step_expected"], ["定位到具体语法错误行"]
        )

    def test_quality_matching_excludes_negative_cases_from_positive_rates(self):
        from run_quality_evaluation import evaluate

        cases = [
            {
                "case_id": "P001",
                "is_negative": False,
                "relevant_log_ids": ["log-1"],
                "expected_causes": ["代码存在语法错误"],
                "expected_steps": ["定位语法错误行"],
            },
            {
                "case_id": "W001",
                "is_negative": True,
                "relevant_log_ids": [],
                "expected_causes": ["当前知识库没有该事件的直接证据"],
                "expected_steps": ["收集更多事件信息"],
            },
        ]
        negative_payload = {
            "diagnosis": ["当前检索结果不足以支持确定性归因"],
            "possible_causes": [],
            "investigation_steps": [],
            "mitigations": [],
            "final_fixes": [],
            "citations": [],
            "confidence": "low",
            "confidence_reason": "没有可核验的日志证据",
            "need_more_information": True,
            "follow_up_questions": ["请提供更多日志"],
        }

        report = evaluate(
            cases,
            [negative_payload, negative_payload],
            evidence_ids=[[], []],
        )

        self.assertEqual(report["positive_count"], 1)
        self.assertEqual(report["cause_match_rate"], 0.0)
        self.assertEqual(report["step_match_rate"], 0.0)
