"""Fail with a machine-readable alert when the local pilot is unhealthy."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import URLError
from urllib.request import urlopen


def get_json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=3) as response:  # noqa: S310 - operator-configured local URL
        return json.load(response)


def main() -> None:
    base_url = os.environ.get("MAC_PILOT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    alerts: list[str] = []
    try:
        readiness = get_json(f"{base_url}/health/ready")
        metrics = get_json(f"{base_url}/metrics")
    except (OSError, URLError, ValueError) as error:
        print(json.dumps({"status": "alert", "alerts": [type(error).__name__]}))
        raise SystemExit(2) from error

    if readiness.get("status") != "ready":
        alerts.append("readiness_failed")
    requests = int(metrics.get("requests_total", 0))
    status_classes = metrics.get("status_classes", {})
    failures = int(status_classes.get("5xx", 0)) if isinstance(status_classes, dict) else 0
    if requests >= 10 and failures / requests > 0.05:
        alerts.append("http_5xx_ratio_above_5_percent")
    if int(metrics.get("duration_ms_max", 0)) > 10_000:
        alerts.append("request_duration_above_10_seconds")

    status = "alert" if alerts else "ok"
    print(json.dumps({"status": status, "alerts": alerts, "metrics": metrics}, sort_keys=True))
    if alerts:
        sys.exit(2)


if __name__ == "__main__":
    main()
