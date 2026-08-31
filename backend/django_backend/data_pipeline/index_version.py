"""Version identity and atomic state management for the log index."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .log_documents import CHUNKER_VERSION, PARSER_VERSION, discover_csv_files

INDEX_SCHEMA_VERSION = "m4-index-state-v1"


def compute_data_content_hash(data_path: str | Path) -> str:
    """Hash sorted source paths and bytes without retaining a corpus in memory."""
    root = Path(data_path)
    files = discover_csv_files(root)
    if root.is_file():
        root = root.parent
    digest = hashlib.sha256()
    for path in files:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _stable_hash(payload: dict[str, Any]) -> str:
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IndexSpec:
    """All inputs that must change the identity of a vector index."""

    logical_version: str
    data_content_hash: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int | None
    embedding_parameters: dict[str, Any]
    parser_version: str
    chunker_version: str
    chunk_size: int
    retrieval_parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def version(self) -> str:
        payload = asdict(self)
        return f"idx-{_stable_hash(payload)[:20]}"

    @property
    def payload(self) -> dict[str, Any]:
        return {**asdict(self), "version": self.version}

    def collection_name(self, base_name: str) -> str:
        safe_base = "".join(char if char.isalnum() or char in "_-" else "_" for char in base_name)
        return f"{safe_base[:38]}__{self.version}"[:63]


def build_index_spec(
    data_path: str | Path,
    *,
    logical_version: str,
    embedding_provider: str,
    embedding_model: str,
    embedding_dimensions: int | None,
    embedding_parameters: dict[str, Any] | None = None,
    chunk_size: int = 1200,
    retrieval_parameters: dict[str, Any] | None = None,
) -> IndexSpec:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return IndexSpec(
        logical_version=str(logical_version),
        data_content_hash=compute_data_content_hash(data_path),
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
        embedding_parameters=dict(embedding_parameters or {}),
        parser_version=PARSER_VERSION,
        chunker_version=CHUNKER_VERSION,
        chunk_size=chunk_size,
        retrieval_parameters=dict(retrieval_parameters or {}),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


class IndexStateStore:
    """Persist index build state and current pointer with an atomic file replace."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": INDEX_SCHEMA_VERSION, "current_version": None, "versions": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": INDEX_SCHEMA_VERSION, "current_version": None, "versions": {}}
        if not isinstance(value, dict) or value.get("schema_version") != INDEX_SCHEMA_VERSION:
            return {"schema_version": INDEX_SCHEMA_VERSION, "current_version": None, "versions": {}}
        value.setdefault("versions", {})
        return value

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def mark_building(self, spec: IndexSpec, collection_name: str) -> None:
        state = self.load()
        state["versions"][spec.version] = {
            "status": "building",
            "collection_name": collection_name,
            "spec": spec.payload,
            "started_at": _now(),
        }
        self._write(state)

    def mark_ready(self, spec: IndexSpec, collection_name: str, document_count: int) -> None:
        if document_count < 0:
            raise ValueError("document_count must be non-negative")
        state = self.load()
        state["versions"][spec.version] = {
            "status": "ready",
            "collection_name": collection_name,
            "spec": spec.payload,
            "document_count": document_count,
            "ready_at": _now(),
        }
        state["current_version"] = spec.version
        self._write(state)

    def mark_failed(self, spec: IndexSpec, collection_name: str, reason: str) -> None:
        state = self.load()
        state["versions"][spec.version] = {
            "status": "failed",
            "collection_name": collection_name,
            "spec": spec.payload,
            "failed_at": _now(),
            "reason": reason[:200],
        }
        self._write(state)


def cleanup_old_index_collections(
    client: Any,
    *,
    base_name: str,
    state: dict[str, Any],
    keep_versions: int = 2,
) -> list[str]:
    """Delete only old versioned collections, never the current or legacy collection."""
    if keep_versions < 1:
        raise ValueError("keep_versions must be positive")
    versions = state.get("versions") or {}
    ready = [
        (version, value)
        for version, value in versions.items()
        if isinstance(value, dict) and value.get("status") == "ready"
    ]
    ready.sort(key=lambda item: item[1].get("ready_at", ""), reverse=True)
    keep = {version for version, _ in ready[:keep_versions]}
    current = state.get("current_version")
    if current:
        keep.add(current)
    removed: list[str] = []
    for version, value in ready:
        if version in keep:
            continue
        collection_name = value.get("collection_name")
        if collection_name and collection_name.startswith(f"{base_name}__"):
            client.delete_collection(collection_name)
            removed.append(collection_name)
    return removed
