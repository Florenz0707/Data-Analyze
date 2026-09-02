"""Small SSE helpers for the chat streaming endpoint."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


def encode_sse(event: str, payload: dict[str, Any]) -> str:
    """Encode one JSON SSE event with a stable event name and UTF-8 payload."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def iter_sse_lines(chunks: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Parse complete SSE frames, primarily for deterministic client tests."""
    buffer = ""
    for chunk in chunks:
        buffer += chunk
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            event = "message"
            data = ""
            for line in frame.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data += line[5:].lstrip()
            if data:
                yield {"event": event, "data": json.loads(data)}
