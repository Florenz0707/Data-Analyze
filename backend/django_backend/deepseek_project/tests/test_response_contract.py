from django.test import SimpleTestCase


class ResponseContractTests(SimpleTestCase):
    def setUp(self):
        from deepseek_project.response_contract import StructuredAnswer

        self.StructuredAnswer = StructuredAnswer
        self.evidence = [
            {
                "document_id": "log-1",
                "content": "database timeout",
                "metadata": {"source_file": "sample.csv", "source_row": 2},
            }
        ]

    def _valid_payload(self):
        return {
            "diagnosis": ["数据库请求超时。"],
            "possible_causes": [
                {"cause": "下游数据库响应慢", "confidence": "high", "evidence_ids": ["log-1"]}
            ],
            "investigation_steps": [
                {
                    "step": "检查数据库慢查询",
                    "expected": "确认是否存在超时语句",
                    "risk": "只读检查",
                    "evidence_ids": ["log-1"],
                }
            ],
            "mitigations": ["暂时降低请求并发。"],
            "final_fixes": ["优化慢查询并设置合理超时。"],
            "citations": [{"evidence_id": "log-1", "quote": "database timeout"}],
            "confidence": "high",
            "confidence_reason": "日志直接包含超时信息。",
            "need_more_information": False,
            "follow_up_questions": [],
        }

    def test_valid_answer_and_citations_render_without_untrusted_text(self):
        from deepseek_project.response_contract import parse_answer, render_markdown

        answer, diagnostics = parse_answer(self._valid_payload(), self.evidence)
        self.assertEqual(diagnostics, [])
        self.assertIsInstance(answer, self.StructuredAnswer)
        markdown = render_markdown(answer, self.evidence)
        self.assertIn("[log-1]", markdown)
        self.assertIn("sample.csv", markdown)

    def test_unknown_evidence_id_is_rejected(self):
        from deepseek_project.response_contract import parse_answer

        payload = self._valid_payload()
        payload["citations"] = [{"evidence_id": "not-retrieved", "quote": "x"}]
        answer, diagnostics = parse_answer(payload, self.evidence)
        self.assertIsNone(answer)
        self.assertEqual(diagnostics[0]["type"], "unknown_evidence_id")

    def test_no_evidence_answer_requires_follow_up(self):
        from deepseek_project.response_contract import no_evidence_answer, render_markdown

        answer = no_evidence_answer()
        self.assertTrue(answer.need_more_information)
        self.assertTrue(answer.follow_up_questions)
        self.assertIn("需要更多信息：是", render_markdown(answer, []))
