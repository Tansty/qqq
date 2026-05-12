#!/usr/bin/env python3
"""
QQQ/Nasdaq-100 allocation assistant for China fund investors.

This tool fetches market data, scores entry timing, and produces a staged
buying plan for QDII Nasdaq-100 mutual funds commonly available in China.
It is a decision aid, not financial advice.
"""

from __future__ import annotations

import argparse
import codecs
import csv
import json
import math
import os
import re
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_FUNDS = [
    {
        "code": "270042",
        "name": "广发纳斯达克100ETF联接人民币(QDII)A",
        "style": "long_term",
        "preferred": True,
    },
    {
        "code": "006479",
        "name": "广发纳斯达克100ETF联接人民币(QDII)C",
        "style": "short_to_mid_term",
        "preferred": True,
    },
    {
        "code": "161130",
        "name": "易方达纳斯达克100ETF联接(QDII-LOF)A",
        "style": "long_term",
        "preferred": True,
    },
    {
        "code": "012870",
        "name": "易方达纳斯达克100ETF联接(QDII-LOF)C",
        "style": "short_to_mid_term",
        "preferred": False,
    },
    {
        "code": "019547",
        "name": "招商纳斯达克100ETF联接(QDII)A",
        "style": "candidate",
        "preferred": False,
    },
]

MODEL_VERSION = "0.2"
DEFAULT_DATA_DIR = Path(os.environ.get("QQQ_ADVISOR_DATA_DIR", "data"))
ADVICE_LOG_FILE = "advice_log.json"
MODEL_PARAMS_FILE = "model_params.json"
MACRO_CONTEXT_FILE = "macro_context.json"
MANUAL_ORDERS_FILE = "manual_orders.json"
ACTUAL_TRADES_FILE = "actual_trades.json"
EVALUATION_HORIZONS = [1, 5, 20]
QQQ_BARS_CACHE_FILE = "qqq_bars_cache.json"


DEFAULT_MODEL_PARAMS = {
    "version": "0.2.0",
    "min_completed_20d_for_evolution": 5,
    "target_weights": {
        "conservative": 0.12,
        "balanced": 0.22,
        "aggressive": 0.32,
    },
    "profile_adjustments": {
        "short_horizon_years": 1.5,
        "short_horizon_multiplier": 0.55,
        "long_horizon_years": 5,
        "long_horizon_multiplier": 1.15,
        "low_loss_pct": 10,
        "low_loss_multiplier": 0.65,
        "high_loss_pct": 30,
        "high_loss_multiplier": 1.10,
        "min_weight": 0.05,
        "max_weight": 0.40,
    },
    "scoring": {
        "base_score": 50,
        "above_ma200_bonus": 12,
        "below_ma200_penalty": -20,
        "above_ma60_bonus": 8,
        "below_ma60_penalty": -6,
        "drawdown_tiers": [
            {"max_pct": -8, "points": 22},
            {"max_pct": -5, "points": 15},
            {"max_pct": -3, "points": 8},
        ],
        "near_high_drawdown_pct": -1,
        "ma20_extension_pct": 4,
        "near_high_extension_penalty": -18,
        "rsi_hot": 75,
        "rsi_hot_penalty": -22,
        "rsi_warm": 68,
        "rsi_warm_penalty": -12,
        "rsi_cold": 35,
        "rsi_cold_bonus": 15,
        "high_volatility_pct": 35,
        "high_volatility_penalty": -8,
    },
    "action_thresholds": {
        "加仓": 78,
        "正常买入": 62,
        "小额试探": 45,
    },
    "trade_fractions": {
        "加仓": 0.35,
        "正常买入": 0.25,
        "小额试探": 0.12,
        "暂不买入": 0.0,
    },
    "sell_rules": {
        "enabled": True,
        "min_sell_cny": 100,
        "trim_excess_fraction": 0.35,
        "max_sell_position_fraction": 0.20,
        "score_sell_threshold": 38,
        "rsi_take_profit": 82,
        "near_high_drawdown_pct": -1,
    },
    "execution_lag": {
        "alipay_fund_nav_lag_trading_days": 1,
        "high_volatility_pct": 25,
        "high_volatility_penalty": -3,
    },
    "macro_scoring": {
        "enabled": True,
        "max_abs_adjustment": 12,
        "risk_off_multiplier": -2,
        "risk_on_multiplier": 1,
        "category_weights": {
            "geopolitics": 1.2,
            "war": 1.4,
            "monetary_policy": 1.1,
            "inflation": 1.0,
            "recession": 1.3,
            "trade": 1.0,
            "regulation": 0.8,
            "earnings": 0.8,
            "technology": 0.7,
            "other": 0.6,
        },
    },
    "currency_macro_scoring": {
        "enabled": True,
        "max_abs_adjustment": 8,
        "usdcny_30d_depreciation_threshold_pct": 2.0,
        "usdcny_30d_appreciation_threshold_pct": -2.0,
        "cny_depreciation_points": -2,
        "cny_appreciation_points": 1,
        "global_rate_hike_points": -3,
        "global_rate_cut_points": 2,
        "usd_liquidity_tightening_points": -4,
        "usd_liquidity_easing_points": 3,
    },
    "factor_model": {
        "enabled": True,
        "score_min": 0,
        "score_max": 100,
        "factors": {
            "trend_ma200": {
                "enabled": True,
                "type": "above_below",
                "left": "close",
                "right": "ma200",
                "above_points": 12,
                "below_points": -20,
                "weight": 1.0,
                "min_weight": 0.5,
                "max_weight": 1.5
            },
            "trend_ma60": {
                "enabled": True,
                "type": "above_below",
                "left": "close",
                "right": "ma60",
                "above_points": 8,
                "below_points": -6,
                "weight": 1.0,
                "min_weight": 0.5,
                "max_weight": 1.5
            },
            "drawdown_buy_zone": {
                "enabled": True,
                "type": "threshold_tiers",
                "field": "drawdown_from_252d_high_pct",
                "tiers": [
                    {"op": "<=", "value": -8, "points": 22},
                    {"op": "<=", "value": -5, "points": 15},
                    {"op": "<=", "value": -3, "points": 8}
                ],
                "weight": 1.0,
                "min_weight": 0.5,
                "max_weight": 1.5
            },
            "near_high_extension": {
                "enabled": True,
                "type": "all_conditions",
                "conditions": [
                    {"field": "drawdown_from_252d_high_pct", "op": ">", "value": -1},
                    {"field": "distance_from_ma20_pct", "op": ">", "value": 4}
                ],
                "points": -18,
                "weight": 1.0,
                "min_weight": 0.5,
                "max_weight": 1.5
            },
            "rsi_hot": {
                "enabled": True,
                "type": "threshold",
                "field": "rsi14",
                "op": ">=",
                "value": 75,
                "points": -22,
                "weight": 1.0,
                "min_weight": 0.4,
                "max_weight": 1.4
            },
            "rsi_warm": {
                "enabled": True,
                "type": "range",
                "field": "rsi14",
                "min": 68,
                "max": 75,
                "points": -12,
                "weight": 1.0,
                "min_weight": 0.4,
                "max_weight": 1.4
            },
            "rsi_cold_above_ma200": {
                "enabled": True,
                "type": "all_conditions",
                "conditions": [
                    {"field": "rsi14", "op": "<=", "value": 35},
                    {"field": "close", "op": ">", "other_field": "ma200"}
                ],
                "points": 15,
                "weight": 1.0,
                "min_weight": 0.5,
                "max_weight": 1.5
            },
            "high_volatility": {
                "enabled": True,
                "type": "threshold",
                "field": "annualized_vol20_pct",
                "op": ">",
                "value": 35,
                "points": -8,
                "weight": 1.0,
                "min_weight": 0.4,
                "max_weight": 1.6
            },
            "vix_stress": {
                "enabled": False,
                "type": "threshold",
                "field": "vix_close",
                "op": ">=",
                "value": 25,
                "points": -10,
                "weight": 1.0,
                "min_weight": 0.3,
                "max_weight": 1.8
            },
            "us10y_rising": {
                "enabled": False,
                "type": "threshold",
                "field": "us10y_momentum_20d_pct",
                "op": ">=",
                "value": 5,
                "points": -6,
                "weight": 1.0,
                "min_weight": 0.3,
                "max_weight": 1.8
            },
            "dxy_rising": {
                "enabled": False,
                "type": "threshold",
                "field": "dxy_momentum_20d_pct",
                "op": ">=",
                "value": 2,
                "points": -5,
                "weight": 1.0,
                "min_weight": 0.3,
                "max_weight": 1.8
            },
            "qqq_volume_spike": {
                "enabled": False,
                "type": "threshold",
                "field": "qqq_volume_ratio20",
                "op": ">=",
                "value": 1.5,
                "points": -4,
                "weight": 1.0,
                "min_weight": 0.3,
                "max_weight": 1.8
            }
        }
    },
}


DEFAULT_MACRO_CONTEXT = {
    "updated_at": None,
    "source": "manual_local_json",
    "notes": "只写结构化摘要，不保存长新闻全文。direction 支持 risk_off/risk_on/neutral。",
    "currency": {
        "usdcny": None,
        "usdcny_change_pct_30d": None,
        "cny_direction": "neutral",
    },
    "central_banks": {
        "fed": "neutral",
        "ecb": "neutral",
        "pboc": "neutral",
        "boj": "neutral",
        "global_bias": "neutral",
    },
    "usd_liquidity": "neutral",
    "events": [],
}


@dataclass(frozen=True)
class Bar:
    day: date
    close: float
    volume: float | None = None


@dataclass(frozen=True)
class FundSnapshot:
    code: str
    name: str
    nav_date: str | None
    nav: float | None
    estimate: float | None
    estimate_time: str | None
    source: str
    note: str | None = None


def http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 15, retries: int = 2) -> str:
    req_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/csv,application/json,text/plain,*/*",
        "Connection": "close",
        **(headers or {}),
    }
    def decode_response(resp: Any) -> str:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        try:
            codecs.lookup(charset)
        except LookupError:
            charset = "utf-8"
        text = raw.decode(charset, errors="replace")
        if text.count("�") > 5:
            text = raw.decode("gbk", errors="replace")
        return text

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return decode_response(resp)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, ssl.SSLError):
                if os.environ.get("QQQ_ADVISOR_STRICT_SSL") == "1":
                    raise
                context = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                    return decode_response(resp)
            last_exc = exc
        except (ConnectionResetError, TimeoutError, OSError) as exc:
            last_exc = exc
        if attempt < retries:
            time.sleep(0.8 * (attempt + 1))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"HTTP 请求失败: {url}")


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"配置文件不存在: {path}\n先运行: python qqq_advisor.py init")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def model_params_path(config: dict[str, Any]) -> Path:
    return Path(config.get("data_dir", str(DEFAULT_DATA_DIR))) / MODEL_PARAMS_FILE


def macro_context_path(config: dict[str, Any]) -> Path:
    return Path(config.get("data_dir", str(DEFAULT_DATA_DIR))) / MACRO_CONTEXT_FILE


def manual_orders_path(config: dict[str, Any]) -> Path:
    return Path(config.get("data_dir", str(DEFAULT_DATA_DIR))) / MANUAL_ORDERS_FILE


def actual_trades_path(config: dict[str, Any]) -> Path:
    return Path(config.get("data_dir", str(DEFAULT_DATA_DIR))) / ACTUAL_TRADES_FILE


def qqq_bars_cache_path(config: dict[str, Any]) -> Path:
    return Path(config.get("data_dir", str(DEFAULT_DATA_DIR))) / QQQ_BARS_CACHE_FILE


def load_model_params(config: dict[str, Any]) -> dict[str, Any]:
    path = model_params_path(config)
    params = load_json(path, None)
    if params is None:
        save_json(path, DEFAULT_MODEL_PARAMS)
        return json.loads(json.dumps(DEFAULT_MODEL_PARAMS))
    merged = json.loads(json.dumps(DEFAULT_MODEL_PARAMS))
    deep_merge(merged, params)
    if merged != params:
        save_json(path, merged)
    return merged


def load_macro_context(config: dict[str, Any]) -> dict[str, Any]:
    path = macro_context_path(config)
    context = load_json(path, None)
    if context is None:
        save_json(path, DEFAULT_MACRO_CONTEXT)
        return json.loads(json.dumps(DEFAULT_MACRO_CONTEXT))
    merged = json.loads(json.dumps(DEFAULT_MACRO_CONTEXT))
    deep_merge(merged, context)
    return merged


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def fetch_qqq_from_stooq() -> list[Bar]:
    url = "https://stooq.com/q/d/l/?s=qqq.us&i=d"
    text = http_get(url)
    rows = csv.DictReader(text.splitlines())
    bars: list[Bar] = []
    for row in rows:
        if not row.get("Date") or not row.get("Close"):
            continue
        try:
            volume = float(row["Volume"]) if row.get("Volume") not in (None, "", "0") else None
            bars.append(Bar(day=datetime.strptime(row["Date"], "%Y-%m-%d").date(), close=float(row["Close"]), volume=volume))
        except ValueError:
            continue
    if len(bars) < 220:
        raise RuntimeError("Stooq 返回的 QQQ 日线不足 220 条，无法计算长期指标")
    return bars


def require_min_qqq_bars(bars: list[Bar], source: str) -> list[Bar]:
    if len(bars) < 220:
        raise RuntimeError(f"{source} 返回的 QQQ 日线不足 220 条，无法计算长期指标")
    return bars


def parse_market_number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    if text in {"", "--", "N/A", "null"}:
        raise ValueError("empty market number")
    return float(text)


def parse_market_date(value: Any) -> date:
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported market date: {value}")


def collect_investing_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            rows.extend(collect_investing_rows(item))
    elif isinstance(value, dict):
        keys = {str(key).lower() for key in value}
        has_date = bool(keys & {"date", "rowdate", "row_date", "pricedate", "time"})
        has_close = bool(keys & {"close", "price", "last_close", "lastcloseraw", "last_close_raw"})
        if has_date and has_close:
            rows.append(value)
        for item in value.values():
            if isinstance(item, (dict, list)):
                rows.extend(collect_investing_rows(item))
    return rows


def first_present(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    lower_map = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        key = name.lower()
        if key in lower_map and lower_map[key] not in (None, ""):
            return lower_map[key]
    raise KeyError(names[0])


def parse_investing_payload(raw: str, financial_id: str) -> list[Bar]:
    if not raw.strip():
        raise RuntimeError("Investing 返回空响应")
    data = json.loads(raw)
    rows = collect_investing_rows(data)
    bars = []
    for row in rows:
        try:
            day = parse_market_date(first_present(row, ("date", "rowDate", "row_date", "priceDate", "time")))
            close = parse_market_number(first_present(row, ("last_closeRaw", "last_close_raw", "close", "price", "last_close")))
            volume = None
            try:
                volume = parse_market_number(first_present(row, ("volume", "vol")))
            except (KeyError, ValueError):
                pass
            bars.append(Bar(day=day, close=close, volume=volume))
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda item: item.day)
    return require_min_qqq_bars(bars, f"Investing financialdata/{financial_id}")


def fetch_nasdaq100_from_investing() -> list[Bar]:
    financial_id = os.environ.get("QQQ_INVESTING_FINANCIALDATA_ID", "20")
    start_day = (date.today() - timedelta(days=900)).isoformat()
    end_day = date.today().isoformat()
    params = urllib.parse.urlencode(
        {
            "start-date": start_day,
            "end-date": end_day,
            "time-frame": "Daily",
            "add-missing-rows": "false",
        }
    )
    raw = http_get(
        f"https://api.investing.com/api/financialdata/historical/{urllib.parse.quote(financial_id)}?{params}",
        headers={
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Domain-Id": "cn",
            "Origin": "https://cn.investing.com",
            "Referer": "https://cn.investing.com/",
        },
    )
    return parse_investing_payload(raw, financial_id)


def fetch_qqq_from_twelve_data() -> list[Bar]:
    api_key = os.environ.get("QQQ_TWELVE_DATA_API_KEY") or os.environ.get("TWELVE_DATA_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 QQQ_TWELVE_DATA_API_KEY")
    params = urllib.parse.urlencode(
        {
            "symbol": "QQQ",
            "interval": "1day",
            "outputsize": 600,
            "order": "ASC",
            "format": "JSON",
            "apikey": api_key,
        }
    )
    raw = http_get(f"https://api.twelvedata.com/time_series?{params}")
    data = json.loads(raw)
    if data.get("status") == "error":
        raise RuntimeError(data.get("message") or "Twelve Data 返回错误")
    values = data.get("values")
    if not isinstance(values, list):
        raise RuntimeError("Twelve Data 返回格式缺少 values")
    bars = []
    for row in values:
        try:
            volume_raw = row.get("volume")
            bars.append(
                Bar(
                    day=datetime.strptime(str(row["datetime"])[:10], "%Y-%m-%d").date(),
                    close=float(row["close"]),
                    volume=float(volume_raw) if volume_raw not in (None, "", "0") else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda item: item.day)
    return require_min_qqq_bars(bars, "Twelve Data")


def fetch_qqq_from_tiingo() -> list[Bar]:
    token = os.environ.get("QQQ_TIINGO_API_TOKEN") or os.environ.get("TIINGO_API_TOKEN")
    if not token:
        raise RuntimeError("未配置 QQQ_TIINGO_API_TOKEN")
    start_day = (date.today() - timedelta(days=900)).isoformat()
    end_day = date.today().isoformat()
    params = urllib.parse.urlencode({"startDate": start_day, "endDate": end_day, "format": "json"})
    raw = http_get(
        f"https://api.tiingo.com/tiingo/daily/QQQ/prices?{params}",
        headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
    )
    data = json.loads(raw)
    if isinstance(data, dict) and data.get("detail"):
        raise RuntimeError(str(data["detail"]))
    if not isinstance(data, list):
        raise RuntimeError("Tiingo 返回格式不是列表")
    bars = []
    for row in data:
        try:
            volume_raw = row.get("volume")
            bars.append(
                Bar(
                    day=datetime.strptime(str(row["date"])[:10], "%Y-%m-%d").date(),
                    close=float(row.get("close") or row["adjClose"]),
                    volume=float(volume_raw) if volume_raw not in (None, "", "0") else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda item: item.day)
    return require_min_qqq_bars(bars, "Tiingo")


def fetch_qqq_from_nasdaq_charting() -> list[Bar]:
    start_day = (date.today() - timedelta(days=900)).isoformat()
    end_day = date.today().isoformat()
    params = urllib.parse.urlencode({"symbol": "QQQ", "date": f"{start_day}~{end_day}"})
    raw = http_get(
        f"https://charting.nasdaq.com/data/charting/historical?{params}&",
        headers={
            "Accept": "*/*",
            "Referer": "https://charting.nasdaq.com/dynamic/chart.html",
            "Origin": "https://charting.nasdaq.com",
        },
    )
    data = json.loads(raw)
    market_data = data.get("marketData")
    if not isinstance(market_data, list):
        raise RuntimeError("Nasdaq charting 返回格式缺少 marketData")
    bars = []
    for row in market_data:
        try:
            volume_raw = row.get("Volume")
            bars.append(
                Bar(
                    day=datetime.strptime(str(row["Date"])[:10], "%Y-%m-%d").date(),
                    close=parse_market_number(row["Close"]),
                    volume=parse_market_number(volume_raw) if volume_raw not in (None, "", "0") else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda item: item.day)
    return require_min_qqq_bars(bars, "Nasdaq charting")


def fetch_nasdaq100_from_google_finance() -> list[Bar]:
    symbol = os.environ.get("QQQ_GOOGLE_FINANCE_SYMBOL", "NDX:INDEXNASDAQ")
    url_symbol = urllib.parse.quote(symbol, safe=":")
    raw = http_get(
        f"https://www.google.com/finance/beta/quote/{url_symbol}?hl=zh-CN",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    pairs = re.findall(r"\[\s*(1[5-9]\d{8,11})\s*,\s*([0-9]{3,8}(?:\.[0-9]+)?)\s*\]", raw)
    by_day: dict[date, float] = {}
    for ts_raw, close_raw in pairs:
        try:
            ts = int(ts_raw)
            if ts > 10_000_000_000:
                ts = ts // 1000
            day = datetime.fromtimestamp(ts, timezone.utc).date()
            if day > date.today() + timedelta(days=1) or day < date.today() - timedelta(days=1200):
                continue
            by_day[day] = float(close_raw)
        except (OverflowError, OSError, ValueError):
            continue
    bars = [Bar(day=day, close=close) for day, close in sorted(by_day.items())]
    return require_min_qqq_bars(bars, f"Google Finance {symbol}")


def fetch_yahoo_bars_from_host(host: str, symbol: str, range_days: str = "2y") -> list[Bar]:
    base = f"https://{host}/v8/finance/chart/{urllib.parse.quote(symbol)}"
    params = urllib.parse.urlencode({"range": range_days, "interval": "1d"})
    raw = http_get(f"{base}?{params}")
    data = json.loads(raw)
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    closes = quote["close"]
    volumes = quote.get("volume") or [None] * len(closes)
    bars = []
    for ts, close, volume in zip(timestamps, closes, volumes):
        if close is None:
            continue
        bars.append(Bar(day=datetime.fromtimestamp(ts, timezone.utc).date(), close=float(close), volume=float(volume) if volume else None))
    return bars


def fetch_yahoo_bars(symbol: str, range_days: str = "2y") -> list[Bar]:
    errors = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            return fetch_yahoo_bars_from_host(host, symbol, range_days=range_days)
        except Exception as exc:  # noqa: BLE001 - alternate Yahoo host is a normal fallback
            errors.append(f"{host}: {exc}")
    raise RuntimeError("Yahoo chart API 请求失败: " + " | ".join(errors))


def fetch_qqq_from_yahoo(range_days: str = "2y") -> list[Bar]:
    bars = fetch_yahoo_bars("QQQ", range_days=range_days)
    return require_min_qqq_bars(bars, "Yahoo")


def serialize_bars(bars: list[Bar]) -> list[dict[str, Any]]:
    return [
        {
            "date": bar.day.isoformat(),
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]


def deserialize_bars(rows: list[dict[str, Any]]) -> list[Bar]:
    bars = []
    for row in rows:
        try:
            bars.append(
                Bar(
                    day=datetime.strptime(str(row["date"]), "%Y-%m-%d").date(),
                    close=float(row["close"]),
                    volume=float(row["volume"]) if row.get("volume") not in (None, "") else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return bars


def save_qqq_bars_cache(path: Path, bars: list[Bar], source: str) -> None:
    save_json(
        path,
        {
            "source": source,
            "cached_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "bars": serialize_bars(bars),
        },
    )


def load_qqq_bars_cache(path: Path) -> tuple[list[Bar], str] | None:
    data = load_json(path, None)
    if not isinstance(data, dict):
        return None
    bars = deserialize_bars(data.get("bars", []))
    if len(bars) < 220:
        return None
    source = str(data.get("source") or "unknown")
    cached_at = data.get("cached_at")
    label = f"cache:{source}"
    if cached_at:
        label += f"@{cached_at}"
    return bars, label


def fetch_qqq_bars(cache_path: Path | None = None) -> tuple[list[Bar], str]:
    errors = []
    fetchers = []
    if os.environ.get("QQQ_TWELVE_DATA_API_KEY") or os.environ.get("TWELVE_DATA_API_KEY"):
        fetchers.append(("twelvedata", fetch_qqq_from_twelve_data))
    if os.environ.get("QQQ_TIINGO_API_TOKEN") or os.environ.get("TIINGO_API_TOKEN"):
        fetchers.append(("tiingo", fetch_qqq_from_tiingo))
    fetchers.extend(
        (
            ("investing_nasdaq100", fetch_nasdaq100_from_investing),
            ("google_finance_ndx", fetch_nasdaq100_from_google_finance),
            ("nasdaq_charting", fetch_qqq_from_nasdaq_charting),
            ("stooq", fetch_qqq_from_stooq),
            ("yahoo", fetch_qqq_from_yahoo),
        )
    )
    for source, fetcher in fetchers:
        try:
            bars = fetcher()
            if cache_path is not None:
                save_qqq_bars_cache(cache_path, bars, source)
            return bars, source
        except Exception as exc:  # noqa: BLE001 - keep fallback resilient for daily jobs
            errors.append(f"{source}: {exc}")
    if cache_path is not None:
        cached = load_qqq_bars_cache(cache_path)
        if cached:
            return cached
    raise RuntimeError("无法获取 QQQ 数据: " + " | ".join(errors))


def latest_bar_on_or_before(series: list[Bar], day: date) -> Bar | None:
    result = None
    for bar in series:
        if bar.day <= day:
            result = bar
        else:
            break
    return result


def fetch_external_factor_series(range_days: str = "2y") -> dict[str, list[Bar]]:
    symbols = {
        "vix": "^VIX",
        "us10y": "^TNX",
        "dxy": "DX-Y.NYB",
    }
    data: dict[str, list[Bar]] = {}
    for name, symbol in symbols.items():
        try:
            data[name] = fetch_yahoo_bars(symbol, range_days=range_days)
        except Exception:
            data[name] = []
    return data


def external_features_for_day(series: dict[str, list[Bar]], day: date) -> dict[str, float | None]:
    features: dict[str, float | None] = {}
    for name, bars in series.items():
        current = latest_bar_on_or_before(bars, day)
        if current is None:
            features[f"{name}_close"] = None
            features[f"{name}_momentum_20d_pct"] = None
            continue
        current_idx = next((idx for idx, item in enumerate(bars) if item.day == current.day), None)
        features[f"{name}_close"] = round(current.close, 4)
        if current_idx is not None and current_idx >= 20 and bars[current_idx - 20].close:
            features[f"{name}_momentum_20d_pct"] = round((current.close / bars[current_idx - 20].close - 1) * 100, 2)
        else:
            features[f"{name}_momentum_20d_pct"] = None
    return features


def moving_average(values: list[float], window: int) -> float:
    if len(values) < window:
        raise ValueError(f"not enough values for MA{window}")
    return statistics.fmean(values[-window:])


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        raise ValueError("not enough values for RSI")
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(values[-period - 1 : -1], values[-period:]):
        diff = cur - prev
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = statistics.fmean(gains)
    avg_loss = statistics.fmean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def max_drawdown_from_high(values: list[float], window: int = 252) -> float:
    recent = values[-window:]
    high = max(recent)
    return (recent[-1] / high - 1.0) * 100


def volatility(values: list[float], window: int = 20) -> float:
    recent = values[-window - 1 :]
    rets = [(b / a - 1.0) for a, b in zip(recent[:-1], recent[1:]) if a > 0]
    if len(rets) < 2:
        return 0.0
    return statistics.stdev(rets) * math.sqrt(252) * 100


def pct_change(values: list[float], window: int) -> float:
    if len(values) <= window or values[-window - 1] == 0:
        return 0.0
    return (values[-1] / values[-window - 1] - 1.0) * 100


def normalize_profile(config: dict[str, Any]) -> dict[str, Any]:
    profile = config.get("profile", {})
    risk = profile.get("risk_level", "balanced")
    if risk not in {"conservative", "balanced", "aggressive"}:
        risk = "balanced"
    horizon = profile.get("horizon_years", 3)
    try:
        horizon = float(horizon)
    except (TypeError, ValueError):
        horizon = 3.0
    max_loss = profile.get("max_acceptable_loss_pct", 20)
    try:
        max_loss = float(max_loss)
    except (TypeError, ValueError):
        max_loss = 20.0
    total = profile.get("total_investable_cny", 0)
    try:
        total = float(total)
    except (TypeError, ValueError):
        total = 0.0
    return {
        "risk_level": risk,
        "horizon_years": horizon,
        "max_acceptable_loss_pct": max_loss,
        "total_investable_cny": total,
    }


def target_nasdaq_weight(profile: dict[str, Any], params: dict[str, Any] | None = None) -> float:
    params = params or DEFAULT_MODEL_PARAMS
    weights = params["target_weights"]
    adjustments = params["profile_adjustments"]
    risk = profile["risk_level"]
    base = weights.get(risk, weights["balanced"])
    if profile["horizon_years"] < adjustments["short_horizon_years"]:
        base *= adjustments["short_horizon_multiplier"]
    elif profile["horizon_years"] >= adjustments["long_horizon_years"]:
        base *= adjustments["long_horizon_multiplier"]
    if profile["max_acceptable_loss_pct"] <= adjustments["low_loss_pct"]:
        base *= adjustments["low_loss_multiplier"]
    elif profile["max_acceptable_loss_pct"] >= adjustments["high_loss_pct"]:
        base *= adjustments["high_loss_multiplier"]
    return min(max(base, adjustments["min_weight"]), adjustments["max_weight"])


def action_from_score(score: int, params: dict[str, Any] | None = None) -> str:
    params = params or DEFAULT_MODEL_PARAMS
    thresholds = params["action_thresholds"]
    if score >= thresholds["加仓"]:
        return "加仓"
    if score >= thresholds["正常买入"]:
        return "正常买入"
    if score >= thresholds["小额试探"]:
        return "小额试探"
    return "暂不买入"


def compare_values(left: float, op: str, right: float) -> bool:
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == "==":
        return left == right
    raise ValueError(f"不支持的比较符: {op}")


def factor_condition_matches(features: dict[str, Any], condition: dict[str, Any]) -> bool:
    if condition["field"] not in features or features.get(condition["field"]) is None:
        return False
    left = float(features[condition["field"]])
    if condition.get("other_field"):
        if condition["other_field"] not in features or features.get(condition["other_field"]) is None:
            return False
        right = float(features[condition["other_field"]])
    else:
        right = float(condition["value"])
    return compare_values(left, condition["op"], right)


def evaluate_factor(features: dict[str, Any], factor: dict[str, Any]) -> float:
    factor_type = factor.get("type")
    if factor_type == "above_below":
        if features.get(factor["left"]) is None or features.get(factor["right"]) is None:
            return 0.0
        left = float(features[factor["left"]])
        right = float(features[factor["right"]])
        return float(factor["above_points"] if left > right else factor["below_points"])
    if factor_type == "threshold":
        if features.get(factor["field"]) is None:
            return 0.0
        value = float(features[factor["field"]])
        return float(factor["points"] if compare_values(value, factor["op"], float(factor["value"])) else 0)
    if factor_type == "range":
        if features.get(factor["field"]) is None:
            return 0.0
        value = float(features[factor["field"]])
        return float(factor["points"] if float(factor["min"]) <= value < float(factor["max"]) else 0)
    if factor_type == "threshold_tiers":
        if features.get(factor["field"]) is None:
            return 0.0
        value = float(features[factor["field"]])
        for tier in factor.get("tiers", []):
            if compare_values(value, tier["op"], float(tier["value"])):
                return float(tier["points"])
        return 0.0
    if factor_type == "all_conditions":
        if all(factor_condition_matches(features, condition) for condition in factor.get("conditions", [])):
            return float(factor["points"])
        return 0.0
    return 0.0


def score_from_factor_model(features: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    scoring = params["scoring"]
    factor_model = params.get("factor_model", {})
    score = float(scoring["base_score"])
    contributions = []
    for name, factor in factor_model.get("factors", {}).items():
        if not factor.get("enabled", True):
            continue
        raw_points = evaluate_factor(features, factor)
        weight = float(factor.get("weight", 1.0))
        weighted_points = raw_points * weight
        if weighted_points:
            contributions.append(
                {
                    "name": name,
                    "raw_points": round(raw_points, 4),
                    "weight": round(weight, 4),
                    "points": round(weighted_points, 4),
                }
            )
        score += weighted_points
    score = min(max(int(round(score)), int(factor_model.get("score_min", 0))), int(factor_model.get("score_max", 100)))
    return {"score": score, "action": action_from_score(score, params), "factor_contributions": contributions}


def score_from_features(features: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or DEFAULT_MODEL_PARAMS
    if params.get("factor_model", {}).get("enabled", False):
        return score_from_factor_model(features, params)
    scoring = params["scoring"]
    score = scoring["base_score"]

    close = float(features["close"])
    ma20 = float(features["ma20"])
    ma60 = float(features["ma60"])
    ma200 = float(features["ma200"])
    current_rsi = float(features["rsi14"])
    drawdown = float(features["drawdown_from_252d_high_pct"])
    vol20 = float(features["annualized_vol20_pct"])
    extended_from_20 = float(features.get("distance_from_ma20_pct", (close / ma20 - 1) * 100))

    if close > ma200:
        score += scoring["above_ma200_bonus"]
    else:
        score += scoring["below_ma200_penalty"]
    if close > ma60:
        score += scoring["above_ma60_bonus"]
    else:
        score += scoring["below_ma60_penalty"]

    drawdown_matched = False
    for tier in scoring["drawdown_tiers"]:
        if drawdown <= tier["max_pct"]:
            score += tier["points"]
            drawdown_matched = True
            break
    if not drawdown_matched and drawdown > scoring["near_high_drawdown_pct"] and extended_from_20 > scoring["ma20_extension_pct"]:
        score += scoring["near_high_extension_penalty"]

    if current_rsi >= scoring["rsi_hot"]:
        score += scoring["rsi_hot_penalty"]
    elif current_rsi >= scoring["rsi_warm"]:
        score += scoring["rsi_warm_penalty"]
    elif current_rsi <= scoring["rsi_cold"] and close > ma200:
        score += scoring["rsi_cold_bonus"]

    if vol20 > scoring["high_volatility_pct"]:
        score += scoring["high_volatility_penalty"]

    score = min(max(int(score), 0), 100)
    return {"score": score, "action": action_from_score(score, params)}


def score_market(bars: list[Bar], params: dict[str, Any] | None = None, external_features: dict[str, Any] | None = None) -> dict[str, Any]:
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    last = closes[-1]
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma200 = moving_average(closes, 200)
    prev_ma20 = statistics.fmean(closes[-25:-5]) if len(closes) >= 25 else ma20
    prev_ma60 = statistics.fmean(closes[-80:-20]) if len(closes) >= 80 else ma60
    current_rsi = rsi(closes)
    drawdown = max_drawdown_from_high(closes)
    vol20 = volatility(closes, 20)
    vol60 = volatility(closes, 60)
    extended_from_20 = (last / ma20 - 1) * 100
    latest_volume = volumes[-1]
    recent_volumes = [v for v in volumes[-20:] if v]
    prev_volumes = [v for v in volumes[-40:-20] if v]
    avg_volume20 = statistics.fmean(recent_volumes) if recent_volumes else None
    prev_avg_volume20 = statistics.fmean(prev_volumes) if prev_volumes else None

    market = {
        "date": bars[-1].day.isoformat(),
        "close": round(last, 4),
        "ma20": round(ma20, 4),
        "ma60": round(ma60, 4),
        "ma200": round(ma200, 4),
        "rsi14": round(current_rsi, 2),
        "drawdown_from_252d_high_pct": round(drawdown, 2),
        "distance_from_ma20_pct": round(extended_from_20, 2),
        "distance_from_ma60_pct": round((last / ma60 - 1) * 100, 2),
        "distance_from_ma200_pct": round((last / ma200 - 1) * 100, 2),
        "momentum_5d_pct": round(pct_change(closes, 5), 2),
        "momentum_20d_pct": round(pct_change(closes, 20), 2),
        "momentum_60d_pct": round(pct_change(closes, 60), 2),
        "ma20_slope_pct": round((ma20 / prev_ma20 - 1) * 100, 2) if prev_ma20 else 0,
        "ma60_slope_pct": round((ma60 / prev_ma60 - 1) * 100, 2) if prev_ma60 else 0,
        "annualized_vol20_pct": round(vol20, 2),
        "annualized_vol60_pct": round(vol60, 2),
        "qqq_volume": round(latest_volume, 2) if latest_volume else None,
        "qqq_volume_ratio20": round(latest_volume / avg_volume20, 4) if latest_volume and avg_volume20 else None,
        "qqq_volume_trend20_pct": round((avg_volume20 / prev_avg_volume20 - 1) * 100, 2) if avg_volume20 and prev_avg_volume20 else None,
    }
    if external_features:
        market.update(external_features)
    market.update(score_from_features(market, params))
    return market


def trade_fraction(action: str, params: dict[str, Any] | None = None) -> float:
    params = params or DEFAULT_MODEL_PARAMS
    return float(params["trade_fractions"][action])


def add_business_days(start: date, days: int) -> date:
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def compact_macro_context(macro: dict[str, Any], max_events: int = 5, max_summary_chars: int = 90) -> dict[str, Any]:
    events = []
    for event in macro.get("events", [])[:max_events]:
        summary = str(event.get("summary", ""))[:max_summary_chars]
        events.append(
            {
                "category": event.get("category", "other"),
                "direction": event.get("direction", "neutral"),
                "severity": event.get("severity", 0),
                "summary": summary,
                "expires_at": event.get("expires_at"),
            }
        )
    return {
        "updated_at": macro.get("updated_at"),
        "source": macro.get("source"),
        "currency": macro.get("currency", {}),
        "central_banks": macro.get("central_banks", {}),
        "usd_liquidity": macro.get("usd_liquidity"),
        "events": events,
    }


def macro_score_adjustment(macro: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    scoring = params.get("macro_scoring", {})
    if not scoring.get("enabled", True):
        return {"points": 0, "active_events": [], "summary": "宏观因子关闭"}

    today = date.today()
    weights = scoring.get("category_weights", {})
    raw_points = 0.0
    active_events = []
    for event in macro.get("events", []):
        expires_at = event.get("expires_at")
        if expires_at:
            try:
                if datetime.strptime(expires_at, "%Y-%m-%d").date() < today:
                    continue
            except ValueError:
                pass
        severity = max(min(float(event.get("severity", 0) or 0), 5), 0)
        category = event.get("category", "other")
        direction = event.get("direction", "neutral")
        weight = float(weights.get(category, weights.get("other", 0.6)))
        if direction == "risk_off":
            raw_points += severity * weight * float(scoring.get("risk_off_multiplier", -2))
        elif direction == "risk_on":
            raw_points += severity * weight * float(scoring.get("risk_on_multiplier", 1))
        active_events.append(
            {
                "category": category,
                "direction": direction,
                "severity": severity,
                "summary": str(event.get("summary", ""))[:120],
            }
        )

    limit = abs(float(scoring.get("max_abs_adjustment", 12)))
    points = int(round(min(max(raw_points, -limit), limit)))
    if points > 0:
        summary = f"宏观事件偏风险偏好，评分调整 +{points}"
    elif points < 0:
        summary = f"宏观事件偏风险规避，评分调整 {points}"
    else:
        summary = "宏观事件对评分影响中性"
    return {"points": points, "active_events": active_events, "summary": summary}


def currency_macro_adjustment(macro: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    scoring = params.get("currency_macro_scoring", {})
    if not scoring.get("enabled", True):
        return {"points": 0, "summary": "汇率和央行因子关闭"}

    points = 0
    details = []
    currency = macro.get("currency", {})
    change_30d = currency.get("usdcny_change_pct_30d")
    if change_30d not in (None, ""):
        change_30d = float(change_30d)
        if change_30d >= float(scoring["usdcny_30d_depreciation_threshold_pct"]):
            points += int(scoring["cny_depreciation_points"])
            details.append(f"人民币30日贬值 {change_30d}%")
        elif change_30d <= float(scoring["usdcny_30d_appreciation_threshold_pct"]):
            points += int(scoring["cny_appreciation_points"])
            details.append(f"人民币30日升值 {change_30d}%")

    central_banks = macro.get("central_banks", {})
    global_bias = central_banks.get("global_bias", "neutral")
    if global_bias == "hiking":
        points += int(scoring["global_rate_hike_points"])
        details.append("全球央行偏加息")
    elif global_bias == "cutting":
        points += int(scoring["global_rate_cut_points"])
        details.append("全球央行偏降息")

    liquidity = macro.get("usd_liquidity", "neutral")
    if liquidity == "tightening":
        points += int(scoring["usd_liquidity_tightening_points"])
        details.append("美元流动性收紧")
    elif liquidity == "easing":
        points += int(scoring["usd_liquidity_easing_points"])
        details.append("美元流动性宽松")

    limit = abs(int(scoring.get("max_abs_adjustment", 8)))
    points = min(max(points, -limit), limit)
    if details:
        summary = "；".join(details)
    else:
        summary = "汇率、央行和美元流动性影响中性"
    return {"points": points, "summary": summary}


def apply_context_adjustments(market: dict[str, Any], macro: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    adjusted = dict(market)
    components = []

    macro_adjustment = macro_score_adjustment(macro, params)
    if macro_adjustment["points"]:
        components.append({"name": "macro", **macro_adjustment})

    currency_adjustment = currency_macro_adjustment(macro, params)
    if currency_adjustment["points"]:
        components.append({"name": "currency_macro", **currency_adjustment})

    lag = params.get("execution_lag", {})
    lag_points = 0
    lag_days = int(lag.get("alipay_fund_nav_lag_trading_days", 1))
    if lag_days > 0 and float(market.get("annualized_vol20_pct", 0)) >= float(lag.get("high_volatility_pct", 25)):
        lag_points = int(lag.get("high_volatility_penalty", -3))
        components.append(
            {
                "name": "execution_lag",
                "points": lag_points,
                "summary": f"支付宝基金按 T+{lag_days} 估算确认净值，且近期波动偏高，降低入场分",
            }
        )

    original_score = int(market["score"])
    adjusted_score = min(max(original_score + macro_adjustment["points"] + currency_adjustment["points"] + lag_points, 0), 100)
    adjusted["base_score_before_context"] = original_score
    adjusted["score"] = adjusted_score
    adjusted["action"] = action_from_score(adjusted_score, params)
    adjusted["context_adjustments"] = components
    adjusted["macro_context"] = compact_macro_context(macro)
    return adjusted


def fetch_fund_snapshot(fund: dict[str, Any]) -> FundSnapshot:
    code = fund["code"]
    name = fund["name"]

    # Eastmoney real-time estimate endpoint. It is public but unofficial; keep
    # it as a convenience snapshot and treat Alipay's page as the final rule.
    url = f"https://fundgz.1234567.com.cn/js/{code}.js"
    try:
        text = http_get(url, headers={"Referer": "https://fund.eastmoney.com/"})
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
            return FundSnapshot(
                code=code,
                name=name,
                nav_date=data.get("jzrq"),
                nav=float(data["dwjz"]) if data.get("dwjz") else None,
                estimate=float(data["gsz"]) if data.get("gsz") else None,
                estimate_time=data.get("gztime"),
                source="eastmoney_fundgz",
            )
    except Exception as exc:  # noqa: BLE001
        return FundSnapshot(
            code=code,
            name=name,
            nav_date=None,
            nav=None,
            estimate=None,
            estimate_time=None,
            source="eastmoney_fundgz",
            note=str(exc),
        )

    return FundSnapshot(code=code, name=name, nav_date=None, nav=None, estimate=None, estimate_time=None, source="none")


def build_recommendation(
    config: dict[str, Any],
    market: dict[str, Any],
    funds: list[FundSnapshot],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = params or DEFAULT_MODEL_PARAMS
    profile = normalize_profile(config)
    target_weight = target_nasdaq_weight(profile, params)
    target_amount = profile["total_investable_cny"] * target_weight
    portfolio = config.get("portfolio", {})
    holdings = portfolio.get("nasdaq_fund_holdings", []) or []
    holding_amount = sum(float(item.get("amount_cny", 0) or 0) for item in holdings)
    current_position = float(portfolio.get("current_nasdaq_position_cny", holding_amount) or 0)
    if current_position == 0 and holding_amount > 0:
        current_position = holding_amount
    remaining_plan = max(target_amount - current_position, 0)
    buy_now = min(remaining_plan * trade_fraction(market["action"], params), remaining_plan)
    sell_now = calculate_sell_amount(current_position, target_amount, market, params)

    min_trade = float(config.get("rules", {}).get("min_trade_cny", 10) or 10)
    if buy_now < min_trade:
        buy_now = 0.0
    if sell_now < float(params.get("sell_rules", {}).get("min_sell_cny", 100)):
        sell_now = 0.0

    if market["action"] == "暂不买入":
        reason = "模型认为短线性价比不足，保留现金等待回撤或下一次定投日。"
    elif market["action"] == "小额试探":
        reason = "趋势仍可，但价格偏高或动能偏热，只适合小额建仓。"
    elif market["action"] == "正常买入":
        reason = "趋势和回撤位置较均衡，可以按计划买入一档。"
    else:
        reason = "出现较明显回撤且长期趋势未显著转弱，可以提高本次买入比例。"

    selected_funds = [f for f in funds if any(x["code"] == f.code and x.get("preferred") for x in DEFAULT_FUNDS)]
    if not selected_funds:
        selected_funds = funds[:2]

    order_day = date.today()
    nav_lag_days = int(params.get("execution_lag", {}).get("alipay_fund_nav_lag_trading_days", 1))
    expected_nav_day = add_business_days(order_day, nav_lag_days)
    manual_order = build_manual_order_ticket(
        market=market,
        selected_funds=selected_funds,
        holdings=holdings,
        buy_now=round(buy_now, 2),
        sell_now=round(sell_now, 2),
        order_day=order_day,
        expected_nav_day=expected_nav_day,
        reason=reason,
    )

    return {
        "model_version": MODEL_VERSION,
        "model_params_version": params.get("version"),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "profile": profile,
        "target_nasdaq_weight_pct": round(target_weight * 100, 2),
        "target_nasdaq_amount_cny": round(target_amount, 2),
        "current_nasdaq_position_cny": round(current_position, 2),
        "remaining_plan_cny": round(remaining_plan, 2),
        "market": market,
        "model_params": params,
        "execution_plan": {
            "platform": "alipay_fund",
            "assumed_rule": f"T+{nav_lag_days}",
            "order_date": order_day.isoformat(),
            "estimated_nav_date": expected_nav_day.isoformat(),
            "note": "按支付宝基金 T+1 估算确认/成交净值；如遇周末、境内外节假日或基金公告，以支付宝页面为准。",
        },
        "decision": {
            "action": market["action"],
            "buy_now_cny": round(buy_now, 2),
            "sell_now_cny": round(sell_now, 2),
            "reason": reason,
            "holding_days_warning": "QDII/支付宝基金确认和到账较慢，短于7天赎回通常成本很高；本工具按中长期分批买入设计。",
        },
        "manual_order": manual_order,
        "fund_candidates": [snapshot.__dict__ for snapshot in selected_funds],
        "holdings": build_holdings_snapshot(holdings),
        "manual_checks_before_order": [
            "打开支付宝确认该基金当天是否可申购、是否限购。",
            "确认支付宝页面显示的确认日是否符合 T+1；遇节假日可能顺延。",
            "确认买入费率、赎回费率、C类销售服务费是否符合持有周期。",
            "确认QDII确认日、海外节假日和赎回到账时间。",
            "如果支付宝页面和本工具数据冲突，以支付宝交易页为准。",
        ],
    }


def build_manual_order_ticket(
    market: dict[str, Any],
    selected_funds: list[FundSnapshot],
    holdings: list[dict[str, Any]],
    buy_now: float,
    sell_now: float,
    order_day: date,
    expected_nav_day: date,
    reason: str,
) -> dict[str, Any]:
    if buy_now <= 0 and sell_now <= 0:
        return {
            "status": "no_order",
            "message": "今日没有生成手动交易单。",
        }
    if sell_now > 0 and holdings:
        holding = holdings[0]
        fund_code = str(holding.get("code"))
        fund_name = holding.get("name") or fund_code
        order_type = "sell"
        amount = sell_now
    elif buy_now > 0 and selected_funds:
        fund = selected_funds[0]
        fund_code = fund.code
        fund_name = fund.name
        order_type = "buy"
        amount = buy_now
    else:
        return {
            "status": "no_order",
            "message": "缺少可生成交易单的基金。",
        }
    return {
        "status": "pending_manual_confirmation",
        "type": order_type,
        "platform": "alipay_fund",
        "fund_code": fund_code,
        "fund_name": fund_name,
        "amount_cny": amount,
        "order_date": order_day.isoformat(),
        "estimated_nav_date": expected_nav_day.isoformat(),
        "model_action": market["action"],
        "model_score": market["score"],
        "reason": reason,
        "safety_notice": "本系统只生成待确认交易单，不会也不能代替你在支付宝提交申购/赎回。请在支付宝页面手动核对并确认。",
    }


def calculate_sell_amount(current_position: float, target_amount: float, market: dict[str, Any], params: dict[str, Any]) -> float:
    rules = params.get("sell_rules", {})
    if not rules.get("enabled", True) or current_position <= 0:
        return 0.0
    excess = max(current_position - target_amount, 0)
    score = int(market.get("score", 50))
    rsi = float(market.get("rsi14", 50))
    drawdown = float(market.get("drawdown_from_252d_high_pct", -99))
    should_trim = (
        excess > 0
        and (
            score <= int(rules.get("score_sell_threshold", 38))
            or (rsi >= float(rules.get("rsi_take_profit", 82)) and drawdown >= float(rules.get("near_high_drawdown_pct", -1)))
        )
    )
    if not should_trim:
        return 0.0
    sell_by_excess = excess * float(rules.get("trim_excess_fraction", 0.35))
    sell_cap = current_position * float(rules.get("max_sell_position_fraction", 0.20))
    return round(min(sell_by_excess, sell_cap), 2)


def build_holdings_snapshot(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots = []
    for holding in holdings:
        code = str(holding.get("code", "")).strip()
        if not code:
            continue
        snapshot = fetch_fund_snapshot(
            {
                "code": code,
                "name": holding.get("name") or code,
            }
        )
        shares = float(holding.get("shares", 0) or 0)
        amount = float(holding.get("amount_cny", 0) or 0)
        cost_nav = holding.get("cost_nav")
        nav = snapshot.nav
        market_value = round(shares * nav, 2) if shares and nav else amount
        cost_value = round(shares * float(cost_nav), 2) if shares and cost_nav else amount
        pnl = round(market_value - cost_value, 2) if market_value and cost_value else None
        pnl_pct = round((market_value / cost_value - 1) * 100, 2) if market_value and cost_value else None
        snapshots.append(
            {
                "code": code,
                "name": holding.get("name") or snapshot.name,
                "amount_cny": amount,
                "shares": shares,
                "cost_nav": float(cost_nav) if cost_nav not in (None, "") else None,
                "nav": nav,
                "nav_date": snapshot.nav_date,
                "estimate": snapshot.estimate,
                "estimate_time": snapshot.estimate_time,
                "market_value_cny": market_value,
                "pnl_cny": pnl,
                "pnl_pct": pnl_pct,
                "source": snapshot.source,
                "note": snapshot.note,
            }
        )
    return snapshots


def advice_log_path(config: dict[str, Any]) -> Path:
    return Path(config.get("data_dir", str(DEFAULT_DATA_DIR))) / ADVICE_LOG_FILE


def advice_record_from_report(report: dict[str, Any]) -> dict[str, Any]:
    market = report["market"]
    decision = report["decision"]
    return {
        "model_version": report.get("model_version", MODEL_VERSION),
        "model_params_version": report.get("model_params_version"),
        "generated_at": report["generated_at"],
        "market_date": market["date"],
        "qqq_close": market["close"],
        "score": market["score"],
        "action": decision["action"],
        "buy_now_cny": decision["buy_now_cny"],
        "sell_now_cny": decision.get("sell_now_cny", 0),
        "target_nasdaq_weight_pct": report["target_nasdaq_weight_pct"],
        "target_nasdaq_amount_cny": report["target_nasdaq_amount_cny"],
        "remaining_plan_cny": report["remaining_plan_cny"],
        "features": {
            "ma20": market["ma20"],
            "ma60": market["ma60"],
            "ma200": market["ma200"],
            "rsi14": market["rsi14"],
            "drawdown_from_252d_high_pct": market["drawdown_from_252d_high_pct"],
            "distance_from_ma20_pct": market["distance_from_ma20_pct"],
            "distance_from_ma60_pct": market.get("distance_from_ma60_pct"),
            "distance_from_ma200_pct": market.get("distance_from_ma200_pct"),
            "momentum_5d_pct": market.get("momentum_5d_pct"),
            "momentum_20d_pct": market.get("momentum_20d_pct"),
            "momentum_60d_pct": market.get("momentum_60d_pct"),
            "ma20_slope_pct": market.get("ma20_slope_pct"),
            "ma60_slope_pct": market.get("ma60_slope_pct"),
            "annualized_vol20_pct": market["annualized_vol20_pct"],
            "annualized_vol60_pct": market.get("annualized_vol60_pct"),
            "factor_contributions": market.get("factor_contributions", []),
            "base_score_before_context": market.get("base_score_before_context"),
            "context_adjustments": market.get("context_adjustments", []),
        },
        "profile": report["profile"],
        "execution_plan": report.get("execution_plan"),
        "macro_context": market.get("macro_context"),
        "model_params": report.get("model_params"),
        "fund_candidates": report["fund_candidates"],
        "holdings": report.get("holdings", []),
        "manual_order": report.get("manual_order"),
    }


def upsert_advice_record(config: dict[str, Any], report: dict[str, Any]) -> None:
    path = advice_log_path(config)
    history = load_json(path, [])
    record = advice_record_from_report(report)
    market_date = record["market_date"]
    replaced = False
    for idx, old in enumerate(history):
        if old.get("market_date") == market_date:
            history[idx] = record
            replaced = True
            break
    if not replaced:
        history.append(record)
    history.sort(key=lambda item: item.get("market_date", ""))
    save_json(path, history)


def upsert_manual_order(config: dict[str, Any], report: dict[str, Any]) -> None:
    order = report.get("manual_order") or {}
    if order.get("status") != "pending_manual_confirmation":
        return
    path = manual_orders_path(config)
    orders = load_json(path, [])
    order_id = f"{order['order_date']}-{order['fund_code']}-{order['type']}"
    record = {
        "id": order_id,
        "created_at": report["generated_at"],
        "market_date": report["market"]["date"],
        "status": "pending_manual_confirmation",
        **order,
    }
    replaced = False
    for idx, old in enumerate(orders):
        if old.get("id") == order_id:
            orders[idx] = {**old, **record}
            replaced = True
            break
    if not replaced:
        orders.append(record)
    orders.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    save_json(path, orders)


def load_manual_orders(config: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    orders = load_json(manual_orders_path(config), [])
    orders.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    if limit is not None:
        return orders[:limit]
    return orders


def load_actual_trades(config: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    trades = load_json(actual_trades_path(config), [])
    trades.sort(key=lambda item: item.get("trade_date", item.get("created_at", "")), reverse=True)
    if limit is not None:
        return trades[:limit]
    return trades


def append_actual_trade(config: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
    trades = load_json(actual_trades_path(config), [])
    record = {
        "id": trade.get("id") or f"{date.today().isoformat()}-{int(time.time())}",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "manual_user_confirmation",
        **trade,
    }
    trades.append(record)
    save_json(actual_trades_path(config), trades)
    return record


def load_advice_history(config: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    history = load_json(advice_log_path(config), [])
    history.sort(key=lambda item: item.get("market_date", ""), reverse=True)
    if limit is not None:
        return history[:limit]
    return history


def trading_day_return(bars: list[Bar], start_day: str, horizon: int) -> dict[str, Any] | None:
    index_by_day = {bar.day.isoformat(): idx for idx, bar in enumerate(bars)}
    start_idx = index_by_day.get(start_day)
    if start_idx is None:
        return None
    end_idx = start_idx + horizon
    if end_idx >= len(bars):
        return None
    start = bars[start_idx]
    end = bars[end_idx]
    return {
        "start_date": start.day.isoformat(),
        "end_date": end.day.isoformat(),
        "start_close": round(start.close, 4),
        "end_close": round(end.close, 4),
        "return_pct": round((end.close / start.close - 1.0) * 100, 2),
    }


def evaluate_action(action: str, return_pct: float) -> dict[str, Any]:
    is_buy = action != "暂不买入"
    correct = return_pct > 0 if is_buy else return_pct <= 0
    if is_buy and correct:
        label = "买入后上涨"
    elif is_buy:
        label = "买入后下跌"
    elif correct:
        label = "避开下跌"
    else:
        label = "错过上涨"
    return {"correct": correct, "label": label}


def optimization_hints(evaluated: list[dict[str, Any]]) -> list[str]:
    complete_20 = [item for item in evaluated if item.get("outcomes", {}).get("20d")]
    if len(complete_20) < 5:
        return ["复盘样本还少，先连续记录至少 5 个具备20交易日结果的建议，再调整模型参数。"]

    missed_hot = 0
    bad_buys = 0
    for item in complete_20:
        outcome = item["outcomes"]["20d"]
        features = item.get("features", {})
        action = item.get("action")
        ret = outcome["return_pct"]
        if action == "暂不买入" and ret > 3 and features.get("rsi14", 0) >= 70:
            missed_hot += 1
        if action != "暂不买入" and ret < -3:
            bad_buys += 1

    hints = []
    if missed_hot >= 3:
        hints.append("多次在高RSI强趋势中错过上涨，可以考虑降低“RSI过热”的扣分，或允许小额试探。")
    if bad_buys >= 3:
        hints.append("多次买入后20日下跌，可以提高回撤触发门槛，或减少小额试探比例。")
    if not hints:
        hints.append("目前没有明显需要改参数的信号，继续积累样本更稳。")
    return hints


def load_latest_report_bars(config: dict[str, Any]) -> tuple[list[Bar], str] | None:
    data_dir = Path(config.get("data_dir", "data"))
    candidates = sorted(data_dir.glob("report-*.json"), reverse=True)
    for path in candidates:
        report = load_json(path, None)
        if not isinstance(report, dict):
            continue
        history = report.get("market", {}).get("history")
        if not isinstance(history, list):
            continue
        bars = deserialize_bars(history)
        if bars:
            return bars, f"cached_report_history:{path.name}"
    return None


def evaluate_advice_history(config: dict[str, Any]) -> dict[str, Any]:
    try:
        bars, source = fetch_qqq_bars(qqq_bars_cache_path(config))
    except Exception as exc:  # noqa: BLE001 - use report history when quote sites are unavailable
        cached = load_latest_report_bars(config)
        if cached is None:
            raise
        bars, source = cached
        source = f"{source}; remote_error={exc}"
    history = list(reversed(load_advice_history(config)))
    evaluated: list[dict[str, Any]] = []

    for record in history:
        outcomes: dict[str, Any] = {}
        for horizon in EVALUATION_HORIZONS:
            result = trading_day_return(bars, record["market_date"], horizon)
            if result is None:
                outcomes[f"{horizon}d"] = None
                continue
            action_eval = evaluate_action(record["action"], result["return_pct"])
            outcomes[f"{horizon}d"] = {**result, **action_eval}
        evaluated.append({**record, "outcomes": outcomes})

    summary: dict[str, Any] = {"source": source, "horizons": {}}
    for horizon in EVALUATION_HORIZONS:
        key = f"{horizon}d"
        complete = [item["outcomes"][key] for item in evaluated if item["outcomes"].get(key)]
        correct = [item for item in complete if item["correct"]]
        summary["horizons"][key] = {
            "completed": len(complete),
            "accuracy_pct": round(len(correct) / len(complete) * 100, 2) if complete else None,
            "average_forward_return_pct": round(statistics.fmean([item["return_pct"] for item in complete]), 2)
            if complete
            else None,
        }

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": summary,
        "optimization_hints": optimization_hints(evaluated),
        "items": list(reversed(evaluated)),
    }


def load_latest_report_market(config: dict[str, Any]) -> dict[str, Any] | None:
    data_dir = Path(config.get("data_dir", "data"))
    candidates = sorted(data_dir.glob("report-*.json"), reverse=True)
    for path in candidates:
        report = load_json(path, None)
        if not isinstance(report, dict) or not isinstance(report.get("market"), dict):
            continue
        market = dict(report["market"])
        if all(key in market for key in ("date", "close", "ma20", "ma60", "ma200", "rsi14", "score", "action")):
            source = market.get("source", "unknown")
            market["source"] = f"cached_report:{source}"
            market["data_warning"] = f"远端行情获取失败，沿用本地报告 {path.name} 的 QQQ 市场数据。"
            return market
    return None


def generate_report(config: dict[str, Any], persist: bool = True, output_path: Path | None = None) -> dict[str, Any]:
    params = load_model_params(config)
    macro = load_macro_context(config)
    try:
        bars, source = fetch_qqq_bars(qqq_bars_cache_path(config))
        external_series = fetch_external_factor_series()
        market = score_market(bars, params, external_features_for_day(external_series, bars[-1].day))
        market["source"] = source
        market["history"] = [{"date": bar.day.isoformat(), "close": round(bar.close, 4)} for bar in bars[-180:]]
        market = apply_context_adjustments(market, macro, params)
    except Exception as exc:  # noqa: BLE001 - keep the dashboard usable with stale local data
        market = load_latest_report_market(config)
        if market is None:
            raise
        warnings = list(market.get("warnings", []))
        warnings.append(str(exc))
        market["warnings"] = warnings

    funds = [fetch_fund_snapshot(fund) for fund in config.get("funds", DEFAULT_FUNDS)]
    report = build_recommendation(config, market, funds, params)

    if persist:
        data_dir = Path(config.get("data_dir", "data"))
        save_json(data_dir / f"report-{date.today().isoformat()}.json", report)
        upsert_advice_record(config, report)
        upsert_manual_order(config, report)
    if output_path:
        save_json(output_path, report)
    return report


def print_report(report: dict[str, Any]) -> None:
    market = report["market"]
    decision = report["decision"]
    execution = report.get("execution_plan", {})
    print(f"生成时间: {report['generated_at']}")
    print(f"QQQ日期/收盘: {market['date']} / {market['close']} ({market['source']})")
    if market.get("data_warning"):
        print(f"数据提示: {market['data_warning']}")
    for warning in market.get("warnings", []):
        print(f"数据源错误: {warning}")
    print(
        "指标: "
        f"MA20={market['ma20']}, MA60={market['ma60']}, MA200={market['ma200']}, "
        f"RSI14={market['rsi14']}, 近252日高点回撤={market['drawdown_from_252d_high_pct']}%"
    )
    print(f"评分: {market['score']}/100")
    print(f"动作: {decision['action']}")
    print(f"本次建议买入: {decision['buy_now_cny']} 元")
    print(f"本次建议卖出: {decision.get('sell_now_cny', 0)} 元")
    print(f"理由: {decision['reason']}")
    if market.get("context_adjustments"):
        print("上下文调整:")
        for item in market["context_adjustments"]:
            print(f"- {item.get('summary')} ({item.get('points')}分)")
    if execution:
        print(
            f"支付宝执行估算: {execution.get('assumed_rule')}，"
            f"下单日 {execution.get('order_date')}，估算确认/净值日 {execution.get('estimated_nav_date')}"
        )
    print(
        f"纳指目标仓位: {report['target_nasdaq_weight_pct']}% / "
        f"{report['target_nasdaq_amount_cny']} 元；剩余计划 {report['remaining_plan_cny']} 元"
    )
    print("\n候选基金:")
    for fund in report["fund_candidates"]:
        nav = fund["nav"] if fund["nav"] is not None else "N/A"
        estimate = fund["estimate"] if fund["estimate"] is not None else "N/A"
        when = fund["estimate_time"] or fund["nav_date"] or "N/A"
        print(f"- {fund['code']} {fund['name']} | 净值={nav} 估值={estimate} 时间={when} 来源={fund['source']}")
    print("\n下单前手动检查:")
    for item in report["manual_checks_before_order"]:
        print(f"- {item}")


def init_config(path: Path) -> None:
    if path.exists():
        raise SystemExit(f"配置文件已存在: {path}")
    sample = {
        "profile": {
            "total_investable_cny": 100000,
            "risk_level": "balanced",
            "horizon_years": 3,
            "max_acceptable_loss_pct": 20,
        },
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
    save_json(path, sample)
    print(f"已创建配置: {path}")
    print("请先修改 total_investable_cny、risk_level、horizon_years、max_acceptable_loss_pct。")


def run(config_path: Path, output_path: Path | None) -> int:
    config = load_config(config_path)
    report = generate_report(config, persist=True, output_path=output_path)
    print_report(report)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="QQQ/Nasdaq-100 daily allocation advisor")
    parser.add_argument("command", choices=["init", "run", "evaluate"], help="init 创建配置；run 生成每日建议；evaluate 复盘历史建议")
    parser.add_argument("--config", default=os.environ.get("QQQ_ADVISOR_CONFIG", "config.json"), help="配置文件路径")
    parser.add_argument("--output", default=None, help="额外保存报告 JSON 的路径")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if args.command == "init":
        init_config(config_path)
        return 0
    if args.command == "run":
        return run(config_path, Path(args.output) if args.output else None)
    if args.command == "evaluate":
        config = load_config(config_path)
        evaluation = evaluate_advice_history(config)
        if args.output:
            save_json(Path(args.output), evaluation)
        print(json.dumps(evaluation["summary"], ensure_ascii=False, indent=2))
        print("\n优化提示:")
        for hint in evaluation["optimization_hints"]:
            print(f"- {hint}")
        return 0
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except urllib.error.URLError as exc:
        print(f"网络错误: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:  # noqa: BLE001
        print(f"运行失败: {exc}", file=sys.stderr)
        raise SystemExit(1)
