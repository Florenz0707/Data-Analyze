import json
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase


class TopKGenerationContractTests(SimpleTestCase):
    def _system(self):
        from topklogsystem import TopKLogSystem

        system = TopKLogSystem.__new__(TopKLogSystem)
        system.system_prompt = "Role: log analyst\nLog: {log_context}\nQuery: {query}"
        system.response_template = "legacy template"
        system.max_parts_num = 3
        system.max_part_length = 70
        system.prompt_version = "test-m5"
        system.structured_repair_retries = 1
        system.generation_retries = 0
        system.min_output_chars = 1
        system.sanitizer_fallback_count = 0
        return system

    @staticmethod
    def _payload(evidence_id="log-1"):
        return {
            "diagnosis": ["数据库超时。"],
            "possible_causes": [
                {"cause": "下游响应慢", "confidence": "high", "evidence_ids": [evidence_id]}
            ],
            "investigation_steps": [
                {
                    "step": "检查慢查询",
                    "expected": "找到超时语句",
                    "risk": "只读",
                    "evidence_ids": [evidence_id],
                }
            ],
            "mitigations": [],
            "final_fixes": [],
            "citations": [{"evidence_id": evidence_id, "quote": "timeout"}],
            "confidence": "high",
            "confidence_reason": "证据明确。",
            "need_more_information": False,
            "follow_up_questions": [],
        }

    def test_structured_json_is_rendered_and_prompt_marks_logs_untrusted(self):
        system = self._system()
        llm = Mock()
        llm.with_structured_output = None
        llm.complete.return_value = SimpleNamespace(text=json.dumps(self._payload()))
        evidence = [{"document_id": "log-1", "content": "timeout", "metadata": {}}]

        result = system.generate_response("why", evidence, llm=llm)

        self.assertIn("# 问题诊断", result)
        self.assertIn("[log-1]", result)
        self.assertEqual(system.last_generation_result["output_mode"], "structured")
        prompt = llm.complete.call_args.args[0]
        self.assertIn("<untrusted_evidence>", prompt)
        self.assertEqual(prompt.count("<untrusted_evidence>"), 1)
        self.assertIn("只返回一个 JSON 对象", prompt)

    def test_invalid_json_is_repaired_once(self):
        system = self._system()
        llm = Mock()
        llm.with_structured_output = None
        llm.complete.side_effect = [
            SimpleNamespace(text="not json"),
            SimpleNamespace(text=json.dumps(self._payload())),
        ]
        evidence = [{"document_id": "log-1", "content": "timeout", "metadata": {}}]

        system.generate_response("why", evidence, llm=llm)

        self.assertEqual(llm.complete.call_count, 2)
        self.assertEqual(system.last_generation_result["repair_attempts"], 1)
        self.assertEqual(system.last_generation_result["output_mode"], "structured")

    def test_native_structured_adapter_is_preferred(self):
        system = self._system()

        class NativeLLM:
            def __init__(self):
                self.complete_called = False

            def with_structured_output(self, _schema):
                return self

            def invoke(self, _prompt):
                return self_payload

            def complete(self, _prompt):
                self.complete_called = True
                return "{}"

        self_payload = self._payload()
        llm = NativeLLM()
        evidence = [{"document_id": "log-1", "content": "timeout", "metadata": {}}]

        result = system.generate_response("why", evidence, llm=llm)

        self.assertIn("[log-1]", result)
        self.assertTrue(system.last_generation_result["native_structured"])
        self.assertFalse(llm.complete_called)

    def test_empty_context_refuses_without_calling_model(self):
        system = self._system()
        llm = Mock()

        result = system.generate_response("unknown", [], llm=llm)

        llm.complete.assert_not_called()
        self.assertEqual(system.last_generation_result["output_mode"], "no_evidence")
        self.assertIn("当前检索结果不足", result)
