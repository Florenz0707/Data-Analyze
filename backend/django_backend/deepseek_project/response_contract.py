"""Validated response contract for evidence-grounded log diagnosis.

The model output is untrusted data.  This module deliberately keeps parsing,
evidence validation, and Markdown rendering separate from the LLM adapter so
the same contract can be used by tests and offline evaluation.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CauseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: str = Field(min_length=1, max_length=300)
    confidence: Literal["high", "medium", "low"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)


class InvestigationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str = Field(min_length=1, max_length=400)
    expected: str = Field(default="", max_length=300)
    risk: str = Field(default="", max_length=200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(default="", max_length=300)


class StructuredAnswer(BaseModel):
    """The only model-generated shape accepted by the M5 response path."""

    model_config = ConfigDict(extra="forbid")

    diagnosis: list[str] = Field(default_factory=list, max_length=10)
    possible_causes: list[CauseItem] = Field(default_factory=list, max_length=10)
    investigation_steps: list[InvestigationStep] = Field(default_factory=list, max_length=10)
    mitigations: list[str] = Field(default_factory=list, max_length=10)
    final_fixes: list[str] = Field(default_factory=list, max_length=10)
    citations: list[EvidenceCitation] = Field(default_factory=list, max_length=20)
    confidence: Literal["high", "medium", "low"]
    confidence_reason: str = Field(min_length=1, max_length=400)
    need_more_information: bool
    follow_up_questions: list[str] = Field(default_factory=list, max_length=10)


def json_schema() -> dict[str, Any]:
    """Return a provider-neutral JSON Schema for prompt/native adapters."""
    return StructuredAnswer.model_json_schema()


def _json_candidate(raw: Any) -> Any:
    if isinstance(raw, StructuredAnswer):
        return raw.model_dump()
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # A bounded recovery for providers that add one sentence around JSON.
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def parse_diagnostics(exc: Exception) -> list[dict[str, str]]:
    """Return safe diagnostics without echoing user or model text."""
    if isinstance(exc, ValidationError):
        return [
            {
                "location": ".".join(str(part) for part in error.get("loc", ())),
                "type": str(error.get("type", "validation_error")),
            }
            for error in exc.errors(include_url=False)
        ][:20]
    return [{"location": "root", "type": type(exc).__name__}]


def validate_evidence_citations(
    answer: StructuredAnswer, evidence: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Validate every model citation against the current retrieval result."""
    valid_ids = {
        str(item.get("document_id")) for item in evidence if item.get("document_id") is not None
    }
    referenced = {citation.evidence_id for citation in answer.citations}
    for cause in answer.possible_causes:
        referenced.update(cause.evidence_ids)
    for step in answer.investigation_steps:
        referenced.update(step.evidence_ids)
    invalid = sorted(item for item in referenced if item not in valid_ids)
    if invalid:
        return [{"location": "citations", "type": "unknown_evidence_id"}]
    if answer.need_more_information and not answer.follow_up_questions:
        return [
            {"location": "follow_up_questions", "type": "required_when_more_information_needed"}
        ]
    return []


def parse_answer(
    raw: Any, evidence: list[dict[str, Any]]
) -> tuple[StructuredAnswer | None, list[dict[str, str]]]:
    """Parse and validate a model payload, including evidence IDs."""
    try:
        answer = StructuredAnswer.model_validate(_json_candidate(raw))
    except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return None, parse_diagnostics(exc)
    diagnostics = validate_evidence_citations(answer, evidence)
    return (answer if not diagnostics else None), diagnostics


def no_evidence_answer() -> StructuredAnswer:
    """Build a deterministic refusal for an empty or unavailable retrieval."""
    return StructuredAnswer(
        diagnosis=["当前检索结果不足以支持确定性归因。"],
        possible_causes=[],
        investigation_steps=[],
        mitigations=[],
        final_fixes=[],
        citations=[],
        confidence="low",
        confidence_reason="没有可核验的日志证据。",
        need_more_information=True,
        follow_up_questions=["请提供相关时间范围、服务名和完整错误日志。"],
    )


def _citation_suffix(ids: list[str]) -> str:
    return " " + " ".join(f"[{item}]" for item in ids) if ids else ""


def render_markdown(answer: StructuredAnswer, evidence: list[dict[str, Any]]) -> str:
    """Render only validated fields; the frontend can continue to render Markdown."""
    evidence_by_id = {str(item.get("document_id")): item for item in evidence}

    def items(values: list[str]) -> list[str]:
        return [f"{index}. {value}" for index, value in enumerate(values, start=1) if value]

    diagnosis_items = items(answer.diagnosis) or ["1. 暂无足够信息。"]
    lines = ["# 问题诊断", *diagnosis_items, ""]
    lines.extend(["# 可能原因（按概率降序排序）"])
    if answer.possible_causes:
        for index, cause in enumerate(answer.possible_causes, start=1):
            lines.append(
                f"{index}. [{cause.confidence}] {cause.cause}{_citation_suffix(cause.evidence_ids)}"
            )
    else:
        lines.append("1. 暂无可核验原因。")
    lines.append("")

    lines.append("# 建议的排查步骤")
    if answer.investigation_steps:
        for index, step in enumerate(answer.investigation_steps, start=1):
            detail = step.step
            if step.expected:
                detail += f"；预期：{step.expected}"
            if step.risk:
                detail += f"；风险：{step.risk}"
            lines.append(f"{index}. {detail}{_citation_suffix(step.evidence_ids)}")
    else:
        lines.append("1. 请先补充可核验日志。")
    lines.append("")

    for title, values in (
        ("临时缓解措施", answer.mitigations),
        ("最终修复建议", answer.final_fixes),
    ):
        lines.append(f"# {title}")
        lines.extend(items(values) or ["1. 暂无建议。"])
        lines.append("")

    lines.append("# 证据与置信度")
    lines.append(f"- 置信度：{answer.confidence}")
    lines.append(f"- 判断依据：{answer.confidence_reason}")
    if answer.citations:
        for citation in answer.citations:
            item = evidence_by_id.get(citation.evidence_id, {})
            metadata = item.get("metadata") or {}
            source = metadata.get("source_file") or "检索结果"
            row = metadata.get("source_row")
            location = f"，行 {row}" if row is not None else ""
            quote = f"：{citation.quote}" if citation.quote else ""
            lines.append(f"- [{citation.evidence_id}] {source}{location}{quote}")
    else:
        lines.append("- 未引用具体证据。")
    if answer.need_more_information:
        lines.append("- 需要更多信息：是")
        lines.extend(f"  - {question}" for question in answer.follow_up_questions)
    else:
        lines.append("- 需要更多信息：否")
    return "\n".join(lines).strip()
