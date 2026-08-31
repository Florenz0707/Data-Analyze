"""Deterministic log cleaning and streaming document construction.

The input corpus contains several unrelated CSV schemas.  This module keeps
parsing independent from indexing so the cleaned records can be tested,
audited and reused by later index builders without loading all Documents in
memory.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

CLEANER_VERSION = "m4-cleaner-v1"
PARSER_VERSION = "m4-parser-v2"
CHUNKER_VERSION = "m4-chunker-v1"
DEFAULT_CHUNK_SIZE = 1200

_LEVELS = {
    "debug": "DEBUG",
    "info": "INFO",
    "information": "INFO",
    "informações": "INFO",
    "notice": "INFO",
    "warn": "WARNING",
    "warning": "WARNING",
    "error": "ERROR",
    "err": "ERROR",
    "erro": "ERROR",
    "fatal": "CRITICAL",
    "critical": "CRITICAL",
    "critico": "CRITICAL",
    "严重": "CRITICAL",
    "错误": "ERROR",
    "警告": "WARNING",
    "aviso": "WARNING",
    "信息": "INFO",
    "informacao": "INFO",
    "informação": "INFO",
}

_FIELD_ALIASES = {
    "service": {"service", "服务", "source", "来源", "application", "应用"},
    "level": {"level", "级别", "entrytype", "entry_type", "日志级别"},
    "error_code": {
        "error",
        "错误",
        "errorcode",
        "error_code",
        "exception",
        "事件标识",
        "eventid",
        "event_id",
    },
    "message": {
        "message",
        "消息",
        "description",
        "描述",
        "描述1",
        "description1",
        "logmessage",
    },
    "component": {"component", "组件", "eventsource", "事件来源名称", "source_name"},
    "cause": {"cause", "原因", "rootcause", "root_cause"},
    "timestamp": {
        "timestamp",
        "time",
        "datetime",
        "date_time",
        "日期和时间",
        "timegenerated",
        "time_generated",
    },
    "language": {"language", "语言", "技术栈", "tech_stack"},
}

_PII_FIELD_MARKERS = (
    "email",
    "mail",
    "phone",
    "mobile",
    "telephone",
    "address",
    "city",
    "zip",
    "country",
    "region",
    "isp",
    "身份证",
    "邮箱",
    "手机号",
    "电话",
    "地址",
    "城市",
    "邮编",
    "用户",
    "username",
    "account",
)
_SECRET_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "authorization",
    "access_token",
    "refresh_token",
    "private_key",
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:api[_ -]?key|secret|password|passwd|authorization|bearer|"
    r"access[_ -]?token|refresh[_ -]?token)\b\s*[:=]\s*\S+"
)
_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b"
)
_QUARANTINE_REASONS = {"secret_field", "secret_value", "email", "pii_value"}


def _normalize_key(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]", "", value.casefold())


def _clean_text(value: Any, *, preserve_newlines: bool = False) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).replace("\x00", "")
    text = "".join(" " if ord(char) < 32 and char not in "\n\r\t" else char for char in text)
    if preserve_newlines:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_level(value: str) -> str:
    cleaned = _clean_text(value).casefold()
    return _LEVELS.get(cleaned, cleaned.upper() if cleaned else "UNKNOWN")


def _normalize_timestamp(value: str) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    candidates = (
        "%m/%d/%Y %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    for fmt in candidates:
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return text


def _infer_language(source_file: str, service: str, value: str) -> str:
    text = f"{source_file} {service} {value}".casefold()
    for marker, language in (
        ("python", "python"),
        ("java", "java"),
        ("linux", "linux"),
        ("bash", "shell"),
        ("windows", "windows"),
        ("computer_events", "windows"),
    ):
        if marker in text:
            return language
    return "generic"


def _field_map(row: dict[str, Any]) -> dict[str, str]:
    return {
        _normalize_key(str(key)): _clean_text(value)
        for key, value in row.items()
        if key is not None
    }


def _get_field(row: dict[str, str], field: str) -> str:
    aliases = {_normalize_key(alias) for alias in _FIELD_ALIASES[field]}
    for key, value in row.items():
        if key in aliases and value:
            return value
    return ""


def _sensitive_reasons(row: dict[str, str]) -> tuple[str, ...]:
    reasons: set[str] = set()
    for key, value in row.items():
        if not value:
            continue
        if any(marker in key for marker in _SECRET_FIELD_MARKERS):
            reasons.add("secret_field")
        if any(marker in key for marker in _PII_FIELD_MARKERS):
            reasons.add("pii_field")
        if _EMAIL_RE.search(value):
            reasons.add("email")
        if _SECRET_VALUE_RE.search(value) or _TOKEN_RE.search(value):
            reasons.add("secret_value")
        if re.search(r"(?:用户\s*ID|身份证|手机号|电话)\s*[:：=]", value, re.I):
            reasons.add("pii_value")
    return tuple(sorted(reasons))


def _safe_metadata(row: dict[str, str], canonical_keys: set[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    reserved_keys = {
        "document_id",
        "source_file",
        "source_row",
        "service",
        "level",
        "error_code",
        "component",
        "timestamp",
        "language",
        "parser_version",
        "cleaner_version",
        "chunk_id",
        "chunk_index",
        "chunk_count",
    }
    for key, value in row.items():
        if not value or key in canonical_keys or key in reserved_keys:
            continue
        if any(marker in key for marker in (*_SECRET_FIELD_MARKERS, *_PII_FIELD_MARKERS)):
            continue
        metadata[key] = value[:512]
    return dict(sorted(metadata.items()))


@dataclass(frozen=True)
class CanonicalLogRecord:
    """One safe, normalized source record used as the document boundary."""

    document_id: str
    source_file: str
    source_row: int
    service: str
    level: str
    error_code: str
    message: str
    component: str
    cause: str
    timestamp: str | None
    language: str
    metadata: dict[str, str] = field(default_factory=dict)
    dedupe_mode: str = "strict"

    @property
    def dedupe_key(self) -> str:
        payload = {
            "service": self.service,
            "level": self.level,
            "message": self.message,
            "language": self.language,
        }
        if self.dedupe_mode == "strict":
            payload.update(
                {
                    "error_code": self.error_code,
                    "component": self.component,
                    "cause": self.cause,
                    "timestamp": self.timestamp,
                    "metadata": self.metadata,
                }
            )
        # Reduced Computer Events exports omit event IDs, components and
        # timestamps. Their shared service/level/message identity is enough
        # to merge the full and reduced representation without applying this
        # relaxed rule to unrelated sources.
        return _stable_hash(payload)

    def to_metadata(self, *, chunk_id: str | None = None, chunk_index: int = 0) -> dict[str, Any]:
        result: dict[str, Any] = {
            "document_id": self.document_id,
            "source_file": self.source_file,
            "source_row": self.source_row,
            "service": self.service,
            "level": self.level,
            "error_code": self.error_code,
            "component": self.component,
            "timestamp": self.timestamp or "",
            "language": self.language,
            "parser_version": PARSER_VERSION,
            "cleaner_version": CLEANER_VERSION,
            **self.metadata,
        }
        if chunk_id is not None:
            result.update({"chunk_id": chunk_id, "chunk_index": chunk_index})
        return result


@dataclass(frozen=True)
class DocumentChunk:
    """A bounded document payload; one record can produce multiple chunks."""

    document_id: str
    chunk_id: str
    chunk_index: int
    text: str
    metadata: dict[str, Any]

    def to_llama_document(self):
        from llama_index.core import Document

        return Document(id_=self.chunk_id, text=self.text, metadata=self.metadata)


@dataclass
class CleaningResult:
    records: list[CanonicalLogRecord]
    quarantined: list[dict[str, Any]]
    quality_report: dict[str, Any]


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _record_from_row(row: dict[str, str], source_file: str, source_row: int) -> CanonicalLogRecord:
    is_bug_pair = "buggy_code" in row or "fixed_code" in row
    is_computer_event = "描述1" in row or "description1" in row or "事件标识" in row
    message = _get_field(row, "message")
    cause = _get_field(row, "cause")
    if is_bug_pair:
        buggy = _clean_text(row.get("buggy_code", ""), preserve_newlines=True)
        fixed = _clean_text(row.get("fixed_code", ""), preserve_newlines=True)
        commit = _clean_text(row.get("commit_message", ""))
        message = "\n".join(part for part in (buggy, f"修复后代码:\n{fixed}") if part)
        cause = commit

    service = _get_field(row, "service") or "unknown"
    level = _normalize_level(_get_field(row, "level"))
    error_code = _get_field(row, "error_code")
    component = _get_field(row, "component")
    timestamp = _normalize_timestamp(_get_field(row, "timestamp"))
    language = _get_field(row, "language") or _infer_language(source_file, service, message)
    canonical_keys = set()
    for field_name in _FIELD_ALIASES:
        canonical_keys.update(_normalize_key(alias) for alias in _FIELD_ALIASES[field_name])
    canonical_keys.update({"buggy_code", "fixed_code", "commit_message"})
    identity = {
        "service": service,
        "level": level,
        "error_code": error_code,
        "message": message,
        "component": component,
        "cause": cause,
        "timestamp": timestamp,
        "language": language,
    }
    return CanonicalLogRecord(
        document_id=f"log-{_stable_hash(identity)[:32]}",
        source_file=source_file,
        source_row=source_row,
        service=service,
        level=level,
        error_code=error_code,
        message=message or "(empty message)",
        component=component,
        cause=cause,
        timestamp=timestamp,
        language=language,
        metadata=_safe_metadata(row, canonical_keys),
        dedupe_mode="computer_event" if is_computer_event else "strict",
    )


def discover_csv_files(data_path: str | Path) -> list[Path]:
    """Return CSV inputs in deterministic relative-path order."""
    path = Path(data_path)
    if path.is_file():
        return [path] if path.suffix.casefold() == ".csv" else []
    if not path.is_dir():
        return []
    return sorted(
        (item for item in path.rglob("*.csv") if item.is_file()), key=lambda item: item.as_posix()
    )


def _read_csv_rows(path: Path) -> Iterator[dict[str, Any]]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    return
                yield from reader
            return
        except UnicodeDecodeError:
            continue


def _relative_source(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def clean_data_sources(data_path: str | Path) -> CleaningResult:
    """Parse, normalize, quarantine and deduplicate CSV records deterministically."""
    root = Path(data_path)
    files = discover_csv_files(root)
    if root.is_file():
        root = root.parent
    winners: dict[str, CanonicalLogRecord] = {}
    quarantined: list[dict[str, Any]] = []
    rows_read = empty_rows = duplicate_rows = redacted_rows = 0
    by_source: dict[str, dict[str, int]] = {}

    for path in files:
        source_file = _relative_source(path, root)
        source_stats = by_source.setdefault(
            source_file,
            {"rows_read": 0, "empty_rows": 0, "quarantined": 0, "duplicates": 0, "accepted": 0},
        )
        # source_row follows the evaluation contract: the first data row is 1;
        # the CSV header is not part of the source record numbering.
        for source_row, raw_row in enumerate(_read_csv_rows(path), start=1):
            rows_read += 1
            source_stats["rows_read"] += 1
            row = _field_map(raw_row)
            if not any(row.values()):
                empty_rows += 1
                source_stats["empty_rows"] += 1
                continue
            reasons = _sensitive_reasons(row)
            if set(reasons) & _QUARANTINE_REASONS:
                quarantined.append(
                    {"source_file": source_file, "source_row": source_row, "reasons": reasons}
                )
                source_stats["quarantined"] += 1
                continue
            if "pii_field" in reasons:
                redacted_rows += 1
            record = _record_from_row(row, source_file, source_row)
            if record.dedupe_key in winners:
                duplicate_rows += 1
                source_stats["duplicates"] += 1
                continue
            winners[record.dedupe_key] = record
            source_stats["accepted"] += 1

    records = sorted(
        winners.values(),
        key=lambda record: (record.document_id, record.source_file, record.source_row),
    )
    report = {
        "cleaner_version": CLEANER_VERSION,
        "parser_version": PARSER_VERSION,
        "files_discovered": len(files),
        "rows_read": rows_read,
        "empty_rows": empty_rows,
        "accepted_records": len(records),
        "duplicate_rows": duplicate_rows,
        "quarantined_records": len(quarantined),
        "redacted_rows": redacted_rows,
        "duplicate_rate": duplicate_rows / rows_read if rows_read else 0.0,
        "post_clean_duplicate_rate": 0.0,
        "quarantine_rate": len(quarantined) / rows_read if rows_read else 0.0,
        "required_field_pass_rate": sum(bool(record.message) for record in records) / len(records)
        if records
        else 1.0,
        "quarantine_reasons": {
            reason: sum(reason in item["reasons"] for item in quarantined)
            for reason in sorted({reason for item in quarantined for reason in item["reasons"]})
        },
        "by_source": by_source,
    }
    return CleaningResult(records=records, quarantined=quarantined, quality_report=report)


def _split_for_chunks(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    units = [unit.strip() for unit in re.split(r"(?<=[。！？.!?])\s+|\n+", text) if unit.strip()]
    if not units:
        units = [text]
    chunks: list[str] = []
    current = ""
    for unit in units:
        while len(unit) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(unit[:max_chars])
            unit = unit[max_chars:]
        candidate = f"{current} {unit}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _record_text(record: CanonicalLogRecord) -> str:
    fields = [
        f"服务: {record.service}",
        f"级别: {record.level}",
        f"错误码: {record.error_code or '未提供'}",
        f"组件: {record.component or '未提供'}",
        f"时间: {record.timestamp or '未提供'}",
        f"语言: {record.language}",
        f"日志消息: {record.message}",
        f"已知原因: {record.cause or '未提供'}",
    ]
    if record.metadata:
        fields.append(
            "其他字段: " + "; ".join(f"{key}={value}" for key, value in record.metadata.items())
        )
    fields.append(f"来源: {record.source_file}#row-{record.source_row}")
    return "\n".join(fields)


def iter_document_chunks(
    records: Iterable[CanonicalLogRecord], *, max_chars: int = DEFAULT_CHUNK_SIZE
) -> Iterator[DocumentChunk]:
    """Yield bounded, metadata-rich chunks without materializing the corpus."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    for record in records:
        fragments = _split_for_chunks(_record_text(record), max_chars)
        total = len(fragments)
        for index, fragment in enumerate(fragments):
            chunk_id = (
                record.document_id if total == 1 else f"{record.document_id}-chunk-{index:04d}"
            )
            metadata = record.to_metadata(chunk_id=chunk_id, chunk_index=index)
            metadata.update({"chunk_count": total, "chunker_version": CHUNKER_VERSION})
            yield DocumentChunk(
                document_id=record.document_id,
                chunk_id=chunk_id,
                chunk_index=index,
                text=fragment,
                metadata=metadata,
            )


def build_document_manifest(
    records: Iterable[CanonicalLogRecord], *, max_chars: int = DEFAULT_CHUNK_SIZE
) -> dict[str, dict[str, Any]]:
    """Create a compact manifest used to detect document updates and deletes."""
    manifest: dict[str, dict[str, Any]] = {}
    for chunk in iter_document_chunks(records, max_chars=max_chars):
        manifest[chunk.chunk_id] = {
            "document_id": chunk.document_id,
            "content_hash": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
            "metadata": chunk.metadata,
        }
    return dict(sorted(manifest.items()))


def diff_document_manifests(
    previous: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    """Return stable upsert/delete IDs for an incremental document update."""
    upsert = sorted(
        chunk_id
        for chunk_id, item in current.items()
        if chunk_id not in previous
        or previous[chunk_id].get("content_hash") != item.get("content_hash")
    )
    delete = sorted(set(previous) - set(current))
    return {"upsert": upsert, "delete": delete}


def iter_llama_documents(
    data_path: str | Path, *, max_chars: int = DEFAULT_CHUNK_SIZE
) -> Iterator[Any]:
    """Clean inputs and stream LlamaIndex Documents one chunk at a time."""
    result = clean_data_sources(data_path)
    for chunk in iter_document_chunks(result.records, max_chars=max_chars):
        yield chunk.to_llama_document()
