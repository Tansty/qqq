#!/usr/bin/env python3
"""Small local web server for the QQQ advisor dashboard."""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from qqq_advisor import (
    DEFAULT_FUNDS,
    evaluate_advice_history,
    generate_report,
    append_actual_trade,
    load_advice_history,
    load_actual_trades,
    load_config,
    load_manual_orders,
    save_json,
)


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
CONFIG_PATH = Path(os.environ.get("QQQ_ADVISOR_CONFIG", str(ROOT / "config.json"))).expanduser()
DATA_DIR = Path(os.environ.get("QQQ_ADVISOR_DATA_DIR", "data")).expanduser()
AUTH_USERNAME = os.environ.get("QQQ_ADVISOR_USERNAME", "advisor")
AUTH_PASSWORD = os.environ.get("QQQ_ADVISOR_PASSWORD")


def default_config() -> dict[str, Any]:
    return {
        "profile": {
            "total_investable_cny": 100000,
            "risk_level": "balanced",
            "horizon_years": 3,
            "max_acceptable_loss_pct": 20,
        },
        "data_dir": str(DATA_DIR),
        "portfolio": {
            "current_nasdaq_position_cny": 0,
            "nasdaq_fund_holdings": [],
        },
        "rules": {
            "min_trade_cny": 10,
            "alipay_only": True,
        },
        "funds": DEFAULT_FUNDS,
    }


def ensure_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        save_json(CONFIG_PATH, default_config())
    config = load_config(CONFIG_PATH)
    if "data_dir" not in config:
        config["data_dir"] = str(DATA_DIR)
        save_json(CONFIG_PATH, config)
    return config


def merge_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = ensure_config()
    profile = payload.get("profile", {})
    portfolio = payload.get("portfolio", {})
    rules = payload.get("rules", {})

    config.setdefault("profile", {})
    config.setdefault("portfolio", {})
    config.setdefault("rules", {})
    config.setdefault("funds", DEFAULT_FUNDS)
    config.setdefault("data_dir", str(DATA_DIR))

    for key in ("total_investable_cny", "risk_level", "horizon_years", "max_acceptable_loss_pct"):
        if key in profile:
            config["profile"][key] = profile[key]
    if "current_nasdaq_position_cny" in portfolio:
        config["portfolio"]["current_nasdaq_position_cny"] = portfolio["current_nasdaq_position_cny"]
    if "nasdaq_fund_holdings" in portfolio and isinstance(portfolio["nasdaq_fund_holdings"], list):
        config["portfolio"]["nasdaq_fund_holdings"] = portfolio["nasdaq_fund_holdings"]
    if "min_trade_cny" in rules:
        config["rules"]["min_trade_cny"] = rules["min_trade_cny"]
    save_json(CONFIG_PATH, config)
    return config


class Handler(BaseHTTPRequestHandler):
    server_version = "QQQAdvisor/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"error": message}, status=status)

    def is_authorized(self) -> bool:
        if not AUTH_PASSWORD:
            return True
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth.removeprefix("Basic ").strip()).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        username, sep, password = decoded.partition(":")
        return bool(sep) and hmac.compare_digest(username, AUTH_USERNAME) and hmac.compare_digest(password, AUTH_PASSWORD)

    def require_authorization(self) -> bool:
        if self.is_authorized():
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="QQQ Advisor"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Unauthorized".encode("utf-8"))
        return False

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def do_HEAD(self) -> None:  # noqa: N802
        if not self.require_authorization():
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self.require_authorization():
            return
        if self.path == "/api/config":
            self.send_json(ensure_config())
            return
        if self.path.startswith("/api/latest-report"):
            config = ensure_config()
            report_path = max(Path(config.get("data_dir", str(DATA_DIR))).glob("report-*.json"), default=None)
            if report_path is None:
                self.send_json({"report": None})
                return
            with report_path.open("r", encoding="utf-8") as f:
                self.send_json({"report": json.load(f)})
            return
        if self.path.startswith("/api/history"):
            self.send_json({"items": load_advice_history(ensure_config(), limit=30)})
            return
        if self.path.startswith("/api/evaluation"):
            self.send_json(evaluate_advice_history(ensure_config()))
            return
        if self.path.startswith("/api/manual-orders"):
            self.send_json({"items": load_manual_orders(ensure_config(), limit=30)})
            return
        if self.path.startswith("/api/actual-trades"):
            self.send_json({"items": load_actual_trades(ensure_config(), limit=50)})
            return

        path = "/index.html" if self.path in {"/", ""} else self.path
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists() or target.is_dir():
            self.send_error_json("Not found", status=HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if not self.require_authorization():
            return
        try:
            if self.path == "/api/config":
                config = merge_config(self.read_json())
                self.send_json(config)
                return
            if self.path == "/api/recommendation":
                config = merge_config(self.read_json())
                report = generate_report(config, persist=True)
                self.send_json(report)
                return
            if self.path == "/api/actual-trades":
                config = ensure_config()
                trade = append_actual_trade(config, self.read_json())
                self.send_json(trade)
                return
            self.send_error_json("Not found", status=HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self.send_error_json(str(exc), status=HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run QQQ advisor web dashboard")
    parser.add_argument("--host", default=os.environ.get("QQQ_ADVISOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=int(os.environ.get("PORT", os.environ.get("QQQ_ADVISOR_PORT", "8765"))), type=int)
    args = parser.parse_args()

    ensure_config()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"QQQ advisor dashboard: http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
