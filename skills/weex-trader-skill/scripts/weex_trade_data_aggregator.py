#!/usr/bin/env python3
"""Normalize WEEX trading data for replay, profile, and risk analysis."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from weex_profile_language import resolve_language


DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
REPLAY_PERIODS = ("7d", "30d", "90d")
PROFILE_PERIODS = ("30d", "90d", "180d", "360d")
COLLECTION_PERIODS = REPLAY_PERIODS + ("180d", "360d")
MAX_FUTURES_WINDOW_DAYS = 90
MAX_FUTURES_FILLS_WINDOW_DAYS = 7
MAX_BILLS_WINDOW_DAYS = 100
MIN_SPLIT_WINDOW_MS = HOUR_MS
POSITION_EPSILON = 0.00000001
FUTURES_ORDER_LIMIT = 1000
FUTURES_FILL_LIMIT = 100
FUTURES_BILL_LIMIT = 100
FUTURES_OPEN_ORDER_LIMIT = 100
FUTURES_PENDING_LIMIT = 100
SPOT_ORDER_LIMIT = 1000
SPOT_ORDER_SAFE_LIMIT = 100
MAX_SPOT_HISTORY_WINDOW_DAYS = 90
SPOT_FILL_LIMIT = 100
SPOT_BILL_LIMIT = 100
KLINE_LIMIT = 100
DEFAULT_TRADING_MODE = "live"
TRADING_MODES = ("live", "demo")
LONG_SIDES = {"long", "buy", "bull"}
SHORT_SIDES = {"short", "sell", "bear"}
SPOT_QUOTE_ASSET_FALLBACKS = ("USDT", "USDC", "BTC", "ETH")
SPOT_CASH_ASSETS = ("USDT", "USDC")
FUTURES_INCLUDED_BILL_TYPES = {
    "position_funding",
    "order_liquidate_fee_income",
    "start_liquidate",
    "finish_liquidate",
    "order_fix_margin_amount",
}
FUTURES_EXCLUDED_BILL_TYPES = {
    "deposit",
    "withdraw",
    "transfer_in",
    "transfer_out",
    "margin_move_in",
    "margin_move_out",
    "position_open_long",
    "position_open_short",
    "position_close_long",
    "position_close_short",
    "order_fill_fee_income",
}


class AggregationInputError(ValueError):
    """Raised when the requested aggregation shape is not supported."""


@dataclass(frozen=True)
class TimeWindow:
    start_ms: int
    end_ms: int


def split_time_range(start_ms: int, end_ms: int, *, max_span_days: int) -> list[TimeWindow]:
    if start_ms < 0:
        raise AggregationInputError("start_ms must be non-negative.")
    if end_ms < start_ms:
        raise AggregationInputError("end_ms must be greater than or equal to start_ms.")
    if max_span_days <= 0:
        raise AggregationInputError("max_span_days must be positive.")

    max_span_ms = max_span_days * DAY_MS
    windows: list[TimeWindow] = []
    cursor = start_ms
    while cursor <= end_ms:
        next_end = min(end_ms, cursor + max_span_ms - 1)
        windows.append(TimeWindow(start_ms=cursor, end_ms=next_end))
        cursor = next_end + 1
    return windows


def _validate_replay_period(period: str) -> str:
    normalized = str(period).strip().lower()
    if normalized not in COLLECTION_PERIODS:
        raise AggregationInputError(
            f"Unsupported replay period: {period}. Expected one of {', '.join(COLLECTION_PERIODS)}."
        )
    return normalized


def _validate_market(market: str) -> str:
    normalized = str(market).strip().lower()
    if normalized not in {"futures", "spot", "all"}:
        raise AggregationInputError("market must be one of futures, spot, or all.")
    return normalized


def _normalize_trading_mode(raw: Any) -> str:
    mode = str(raw or DEFAULT_TRADING_MODE).strip().lower()
    if mode not in TRADING_MODES:
        raise AggregationInputError(f"invalid_trading_mode: expected one of {', '.join(TRADING_MODES)}")
    return mode


def _validate_trading_mode_market(trading_mode: str, market: str) -> str:
    mode = _normalize_trading_mode(trading_mode)
    if mode == "demo" and market != "futures":
        raise AggregationInputError("demo_spot_unsupported: demo trading_mode is only supported for futures")
    return mode


def _environment_for_trading_mode(trading_mode: str, market: str) -> dict[str, Any]:
    mode = _normalize_trading_mode(trading_mode)
    if mode == "demo":
        return {
            "trading_mode": "demo",
            "label": "demo",
            "market": "futures",
            "uses_real_funds": False,
            "notice": "This operation targets WEEX futures demo mode.",
        }
    return {
        "trading_mode": "live",
        "label": "live",
        "market": market,
        "uses_real_funds": True,
        "notice": f"This operation targets real WEEX {market} trading.",
    }


def _user_environment_prefix(environment: dict[str, Any], language: str | None = None) -> str:
    resolved_language = resolve_language(language)
    mode = _normalize_trading_mode(environment.get("trading_mode"))
    if resolved_language == "zh":
        label = "模拟盘" if mode == "demo" else "真实盘"
        return f"当前交易环境：{label}"
    label = "demo trading" if mode == "demo" else "real trading"
    return f"Current trading mode: {label}"


def _normalize_demo_symbol_for_display(raw: Any) -> str:
    symbol = str(raw or "UNKNOWN").strip().upper()
    if symbol.endswith("SUSDT") and len(symbol) > len("SUSDT"):
        return f"{symbol[:-5]}USDT"
    return symbol or "UNKNOWN"


def _normalize_symbol_for_trading_mode(raw: Any, trading_mode: str) -> str:
    if _normalize_trading_mode(trading_mode) == "demo":
        return _normalize_demo_symbol_for_display(raw)
    return str(raw or "UNKNOWN")


def _period_to_days(period: str) -> int:
    return int(period.removesuffix("d"))


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _coerce_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _normalize_account_scope(
    market: str,
    mapping: dict[str, Any] | None = None,
    *,
    trading_mode: str = DEFAULT_TRADING_MODE,
) -> str:
    explicit = _pick(mapping or {}, "account_scope", "accountScope")
    if explicit not in (None, ""):
        return str(explicit)
    if _normalize_trading_mode(trading_mode) == "demo" and market == "futures":
        return "sim_futures"
    if market == "futures":
        return "personal_futures"
    if market == "spot":
        return "personal_spot"
    return f"personal_{market}"


def _normalize_margin_type(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().upper()
    return normalized or None


def _normalize_position_mode(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().upper()
    if normalized in {"ONE_WAY", "ONEWAY"}:
        return "COMBINED"
    if normalized == "HEDGE":
        return "SEPARATED"
    return normalized or None


def _extract_meta(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        meta = payload.get("_meta")
        if isinstance(meta, dict):
            return meta
    return {}


def _extract_list_payload(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extend_unique_dict_rows(target: list[dict[str, Any]], rows: list[dict[str, Any]], *, identity_keys: tuple[str, ...]) -> None:
    seen = {
        tuple(str(item.get(key) or "") for key in identity_keys)
        for item in target
    }
    for row in rows:
        identity = tuple(str(row.get(key) or "") for key in identity_keys)
        if identity in seen:
            continue
        seen.add(identity)
        target.append(row)


def _merge_degraded_reasons(target: list[str], reasons: list[str]) -> None:
    for reason in reasons:
        if reason and reason not in target:
            target.append(reason)


def _merge_constraints(target: list[dict[str, Any]], constraints: list[dict[str, Any]]) -> None:
    seen = {
        (str(item.get("code") or ""), str(item.get("message") or ""))
        for item in target
    }
    for item in constraints:
        code = str(item.get("code") or "")
        message = str(item.get("message") or "")
        identity = (code, message)
        if identity in seen:
            continue
        seen.add(identity)
        target.append({"code": code, "message": message})


def _should_retry_spot_history_orders_with_safe_limit(error: Exception, *, limit: int) -> bool:
    if limit <= SPOT_ORDER_SAFE_LIMIT:
        return False
    message = str(error).lower()
    return (
        "spot.order.history_orders" in message
        and ("unknown error occurred" in message or "'code': -1000" in message or '"code": -1000' in message)
    )


def _should_degrade_spot_balance_error(error: Exception) -> bool:
    message = str(error).lower()
    return (
        "spot.account.get_account_balance" in message
        and ("unknown error occurred" in message or "'code': -1000" in message or '"code": -1000' in message)
    )


def _should_degrade_spot_kline_error(error: Exception) -> bool:
    message = str(error).lower()
    return (
        "spot.market.get_k_line_data" in message
        and (
            "rate limit exceeded" in message
            or "'code': 429" in message
            or '"code": 429' in message
            or "unknown error occurred" in message
            or "'code': -1000" in message
            or '"code": -1000' in message
        )
    )


def _should_degrade_spot_bills_error(error: Exception) -> bool:
    message = str(error).lower()
    return (
        "spot.account.get_bill_records" in message
        and (
            "too many high-frequency order requests" in message
            or "'code': -1059" in message
            or '"code": -1059' in message
            or "unknown error occurred" in message
            or "'code': -1000" in message
            or '"code": -1000' in message
            or "rate limit exceeded" in message
            or "'code': 429" in message
            or '"code": 429' in message
        )
    )


def _sample_quality(closed_trade_count: int) -> str:
    if closed_trade_count >= 20:
        return "full"
    if closed_trade_count >= 10:
        return "limited"
    return "minimal"


def _normalize_bill_entry(row: dict[str, Any], *, fallback_market: str) -> dict[str, Any]:
    market = str(_pick(row, "market") or fallback_market or "").strip().lower() or fallback_market
    return {
        "market": market,
        "symbol": str(_pick(row, "symbol") or ""),
        "type": str(_pick(row, "type", "incomeType", "bizType") or "unknown").strip().lower(),
        "amount": _to_float(_pick(row, "amount", "income", "deltaAmount")),
        "fee": _to_float(_pick(row, "fee", "fillFee", "fees")),
        "time": _safe_int(_pick(row, "time", "cTime")),
    }


def _classify_bill_adjustment(bill: dict[str, Any]) -> str:
    market = str(bill.get("market") or "").strip().lower()
    bill_type = str(bill.get("type") or "").strip().lower()
    if not bill_type:
        return "unknown"
    if market == "spot":
        return "exclude"
    if market != "futures":
        return "unknown"
    if bill_type in FUTURES_INCLUDED_BILL_TYPES or bill_type.startswith("tracking_"):
        return "include"
    if bill_type in FUTURES_EXCLUDED_BILL_TYPES:
        return "exclude"
    return "unknown"


def _summarize_bill_adjustments(rows: list[dict[str, Any]], *, fallback_market: str) -> dict[str, Any]:
    adjustment_total = 0.0
    adjustment_count = 0
    unclassified_types: set[str] = set()

    for row in rows:
        bill = _normalize_bill_entry(row, fallback_market=fallback_market)
        amount = _to_float(bill.get("amount"))
        if amount in (None, 0.0):
            continue
        classification = _classify_bill_adjustment(bill)
        if classification == "include":
            adjustment_total += amount or 0.0
            adjustment_count += 1
        elif classification == "unknown":
            unclassified_types.add(str(bill.get("type") or "unknown"))

    return {
        "bill_adjustment_total": round(adjustment_total, 8),
        "bill_adjustment_count": adjustment_count,
        "unclassified_bill_types": sorted(unclassified_types),
    }


def _normalize_trade_position_side(raw_position_side: Any, *, market: str, fallback_side: Any = None) -> str | None:
    side_text = str(raw_position_side or "").strip().lower()
    if side_text in LONG_SIDES:
        return "long"
    if side_text in SHORT_SIDES:
        return "short"
    if market == "spot":
        return "long"

    fallback_text = str(fallback_side or "").strip().lower()
    if fallback_text in LONG_SIDES:
        return "long"
    if fallback_text in SHORT_SIDES:
        return "short"
    return None


def _trade_count_key(fill: dict[str, Any], order: dict[str, Any], *, market: str) -> tuple[str, str, str, str, str]:
    account_scope = str(fill.get("account_scope") or order.get("account_scope") or "").strip()
    if not account_scope and market in {"futures", "spot"}:
        account_scope = f"personal_{market}"
    if not account_scope:
        account_scope = "personal"
    symbol = str(fill.get("symbol") or order.get("symbol") or "UNKNOWN").upper()
    position_side = _normalize_trade_position_side(
        fill.get("position_side") or order.get("position_side"),
        market=market,
        fallback_side=fill.get("side") or order.get("side"),
    ) or "net"
    position_mode = str(fill.get("position_mode") or order.get("position_mode") or "UNKNOWN")
    return account_scope, market, symbol, position_side, position_mode


def _infer_fill_action(fill: dict[str, Any], order: dict[str, Any], *, market: str) -> str:
    if order.get("reduce_only") or order.get("close_position"):
        return "exit"

    side = str(fill.get("side") or order.get("side") or "").strip().lower()
    position_side = _normalize_trade_position_side(
        fill.get("position_side") or order.get("position_side"),
        market=market,
        fallback_side=side,
    )
    if market == "spot":
        return "entry" if side in LONG_SIDES else "exit"
    if position_side == "long":
        return "entry" if side in LONG_SIDES else "exit"
    if position_side == "short":
        return "entry" if side in SHORT_SIDES else "exit"
    return "entry" if side in LONG_SIDES else "exit"


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _remaining_order_quantity(entry: dict[str, Any]) -> float | None:
    quantity = _to_float(entry.get("quantity"))
    if quantity is None:
        return None
    executed_qty = _to_float(entry.get("executed_qty")) or 0.0
    return max(0.0, abs(quantity) - abs(executed_qty))


def _position_bucket(entry: dict[str, Any], *, market: str) -> tuple[str, str]:
    symbol = str(entry.get("symbol") or "UNKNOWN").strip().upper()
    position_side = _normalize_trade_position_side(
        entry.get("position_side"),
        market=market,
        fallback_side=entry.get("side"),
    ) or "net"
    return symbol, position_side


def _normalize_order_identifier(row: dict[str, Any]) -> str:
    for key in ("order_id", "orderId", "actualOrderId", "algoId"):
        value = _pick(row, key)
        if value in (None, "", 0, "0"):
            continue
        return str(value)
    return ""


def _matching_bucket_quantity(
    rows: list[dict[str, Any]],
    *,
    market: str,
    symbol: str,
    position_side: str,
) -> float:
    total = 0.0
    target = (symbol, position_side)
    for row in rows:
        if _position_bucket(row, market=market) != target:
            continue
        quantity = abs(_to_float(row.get("quantity")) or 0.0)
        total += quantity
    return total


def _matching_working_order_quantity(
    rows: list[dict[str, Any]],
    *,
    market: str,
    symbol: str,
    position_side: str,
    action: str,
) -> float:
    total = 0.0
    target = (symbol, position_side)
    for row in rows:
        if _position_bucket(row, market=market) != target:
            continue
        if _infer_fill_action(row, row, market=market) != action:
            continue
        quantity = _remaining_order_quantity(row)
        if quantity is None:
            continue
        total += quantity
    return total


def _collect_closed_episode_stats(
    fills: list[dict[str, Any]],
    orders: list[dict[str, Any]],
) -> list[dict[str, float | int | None]]:
    orders_by_id = {
        str(order.get("order_id") or ""): order
        for order in orders
        if str(order.get("order_id") or "")
    }
    sorted_fills = sorted(
        fills,
        key=lambda item: (int(item.get("time") or 0), str(item.get("order_id") or "")),
    )
    active_episodes: dict[tuple[str, str, str, str, str], dict[str, float | int | None]] = {}
    closed_episodes: list[dict[str, float | int | None]] = []

    for fill in sorted_fills:
        order = orders_by_id.get(str(fill.get("order_id") or ""), {})
        market = str(fill.get("market") or order.get("market") or "unknown").strip().lower()
        key = _trade_count_key(fill, order, market=market)
        action = _infer_fill_action(fill, order, market=market)
        quantity = abs(_to_float(fill.get("quantity")) or 0.0)
        fill_time = _safe_int(fill.get("time"))
        raw_realized_pnl = _to_float(fill.get("realized_pnl"))
        raw_fee = _to_float(fill.get("fee"))
        realized_pnl = raw_realized_pnl or 0.0
        fee = abs(raw_fee or 0.0)
        if quantity <= POSITION_EPSILON:
            continue

        episode = active_episodes.get(key)
        if action == "entry":
            if episode is None or (episode.get("open_quantity") or 0.0) <= POSITION_EPSILON:
                episode = {
                    "open_time": fill_time,
                    "open_quantity": 0.0,
                    "realized_pnl": 0.0,
                    "fees": 0.0,
                    "realized_pnl_complete": True,
                    "fee_complete": True,
                }
                active_episodes[key] = episode
            if raw_fee is None:
                episode["fee_complete"] = False
            episode["open_quantity"] = float((episode.get("open_quantity") or 0.0) + quantity)
            if episode.get("open_time") is None:
                episode["open_time"] = fill_time
            episode["realized_pnl"] = float((episode.get("realized_pnl") or 0.0) + realized_pnl)
            episode["fees"] = float((episode.get("fees") or 0.0) + fee)
            continue

        if episode is None:
            continue

        if raw_realized_pnl is None:
            episode["realized_pnl_complete"] = False
        if raw_fee is None:
            episode["fee_complete"] = False
        episode["realized_pnl"] = float((episode.get("realized_pnl") or 0.0) + realized_pnl)
        episode["fees"] = float((episode.get("fees") or 0.0) + fee)
        remaining_quantity = max(0.0, float(episode.get("open_quantity") or 0.0) - quantity)
        episode["open_quantity"] = remaining_quantity
        if remaining_quantity <= POSITION_EPSILON:
            open_time = _safe_int(episode.get("open_time"))
            hold_ms = None
            if open_time is not None and fill_time is not None and fill_time >= open_time:
                hold_ms = fill_time - open_time
            net_pnl_complete = bool(episode.get("realized_pnl_complete", True)) and bool(episode.get("fee_complete", True))
            closed_episodes.append(
                {
                    "open_time": open_time,
                    "close_time": fill_time,
                    "hold_ms": hold_ms,
                    "net_pnl": (
                        float((episode.get("realized_pnl") or 0.0) - (episode.get("fees") or 0.0))
                        if net_pnl_complete
                        else None
                    ),
                    "net_pnl_complete": net_pnl_complete,
                }
            )
            active_episodes.pop(key, None)

    return closed_episodes


def _build_order_risk_tp_sl_state(
    *,
    market: str,
    symbol: str | None,
    position_side: str | None,
    preview_quantity: float | None,
    preview_has_take_profit: bool,
    preview_has_stop_loss: bool,
    positions: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
    conditional_orders: list[dict[str, Any]],
) -> dict[str, float | bool]:
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_position_side = _normalize_trade_position_side(
        position_side,
        market=market,
        fallback_side=None,
    ) or "net"
    required_qty = _matching_bucket_quantity(
        positions,
        market=market,
        symbol=normalized_symbol,
        position_side=normalized_position_side,
    )
    required_qty += _matching_working_order_quantity(
        open_orders,
        market=market,
        symbol=normalized_symbol,
        position_side=normalized_position_side,
        action="entry",
    )
    required_qty += abs(preview_quantity or 0.0)

    reserved_close_qty = _matching_working_order_quantity(
        open_orders,
        market=market,
        symbol=normalized_symbol,
        position_side=normalized_position_side,
        action="exit",
    )

    take_profit_covered_qty = abs(preview_quantity or 0.0) if preview_has_take_profit else 0.0
    stop_loss_covered_qty = abs(preview_quantity or 0.0) if preview_has_stop_loss else 0.0

    for order in conditional_orders:
        if _position_bucket(order, market=market) != (normalized_symbol, normalized_position_side):
            continue
        remaining_qty = _remaining_order_quantity(order)
        if order.get("close_position"):
            remaining_qty = required_qty
        if remaining_qty is None:
            continue
        if order.get("tp_trigger_price") not in (None, ""):
            take_profit_covered_qty += remaining_qty
        if order.get("sl_trigger_price") not in (None, ""):
            stop_loss_covered_qty += remaining_qty

    take_profit_covered_qty = max(0.0, take_profit_covered_qty - reserved_close_qty)
    stop_loss_covered_qty = max(0.0, stop_loss_covered_qty - reserved_close_qty)
    has_take_profit = required_qty > POSITION_EPSILON and take_profit_covered_qty + POSITION_EPSILON >= required_qty
    has_stop_loss = required_qty > POSITION_EPSILON and stop_loss_covered_qty + POSITION_EPSILON >= required_qty

    return {
        "has_take_profit": has_take_profit,
        "has_stop_loss": has_stop_loss,
        "required_covered_qty": round(required_qty, 8),
        "take_profit_covered_qty": round(take_profit_covered_qty, 8),
        "stop_loss_covered_qty": round(stop_loss_covered_qty, 8),
    }


def _profile_sample_trade_count(payload: dict[str, Any]) -> int:
    if "reconstructed_closed_trade_count" in payload:
        return int(payload.get("reconstructed_closed_trade_count") or 0)
    return int(payload.get("closed_trade_count") or 0)


def _extract_rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
    return []


def _ensure_dict_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    return _extract_rows(payload)


def _normalize_balance_entries(
    payload: Any,
    market: str,
    *,
    trading_mode: str = DEFAULT_TRADING_MODE,
) -> list[dict[str, Any]]:
    if market == "spot":
        rows = _extract_rows(payload, "balances")
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            available_balance = _to_float(_pick(row, "availableBalance", "free"))
            locked = _to_float(_pick(row, "locked"))
            balance = _to_float(_pick(row, "balance"))
            if balance is None and (available_balance is not None or locked is not None):
                balance = (available_balance or 0.0) + (locked or 0.0)
            normalized_rows.append(
                {
                    "account_scope": _normalize_account_scope(market, row, trading_mode=trading_mode),
                    "market": market,
                    "asset": str(_pick(row, "asset", "coinName") or "UNKNOWN"),
                    "balance": balance,
                    "available_balance": available_balance,
                    "locked": locked,
                    "equity": None,
                    "unrealized_pnl": None,
                }
            )
        return normalized_rows

    rows = _ensure_dict_rows(payload)
    return [
        {
            "account_scope": _normalize_account_scope(market, row, trading_mode=trading_mode),
            "market": market,
            "asset": str(_pick(row, "asset") or "UNKNOWN"),
            "balance": _to_float(_pick(row, "balance")),
            "available_balance": _to_float(_pick(row, "availableBalance")),
            "locked": _to_float(_pick(row, "frozen")),
            "equity": _to_float(_pick(row, "balance")),
            "unrealized_pnl": _to_float(_pick(row, "unrealizePnl", "unrealizedPnl")),
        }
        for row in rows
    ]


def _normalize_positions(
    payload: Any,
    market: str,
    *,
    trading_mode: str = DEFAULT_TRADING_MODE,
) -> list[dict[str, Any]]:
    rows = _extract_rows(payload, "positions", "items")
    if not rows and isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
    return [
        {
            "account_scope": _normalize_account_scope(market, row, trading_mode=trading_mode),
            "market": market,
            "symbol": _normalize_symbol_for_trading_mode(_pick(row, "symbol", "instId"), trading_mode),
            "side": str(_pick(row, "side", "positionSide") or "unknown").lower(),
            "margin_type": _normalize_margin_type(_pick(row, "marginType", "margin_type")),
            "position_mode": _normalize_position_mode(
                _pick(row, "positionMode", "position_mode", "separatedMode")
            ),
            "quantity": _to_float(_pick(row, "size", "quantity", "qty")),
            "notional": _to_float(_pick(row, "openValue", "value", "notional")),
            "unrealized_pnl": _to_float(_pick(row, "unrealizePnl", "unrealizedPnl", "unrealized_pnl")),
            "leverage": _to_float(_pick(row, "leverage")),
            "created_time": int(_pick(row, "createdTime", "time") or 0),
            "updated_time": int(_pick(row, "updatedTime", "updateTime") or 0),
        }
        for row in rows
    ]


def _normalize_orders(
    payload: Any,
    market: str,
    *,
    trading_mode: str = DEFAULT_TRADING_MODE,
) -> list[dict[str, Any]]:
    rows = _extract_rows(payload, "orders", "items")
    if not rows and isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
    return [
        {
            "account_scope": _normalize_account_scope(market, row, trading_mode=trading_mode),
            "market": market,
            "symbol": _normalize_symbol_for_trading_mode(_pick(row, "symbol"), trading_mode),
            "order_id": _normalize_order_identifier(row),
            "algo_id": str(_pick(row, "algoId") or ""),
            "client_order_id": str(
                _pick(row, "client_order_id", "clientOrderId", "clientAlgoId", "origClientOrderId") or ""
            ),
            "side": str(_pick(row, "side") or "unknown").lower(),
            "position_side": str(_pick(row, "position_side", "positionSide") or "").lower() or None,
            "margin_type": _normalize_margin_type(_pick(row, "marginType", "margin_type")),
            "position_mode": _normalize_position_mode(
                _pick(row, "positionMode", "position_mode", "separatedMode")
            ),
            "order_type": str(_pick(row, "type", "orderType") or "unknown").lower(),
            "status": str(_pick(row, "status", "algoStatus") or "unknown"),
            "reduce_only": bool(_coerce_bool(_pick(row, "reduceOnly", "reduce_only"))),
            "close_position": bool(_coerce_bool(_pick(row, "closePosition", "close_position"))),
            "working_type": str(_pick(row, "workingType", "working_type") or ""),
            "quantity": _to_float(_pick(row, "origQty", "quantity")),
            "executed_qty": _to_float(_pick(row, "executedQty")),
            "quote_qty": _to_float(_pick(row, "cumQuote", "cummulativeQuoteQty")),
            "avg_price": _to_float(_pick(row, "avgPrice")),
            "price": _to_float(_pick(row, "price")),
            "tp_trigger_price": _to_float(_pick(row, "tpTriggerPrice", "tp_trigger_price")),
            "tp_price": _to_float(_pick(row, "tpPrice", "tp_price")),
            "sl_trigger_price": _to_float(_pick(row, "slTriggerPrice", "sl_trigger_price")),
            "sl_price": _to_float(_pick(row, "slPrice", "sl_price")),
            "time": int(_pick(row, "time", "createdTime", "createTime") or 0),
            "update_time": int(_pick(row, "updateTime", "updatedTime") or 0),
            "trigger_time": int(_pick(row, "triggerTime") or 0),
        }
        for row in rows
    ]


def _normalize_fills(
    payload: Any,
    market: str,
    *,
    trading_mode: str = DEFAULT_TRADING_MODE,
) -> list[dict[str, Any]]:
    rows = _extract_rows(payload, "fills", "trades", "items")
    if not rows and isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        side = str(_pick(row, "side") or "").strip().lower()
        if not side and market == "spot":
            is_buyer = _coerce_bool(_pick(row, "isBuyer", "buyer"))
            if is_buyer is True:
                side = "buy"
            elif is_buyer is False:
                side = "sell"
        normalized_rows.append(
            {
                "account_scope": _normalize_account_scope(market, row, trading_mode=trading_mode),
                "market": market,
                "fill_id": str(_pick(row, "id", "tradeId") or ""),
                "order_id": str(_pick(row, "order_id", "orderId") or ""),
                "symbol": _normalize_symbol_for_trading_mode(_pick(row, "symbol"), trading_mode),
                "side": side or "unknown",
                "position_side": str(_pick(row, "position_side", "positionSide") or "").lower() or None,
                "margin_type": _normalize_margin_type(_pick(row, "marginType", "margin_type")),
                "position_mode": _normalize_position_mode(
                    _pick(row, "positionMode", "position_mode", "separatedMode")
                ),
                "price": _to_float(_pick(row, "price")),
                "quantity": _to_float(_pick(row, "qty", "quantity")),
                "notional": _to_float(_pick(row, "quoteQty", "quoteQty", "turnover")),
                "realized_pnl": _to_float(_pick(row, "realizedPnl", "realized_pnl", "pnl")),
                "fee": _to_float(_pick(row, "commission", "fee", "fees")),
                "time": int(_pick(row, "time") or 0),
            }
        )
    return normalized_rows


def _normalize_bills(payload: Any, market: str) -> list[dict[str, Any]]:
    rows = _extract_rows(payload, "items", "bills")
    return [
        {
            "account_scope": _normalize_account_scope(market, row),
            "market": market,
            "bill_id": str(_pick(row, "billId") or ""),
            "asset": str(_pick(row, "asset", "coinName") or "UNKNOWN"),
            "symbol": str(_pick(row, "symbol") or ""),
            "amount": _to_float(_pick(row, "income", "deltaAmount")),
            "type": str(_pick(row, "incomeType", "bizType") or "unknown"),
            "fee": _to_float(_pick(row, "fillFee", "fees")),
            "balance_after": _to_float(_pick(row, "balance", "afterAmount")),
            "time": int(_pick(row, "time", "cTime") or 0),
        }
        for row in rows
    ]


def _normalize_klines(payload: Any, market: str, symbol: str | None) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, list) or len(item) < 7:
            continue
        rows.append(
            {
                "market": market,
                "symbol": symbol or "UNKNOWN",
                "open_time": int(item[0]),
                "open": _to_float(item[1]),
                "high": _to_float(item[2]),
                "low": _to_float(item[3]),
                "close": _to_float(item[4]),
                "volume": _to_float(item[5]),
                "close_time": int(item[6]),
                "quote_volume": _to_float(item[7]) if len(item) > 7 else None,
                "trades": int(item[8]) if len(item) > 8 and str(item[8]).isdigit() else None,
            }
        )
    return rows


def _safe_current_price_from_klines(
    *,
    fetch_klines: Any,
    market: str,
    symbol: str,
    degraded_reasons: list[str],
) -> float | None:
    try:
        candles = _normalize_klines(fetch_klines(), market, symbol)
    except AggregationInputError:
        _merge_degraded_reasons(degraded_reasons, [f"{market}_market_snapshot_unavailable"])
        return None
    if candles:
        return candles[-1]["close"]
    return None


def _extract_latest_price(payload: Any) -> float | None:
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        candidates.append(payload)
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.append(data)
        elif isinstance(data, list):
            candidates.extend(item for item in data if isinstance(item, dict))
    elif isinstance(payload, list):
        candidates.extend(item for item in payload if isinstance(item, dict))

    for candidate in candidates:
        price = _to_float(_pick(candidate, "lastPrice", "price", "markPrice", "close"))
        if price is not None:
            return price
    return None


def _safe_current_price(
    *,
    fetch_latest_price: Any,
    market: str,
    symbol: str,
    degraded_reasons: list[str],
    fetch_klines: Any | None = None,
) -> float | None:
    try:
        latest_price = _extract_latest_price(fetch_latest_price())
    except AggregationInputError:
        latest_price = None
    if latest_price is not None:
        return latest_price
    if fetch_klines is not None:
        return _safe_current_price_from_klines(
            fetch_klines=fetch_klines,
            market=market,
            symbol=symbol,
            degraded_reasons=degraded_reasons,
        )
    _merge_degraded_reasons(degraded_reasons, [f"{market}_market_snapshot_unavailable"])
    return None


def _infer_spot_quote_asset(symbol: str | None, balances: list[dict[str, Any]]) -> str | None:
    if not symbol:
        return None
    normalized_symbol = str(symbol).strip().upper()
    candidate_assets = {
        str(row.get("asset") or "").strip().upper()
        for row in balances
        if str(row.get("asset") or "").strip()
    }
    candidate_assets.update(SPOT_QUOTE_ASSET_FALLBACKS)
    for asset in sorted(candidate_assets, key=len, reverse=True):
        if asset and normalized_symbol.endswith(asset) and len(normalized_symbol) > len(asset):
            return asset
    return None


def _extract_spot_balance_value_usdt(
    *,
    asset: str,
    amount: float | None,
    fetch_price: Any,
    degraded_reasons: list[str],
) -> float | None:
    if amount is None:
        return None
    if amount == 0.0:
        return 0.0

    normalized_asset = str(asset).strip().upper()
    if normalized_asset == "USDT":
        return amount

    try:
        price_payload = fetch_price(symbol=f"{normalized_asset}USDT")
    except AggregationInputError:
        price_payload = None

    price = _extract_latest_price(price_payload)
    if price is None:
        _merge_degraded_reasons(degraded_reasons, ["spot_equity_estimate_partial"])
        return None
    return amount * price


def _build_spot_account_estimates(
    *,
    balances: list[dict[str, Any]],
    symbol: str | None,
    fetch_spot_latest_price: Any,
    degraded_reasons: list[str],
) -> tuple[dict[str, float | None], list[dict[str, Any]]]:
    equity_total = 0.0
    saw_equity_component = False
    available_equity_total = 0.0
    saw_available_component = False
    positions: list[dict[str, Any]] = []
    price_cache: dict[str, Any] = {}

    def cached_fetch_spot_latest_price(*, symbol: str) -> Any:
        if symbol not in price_cache:
            price_cache[symbol] = fetch_spot_latest_price(symbol=symbol)
        return price_cache[symbol]

    for row in balances:
        asset = str(row.get("asset") or "").strip().upper()
        if not asset:
            continue

        balance_amount = _to_float(row.get("balance"))
        available_amount = _to_float(row.get("available_balance"))
        balance_value = _extract_spot_balance_value_usdt(
            asset=asset,
            amount=balance_amount,
            fetch_price=cached_fetch_spot_latest_price,
            degraded_reasons=degraded_reasons,
        )
        if balance_value is not None:
            equity_total += balance_value
            saw_equity_component = True

        available_value = _extract_spot_balance_value_usdt(
            asset=asset,
            amount=available_amount,
            fetch_price=cached_fetch_spot_latest_price,
            degraded_reasons=degraded_reasons,
        )
        if available_value is not None:
            available_equity_total += available_value
            saw_available_component = True

        if asset in SPOT_CASH_ASSETS:
            continue
        if balance_amount is None or abs(balance_amount) <= POSITION_EPSILON:
            continue
        if balance_value is None or abs(balance_value) <= POSITION_EPSILON:
            continue

        positions.append(
            {
                "account_scope": str(row.get("account_scope") or _normalize_account_scope("spot", row)),
                "market": "spot",
                "symbol": f"{asset}USDT",
                "side": "long",
                "margin_type": None,
                "position_mode": "COMBINED",
                "quantity": balance_amount,
                "notional": balance_value,
                "leverage": 1.0,
                "created_time": 0,
                "updated_time": 0,
            }
        )

    quote_asset = _infer_spot_quote_asset(symbol, balances)
    quote_available_balance = None
    if quote_asset:
        quote_row = next((row for row in balances if str(row.get("asset") or "").upper() == quote_asset), None)
        if quote_row is not None:
            quote_available_balance = _extract_spot_balance_value_usdt(
                asset=quote_asset,
                amount=_to_float(quote_row.get("available_balance")),
                fetch_price=cached_fetch_spot_latest_price,
                degraded_reasons=degraded_reasons,
            )

    positions.sort(key=lambda row: abs(_to_float(row.get("notional")) or 0.0), reverse=True)
    return (
        {
            "equity": equity_total if saw_equity_component else None,
            "available_balance": (
                quote_available_balance
                if quote_available_balance is not None
                else (available_equity_total if saw_available_component else None)
            ),
        },
        positions,
    )


def _estimate_spot_account_snapshot(
    *,
    balances: list[dict[str, Any]],
    symbol: str | None,
    fetch_spot_latest_price: Any,
    degraded_reasons: list[str],
) -> dict[str, float | None]:
    account_snapshot, _ = _build_spot_account_estimates(
        balances=balances,
        symbol=symbol,
        fetch_spot_latest_price=fetch_spot_latest_price,
        degraded_reasons=degraded_reasons,
    )
    return account_snapshot


def _estimate_futures_position_price(
    *,
    positions: list[dict[str, Any]],
    symbol: str | None,
) -> float | None:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return None

    for row in positions:
        if str(row.get("symbol") or "").strip().upper() != normalized_symbol:
            continue
        quantity = abs(_to_float(row.get("quantity")) or 0.0)
        notional = abs(_to_float(row.get("notional")) or 0.0)
        if quantity <= POSITION_EPSILON or notional <= 0.0:
            continue
        return notional / quantity
    return None


def _mark_market_snapshot_estimated(
    *,
    degraded_reasons: list[str],
    market: str,
    reason: str,
) -> None:
    unavailable_reason = f"{market}_market_snapshot_unavailable"
    while unavailable_reason in degraded_reasons:
        degraded_reasons.remove(unavailable_reason)
    _merge_degraded_reasons(degraded_reasons, [reason])


def _pick_primary_futures_symbol(
    *,
    positions: list[dict[str, Any]],
    recent_orders: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
    conditional_orders: list[dict[str, Any]],
) -> str | None:
    if positions:
        primary_position = max(
            positions,
            key=lambda row: abs(_to_float(row.get("notional")) or 0.0),
        )
        symbol = str(primary_position.get("symbol") or "").strip().upper()
        if symbol:
            return symbol

    for collection in (recent_orders, open_orders, conditional_orders):
        ranked = sorted(
            collection,
            key=lambda row: int(_pick(row, "time", "update_time", "updateTime", "created_time", "createdTime") or 0),
            reverse=True,
        )
        for row in ranked:
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol:
                return symbol
    return None


def _count_closed_trades(fills: list[dict[str, Any]], orders: list[dict[str, Any]]) -> int:
    orders_by_id = {
        str(order.get("order_id") or ""): order
        for order in orders
        if str(order.get("order_id") or "")
    }
    sorted_fills = sorted(
        fills,
        key=lambda item: (int(item.get("time") or 0), str(item.get("order_id") or "")),
    )
    active_quantities: dict[tuple[str, str, str, str, str], float] = {}
    exit_only_order_ids: set[str] = set()
    closed_trade_count = 0

    for fill in sorted_fills:
        order = orders_by_id.get(str(fill.get("order_id") or ""), {})
        market = str(fill.get("market") or order.get("market") or "unknown").strip().lower()
        key = _trade_count_key(fill, order, market=market)
        action = _infer_fill_action(fill, order, market=market)
        quantity = abs(_to_float(fill.get("quantity")) or 0.0)

        if action == "entry":
            active_quantities[key] = active_quantities.get(key, 0.0) + quantity
            continue

        current_quantity = active_quantities.get(key, 0.0)
        if current_quantity <= POSITION_EPSILON:
            order_id = str(fill.get("order_id") or "")
            fallback_id = f"{key}:{int(fill.get('time') or 0)}"
            exit_token = order_id or fallback_id
            if exit_token not in exit_only_order_ids:
                exit_only_order_ids.add(exit_token)
                closed_trade_count += 1
            continue

        remaining_quantity = max(0.0, current_quantity - quantity)
        if remaining_quantity <= POSITION_EPSILON:
            active_quantities.pop(key, None)
            closed_trade_count += 1
        else:
            active_quantities[key] = remaining_quantity

    if closed_trade_count:
        return closed_trade_count

    pnl_based_close_tokens = {
        str(fill.get("order_id") or f"pnl:{fill.get('symbol')}:{int(fill.get('time') or 0)}")
        for fill in sorted_fills
        if abs(_to_float(fill.get("realized_pnl")) or 0.0) > POSITION_EPSILON
    }
    if pnl_based_close_tokens:
        return len(pnl_based_close_tokens)

    filled_orders = sorted(
        [order for order in orders if str(order.get("status") or "").upper() == "FILLED"],
        key=lambda item: (int(item.get("time") or item.get("update_time") or 0), str(item.get("order_id") or "")),
    )
    if not filled_orders:
        return 0

    active_quantities = {}
    exit_only_order_ids = set()
    order_based_count = 0
    for order in filled_orders:
        market = str(order.get("market") or "unknown").strip().lower()
        key = _trade_count_key(order, order, market=market)
        action = _infer_fill_action(order, order, market=market)
        quantity = abs(_to_float(order.get("executed_qty") or order.get("quantity")) or 0.0)

        if action == "entry":
            active_quantities[key] = active_quantities.get(key, 0.0) + quantity
            continue

        current_quantity = active_quantities.get(key, 0.0)
        if current_quantity <= POSITION_EPSILON:
            order_id = str(order.get("order_id") or "")
            fallback_id = f"{key}:{int(order.get('time') or order.get('update_time') or 0)}"
            exit_token = order_id or fallback_id
            if exit_token not in exit_only_order_ids:
                exit_only_order_ids.add(exit_token)
                order_based_count += 1
            continue

        remaining_quantity = max(0.0, current_quantity - quantity)
        if remaining_quantity <= POSITION_EPSILON:
            active_quantities.pop(key, None)
            order_based_count += 1
        else:
            active_quantities[key] = remaining_quantity

    return order_based_count


def _has_replay_carry_in(fills: list[dict[str, Any]], orders: list[dict[str, Any]]) -> bool:
    orders_by_id = {
        str(order.get("order_id") or ""): order
        for order in orders
        if str(order.get("order_id") or "")
    }
    sorted_fills = sorted(
        fills,
        key=lambda item: (int(item.get("time") or 0), str(item.get("order_id") or "")),
    )
    active_quantities: dict[tuple[str, str, str, str, str], float] = {}

    for fill in sorted_fills:
        order = orders_by_id.get(str(fill.get("order_id") or ""), {})
        market = str(fill.get("market") or order.get("market") or "unknown").strip().lower()
        key = _trade_count_key(fill, order, market=market)
        action = _infer_fill_action(fill, order, market=market)
        quantity = abs(_to_float(fill.get("quantity")) or 0.0)

        if action == "entry":
            active_quantities[key] = active_quantities.get(key, 0.0) + quantity
            continue

        current_quantity = active_quantities.get(key, 0.0)
        if current_quantity <= POSITION_EPSILON:
            return True

        remaining_quantity = max(0.0, current_quantity - quantity)
        if remaining_quantity <= POSITION_EPSILON:
            active_quantities.pop(key, None)
        else:
            active_quantities[key] = remaining_quantity

    return False


class TradeDataAggregator:
    def __init__(self, fetcher: Any | None = None) -> None:
        self.fetcher = fetcher or WeexApiFetcher()

    def _collect_spot_balances(
        self,
        *,
        profile_name: str,
        degraded_reasons: list[str],
    ) -> tuple[list[dict[str, Any]], bool]:
        try:
            payload = self.fetcher.fetch_spot_balance(profile_name=profile_name)
        except AggregationInputError as exc:
            if _should_degrade_spot_balance_error(exc):
                _merge_degraded_reasons(degraded_reasons, ["spot_balance_unavailable"])
                return [], True
            raise
        return _normalize_balance_entries(payload, "spot"), False

    def collect_replay_payload(
        self,
        *,
        profile_name: str,
        market: str,
        trading_mode: str = DEFAULT_TRADING_MODE,
        period: str,
        symbol: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        normalized_market = _validate_market(market)
        mode = _validate_trading_mode_market(trading_mode, normalized_market)
        environment = _environment_for_trading_mode(mode, normalized_market)
        normalized_period = _validate_replay_period(period)
        normalized_symbol = str(symbol).strip().upper() if symbol else None
        now_ms = int(time.time() * 1000)
        end_ms = now_ms
        start_ms = max(0, end_ms - (_period_to_days(normalized_period) * DAY_MS))

        if normalized_market == "spot" and not normalized_symbol:
            raise AggregationInputError(
                "spot replay collection requires a symbol because current spot history endpoints are symbol-specific."
            )
        if normalized_market == "all" and not normalized_symbol:
            constraints = [
                {
                    "code": "spot_symbol_required",
                    "message": "spot history is only collected when symbol is provided.",
                }
            ]
        else:
            constraints = []
        partial = normalized_market == "all" and not normalized_symbol
        degraded_reasons: list[str] = []
        if partial:
            _merge_degraded_reasons(degraded_reasons, ["spot_history_skipped_without_symbol"])

        balances: list[dict[str, Any]] = []
        positions: list[dict[str, Any]] = []
        orders: list[dict[str, Any]] = []
        fills: list[dict[str, Any]] = []
        bills: list[dict[str, Any]] = []
        price_series: list[dict[str, Any]] = []

        if self.fetcher is not None and normalized_market in {"futures", "all"}:
            futures_balance_payload = self.fetcher.fetch_futures_balance(
                profile_name=profile_name,
                trading_mode=mode,
            )
            futures_positions_payload = self.fetcher.fetch_futures_positions(
                profile_name=profile_name,
                trading_mode=mode,
            )
            futures_orders_payload = self.fetcher.fetch_futures_orders(
                profile_name=profile_name,
                trading_mode=mode,
                start_ms=start_ms,
                end_ms=end_ms,
                symbol=normalized_symbol,
            )
            balances.extend(_normalize_balance_entries(futures_balance_payload, "futures", trading_mode=mode))
            positions.extend(_normalize_positions(futures_positions_payload, "futures", trading_mode=mode))
            orders.extend(_normalize_orders(futures_orders_payload, "futures", trading_mode=mode))
            futures_meta_sources: list[Any] = [futures_orders_payload]
            if mode == "demo":
                partial = True
                _merge_degraded_reasons(
                    degraded_reasons,
                    [
                        "demo_futures_fills_unavailable",
                        "demo_futures_bills_unavailable",
                        "demo_futures_open_orders_unavailable",
                        "demo_futures_conditional_orders_unavailable",
                        "demo_futures_tp_sl_state_unavailable",
                    ],
                )
            else:
                futures_fills_payload = self.fetcher.fetch_futures_fills(
                    profile_name=profile_name,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    symbol=normalized_symbol,
                )
                futures_bills_payload = self.fetcher.fetch_futures_bills(
                    profile_name=profile_name,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    symbol=normalized_symbol,
                )
                futures_pending_history_payload = self.fetcher.fetch_futures_historical_pending_orders(
                    profile_name=profile_name,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    symbol=normalized_symbol,
                )
                orders.extend(_normalize_orders(futures_pending_history_payload, "futures"))
                fills.extend(_normalize_fills(futures_fills_payload, "futures"))
                bills.extend(_normalize_bills(futures_bills_payload, "futures"))
                futures_meta_sources.extend(
                    [
                        futures_pending_history_payload,
                        futures_fills_payload,
                        futures_bills_payload,
                    ]
                )
            for source in futures_meta_sources:
                meta = _extract_meta(source)
                if meta.get("partial"):
                    partial = True
                _merge_degraded_reasons(degraded_reasons, list(meta.get("degraded_reasons") or []))
                _merge_constraints(constraints, list(meta.get("constraints") or []))
            if normalized_symbol:
                price_series.extend(
                    _normalize_klines(
                        self.fetcher.fetch_futures_klines(
                            symbol=normalized_symbol,
                            start_ms=start_ms,
                            end_ms=end_ms,
                        ),
                        "futures",
                        normalized_symbol,
                    )
                )

        if self.fetcher is not None and normalized_market in {"spot", "all"} and normalized_symbol:
            spot_balances, spot_balance_partial = self._collect_spot_balances(
                profile_name=profile_name,
                degraded_reasons=degraded_reasons,
            )
            spot_orders_payload = self.fetcher.fetch_spot_orders(
                profile_name=profile_name,
                start_ms=start_ms,
                end_ms=end_ms,
                symbol=normalized_symbol,
            )
            spot_fills_payload = self.fetcher.fetch_spot_fills(
                profile_name=profile_name,
                start_ms=start_ms,
                end_ms=end_ms,
                symbol=normalized_symbol,
            )
            try:
                spot_bills_payload = self.fetcher.fetch_spot_bills(
                    profile_name=profile_name,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    symbol=normalized_symbol,
                )
            except AggregationInputError as exc:
                if _should_degrade_spot_bills_error(exc):
                    partial = True
                    _merge_degraded_reasons(degraded_reasons, ["spot_bills_unavailable"])
                    spot_bills_payload = {"items": []}
                else:
                    raise
            partial = partial or spot_balance_partial
            balances.extend(spot_balances)
            orders.extend(_normalize_orders(spot_orders_payload, "spot"))
            fills.extend(_normalize_fills(spot_fills_payload, "spot"))
            bills.extend(_normalize_bills(spot_bills_payload, "spot"))
            for source in (spot_orders_payload, spot_fills_payload, spot_bills_payload):
                meta = _extract_meta(source)
                if meta.get("partial"):
                    partial = True
                _merge_degraded_reasons(degraded_reasons, list(meta.get("degraded_reasons") or []))
                _merge_constraints(constraints, list(meta.get("constraints") or []))
            try:
                spot_kline_payload = self.fetcher.fetch_spot_klines(symbol=normalized_symbol)
            except AggregationInputError as exc:
                if _should_degrade_spot_kline_error(exc):
                    partial = True
                    _merge_degraded_reasons(degraded_reasons, ["spot_kline_unavailable"])
                else:
                    raise
            else:
                price_series.extend(
                    _normalize_klines(
                        spot_kline_payload,
                        "spot",
                        normalized_symbol,
                    )
                )
                _merge_degraded_reasons(degraded_reasons, ["spot_kline_window_unbounded"])
            if normalized_period in {"180d", "360d"}:
                partial = True

        closed_trade_count = _count_closed_trades(fills, orders)
        closed_episode_stats = _collect_closed_episode_stats(fills, orders)
        reconstructed_closed_trade_count = len(closed_episode_stats)
        if _has_replay_carry_in(fills, orders):
            partial = True
            _merge_degraded_reasons(degraded_reasons, ["replay_carry_in_detected"])
        if any(episode.get("net_pnl") is None for episode in closed_episode_stats):
            partial = True
            _merge_degraded_reasons(degraded_reasons, ["replay_episode_pnl_unavailable"])
        bill_summary = _summarize_bill_adjustments(bills, fallback_market=normalized_market)
        if bill_summary.get("unclassified_bill_types"):
            _merge_degraded_reasons(degraded_reasons, ["replay_bill_types_unclassified"])

        return {
            "analysis_type": "replay",
            "trading_mode": mode,
            "environment": environment,
            "user_environment_prefix": _user_environment_prefix(environment),
            "market": normalized_market,
            "period": normalized_period,
            "symbol": normalized_symbol,
            "focus": str(focus).strip().lower() if focus else None,
            "time_range": {
                "start_ms": start_ms,
                "end_ms": end_ms,
            },
            "closed_trade_count": closed_trade_count,
            "reconstructed_closed_trade_count": reconstructed_closed_trade_count,
            "orders": orders,
            "fills": fills,
            "positions": positions,
            "balances": balances,
            "bills": bills,
            "price_series": price_series,
            "constraints": constraints,
            "partial": partial,
            "degraded_reasons": degraded_reasons,
        }

    def collect_profile_payload(
        self,
        *,
        profile_name: str,
        market: str,
        trading_mode: str = DEFAULT_TRADING_MODE,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        last_payload: dict[str, Any] | None = None
        for index, period in enumerate(PROFILE_PERIODS):
            payload = self.collect_replay_payload(
                profile_name=profile_name,
                market=market,
                trading_mode=trading_mode,
                period=period,
                symbol=symbol,
                focus="profile",
            )
            payload["profile_period_candidate"] = period
            last_payload = payload
            sample_trade_count = _profile_sample_trade_count(payload)
            sample_quality = _sample_quality(sample_trade_count)
            if sample_quality != "minimal":
                payload["raw_closed_trade_count"] = int(payload.get("closed_trade_count") or 0)
                payload["closed_trade_count"] = sample_trade_count
                payload["selected_period"] = period
                payload["fallback_applied"] = index > 0
                payload["analysis_type"] = "profile"
                payload["sample_quality"] = sample_quality
                return payload

        if last_payload is None:
            raise AggregationInputError("Unable to build a profile payload without replay candidates.")

        sample_trade_count = _profile_sample_trade_count(last_payload)
        last_payload["raw_closed_trade_count"] = int(last_payload.get("closed_trade_count") or 0)
        last_payload["closed_trade_count"] = sample_trade_count
        last_payload["analysis_type"] = "profile"
        last_payload["selected_period"] = PROFILE_PERIODS[-1]
        last_payload["fallback_applied"] = len(PROFILE_PERIODS) > 1
        last_payload["sample_quality"] = _sample_quality(sample_trade_count)
        return last_payload

    def collect_order_risk_payload(
        self,
        *,
        profile_name: str,
        market: str,
        trading_mode: str = DEFAULT_TRADING_MODE,
        raw_order: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_market = _validate_market(market)
        mode = _validate_trading_mode_market(trading_mode, normalized_market)
        environment = _environment_for_trading_mode(mode, normalized_market)
        if normalized_market == "all":
            raise AggregationInputError("order risk preview requires a concrete market, not 'all'.")

        symbol = str(_pick(raw_order, "symbol") or "").strip().upper() or None
        tp_trigger = _pick(raw_order, "tp_trigger_price", "tpTriggerPrice")
        sl_trigger = _pick(raw_order, "sl_trigger_price", "slTriggerPrice")
        order_preview = {
            "market": normalized_market,
            "symbol": symbol,
            "side": str(_pick(raw_order, "side") or "").upper(),
            "position_side": str(_pick(raw_order, "position_side", "positionSide") or "").upper() or None,
            "order_type": str(_pick(raw_order, "order_type", "type") or "").upper(),
            "quantity": _to_float(_pick(raw_order, "quantity")),
            "price": _to_float(_pick(raw_order, "price")),
            "time_in_force": _pick(raw_order, "time_in_force", "timeInForce"),
        }
        tp_sl = {
            "has_take_profit": tp_trigger not in (None, ""),
            "has_stop_loss": sl_trigger not in (None, ""),
        }
        partial = False
        degraded_reasons: list[str] = []

        balances: list[dict[str, Any]] = []
        positions: list[dict[str, Any]] = []
        recent_orders: list[dict[str, Any]] = []
        conditional_orders: list[dict[str, Any]] = []
        open_orders: list[dict[str, Any]] = []
        current_price: float | None = None
        end_ms = int(time.time() * 1000)
        start_ms = max(0, end_ms - HOUR_MS)

        if normalized_market == "futures":
            balances = _normalize_balance_entries(
                self.fetcher.fetch_futures_balance(profile_name=profile_name, trading_mode=mode),
                "futures",
                trading_mode=mode,
            )
            positions = _normalize_positions(
                self.fetcher.fetch_futures_positions(profile_name=profile_name, trading_mode=mode),
                "futures",
                trading_mode=mode,
            )
            recent_orders = _normalize_orders(
                self.fetcher.fetch_futures_orders(
                    profile_name=profile_name,
                    trading_mode=mode,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    symbol=symbol,
                ),
                "futures",
                trading_mode=mode,
            )
            if mode == "demo":
                partial = True
                _merge_degraded_reasons(
                    degraded_reasons,
                    [
                        "demo_futures_open_orders_unavailable",
                        "demo_futures_conditional_orders_unavailable",
                        "demo_futures_tp_sl_state_unavailable",
                    ],
                )
                open_orders = []
                conditional_orders = []
            else:
                open_orders = _normalize_orders(
                    self.fetcher.fetch_futures_open_orders(
                        profile_name=profile_name,
                        symbol=symbol,
                    ),
                    "futures",
                )
                conditional_orders = _normalize_orders(
                    self.fetcher.fetch_futures_pending_orders(
                        profile_name=profile_name,
                        symbol=symbol,
                    ),
                    "futures",
                )
            if symbol:
                current_price = _safe_current_price(
                    fetch_latest_price=lambda: self.fetcher.fetch_futures_latest_price(symbol=symbol),
                    market="futures",
                    symbol=symbol,
                    degraded_reasons=degraded_reasons,
                    fetch_klines=lambda: self.fetcher.fetch_futures_klines(
                        symbol=symbol,
                        start_ms=start_ms,
                        end_ms=end_ms,
                    ),
                )
                if current_price is None:
                    current_price = _estimate_futures_position_price(
                        positions=positions,
                        symbol=symbol,
                    )
                    if current_price is not None:
                        _mark_market_snapshot_estimated(
                            degraded_reasons=degraded_reasons,
                            market="futures",
                            reason="futures_market_snapshot_estimated_from_position",
                        )
        else:
            balances, spot_balance_partial = self._collect_spot_balances(
                profile_name=profile_name,
                degraded_reasons=degraded_reasons,
            )
            partial = partial or spot_balance_partial
            recent_orders = _normalize_orders(
                self.fetcher.fetch_spot_orders(
                    profile_name=profile_name,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    symbol=symbol,
                ),
                "spot",
            )
            if symbol:
                current_price = _safe_current_price(
                    fetch_latest_price=lambda: self.fetcher.fetch_spot_latest_price(symbol=symbol),
                    market="spot",
                    symbol=symbol,
                    degraded_reasons=degraded_reasons,
                    fetch_klines=lambda: self.fetcher.fetch_spot_klines(symbol=symbol),
                )
            open_orders = _normalize_orders(
                self.fetcher.fetch_spot_open_orders(
                    profile_name=profile_name,
                    symbol=symbol,
                ),
                "spot",
            )
            _merge_degraded_reasons(degraded_reasons, ["spot_tp_sl_state_unavailable"])

        primary_balance = None
        if balances:
            primary_balance = next((row for row in balances if row.get("asset") == "USDT"), balances[0])
        if normalized_market == "spot":
            account_snapshot, positions = _build_spot_account_estimates(
                balances=balances,
                symbol=symbol,
                fetch_spot_latest_price=self.fetcher.fetch_spot_latest_price,
                degraded_reasons=degraded_reasons,
            )
        else:
            account_snapshot = {
                "account_scope": "sim_futures" if mode == "demo" else "personal_futures",
                "equity": primary_balance.get("equity") if primary_balance else None,
                "available_balance": primary_balance.get("available_balance") if primary_balance else None,
            }

        if normalized_market == "futures":
            tp_sl.update(
                _build_order_risk_tp_sl_state(
                    market="futures",
                    symbol=symbol,
                    position_side=str(order_preview.get("position_side") or ""),
                    preview_quantity=_to_float(order_preview.get("quantity")),
                    preview_has_take_profit=bool(tp_sl.get("has_take_profit")),
                    preview_has_stop_loss=bool(tp_sl.get("has_stop_loss")),
                    positions=positions,
                    open_orders=open_orders,
                    conditional_orders=conditional_orders,
                )
            )

        return {
            "trading_mode": mode,
            "environment": environment,
            "user_environment_prefix": _user_environment_prefix(environment),
            "order_preview": order_preview,
            "tp_sl": tp_sl,
            "account_snapshot": account_snapshot,
            "positions": positions,
            "recent_orders": recent_orders,
            "open_orders": open_orders,
            "conditional_orders": conditional_orders,
            "market_snapshot": {
                "symbol": symbol,
                "current_price": current_price,
            },
            "partial": partial,
            "degraded_reasons": degraded_reasons,
        }

    def collect_account_risk_payload(
        self,
        *,
        profile_name: str,
        market: str,
        trading_mode: str = DEFAULT_TRADING_MODE,
        symbol: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        normalized_market = _validate_market(market)
        mode = _validate_trading_mode_market(trading_mode, normalized_market)
        environment = _environment_for_trading_mode(mode, normalized_market)
        if normalized_market == "all":
            raise AggregationInputError("account risk scan requires a concrete market, not 'all'.")

        normalized_symbol = str(symbol).strip().upper() if symbol else None
        partial = False
        degraded_reasons: list[str] = []
        end_ms = int(time.time() * 1000)
        start_ms = max(0, end_ms - HOUR_MS)

        balances: list[dict[str, Any]]
        positions: list[dict[str, Any]]
        recent_orders: list[dict[str, Any]]
        open_orders: list[dict[str, Any]]
        conditional_orders: list[dict[str, Any]] = []
        current_price: float | None = None
        market_snapshot_symbol = normalized_symbol

        if normalized_market == "futures":
            balances = _normalize_balance_entries(
                self.fetcher.fetch_futures_balance(profile_name=profile_name, trading_mode=mode),
                "futures",
                trading_mode=mode,
            )
            positions = _normalize_positions(
                self.fetcher.fetch_futures_positions(profile_name=profile_name, trading_mode=mode),
                "futures",
                trading_mode=mode,
            )
            recent_orders = _normalize_orders(
                self.fetcher.fetch_futures_orders(
                    profile_name=profile_name,
                    trading_mode=mode,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    symbol=normalized_symbol,
                ),
                "futures",
                trading_mode=mode,
            )
            if mode == "demo":
                partial = True
                _merge_degraded_reasons(
                    degraded_reasons,
                    [
                        "demo_futures_open_orders_unavailable",
                        "demo_futures_conditional_orders_unavailable",
                        "demo_futures_tp_sl_state_unavailable",
                    ],
                )
                open_orders = []
                conditional_orders = []
            else:
                open_orders = _normalize_orders(
                    self.fetcher.fetch_futures_open_orders(
                        profile_name=profile_name,
                        symbol=normalized_symbol,
                    ),
                    "futures",
                )
                conditional_orders = _normalize_orders(
                    self.fetcher.fetch_futures_pending_orders(
                        profile_name=profile_name,
                        symbol=normalized_symbol,
                    ),
                    "futures",
                )
            if not market_snapshot_symbol:
                market_snapshot_symbol = _pick_primary_futures_symbol(
                    positions=positions,
                    recent_orders=recent_orders,
                    open_orders=open_orders,
                    conditional_orders=conditional_orders,
                )
            if market_snapshot_symbol:
                current_price = _safe_current_price(
                    fetch_latest_price=lambda: self.fetcher.fetch_futures_latest_price(symbol=market_snapshot_symbol),
                    market="futures",
                    symbol=market_snapshot_symbol,
                    degraded_reasons=degraded_reasons,
                    fetch_klines=lambda: self.fetcher.fetch_futures_klines(
                        symbol=market_snapshot_symbol,
                        start_ms=start_ms,
                        end_ms=end_ms,
                    ),
                )
                if current_price is None:
                    current_price = _estimate_futures_position_price(
                        positions=positions,
                        symbol=market_snapshot_symbol,
                    )
                    if current_price is not None:
                        _mark_market_snapshot_estimated(
                            degraded_reasons=degraded_reasons,
                            market="futures",
                            reason="futures_market_snapshot_estimated_from_position",
                        )
        else:
            balances, spot_balance_partial = self._collect_spot_balances(
                profile_name=profile_name,
                degraded_reasons=degraded_reasons,
            )
            partial = partial or spot_balance_partial
            positions = []
            recent_orders = _normalize_orders(
                self.fetcher.fetch_spot_orders(
                    profile_name=profile_name,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    symbol=normalized_symbol,
                ),
                "spot",
            )
            open_orders = _normalize_orders(
                self.fetcher.fetch_spot_open_orders(
                    profile_name=profile_name,
                    symbol=normalized_symbol,
                ),
                "spot",
            )
            if normalized_symbol:
                current_price = _safe_current_price(
                    fetch_latest_price=lambda: self.fetcher.fetch_spot_latest_price(symbol=normalized_symbol),
                    market="spot",
                    symbol=normalized_symbol,
                    degraded_reasons=degraded_reasons,
                    fetch_klines=lambda: self.fetcher.fetch_spot_klines(symbol=normalized_symbol),
                )
            _merge_degraded_reasons(degraded_reasons, ["spot_tp_sl_state_unavailable"])

        primary_balance = None
        if balances:
            primary_balance = next((row for row in balances if row.get("asset") == "USDT"), balances[0])
        if normalized_market == "spot":
            account_snapshot, positions = _build_spot_account_estimates(
                balances=balances,
                symbol=normalized_symbol,
                fetch_spot_latest_price=self.fetcher.fetch_spot_latest_price,
                degraded_reasons=degraded_reasons,
            )
        else:
            account_snapshot = {
                "account_scope": "sim_futures" if mode == "demo" else "personal_futures",
                "equity": primary_balance.get("equity") if primary_balance else None,
                "available_balance": primary_balance.get("available_balance") if primary_balance else None,
            }

        return {
            "mode": "account_scan",
            "trading_mode": mode,
            "environment": environment,
            "user_environment_prefix": _user_environment_prefix(environment, language),
            "market": normalized_market,
            "symbol": normalized_symbol,
            "account_snapshot": account_snapshot,
            "positions": positions,
            "recent_orders": recent_orders,
            "open_orders": open_orders,
            "conditional_orders": conditional_orders,
            "market_snapshot": {
                "symbol": market_snapshot_symbol,
                "current_price": current_price,
            },
            "partial": partial,
            "degraded_reasons": degraded_reasons,
            "constraints": [],
        }


class WeexApiFetcher:
    def _contract_module(self) -> Any:
        import weex_contract_api as contract_api

        return contract_api

    def _spot_module(self) -> Any:
        import weex_spot_api as spot_api

        return spot_api

    def _build_contract_client(self, profile_name: str) -> tuple[Any, Any]:
        contract_api = self._contract_module()
        contract_api.refresh_agent_records(command="trade-aggregator.contract")
        contract_api.ensure_private_runtime_ready(
            command="trade-aggregator.contract",
            auto_setup=True,
            language=None,
        )
        profile = contract_api.resolve_runtime_profile(
            requested_profile=profile_name,
            allow_invalid_default=False,
        )
        contract_api.require_private_profile(profile)
        env_base_url = os.getenv("WEEX_CONTRACT_API_BASE") or os.getenv("WEEX_API_BASE")
        base_url = (
            (profile.contract_base_url if profile else "")
            or env_base_url
            or contract_api.DEFAULT_BASE_URL
        )
        locale = os.getenv("WEEX_LOCALE") or contract_api.DEFAULT_LOCALE
        timeout = float(os.getenv("WEEX_API_TIMEOUT", contract_api.DEFAULT_TIMEOUT))
        client = contract_api.WeexContractClient(
            base_url=base_url,
            timeout=timeout,
            locale=locale,
            api_key=None,
            api_secret=None,
            api_passphrase=None,
            profile_name=profile.name if profile else None,
        )
        return contract_api, client

    def _build_public_contract_client(self) -> tuple[Any, Any]:
        contract_api = self._contract_module()
        contract_api.refresh_agent_records(command="trade-aggregator.contract.public")
        env_base_url = os.getenv("WEEX_CONTRACT_API_BASE") or os.getenv("WEEX_API_BASE")
        base_url = env_base_url or contract_api.DEFAULT_BASE_URL
        locale = os.getenv("WEEX_LOCALE") or contract_api.DEFAULT_LOCALE
        timeout = float(os.getenv("WEEX_API_TIMEOUT", contract_api.DEFAULT_TIMEOUT))
        client = contract_api.WeexContractClient(
            base_url=base_url,
            timeout=timeout,
            locale=locale,
            api_key=None,
            api_secret=None,
            api_passphrase=None,
            profile_name=None,
        )
        return contract_api, client

    def _build_spot_client(self, profile_name: str) -> tuple[Any, Any]:
        spot_api = self._spot_module()
        spot_api.refresh_agent_records(command="trade-aggregator.spot")
        spot_api.ensure_private_runtime_ready(
            command="trade-aggregator.spot",
            auto_setup=True,
            language=None,
        )
        profile = spot_api.resolve_runtime_profile(
            requested_profile=profile_name,
            allow_invalid_default=False,
        )
        spot_api.require_private_profile(profile)
        env_base_url = os.getenv("WEEX_SPOT_API_BASE") or os.getenv("WEEX_API_BASE")
        base_url = (
            (profile.spot_base_url if profile else "")
            or env_base_url
            or spot_api.DEFAULT_BASE_URL
        )
        locale = os.getenv("WEEX_LOCALE") or spot_api.DEFAULT_LOCALE
        timeout = float(os.getenv("WEEX_API_TIMEOUT", spot_api.DEFAULT_TIMEOUT))
        client = spot_api.WeexSpotClient(
            base_url=base_url,
            timeout=timeout,
            locale=locale,
            api_key=None,
            api_secret=None,
            api_passphrase=None,
            profile_name=profile.name if profile else None,
        )
        return spot_api, client

    def _build_public_spot_client(self) -> tuple[Any, Any]:
        spot_api = self._spot_module()
        spot_api.refresh_agent_records(command="trade-aggregator.spot.public")
        env_base_url = os.getenv("WEEX_SPOT_API_BASE") or os.getenv("WEEX_API_BASE")
        base_url = env_base_url or spot_api.DEFAULT_BASE_URL
        locale = os.getenv("WEEX_LOCALE") or spot_api.DEFAULT_LOCALE
        timeout = float(os.getenv("WEEX_API_TIMEOUT", spot_api.DEFAULT_TIMEOUT))
        client = spot_api.WeexSpotClient(
            base_url=base_url,
            timeout=timeout,
            locale=locale,
            api_key=None,
            api_secret=None,
            api_passphrase=None,
            profile_name=None,
        )
        return spot_api, client

    def _send_contract_request(
        self,
        *,
        profile_name: str,
        endpoint_key: str,
        query: dict[str, Any],
        body: dict[str, Any] | None = None,
        public: bool = False,
        trading_mode: str = DEFAULT_TRADING_MODE,
    ) -> Any:
        if public:
            contract_api, client = self._build_public_contract_client()
        else:
            contract_api, client = self._build_contract_client(profile_name)
        endpoint = contract_api.ENDPOINTS[endpoint_key]
        contract_api.validate_endpoint_trading_mode(endpoint, trading_mode)
        prepared = client.prepare_request(endpoint, query=query, body=body or {})
        response = client.send(prepared)
        if not response.get("ok"):
            raise AggregationInputError(
                f"Contract request failed for {endpoint_key}: {response.get('error')}"
            )
        return response.get("data")

    def _send_spot_request(
        self,
        *,
        profile_name: str,
        endpoint_key: str,
        query: dict[str, Any],
        body: dict[str, Any] | None = None,
        public: bool = False,
    ) -> Any:
        if public:
            spot_api, client = self._build_public_spot_client()
        else:
            spot_api, client = self._build_spot_client(profile_name)
        endpoint = spot_api.ENDPOINTS[endpoint_key]
        prepared = client.prepare_request(endpoint, query=query, body=body or {})
        response = client.send(prepared)
        if not response.get("ok"):
            raise AggregationInputError(
                f"Spot request failed for {endpoint_key}: {response.get('error')}"
            )
        return response.get("data")

    def _build_meta_payload(
        self,
        rows: list[dict[str, Any]],
        *,
        partial: bool,
        degraded_reasons: list[str],
    ) -> Any:
        if not partial and not degraded_reasons:
            return rows
        return {
            "items": rows,
            "_meta": {
                "partial": partial,
                "degraded_reasons": degraded_reasons,
            },
        }

    def fetch_futures_balance(
        self,
        *,
        profile_name: str,
        trading_mode: str = DEFAULT_TRADING_MODE,
    ) -> Any:
        mode = _normalize_trading_mode(trading_mode)
        endpoint_key = "sim.account.get_account_balance" if mode == "demo" else "account.get_account_balance"
        kwargs: dict[str, Any] = {
            "profile_name": profile_name,
            "endpoint_key": endpoint_key,
            "query": {},
        }
        if mode == "demo":
            kwargs["trading_mode"] = mode
        return self._send_contract_request(**kwargs)

    def fetch_futures_positions(
        self,
        *,
        profile_name: str,
        trading_mode: str = DEFAULT_TRADING_MODE,
    ) -> Any:
        mode = _normalize_trading_mode(trading_mode)
        endpoint_key = "sim.account.get_all_positions" if mode == "demo" else "account.get_all_positions"
        kwargs: dict[str, Any] = {
            "profile_name": profile_name,
            "endpoint_key": endpoint_key,
            "query": {},
        }
        if mode == "demo":
            kwargs["trading_mode"] = mode
        return self._send_contract_request(**kwargs)

    def fetch_futures_orders(
        self,
        *,
        profile_name: str,
        trading_mode: str = DEFAULT_TRADING_MODE,
        start_ms: int,
        end_ms: int,
        symbol: str | None,
    ) -> Any:
        mode = _normalize_trading_mode(trading_mode)
        endpoint_key = "sim.transaction.get_order_history" if mode == "demo" else "transaction.get_order_history"
        rows: list[dict[str, Any]] = []
        for window in split_time_range(start_ms, end_ms, max_span_days=MAX_FUTURES_WINDOW_DAYS):
            page = 0
            while True:
                query: dict[str, Any] = {
                    "startTime": window.start_ms,
                    "endTime": window.end_ms,
                    "limit": FUTURES_ORDER_LIMIT,
                    "page": page,
                }
                if symbol:
                    query["symbol"] = symbol
                kwargs: dict[str, Any] = {
                    "profile_name": profile_name,
                    "endpoint_key": endpoint_key,
                    "query": query,
                }
                if mode == "demo":
                    kwargs["trading_mode"] = mode
                payload = self._send_contract_request(**kwargs)
                page_rows = _extract_list_payload(payload, "items", "orders")
                if not page_rows:
                    break
                _extend_unique_dict_rows(
                    rows,
                    page_rows,
                    identity_keys=("orderId", "clientOrderId", "time", "symbol"),
                )
                if len(page_rows) < FUTURES_ORDER_LIMIT:
                    break
                page += 1
        return rows

    def fetch_futures_fills(
        self,
        *,
        profile_name: str,
        start_ms: int,
        end_ms: int,
        symbol: str | None,
    ) -> Any:
        rows: list[dict[str, Any]] = []
        degraded_reasons: list[str] = []
        partial = False

        def collect_window(window_start: int, window_end: int) -> None:
            nonlocal partial
            query: dict[str, Any] = {
                "startTime": window_start,
                "endTime": window_end,
                "limit": FUTURES_FILL_LIMIT,
            }
            if symbol:
                query["symbol"] = symbol
            payload = self._send_contract_request(
                profile_name=profile_name,
                endpoint_key="transaction.get_trade_details",
                query=query,
            )
            page_rows = _extract_list_payload(payload, "items", "trades", "fills")
            if len(page_rows) >= FUTURES_FILL_LIMIT and (window_end - window_start) > MIN_SPLIT_WINDOW_MS:
                midpoint = window_start + ((window_end - window_start) // 2)
                collect_window(window_start, midpoint)
                collect_window(midpoint + 1, window_end)
                return
            if len(page_rows) >= FUTURES_FILL_LIMIT and (window_end - window_start) <= MIN_SPLIT_WINDOW_MS:
                partial = True
                _merge_degraded_reasons(degraded_reasons, ["futures_fills_limit_hit"])
            _extend_unique_dict_rows(rows, page_rows, identity_keys=("id", "tradeId", "orderId", "time", "symbol"))

        for window in split_time_range(start_ms, end_ms, max_span_days=MAX_FUTURES_FILLS_WINDOW_DAYS):
            collect_window(window.start_ms, window.end_ms)
        return self._build_meta_payload(rows, partial=partial, degraded_reasons=degraded_reasons)

    def fetch_futures_historical_pending_orders(
        self,
        *,
        profile_name: str,
        start_ms: int,
        end_ms: int,
        symbol: str | None,
    ) -> Any:
        rows: list[dict[str, Any]] = []
        for window in split_time_range(start_ms, end_ms, max_span_days=MAX_FUTURES_WINDOW_DAYS):
            page = 1
            while True:
                query: dict[str, Any] = {
                    "startTime": window.start_ms,
                    "endTime": window.end_ms,
                    "limit": FUTURES_ORDER_LIMIT,
                    "page": page,
                }
                if symbol:
                    query["symbol"] = symbol
                payload = self._send_contract_request(
                    profile_name=profile_name,
                    endpoint_key="transaction.get_historical_pending_orders",
                    query=query,
                )
                page_rows = _extract_list_payload(payload, "items", "orders")
                if not page_rows:
                    break
                _extend_unique_dict_rows(
                    rows,
                    page_rows,
                    identity_keys=("algoId", "actualOrderId", "createTime", "symbol"),
                )
                has_more = bool((payload or {}).get("hasMore")) if isinstance(payload, dict) else False
                if not has_more:
                    break
                page += 1
        return rows

    def fetch_futures_bills(
        self,
        *,
        profile_name: str,
        start_ms: int,
        end_ms: int,
        symbol: str | None,
    ) -> Any:
        rows: list[dict[str, Any]] = []
        degraded_reasons: list[str] = []
        partial = False

        def collect_window(window_start: int, window_end: int) -> None:
            nonlocal partial
            body: dict[str, Any] = {
                "startTime": window_start,
                "endTime": window_end,
                "limit": FUTURES_BILL_LIMIT,
            }
            if symbol:
                body["symbol"] = symbol
            payload = self._send_contract_request(
                profile_name=profile_name,
                endpoint_key="account.get_contract_bills",
                query={},
                body=body,
            )
            page_rows = _extract_list_payload(payload, "items", "bills")
            has_next = bool((payload or {}).get("hasNextPage")) if isinstance(payload, dict) else False
            if has_next and (window_end - window_start) > MIN_SPLIT_WINDOW_MS:
                midpoint = window_start + ((window_end - window_start) // 2)
                collect_window(window_start, midpoint)
                collect_window(midpoint + 1, window_end)
                return
            if has_next:
                partial = True
                _merge_degraded_reasons(degraded_reasons, ["futures_bills_window_truncated"])
            _extend_unique_dict_rows(rows, page_rows, identity_keys=("billId", "time", "symbol", "incomeType"))

        for window in split_time_range(start_ms, end_ms, max_span_days=MAX_BILLS_WINDOW_DAYS):
            collect_window(window.start_ms, window.end_ms)
        return self._build_meta_payload(rows, partial=partial, degraded_reasons=degraded_reasons)

    def fetch_futures_klines(
        self,
        *,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> Any:
        rows: list[list[Any]] = []
        cursor = start_ms
        chunk_ms = KLINE_LIMIT * HOUR_MS
        while cursor <= end_ms:
            chunk_end = min(end_ms, cursor + chunk_ms - 1)
            payload = self._send_contract_request(
                profile_name="",
                endpoint_key="market.get_history_klines",
                query={
                    "symbol": symbol,
                    "interval": "1h",
                    "startTime": cursor,
                    "endTime": chunk_end,
                    "limit": KLINE_LIMIT,
                    "priceType": "LAST",
                },
                public=True,
            )
            if isinstance(payload, list):
                rows.extend(payload)
            cursor = chunk_end + 1
        return rows

    def fetch_spot_balance(self, *, profile_name: str) -> Any:
        return self._send_spot_request(
            profile_name=profile_name,
            endpoint_key="spot.account.get_account_balance",
            query={},
        )

    def fetch_spot_latest_price(self, *, symbol: str) -> Any:
        return self._send_spot_request(
            profile_name="",
            endpoint_key="spot.market.get_ticker_info",
            query={"symbol": symbol},
            public=True,
        )

    def fetch_spot_orders(
        self,
        *,
        profile_name: str,
        start_ms: int,
        end_ms: int,
        symbol: str | None,
    ) -> Any:
        if not symbol:
            return []
        rows: list[dict[str, Any]] = []
        for window in split_time_range(start_ms, end_ms, max_span_days=MAX_SPOT_HISTORY_WINDOW_DAYS):
            page = 1
            request_limit = SPOT_ORDER_LIMIT
            while True:
                try:
                    payload = self._send_spot_request(
                        profile_name=profile_name,
                        endpoint_key="spot.order.history_orders",
                        query={
                            "symbol": symbol,
                            "startTime": window.start_ms,
                            "endTime": window.end_ms,
                            "limit": request_limit,
                            "page": page,
                        },
                    )
                except AggregationInputError as exc:
                    if page == 1 and _should_retry_spot_history_orders_with_safe_limit(exc, limit=request_limit):
                        request_limit = SPOT_ORDER_SAFE_LIMIT
                        continue
                    raise
                page_rows = _extract_list_payload(payload, "items", "orders")
                if not page_rows:
                    break
                _extend_unique_dict_rows(rows, page_rows, identity_keys=("orderId", "clientOrderId", "time", "symbol"))
                if len(page_rows) < request_limit:
                    break
                page += 1
        return rows

    def fetch_spot_fills(
        self,
        *,
        profile_name: str,
        start_ms: int,
        end_ms: int,
        symbol: str | None,
    ) -> Any:
        if not symbol:
            return []
        rows: list[dict[str, Any]] = []
        degraded_reasons: list[str] = []
        partial = False

        def collect_window(window_start: int, window_end: int) -> None:
            nonlocal partial
            payload = self._send_spot_request(
                profile_name=profile_name,
                endpoint_key="spot.order.transaction_details",
                query={
                    "symbol": symbol,
                    "startTime": window_start,
                    "endTime": window_end,
                    "limit": SPOT_FILL_LIMIT,
                },
            )
            page_rows = _extract_list_payload(payload, "items", "trades", "fills")
            if len(page_rows) >= SPOT_FILL_LIMIT and (window_end - window_start) > MIN_SPLIT_WINDOW_MS:
                midpoint = window_start + ((window_end - window_start) // 2)
                collect_window(window_start, midpoint)
                collect_window(midpoint + 1, window_end)
                return
            if len(page_rows) >= SPOT_FILL_LIMIT and (window_end - window_start) <= MIN_SPLIT_WINDOW_MS:
                partial = True
                _merge_degraded_reasons(degraded_reasons, ["spot_fills_limit_hit"])
            _extend_unique_dict_rows(rows, page_rows, identity_keys=("id", "orderId", "time", "symbol"))

        for window in split_time_range(start_ms, end_ms, max_span_days=MAX_SPOT_HISTORY_WINDOW_DAYS):
            collect_window(window.start_ms, window.end_ms)
        return self._build_meta_payload(rows, partial=partial, degraded_reasons=degraded_reasons)

    def fetch_spot_bills(
        self,
        *,
        profile_name: str,
        start_ms: int,
        end_ms: int,
        symbol: str | None,
    ) -> Any:
        del symbol
        rows: list[dict[str, Any]] = []
        degraded_reasons: list[str] = []
        partial = False

        def collect_window(window_start: int, window_end: int) -> None:
            nonlocal partial
            payload = self._send_spot_request(
                profile_name=profile_name,
                endpoint_key="spot.account.get_bill_records",
                query={},
                body={
                    "after": window_start,
                    "before": window_end,
                    "limit": SPOT_BILL_LIMIT,
                },
            )
            page_rows = _extract_list_payload(payload, "items", "bills")
            if len(page_rows) >= SPOT_BILL_LIMIT and (window_end - window_start) > MIN_SPLIT_WINDOW_MS:
                midpoint = window_start + ((window_end - window_start) // 2)
                collect_window(window_start, midpoint)
                collect_window(midpoint + 1, window_end)
                return
            if len(page_rows) >= SPOT_BILL_LIMIT and (window_end - window_start) <= MIN_SPLIT_WINDOW_MS:
                partial = True
                _merge_degraded_reasons(degraded_reasons, ["spot_bills_window_truncated"])
            _extend_unique_dict_rows(rows, page_rows, identity_keys=("billId", "cTime", "coinName", "bizType"))

        for window in split_time_range(start_ms, end_ms, max_span_days=MAX_SPOT_HISTORY_WINDOW_DAYS):
            collect_window(window.start_ms, window.end_ms)
        return self._build_meta_payload(rows, partial=partial, degraded_reasons=degraded_reasons)

    def fetch_spot_klines(
        self,
        *,
        symbol: str,
    ) -> Any:
        return self._send_spot_request(
            profile_name="",
            endpoint_key="spot.market.get_k_line_data",
            query={
                "symbol": symbol,
                "interval": "1h",
            },
            public=True,
        )

    def fetch_futures_latest_price(self, *, symbol: str) -> Any:
        return self._send_contract_request(
            profile_name="",
            endpoint_key="market.get_symbol_price",
            query={
                "symbol": symbol,
                "priceType": "MARK",
            },
            public=True,
        )

    def fetch_futures_open_orders(
        self,
        *,
        profile_name: str,
        symbol: str | None,
    ) -> Any:
        rows: list[dict[str, Any]] = []
        page = 0
        while True:
            query: dict[str, Any] = {
                "limit": FUTURES_OPEN_ORDER_LIMIT,
                "page": page,
            }
            if symbol:
                query["symbol"] = symbol
            payload = self._send_contract_request(
                profile_name=profile_name,
                endpoint_key="transaction.get_current_order_status",
                query=query,
            )
            page_rows = _extract_list_payload(payload, "items", "orders")
            if not page_rows:
                break
            _extend_unique_dict_rows(
                rows,
                page_rows,
                identity_keys=("orderId", "clientOrderId", "time", "symbol"),
            )
            if len(page_rows) < FUTURES_OPEN_ORDER_LIMIT:
                break
            page += 1
        return rows

    def fetch_futures_pending_orders(
        self,
        *,
        profile_name: str,
        symbol: str | None,
    ) -> Any:
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            query: dict[str, Any] = {
                "page": page,
                "limit": FUTURES_PENDING_LIMIT,
            }
            if symbol:
                query["symbol"] = symbol
            payload = self._send_contract_request(
                profile_name=profile_name,
                endpoint_key="transaction.get_current_pending_orders",
                query=query,
            )
            page_rows = _extract_list_payload(payload, "items", "orders")
            if not page_rows:
                break
            _extend_unique_dict_rows(rows, page_rows, identity_keys=("algoId", "actualOrderId", "createTime", "symbol"))
            if len(page_rows) < FUTURES_PENDING_LIMIT:
                break
            page += 1
        return rows

    def fetch_spot_open_orders(
        self,
        *,
        profile_name: str,
        symbol: str | None,
    ) -> Any:
        query: dict[str, Any] = {}
        if symbol:
            query["symbol"] = symbol
        return self._send_spot_request(
            profile_name=profile_name,
            endpoint_key="spot.order.unfinished_orders",
            query=query,
        )


def _output_json(payload: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


def _output_error(error: str, pretty: bool) -> None:
    _output_json({"ok": False, "error": error}, pretty)


def _arg_value(args: argparse.Namespace, name: str, default: Any = None) -> Any:
    return vars(args).get(name, default)


def _parse_order_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid --order-json payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("--order-json must decode to a JSON object.")
    return payload


def cmd_collect_replay(args: argparse.Namespace) -> int:
    payload = TradeDataAggregator().collect_replay_payload(
        profile_name=args.profile,
        market=args.market,
        trading_mode=getattr(args, "trading_mode", DEFAULT_TRADING_MODE),
        period=args.period,
        symbol=args.symbol,
        focus=args.focus,
    )
    _output_json(payload, args.pretty)
    return 0


def cmd_collect_profile(args: argparse.Namespace) -> int:
    payload = TradeDataAggregator().collect_profile_payload(
        profile_name=args.profile,
        market=args.market,
        trading_mode=getattr(args, "trading_mode", DEFAULT_TRADING_MODE),
        symbol=args.symbol,
    )
    _output_json(payload, args.pretty)
    return 0


def cmd_collect_order_risk(args: argparse.Namespace) -> int:
    payload = TradeDataAggregator().collect_order_risk_payload(
        profile_name=args.profile,
        market=args.market,
        trading_mode=getattr(args, "trading_mode", DEFAULT_TRADING_MODE),
        raw_order=_parse_order_json(args.order_json),
    )
    _output_json(payload, args.pretty)
    return 0


def cmd_collect_account_risk(args: argparse.Namespace) -> int:
    payload = TradeDataAggregator().collect_account_risk_payload(
        profile_name=args.profile,
        market=args.market,
        trading_mode=getattr(args, "trading_mode", DEFAULT_TRADING_MODE),
        symbol=args.symbol,
        language=_arg_value(args, "language", None),
    )
    _output_json(payload, args.pretty)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect normalized WEEX trading data for replay, profile, and risk analysis."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_replay = subparsers.add_parser("collect-replay", help="Collect replay payload data.")
    collect_replay.add_argument("--profile", required=True, help="Saved profile name.")
    collect_replay.add_argument("--market", required=True, choices=("futures", "spot", "all"))
    collect_replay.add_argument("--trading-mode", choices=TRADING_MODES, default=DEFAULT_TRADING_MODE)
    collect_replay.add_argument("--period", required=True, choices=COLLECTION_PERIODS)
    collect_replay.add_argument("--symbol", default=None, help="Trading pair symbol when required.")
    collect_replay.add_argument("--focus", default=None, help="Optional replay focus tag.")
    collect_replay.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    collect_profile = subparsers.add_parser("collect-profile", help="Collect profile payload data.")
    collect_profile.add_argument("--profile", required=True, help="Saved profile name.")
    collect_profile.add_argument("--market", required=True, choices=("futures", "spot", "all"))
    collect_profile.add_argument("--trading-mode", choices=TRADING_MODES, default=DEFAULT_TRADING_MODE)
    collect_profile.add_argument("--symbol", default=None, help="Trading pair symbol when required.")
    collect_profile.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    collect_order_risk = subparsers.add_parser("collect-order-risk", help="Collect order-risk payload data.")
    collect_order_risk.add_argument("--profile", required=True, help="Saved profile name.")
    collect_order_risk.add_argument("--market", required=True, choices=("futures", "spot"))
    collect_order_risk.add_argument("--trading-mode", choices=TRADING_MODES, default=DEFAULT_TRADING_MODE)
    collect_order_risk.add_argument("--order-json", required=True, help="JSON order payload.")
    collect_order_risk.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    collect_account_risk = subparsers.add_parser("collect-account-risk", help="Collect account-risk payload data.")
    collect_account_risk.add_argument("--profile", required=True, help="Saved profile name.")
    collect_account_risk.add_argument("--market", required=True, choices=("futures", "spot"))
    collect_account_risk.add_argument("--trading-mode", choices=TRADING_MODES, default=DEFAULT_TRADING_MODE)
    collect_account_risk.add_argument("--symbol", default=None, help="Optional trading pair focus.")
    collect_account_risk.add_argument("--language", choices=("zh", "en"), default=None, help="Language for user-facing environment prefix.")
    collect_account_risk.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "collect-replay":
            return cmd_collect_replay(args)
        if args.command == "collect-profile":
            return cmd_collect_profile(args)
        if args.command == "collect-order-risk":
            return cmd_collect_order_risk(args)
        if args.command == "collect-account-risk":
            return cmd_collect_account_risk(args)
        raise SystemExit(f"Unsupported command: {args.command}")
    except AggregationInputError as exc:
        _output_error(str(exc), bool(getattr(args, "pretty", False)))
        return 1


__all__ = [
    "AggregationInputError",
    "DAY_MS",
    "PROFILE_PERIODS",
    "REPLAY_PERIODS",
    "TimeWindow",
    "TradeDataAggregator",
    "WeexApiFetcher",
    "build_parser",
    "cmd_collect_account_risk",
    "cmd_collect_order_risk",
    "cmd_collect_profile",
    "cmd_collect_replay",
    "main",
    "split_time_range",
]


if __name__ == "__main__":
    raise SystemExit(main())
