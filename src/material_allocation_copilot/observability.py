"""Low-cardinality operational metrics and JSON logging for a controlled pilot."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from threading import Lock
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("request_id", "status_code", "duration_ms", "event"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_json_logging(level: str) -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


class OperationalMetrics:
    """Process-local operational counters; never stores request bodies or identifiers."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests = 0
        self._duration_ms_total = 0
        self._duration_ms_max = 0
        self._status_classes: Counter[str] = Counter()

    def observe(self, *, status_code: int, duration_ms: int) -> None:
        with self._lock:
            self._requests += 1
            self._duration_ms_total += duration_ms
            self._duration_ms_max = max(self._duration_ms_max, duration_ms)
            self._status_classes[f"{status_code // 100}xx"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            average = self._duration_ms_total / self._requests if self._requests else 0.0
            return {
                "requests_total": self._requests,
                "status_classes": dict(sorted(self._status_classes.items())),
                "duration_ms_average": round(average, 3),
                "duration_ms_max": self._duration_ms_max,
            }
