"""Open the local vocabulary app after its Flask server is ready."""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


APP_ID = "english-vocabulary"


def is_app_ready(base_url: str, timeout: float = 1.0) -> bool:
    """Return whether the expected vocabulary app answers on its health endpoint."""
    health_url = f"{base_url.rstrip('/')}/health"
    try:
        with urlopen(health_url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("app") == APP_ID
        and payload.get("status") == "ok"
    )


def wait_for_app(base_url: str, attempts: int = 40, interval: float = 0.25) -> bool:
    """Wait briefly for Flask to start without opening the browser too early."""
    for _ in range(attempts):
        if is_app_ready(base_url):
            return True
        time.sleep(interval)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        return 0 if is_app_ready(args.base_url) else 1

    if not wait_for_app(args.base_url):
        print("应用启动超时，请检查窗口中的错误信息。")
        return 1

    webbrowser.open(args.base_url, new=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
