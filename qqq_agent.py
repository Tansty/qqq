#!/usr/bin/env python3
"""Self-improving local agent for the QQQ advisor.

The agent keeps all durable data in local JSON files. It can optionally call
Qwen through an OpenAI-compatible endpoint for analysis text, but parameter
promotion remains rule-gated and backtest-gated.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.request
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from qqq_advisor import (
    DEFAULT_DATA_DIR,
    DEFAULT_MODEL_PARAMS,
    evaluate_action,
    evaluate_advice_history,
    fetch_qqq_bars,
    fetch_external_factor_series,
    external_features_for_day,
    generate_report,
    load_config,
    load_json,
    load_macro_context,
    load_model_params,
    model_params_path,
    save_json,
    score_from_features,
    score_market,
)


AGENT_STATE_FILE = "agent_state.json"
EVOLUTION_LOG_FILE = "evolution_log.json"
TRAINING_LOG_FILE = "training_log.json"
DEFAULT_QWEN_MODEL = "qwen3.6-plus-2026-04-02"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
MAX_LLM_HISTORY_ITEMS = 8
MAX_LLM_MACRO_EVENTS = 5
EXPLORATION_SEED = 20260511
ACTION_EXPOSURE = {
    "暂不买入": 0.0,
    "小额试探": 0.35,
    "正常买入": 0.7,
    "加仓": 1.0,
}
ALLOWED_FACTOR_FIELDS = {
    "close",
    "ma20",
    "ma60",
    "ma200",
    "rsi14",
    "drawdown_from_252d_high_pct",
    "distance_from_ma20_pct",
    "distance_from_ma60_pct",
    "distance_from_ma200_pct",
    "momentum_5d_pct",
    "momentum_20d_pct",
    "momentum_60d_pct",
    "ma20_slope_pct",
    "ma60_slope_pct",
    "annualized_vol20_pct",
    "annualized_vol60_pct",
    "vix_close",
    "vix_momentum_20d_pct",
    "us10y_close",
    "us10y_momentum_20d_pct",
    "dxy_close",
    "dxy_momentum_20d_pct",
    "qqq_volume",
    "qqq_volume_ratio20",
    "qqq_volume_trend20_pct",
}


def data_dir(config: dict[str, Any]) -> Path:
    return Path(config.get("data_dir", str(DEFAULT_DATA_DIR)))


def agent_state_path(config: dict[str, Any]) -> Path:
    return data_dir(config) / AGENT_STATE_FILE


def evolution_log_path(config: dict[str, Any]) -> Path:
    return data_dir(config) / EVOLUTION_LOG_FILE


def training_log_path(config: dict[str, Any]) -> Path:
    return data_dir(config) / TRAINING_LOG_FILE


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def append_json_log(path: Path, record: dict[str, Any]) -> None:
    records = load_json(path, [])
    records.append(record)
    save_json(path, records)


def completed_items(evaluation: dict[str, Any], horizon: str = "20d") -> list[dict[str, Any]]:
    return [item for item in evaluation.get("items", []) if item.get("outcomes", {}).get(horizon)]


def features_for_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item.get("features", {}),
        "close": item.get("qqq_close"),
    }


def evaluate_params(evaluation: dict[str, Any], params: dict[str, Any], horizon: str = "20d") -> dict[str, Any]:
    items = completed_items(evaluation, horizon)
    results = []
    for item in items:
        scored = score_from_features(features_for_item(item), params)
        outcome = item["outcomes"][horizon]
        judged = evaluate_action(scored["action"], outcome["return_pct"])
        results.append(
            {
                "market_date": item["market_date"],
                "action": scored["action"],
                "score": scored["score"],
                "return_pct": outcome["return_pct"],
                "correct": judged["correct"],
                "label": judged["label"],
                "rsi14": item.get("features", {}).get("rsi14"),
            }
        )

    correct = [item for item in results if item["correct"]]
    avg_return_when_buy = [
        item["return_pct"]
        for item in results
        if item["action"] != "暂不买入"
    ]
    strategy_returns = [ACTION_EXPOSURE.get(item["action"], 0.0) * item["return_pct"] for item in results]
    market_returns = [item["return_pct"] for item in results]
    participation = [ACTION_EXPOSURE.get(item["action"], 0.0) for item in results]
    strategy_return_pct = round(sum(strategy_returns) / len(strategy_returns), 2) if strategy_returns else None
    market_return_pct = round(sum(market_returns) / len(market_returns), 2) if market_returns else None
    participation_pct = round(sum(participation) / len(participation) * 100, 2) if participation else None
    accuracy_pct = round(len(correct) / len(results) * 100, 2) if results else None
    avg_buy_return = round(sum(avg_return_when_buy) / len(avg_return_when_buy), 2) if avg_return_when_buy else None
    objective_score = None
    if results:
        objective_score = round(
            (accuracy_pct or 0) * 0.35
            + (strategy_return_pct or 0) * 5.0
            + (avg_buy_return or 0) * 2.0
            + min(participation_pct or 0, 80) * 0.08,
            4,
        )
    return {
        "horizon": horizon,
        "completed": len(results),
        "accuracy_pct": accuracy_pct,
        "average_return_when_buy_pct": avg_buy_return,
        "strategy_return_pct": strategy_return_pct,
        "market_return_pct": market_return_pct,
        "participation_pct": participation_pct,
        "objective_score": objective_score,
        "results": results,
    }


def bumped_version(version: str) -> str:
    parts = version.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    except (ValueError, IndexError):
        return f"{version}.{int(time.time())}"


def propose_candidate(current: dict[str, Any], evaluation: dict[str, Any], horizon_key: str = "20d") -> tuple[dict[str, Any], list[str]]:
    candidate = deepcopy(current)
    reasons = []
    items = completed_items(evaluation, horizon_key)
    scoring = candidate["scoring"]
    thresholds = candidate["action_thresholds"]

    missed_hot = 0
    bad_buys = 0
    for item in items:
        scored = score_from_features(features_for_item(item), current)
        ret = item["outcomes"][horizon_key]["return_pct"]
        rsi = float(item.get("features", {}).get("rsi14", 0))
        if scored["action"] == "暂不买入" and ret > 3 and rsi >= 70:
            missed_hot += 1
        if scored["action"] != "暂不买入" and ret < -3:
            bad_buys += 1

    if missed_hot >= 3:
        scoring["rsi_hot_penalty"] = min(scoring["rsi_hot_penalty"] + 4, -8)
        scoring["rsi_warm_penalty"] = min(scoring["rsi_warm_penalty"] + 3, -4)
        thresholds["小额试探"] = max(thresholds["小额试探"] - 2, 35)
        candidate["trade_fractions"]["小额试探"] = min(candidate["trade_fractions"]["小额试探"] + 0.02, 0.18)
        reasons.append("20日复盘多次显示高RSI强趋势下错过上涨，候选参数降低RSI惩罚并略微放宽小额试探。")

    if bad_buys >= 3:
        thresholds["正常买入"] = min(thresholds["正常买入"] + 2, 72)
        thresholds["加仓"] = min(thresholds["加仓"] + 2, 88)
        candidate["trade_fractions"]["小额试探"] = max(candidate["trade_fractions"]["小额试探"] - 0.02, 0.06)
        candidate["trade_fractions"]["正常买入"] = max(candidate["trade_fractions"]["正常买入"] - 0.03, 0.12)
        reasons.append("20日复盘多次显示买入后下跌，候选参数提高买入门槛并降低单次买入比例。")

    if not reasons:
        reasons.append("没有发现足够强的系统性偏差，本轮不建议调整核心参数。")

    if reasons and not reasons[0].startswith("没有发现"):
        candidate["version"] = bumped_version(str(current.get("version", DEFAULT_MODEL_PARAMS["version"])))
        candidate["updated_at"] = now_iso()
        candidate["updated_by"] = "qqq_agent"

    return candidate, reasons


def candidate_weight_variants(current: dict[str, Any], limit: int = 24) -> list[tuple[dict[str, Any], str]]:
    factor_model = current.get("factor_model", {})
    factors = factor_model.get("factors", {})
    variants: list[tuple[dict[str, Any], str]] = []
    for name, factor in factors.items():
        if not factor.get("enabled", True):
            continue
        base_weight = float(factor.get("weight", 1.0))
        min_weight = float(factor.get("min_weight", 0.2))
        max_weight = float(factor.get("max_weight", 2.0))
        for delta in (-0.1, 0.1):
            new_weight = round(min(max(base_weight + delta, min_weight), max_weight), 4)
            if new_weight == base_weight:
                continue
            candidate = deepcopy(current)
            candidate["factor_model"]["factors"][name]["weight"] = new_weight
            candidate["version"] = bumped_version(str(current.get("version", DEFAULT_MODEL_PARAMS["version"])))
            candidate["updated_at"] = now_iso()
            candidate["updated_by"] = "qqq_agent_factor_explorer"
            variants.append((candidate, f"探索因子 {name} 权重 {base_weight} -> {new_weight}"))

    rng = random.Random(EXPLORATION_SEED + int(time.time() // 86400))
    rng.shuffle(variants)
    return variants[:limit]


def candidate_threshold_variants(current: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    variants = []
    threshold_steps = {
        "小额试探": [-6, -3, 3],
        "正常买入": [-5, -2, 2, 5],
        "加仓": [-4, 4],
    }
    for name, deltas in threshold_steps.items():
        for delta in deltas:
            candidate = deepcopy(current)
            old = int(candidate["action_thresholds"][name])
            candidate["action_thresholds"][name] = old + delta
            if candidate["action_thresholds"]["小额试探"] >= candidate["action_thresholds"]["正常买入"]:
                continue
            if candidate["action_thresholds"]["正常买入"] >= candidate["action_thresholds"]["加仓"]:
                continue
            candidate["version"] = bumped_version(str(current.get("version", DEFAULT_MODEL_PARAMS["version"])))
            candidate["updated_at"] = now_iso()
            candidate["updated_by"] = "qqq_agent_threshold_explorer"
            variants.append((candidate, f"探索动作阈值 {name} {old} -> {old + delta}"))
    return variants


def candidate_trade_fraction_variants(current: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    variants = []
    bounds = {
        "小额试探": (0.04, 0.25),
        "正常买入": (0.10, 0.45),
        "加仓": (0.15, 0.65),
    }
    for action, (low, high) in bounds.items():
        old = float(current["trade_fractions"][action])
        for delta in (-0.05, 0.05):
            new = round(min(max(old + delta, low), high), 4)
            if new == old:
                continue
            candidate = deepcopy(current)
            candidate["trade_fractions"][action] = new
            candidate["version"] = bumped_version(str(current.get("version", DEFAULT_MODEL_PARAMS["version"])))
            candidate["updated_at"] = now_iso()
            candidate["updated_by"] = "qqq_agent_trade_fraction_explorer"
            variants.append((candidate, f"探索交易比例 {action} {old} -> {new}"))
    return variants


def candidate_random_combo_variants(current: dict[str, Any], limit: int = 60) -> list[tuple[dict[str, Any], str]]:
    rng = random.Random(EXPLORATION_SEED + 17)
    factor_names = [name for name, factor in current.get("factor_model", {}).get("factors", {}).items() if factor.get("enabled", True)]
    variants = []
    for idx in range(limit):
        candidate = deepcopy(current)
        changed = []
        for name in rng.sample(factor_names, k=min(len(factor_names), rng.randint(1, 3))):
            factor = candidate["factor_model"]["factors"][name]
            old = float(factor.get("weight", 1.0))
            delta = rng.choice([-0.2, -0.1, 0.1, 0.2])
            new = round(min(max(old + delta, float(factor.get("min_weight", 0.2))), float(factor.get("max_weight", 2.0))), 4)
            factor["weight"] = new
            changed.append(f"{name}:{old}->{new}")
        if rng.random() < 0.7:
            old = int(candidate["action_thresholds"]["小额试探"])
            candidate["action_thresholds"]["小额试探"] = max(25, min(58, old + rng.choice([-8, -5, -3, 3, 5, 8])))
            changed.append(f"小额试探阈值:{old}->{candidate['action_thresholds']['小额试探']}")
        if rng.random() < 0.45:
            old = int(candidate["action_thresholds"]["正常买入"])
            candidate["action_thresholds"]["正常买入"] = max(45, min(76, old + rng.choice([-6, -3, 3, 6])))
            changed.append(f"正常买入阈值:{old}->{candidate['action_thresholds']['正常买入']}")
        if rng.random() < 0.35:
            action = rng.choice(["小额试探", "正常买入", "加仓"])
            old = float(candidate["trade_fractions"][action])
            bounds = {"小额试探": (0.02, 0.3), "正常买入": (0.08, 0.5), "加仓": (0.15, 0.75)}
            low, high = bounds[action]
            new = round(min(max(old + rng.choice([-0.08, -0.04, 0.04, 0.08]), low), high), 4)
            candidate["trade_fractions"][action] = new
            changed.append(f"{action}比例:{old}->{new}")
        if candidate["action_thresholds"]["小额试探"] >= candidate["action_thresholds"]["正常买入"]:
            continue
        candidate["version"] = bumped_version(str(current.get("version", DEFAULT_MODEL_PARAMS["version"])))
        candidate["updated_at"] = now_iso()
        candidate["updated_by"] = "qqq_agent_random_combo_explorer"
        variants.append((candidate, "组合探索 " + "; ".join(changed)))
    return variants


def safe_factor_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name.strip().lower())
    return cleaned[:48] or f"factor_{int(time.time())}"


def validate_factor_spec(name: str, spec: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    factor_type = spec.get("type")
    if factor_type not in {"threshold", "range", "threshold_tiers", "all_conditions", "above_below"}:
        return None
    normalized = deepcopy(spec)
    normalized["enabled"] = bool(normalized.get("enabled", True))
    normalized["weight"] = float(normalized.get("weight", 1.0))
    normalized["min_weight"] = float(normalized.get("min_weight", 0.3))
    normalized["max_weight"] = float(normalized.get("max_weight", 1.7))
    normalized["weight"] = min(max(normalized["weight"], normalized["min_weight"]), normalized["max_weight"])

    def check_field(field: str | None) -> bool:
        return bool(field and field in ALLOWED_FACTOR_FIELDS)

    if factor_type == "above_below":
        if not check_field(normalized.get("left")) or not check_field(normalized.get("right")):
            return None
        normalized["above_points"] = max(min(float(normalized.get("above_points", 0)), 25), -25)
        normalized["below_points"] = max(min(float(normalized.get("below_points", 0)), 25), -25)
    elif factor_type in {"threshold", "range", "threshold_tiers"}:
        if not check_field(normalized.get("field")):
            return None
        if factor_type == "threshold":
            if normalized.get("op") not in {">", ">=", "<", "<=", "=="}:
                return None
            normalized["points"] = max(min(float(normalized.get("points", 0)), 25), -25)
        elif factor_type == "range":
            normalized["points"] = max(min(float(normalized.get("points", 0)), 25), -25)
        else:
            tiers = []
            for tier in normalized.get("tiers", [])[:5]:
                if tier.get("op") not in {">", ">=", "<", "<=", "=="}:
                    continue
                tiers.append(
                    {
                        "op": tier["op"],
                        "value": float(tier.get("value", 0)),
                        "points": max(min(float(tier.get("points", 0)), 25), -25),
                    }
                )
            if not tiers:
                return None
            normalized["tiers"] = tiers
    elif factor_type == "all_conditions":
        conditions = []
        for condition in normalized.get("conditions", [])[:4]:
            if not check_field(condition.get("field")) or condition.get("op") not in {">", ">=", "<", "<=", "=="}:
                continue
            item = {"field": condition["field"], "op": condition["op"]}
            if condition.get("other_field"):
                if not check_field(condition["other_field"]):
                    continue
                item["other_field"] = condition["other_field"]
            else:
                item["value"] = float(condition.get("value", 0))
            conditions.append(item)
        if not conditions:
            return None
        normalized["conditions"] = conditions
        normalized["points"] = max(min(float(normalized.get("points", 0)), 25), -25)
    return safe_factor_name(name), normalized


def local_factor_discovery_variants(current: dict[str, Any], evaluation: dict[str, Any], horizon_key: str, limit: int = 80) -> list[tuple[dict[str, Any], str]]:
    items = completed_items(evaluation, horizon_key)
    if not items:
        return []
    candidates = []
    fields = [
        "momentum_5d_pct",
        "momentum_20d_pct",
        "momentum_60d_pct",
        "distance_from_ma60_pct",
        "distance_from_ma200_pct",
        "ma20_slope_pct",
        "ma60_slope_pct",
        "annualized_vol60_pct",
        "vix_close",
        "vix_momentum_20d_pct",
        "us10y_close",
        "us10y_momentum_20d_pct",
        "dxy_close",
        "dxy_momentum_20d_pct",
        "qqq_volume_ratio20",
        "qqq_volume_trend20_pct",
    ]
    for field in fields:
        values = sorted(float(item["features"].get(field, 0) or 0) for item in items if item["features"].get(field) not in (None, ""))
        if not values:
            continue
        for q, op, points in ((0.25, "<=", -8), (0.75, ">=", 8)):
            threshold = values[min(max(int(len(values) * q), 0), len(values) - 1)]
            name = f"auto_{field}_{op.replace('=', 'e').replace('<', 'lt').replace('>', 'gt')}_{str(round(threshold, 2)).replace('-', 'n').replace('.', '_')}"
            spec = {
                "enabled": True,
                "type": "threshold",
                "field": field,
                "op": op,
                "value": threshold,
                "points": points,
                "weight": 1.0,
                "min_weight": 0.3,
                "max_weight": 1.7,
            }
            validated = validate_factor_spec(name, spec)
            if not validated:
                continue
            factor_name, factor = validated
            candidate = deepcopy(current)
            candidate.setdefault("factor_model", {}).setdefault("factors", {})[factor_name] = factor
            candidate["version"] = bumped_version(str(current.get("version", DEFAULT_MODEL_PARAMS["version"])))
            candidate["updated_at"] = now_iso()
            candidate["updated_by"] = "qqq_agent_local_factor_discovery"
            candidates.append((candidate, f"本地发现候选因子 {factor_name}"))
    return candidates[:limit]


def metric_value(score: dict[str, Any], optimize: str) -> float:
    if optimize == "accuracy":
        return float(score.get("accuracy_pct") or -9999)
    if optimize == "strategy_return":
        return float(score.get("strategy_return_pct") or -9999)
    return float(score.get("objective_score") or -9999)


def explore_factor_weights(
    current: dict[str, Any],
    evaluation: dict[str, Any],
    horizon_key: str = "20d",
    search_iterations: int = 120,
    optimize: str = "objective",
    extra_variants: list[tuple[dict[str, Any], str]] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    baseline = evaluate_params(evaluation, current, horizon_key)
    if baseline["completed"] == 0:
        return None, {"baseline": baseline, "tested": 0, "best": None}

    best_candidate = None
    best_reason = None
    best_score = baseline
    tested = 0
    variants = (
        candidate_weight_variants(current, limit=40)
        + candidate_threshold_variants(current)
        + candidate_trade_fraction_variants(current)
        + candidate_random_combo_variants(current, limit=search_iterations)
        + local_factor_discovery_variants(current, evaluation, horizon_key)
        + (extra_variants or [])
    )
    for candidate, reason in variants:
        tested += 1
        if candidate == current:
            continue
        score = evaluate_params(evaluation, candidate, horizon_key)
        score_obj = metric_value(score, optimize)
        best_obj = metric_value(best_score, optimize)
        if score_obj > best_obj and candidate != current:
            best_candidate = candidate
            best_reason = reason
            best_score = score

    return best_candidate, {
        "baseline": {k: v for k, v in baseline.items() if k != "results"},
        "tested": tested,
        "best": {
            "reason": best_reason,
            "score": {k: v for k, v in best_score.items() if k != "results"},
            "version": best_candidate.get("version") if best_candidate else None,
        }
        if best_candidate
        else None,
    }


def build_training_evaluation(config: dict[str, Any], lookback: int = 100, horizon: int = 20) -> dict[str, Any]:
    params = load_model_params(config)
    bars, source = fetch_qqq_bars()
    external_series = fetch_external_factor_series()
    horizon_key = f"{horizon}d"
    latest_labeled_idx = len(bars) - horizon - 1
    start_idx = max(219, latest_labeled_idx - lookback + 1)
    if latest_labeled_idx < start_idx:
        raise RuntimeError("可训练样本不足：需要至少 220 日历史和足够未来收益标签")

    items = []
    for idx in range(start_idx, latest_labeled_idx + 1):
        start = bars[idx]
        market = score_market(
            bars[: idx + 1],
            params,
            external_features_for_day(external_series, start.day),
        )
        end = bars[idx + horizon]
        return_pct = round((end.close / start.close - 1.0) * 100, 2)
        judged = evaluate_action(market["action"], return_pct)
        item = {
            "model_version": "training",
            "generated_at": now_iso(),
            "market_date": start.day.isoformat(),
            "qqq_close": round(start.close, 4),
            "score": market["score"],
            "action": market["action"],
            "buy_now_cny": 0,
            "sell_now_cny": 0,
            "features": {
                "ma20": market["ma20"],
                "ma60": market["ma60"],
                "ma200": market["ma200"],
                "rsi14": market["rsi14"],
                "drawdown_from_252d_high_pct": market["drawdown_from_252d_high_pct"],
                "distance_from_ma20_pct": market["distance_from_ma20_pct"],
                "distance_from_ma60_pct": market["distance_from_ma60_pct"],
                "distance_from_ma200_pct": market["distance_from_ma200_pct"],
                "momentum_5d_pct": market["momentum_5d_pct"],
                "momentum_20d_pct": market["momentum_20d_pct"],
                "momentum_60d_pct": market["momentum_60d_pct"],
                "ma20_slope_pct": market["ma20_slope_pct"],
                "ma60_slope_pct": market["ma60_slope_pct"],
                "annualized_vol20_pct": market["annualized_vol20_pct"],
                "annualized_vol60_pct": market["annualized_vol60_pct"],
                "vix_close": market.get("vix_close"),
                "vix_momentum_20d_pct": market.get("vix_momentum_20d_pct"),
                "us10y_close": market.get("us10y_close"),
                "us10y_momentum_20d_pct": market.get("us10y_momentum_20d_pct"),
                "dxy_close": market.get("dxy_close"),
                "dxy_momentum_20d_pct": market.get("dxy_momentum_20d_pct"),
                "qqq_volume": market.get("qqq_volume"),
                "qqq_volume_ratio20": market.get("qqq_volume_ratio20"),
                "qqq_volume_trend20_pct": market.get("qqq_volume_trend20_pct"),
            },
            "outcomes": {
                horizon_key: {
                    "start_date": start.day.isoformat(),
                    "end_date": end.day.isoformat(),
                    "start_close": round(start.close, 4),
                    "end_close": round(end.close, 4),
                    "return_pct": return_pct,
                    **judged,
                }
            },
        }
        items.append(item)

    current_score = evaluate_params({"items": items}, params, horizon_key)
    return {
        "generated_at": now_iso(),
        "source": source,
        "lookback": lookback,
        "horizon": horizon,
        "horizon_key": horizon_key,
        "sample_start": items[0]["market_date"],
        "sample_end": items[-1]["market_date"],
        "summary": {
            "source": source,
            "horizons": {
                horizon_key: {
                    "completed": current_score["completed"],
                    "accuracy_pct": current_score["accuracy_pct"],
                    "average_forward_return_pct": None,
                }
            },
        },
        "items": items,
    }


def split_evaluation(evaluation: dict[str, Any], train_ratio: float = 0.7) -> tuple[dict[str, Any], dict[str, Any]]:
    items = evaluation["items"]
    split_at = max(1, min(len(items) - 1, int(len(items) * train_ratio)))
    base = {k: v for k, v in evaluation.items() if k != "items"}
    train_eval = {**base, "items": items[:split_at]}
    validation_eval = {**base, "items": items[split_at:]}
    return train_eval, validation_eval


def train_recent(
    config: dict[str, Any],
    lookback: int = 100,
    horizon: int = 20,
    apply: bool = True,
    search_iterations: int = 120,
    optimize: str = "objective",
    target_accuracy: float | None = None,
    use_qwen: bool = False,
) -> dict[str, Any]:
    params = load_model_params(config)
    evaluation = build_training_evaluation(config, lookback=lookback, horizon=horizon)
    horizon_key = evaluation["horizon_key"]
    train_eval, validation_eval = split_evaluation(evaluation)
    baseline = evaluate_params(evaluation, params, horizon_key)
    baseline_train = evaluate_params(train_eval, params, horizon_key)
    baseline_validation = evaluate_params(validation_eval, params, horizon_key)
    rule_candidate, reasons = propose_candidate(params, train_eval, horizon_key)
    extra_variants = qwen_factor_variants(config, rule_candidate, train_eval) if use_qwen else []
    explored_candidate, exploration = explore_factor_weights(
        rule_candidate,
        train_eval,
        horizon_key,
        search_iterations=search_iterations,
        optimize=optimize,
        extra_variants=extra_variants,
    )
    candidate = explored_candidate or rule_candidate
    if explored_candidate is not None:
        reasons.append(f"训练集因子权重探索发现候选：{exploration['best']['reason']}")
    candidate_score = evaluate_params(evaluation, candidate, horizon_key)
    candidate_train = evaluate_params(train_eval, candidate, horizon_key)
    candidate_validation = evaluate_params(validation_eval, candidate, horizon_key)

    baseline_obj = metric_value(baseline_validation, optimize)
    candidate_obj = metric_value(candidate_validation, optimize)
    reached_target = target_accuracy is None or (candidate_validation["accuracy_pct"] or 0) >= target_accuracy
    should_apply = apply and candidate != params and candidate_validation["completed"] > 0 and candidate_obj > baseline_obj and reached_target
    if should_apply:
        save_json(model_params_path(config), candidate)

    result = {
        "generated_at": now_iso(),
        "mode": "recent_window_training",
        "lookback": lookback,
        "horizon": horizon,
        "search_iterations": search_iterations,
        "optimize": optimize,
        "target_accuracy": target_accuracy,
        "qwen_factor_search": bool(use_qwen),
        "qwen_factor_variants": len(extra_variants),
        "sample_start": evaluation["sample_start"],
        "sample_end": evaluation["sample_end"],
        "baseline": {k: v for k, v in baseline.items() if k != "results"},
        "candidate": {k: v for k, v in candidate_score.items() if k != "results"},
        "train_split": {
            "baseline": {k: v for k, v in baseline_train.items() if k != "results"},
            "candidate": {k: v for k, v in candidate_train.items() if k != "results"},
        },
        "validation_split": {
            "baseline": {k: v for k, v in baseline_validation.items() if k != "results"},
            "candidate": {k: v for k, v in candidate_validation.items() if k != "results"},
        },
        "applied": should_apply,
        "reached_target": reached_target,
        "reasons": reasons,
        "factor_exploration": exploration,
        "params_version_before": params.get("version"),
        "params_version_after": candidate.get("version") if should_apply else params.get("version"),
    }
    append_json_log(training_log_path(config), result)
    return result


def compact_evaluation_for_llm(evaluation: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in evaluation.get("items", [])[:MAX_LLM_HISTORY_ITEMS]:
        features = item.get("features", {})
        outcomes = item.get("outcomes", {})
        items.append(
            {
                "d": item.get("market_date"),
                "a": item.get("action"),
                "s": item.get("score"),
                "rsi": features.get("rsi14"),
                "dd": features.get("drawdown_from_252d_high_pct"),
                "adj": features.get("context_adjustments", []),
                "r20": outcomes.get("20d", {}).get("return_pct") if outcomes.get("20d") else None,
                "ok20": outcomes.get("20d", {}).get("correct") if outcomes.get("20d") else None,
            }
        )
    return {"summary": evaluation.get("summary"), "recent": items}


def compact_macro_for_llm(config: dict[str, Any]) -> dict[str, Any]:
    macro = load_macro_context(config)
    events = []
    for event in macro.get("events", [])[:MAX_LLM_MACRO_EVENTS]:
        events.append(
            {
                "cat": event.get("category", "other"),
                "dir": event.get("direction", "neutral"),
                "sev": event.get("severity", 0),
                "txt": str(event.get("summary", ""))[:90],
                "exp": event.get("expires_at"),
            }
        )
    return {"updated_at": macro.get("updated_at"), "events": events}


def qwen_analysis(
    config: dict[str, Any],
    evaluation: dict[str, Any],
    current: dict[str, Any],
    candidate: dict[str, Any],
    reasons: list[str],
) -> str | None:
    api_key = os.environ.get("QWEN_API_KEY")
    if not api_key:
        return None

    payload = {
        "model": os.environ.get("QWEN_MODEL", DEFAULT_QWEN_MODEL),
        "messages": [
            {
                "role": "system",
                "content": "你是一个谨慎的量化投资模型复盘助手。只分析参数调整逻辑，不给确定收益承诺。",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "请用中文简短评估本轮QQQ择时模型参数是否值得调整。",
                        "token_policy": "只基于下列压缩JSON分析，不要求更多上下文。",
                        "evaluation": compact_evaluation_for_llm(evaluation),
                        "macro": compact_macro_for_llm(config),
                        "local_reasons": reasons,
                        "versions": {"current": current.get("version"), "candidate": candidate.get("version")},
                        "changed_params": diff_params_for_llm(current, candidate),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.2,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        os.environ.get("QWEN_BASE_URL", DEFAULT_QWEN_BASE_URL),
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def qwen_factor_proposals(config: dict[str, Any], evaluation: dict[str, Any], max_factors: int = 6) -> list[tuple[str, dict[str, Any], str]]:
    api_key = os.environ.get("QWEN_API_KEY")
    if not api_key:
        return []
    payload = {
        "model": os.environ.get("QWEN_MODEL", DEFAULT_QWEN_MODEL),
        "messages": [
            {
                "role": "system",
                "content": "你是量化因子设计助手。只返回JSON，不要解释。不得承诺收益。",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "基于压缩训练样本提出最多6个QQQ择时候选因子。只用允许字段。返回格式: {\"factors\":[{\"name\":\"...\",\"spec\":{...},\"reason\":\"...\"}]}",
                        "allowed_fields": sorted(ALLOWED_FACTOR_FIELDS),
                        "allowed_types": ["threshold", "range", "threshold_tiers", "all_conditions", "above_below"],
                        "constraints": {
                            "points_range": [-25, 25],
                            "weight_range": [0.3, 1.7],
                            "max_conditions": 4,
                            "max_tiers": 5,
                        },
                        "evaluation": compact_evaluation_for_llm(evaluation),
                        "macro": compact_macro_for_llm(config),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        os.environ.get("QWEN_BASE_URL", DEFAULT_QWEN_BASE_URL),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    proposals = []
    for item in parsed.get("factors", [])[:max_factors]:
        validated = validate_factor_spec(item.get("name", ""), item.get("spec", {}))
        if validated:
            name, spec = validated
            proposals.append((name, spec, str(item.get("reason", ""))[:120]))
    return proposals


def qwen_factor_variants(config: dict[str, Any], current: dict[str, Any], evaluation: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    variants = []
    try:
        proposals = qwen_factor_proposals(config, evaluation)
    except Exception as exc:  # noqa: BLE001
        return [(current, f"Qwen因子提案失败: {exc}")]
    for name, spec, reason in proposals:
        if name in current.get("factor_model", {}).get("factors", {}):
            continue
        candidate = deepcopy(current)
        candidate.setdefault("factor_model", {}).setdefault("factors", {})[name] = spec
        candidate["version"] = bumped_version(str(current.get("version", DEFAULT_MODEL_PARAMS["version"])))
        candidate["updated_at"] = now_iso()
        candidate["updated_by"] = "qwen_factor_proposal"
        variants.append((candidate, f"Qwen提案因子 {name}: {reason}"))
    return variants


def diff_params_for_llm(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for section in ("scoring", "action_thresholds", "trade_fractions", "execution_lag", "macro_scoring", "currency_macro_scoring"):
        section_changes = {}
        current_section = current.get(section, {})
        candidate_section = candidate.get(section, {})
        for key, value in candidate_section.items():
            if isinstance(value, (dict, list)):
                continue
            if current_section.get(key) != value:
                section_changes[key] = {"from": current_section.get(key), "to": value}
        if section_changes:
            changes[section] = section_changes
    factor_changes = {}
    current_factors = current.get("factor_model", {}).get("factors", {})
    candidate_factors = candidate.get("factor_model", {}).get("factors", {})
    for name, factor in candidate_factors.items():
        old = current_factors.get(name, {})
        if old.get("enabled") != factor.get("enabled") or old.get("weight") != factor.get("weight"):
            factor_changes[name] = {
                "enabled": {"from": old.get("enabled"), "to": factor.get("enabled")},
                "weight": {"from": old.get("weight"), "to": factor.get("weight")},
            }
    if factor_changes:
        changes["factor_model"] = factor_changes
    return changes


def evolve(config: dict[str, Any], use_qwen: bool = True) -> dict[str, Any]:
    params = load_model_params(config)
    evaluation = evaluate_advice_history(config)
    current_score = evaluate_params(evaluation, params, "20d")
    candidate, reasons = propose_candidate(params, evaluation, "20d")
    explored_candidate, exploration = explore_factor_weights(params, evaluation, "20d")
    if explored_candidate is not None:
        candidate = explored_candidate
        reasons.append(f"因子权重探索发现更优候选：{exploration['best']['reason']}")
    candidate_score = evaluate_params(evaluation, candidate, "20d")
    min_completed = int(params.get("min_completed_20d_for_evolution", 5))

    should_apply = False
    if current_score["completed"] >= min_completed and candidate != params:
        current_acc = current_score["accuracy_pct"] or 0
        candidate_acc = candidate_score["accuracy_pct"] or 0
        should_apply = candidate_acc > current_acc

    qwen_text = None
    if use_qwen:
        try:
            qwen_text = qwen_analysis(config, evaluation, params, candidate, reasons)
        except Exception as exc:  # noqa: BLE001
            qwen_text = f"Qwen分析调用失败: {exc}"

    decision = {
        "generated_at": now_iso(),
        "current_version": params.get("version"),
        "candidate_version": candidate.get("version"),
        "completed_20d": current_score["completed"],
        "min_completed_20d_for_evolution": min_completed,
        "current_score": {k: v for k, v in current_score.items() if k != "results"},
        "candidate_score": {k: v for k, v in candidate_score.items() if k != "results"},
        "factor_exploration": exploration,
        "applied": should_apply,
        "reasons": reasons,
        "qwen_analysis": qwen_text,
    }

    if should_apply:
        save_json(model_params_path(config), candidate)
        decision["applied_params_path"] = str(model_params_path(config))

    append_json_log(evolution_log_path(config), decision)
    save_json(
        agent_state_path(config),
        {
            "last_run_at": now_iso(),
            "last_decision": decision,
            "qwen_model": os.environ.get("QWEN_MODEL", DEFAULT_QWEN_MODEL),
            "qwen_enabled": bool(os.environ.get("QWEN_API_KEY")),
        },
    )
    return decision


def run_daily(config: dict[str, Any], use_qwen: bool = True) -> dict[str, Any]:
    report = generate_report(config, persist=True)
    evolution = evolve(config, use_qwen=use_qwen)
    return {"report": report, "evolution": evolution}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local self-improving QQQ advisor agent")
    parser.add_argument("command", choices=["daily", "evolve", "train", "status"], help="daily 生成建议并尝试进化；evolve 仅复盘调参；train 用历史窗口训练；status 查看agent状态")
    parser.add_argument("--config", default=os.environ.get("QQQ_ADVISOR_CONFIG", "config.json"))
    parser.add_argument("--no-qwen", action="store_true", help="不调用Qwen，只使用本地规则进化")
    parser.add_argument("--lookback", default=100, type=int, help="train 使用最近多少个可标注交易日")
    parser.add_argument("--horizon", default=20, type=int, help="train 使用多少交易日后的收益做标签")
    parser.add_argument("--no-apply", action="store_true", help="train 只评估候选参数，不写入 model_params.json")
    parser.add_argument("--search-iterations", default=120, type=int, help="train 随机组合探索次数")
    parser.add_argument("--optimize", choices=["objective", "accuracy", "strategy_return"], default="objective", help="train 优化目标")
    parser.add_argument("--target-accuracy", default=None, type=float, help="train 验证集目标准确率，未达到则不应用")
    parser.add_argument("--use-qwen-factors", action="store_true", help="train 允许Qwen提出候选因子，仍需本地验证通过才应用")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    if args.command == "daily":
        result = run_daily(config, use_qwen=not args.no_qwen)
        print(json.dumps(result["evolution"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "evolve":
        print(json.dumps(evolve(config, use_qwen=not args.no_qwen), ensure_ascii=False, indent=2))
        return 0
    if args.command == "train":
        print(
            json.dumps(
                train_recent(
                    config,
                    lookback=args.lookback,
                    horizon=args.horizon,
                    apply=not args.no_apply,
                    search_iterations=args.search_iterations,
                    optimize=args.optimize,
                    target_accuracy=args.target_accuracy,
                    use_qwen=args.use_qwen_factors and not args.no_qwen,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "status":
        print(json.dumps(load_json(agent_state_path(config), {}), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
