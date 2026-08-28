#!/usr/bin/env python3
"""Measure a separate Django process from launch to HTTP readiness."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "django_backend"
DEFAULT_OUTPUT = Path(__file__).with_name("evidence") / "service_startup_baseline.json"


def rss_kib(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError):
        pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    endpoint = f"http://{args.host}:{args.port}/api/openapi.json"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    environment = os.environ.copy()
    environment["DJANGO_PORT"] = str(args.port)
    # AppConfig intentionally preloads only in the reloader child while DEBUG is
    # enabled. This benchmark has no reloader, so emulate that child explicitly.
    environment["RUN_MAIN"] = "true"
    started = time.perf_counter()
    peak_rss = 0
    ready_seconds = None
    return_code = None
    with tempfile.NamedTemporaryFile(prefix="data-analyze-m0-startup-", mode="w+") as log:
        process = subprocess.Popen(
            [
                str(BACKEND / ".venv" / "bin" / "python"),
                "manage.py",
                "runserver",
                f"{args.host}:{args.port}",
                "--noreload",
            ],
            cwd=BACKEND,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            while time.perf_counter() - started < args.timeout:
                peak_rss = max(peak_rss, rss_kib(process.pid))
                if process.poll() is not None:
                    return_code = process.returncode
                    break
                try:
                    with opener.open(endpoint, timeout=1) as response:
                        if response.status == 200:
                            response.read()
                            ready_seconds = time.perf_counter() - started
                            break
                except Exception:
                    pass
                time.sleep(0.1)
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            return_code = process.returncode
        if ready_seconds is None:
            log.seek(0)
            tail = log.read()[-4000:]
            raise RuntimeError(
                f"service did not become ready; return_code={return_code}; log_tail={tail}"
            )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "endpoint": endpoint,
        "ready_seconds": ready_seconds,
        "peak_process_rss_kib": peak_rss,
        "exit_code_after_sigterm": return_code,
        "conditions": "separate --noreload Django process with RUN_MAIN=true to exercise AppConfig preload; existing Ollama daemon and model cache were not stopped",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
