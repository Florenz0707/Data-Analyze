#!/usr/bin/env python3
"""Collect a redacted, reproducible M0 environment and data manifest."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "django_backend"
DEFAULT_OUTPUT = Path(__file__).with_name("evidence") / "environment_manifest.json"


def command(*args: str) -> str | None:
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_record(path: Path) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = sum(1 for _ in csv.DictReader(handle))
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "rows": rows,
        "sha256": sha256(path),
    }


def memory_record() -> dict:
    values = {}
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text().splitlines():
            key, value = line.split(":", 1)
            if key in {"MemTotal", "SwapTotal"}:
                values[key] = value.strip()
    return values


def cpu_model() -> str | None:
    cpuinfo = Path("/proc/cpuinfo")
    if not cpuinfo.exists():
        return None
    for line in cpuinfo.read_text(errors="replace").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return None


def ollama_record(base_url: str) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read(path: str) -> dict:
        with opener.open(base_url.rstrip("/") + path, timeout=10) as response:
            return json.load(response)

    try:
        version = read("/api/version").get("version")
        models = read("/api/tags").get("models") or []
        return {
            "version": version,
            "models": [
                {
                    "name": model.get("name"),
                    "digest": model.get("digest"),
                    "bytes": model.get("size"),
                    "parameter_size": (model.get("details") or {}).get("parameter_size"),
                    "quantization": (model.get("details") or {}).get("quantization_level"),
                }
                for model in models
            ],
        }
    except Exception as exc:
        return {"error": type(exc).__name__}


def chroma_record(path: Path) -> dict:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as database:
        collections = [
            {"id": row[0], "name": row[1], "dimension": row[2]}
            for row in database.execute("SELECT id, name, dimension FROM collections ORDER BY name")
        ]
        embedding_count = database.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        logical_digest = hashlib.sha256()
        for (document,) in database.execute(
            "SELECT string_value FROM embedding_metadata "
            "WHERE key = 'chroma:document' ORDER BY string_value"
        ):
            logical_digest.update((document or "").encode())
            logical_digest.update(b"\0")
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "logical_document_sha256": logical_digest.hexdigest(),
        "collections": collections,
        "embedding_count": embedding_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    config_path = BACKEND / "config" / "llm_config.yaml"
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    ollama_config = config.get("OLLAMA_CONFIG") or {}
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": command("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        "ports": {"backend": 8081, "frontend": 8082, "ollama": 11434},
        "host": {
            "platform": platform.platform(),
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "cpu_model": cpu_model(),
            "logical_cpu_count": os.cpu_count(),
            "memory": memory_record(),
            "gpu": command(
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            )
            or "not detected in WSL",
        },
        "toolchain": {
            "python": command(str(BACKEND / ".venv" / "bin" / "python"), "--version"),
            "node": command("node", "--version"),
            "npm": command("npm", "--version"),
            "uv": command("uv", "--version"),
            "pre_commit": command("pre-commit", "--version"),
        },
        "configuration": {
            "path": str(config_path.relative_to(ROOT)),
            "sha256": sha256(config_path),
            "llm_provider": config.get("LLM_PROVIDER"),
            "embedding_provider": config.get("EMBEDDING_PROVIDER"),
            "llm_model": ollama_config.get("model"),
            "embedding_model": ollama_config.get("embedding_model"),
            "response_top_k": config.get("RESPONSE_TOP_K"),
            "generation_retries": config.get("LLM_GENERATION_RETRIES"),
            "min_output_chars": config.get("LLM_MIN_OUTPUT_CHARS"),
            "max_parts": config.get("LLM_MAX_PARTS_NUM"),
            "max_part_length": config.get("LLM_MAX_PART_LENGTH"),
        },
        "ollama": ollama_record(args.ollama_url),
        "log_files": [
            csv_record(path) for path in sorted((BACKEND / "data" / "log").glob("*.csv"))
        ],
        "vector_store": chroma_record(BACKEND / "data" / "vector_stores" / "chroma.sqlite3"),
    }
    report["log_row_total"] = sum(item["rows"] for item in report["log_files"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "log_rows": report["log_row_total"],
                "vector_count": report["vector_store"]["embedding_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
