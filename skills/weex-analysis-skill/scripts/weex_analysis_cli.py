#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import weex_risk_review_core as risk_review_core


SNAPSHOT_POSITION_KEYS = ("positions", "positionList", "items")
FILL_KEYS = ("fills", "trades", "items")
BILL_KEYS = ("bills",)
LONG_SIDES = {"long", "buy", "bull"}
SHORT_SIDES = {"short", "sell", "bear"}
ORDER_KEYS = ("orders", "items")
HOUR_MS = 60 * 60 * 1000
WINDOW_30_MIN_MS = 30 * 60 * 1000
WINDOW_60_MIN_MS = 60 * 60 * 1000
EIGHT_HOURS_MS = 8 * HOUR_MS
POSITION_EPSILON = Decimal("0.00000001")
PRICE_CONTEXT_LOOKBACK_CANDLES = 6
PRICE_CONTEXT_FORWARD_HOURS = 3
PRICE_RESET_VOL_MULTIPLIER = Decimal("1.5")
PRICE_FOLLOW_THROUGH_VOL_MULTIPLIER = Decimal("1.0")
PRICE_CONTEXT_MIN_RESET_RATIO = Decimal("0.002")
PRICE_CONTEXT_MIN_FOLLOW_THROUGH_RATIO = Decimal("0.001")
PRICE_CONTEXT_HIGH_FREQUENCY_VOL_MULTIPLIER = Decimal("1.5")
PRICE_CONTEXT_HIGH_FREQUENCY_HIGH_VOL_MIN_RATIO = Decimal("0.01")
PRICE_CONTEXT_HIGH_FREQUENCY_QUIET_RATIO = Decimal("0.0015")
HIGH_FREQUENCY_DEFAULT_THRESHOLD = 3
HIGH_FREQUENCY_ELEVATED_VOL_THRESHOLD = 5
PROTECTIVE_ORDER_TYPES = {
    "take_profit",
    "take_profit_market",
    "stop",
    "stop_market",
    "trailing_stop_market",
}
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
STANDARD_ANALYSIS_DISCLAIMER = (
    "Disclaimer: This result is generated solely from the current input data and is for reference only. "
    "It does not constitute any investment or trading advice. Please make your own independent judgment "
    "based on real-time data, official rules, and your own risk tolerance. Responsibility for related "
    "decisions and execution rests solely with the user."
)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 8)


def _ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in (None, Decimal("0")):
        return None
    return numerator / denominator


def _compute_profile_risk_score(
    *,
    win_rate: Decimal | None,
    profit_factor: Decimal | None,
    active_day_trade_average: Decimal | None,
    median_hold_ms: Decimal | None,
) -> Decimal | None:
    risk_score = Decimal("0")
    risk_inputs_available = False
    if win_rate is not None:
        risk_inputs_available = True
        if win_rate < Decimal("0.5"):
            risk_score += Decimal("0.2")
        if win_rate < Decimal("0.4"):
            risk_score += Decimal("0.15")
    if profit_factor is not None:
        risk_inputs_available = True
        if profit_factor < Decimal("1"):
            risk_score += Decimal("0.2")
        if profit_factor < Decimal("0.5"):
            risk_score += Decimal("0.15")
        if profit_factor < Decimal("0.25"):
            risk_score += Decimal("0.15")
    if active_day_trade_average is not None:
        risk_inputs_available = True
        if active_day_trade_average >= Decimal("5"):
            risk_score += Decimal("0.15")
    if median_hold_ms is not None:
        risk_inputs_available = True
        if median_hold_ms < Decimal(str(2 * 60 * 60 * 1000)):
            risk_score += Decimal("0.15")
    if not risk_inputs_available:
        return None
    return min(risk_score, Decimal("1.0"))


def _status_context(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "partial": False,
            "degraded_reasons": [],
            "constraints": [],
        }

    return {
        "partial": bool(payload.get("partial")),
        "degraded_reasons": [str(item) for item in payload.get("degraded_reasons", []) if str(item)],
        "constraints": [item for item in payload.get("constraints", []) if isinstance(item, dict)],
    }


def _payload_environment_context(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    environment = payload.get("environment")
    if not isinstance(environment, dict):
        environment = {}
    trading_mode = payload.get("trading_mode") or environment.get("trading_mode")
    account_scope = payload.get("account_scope")
    language = payload.get("language") or payload.get("locale") or environment.get("language")
    context: dict[str, Any] = {}
    if trading_mode not in (None, ""):
        context["trading_mode"] = str(trading_mode)
    if environment:
        context["environment"] = dict(environment)
    if account_scope not in (None, ""):
        context["account_scope"] = str(account_scope)
    if language not in (None, ""):
        context["language"] = str(language)
    return context


def _attach_payload_context(result: dict[str, Any], payload: Any) -> dict[str, Any]:
    context = _payload_environment_context(payload)
    if not context:
        return result
    updated = dict(result)
    updated.update(context)
    return updated


def _attach_standard_disclaimer(result: dict[str, Any]) -> dict[str, Any]:
    updated = dict(result)
    updated["disclaimer"] = STANDARD_ANALYSIS_DISCLAIMER
    return updated


def _result_language(result: dict[str, Any], environment: dict[str, Any]) -> str:
    language = str(result.get("language") or environment.get("language") or "").strip().lower()
    if language.startswith("zh"):
        return "zh"
    return "en"


def _user_facing_trading_mode_label(environment: dict[str, Any], result: dict[str, Any]) -> str:
    mode = str(environment.get("trading_mode") or result.get("trading_mode") or "").strip().lower()
    if mode == "demo":
        if _result_language(result, environment) == "zh":
            return "模拟盘"
        return "demo trading"
    if mode == "live":
        if _result_language(result, environment) == "zh":
            return "真实盘"
        return "real trading"
    return mode or "unknown"


def _append_standard_disclaimer(lines: list[str], disclaimer: Any) -> None:
    text = str(disclaimer or STANDARD_ANALYSIS_DISCLAIMER).strip()
    if not text:
        return
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(text)


def _environment_text_lines(result: dict[str, Any]) -> list[str]:
    environment = result.get("environment")
    if not isinstance(environment, dict):
        environment = {}
    if not environment and result.get("trading_mode") in (None, ""):
        return []
    lines = [
        f"Trading Mode: {_user_facing_trading_mode_label(environment, result)}",
    ]
    if environment.get("market") not in (None, ""):
        lines.append(f"Market: {environment['market']}")
    if "uses_real_funds" in environment:
        lines.append(f"Uses Real Funds: {str(bool(environment['uses_real_funds'])).lower()}")
    if environment.get("notice"):
        lines.append(f"Trading Notice: {environment['notice']}")
    return lines


def _prepend_environment_text(lines: list[str], result: dict[str, Any]) -> None:
    environment_lines = _environment_text_lines(result)
    if not environment_lines:
        return
    lines[:0] = [*environment_lines, ""]


def _concentration_alert_is_material(
    *,
    largest_notional: Decimal,
    total_notional: Decimal,
    leverage_estimate: Decimal | None,
) -> bool:
    if total_notional <= 0:
        return False
    largest_share = _ratio(largest_notional, total_notional)
    if largest_share is None or largest_share < Decimal("0.6"):
        return False
    if leverage_estimate is None:
        return True
    return leverage_estimate >= Decimal("0.25")


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _load_json_input(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _extract_list(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    data = payload.get("data")
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

    return []


def _extract_balance(payload: Any, *keys: str) -> Decimal | None:
    if isinstance(payload, dict):
        direct = _pick(payload, *keys)
        value = _to_decimal(direct)
        if value is not None:
            return value
        data = payload.get("data")
        if isinstance(data, dict):
            nested = _pick(data, *keys)
            return _to_decimal(nested)
    return None


def _coerce_time(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _normalize_side(raw_side: Any, quantity: Decimal | None) -> str:
    side_text = str(raw_side or "").strip().lower()
    if side_text in LONG_SIDES:
        return "long"
    if side_text in SHORT_SIDES:
        return "short"
    if quantity is not None and quantity < 0:
        return "short"
    return "long"


def _compute_unrealized_pnl(side: str, quantity: Decimal | None, entry_price: Decimal | None, mark_price: Decimal | None) -> Decimal | None:
    if quantity is None or entry_price is None or mark_price is None:
        return None
    signed_qty = abs(quantity)
    if side == "short":
        return (entry_price - mark_price) * signed_qty
    return (mark_price - entry_price) * signed_qty


def _normalize_position_side(raw_position_side: Any, *, market: Any = None, fallback_side: Any = None) -> str | None:
    side_text = str(raw_position_side or "").strip().lower()
    if side_text in LONG_SIDES:
        return "long"
    if side_text in SHORT_SIDES:
        return "short"

    market_text = str(market or "").strip().lower()
    if market_text == "spot":
        return "long"

    fallback_text = str(fallback_side or "").strip().lower()
    if fallback_text in LONG_SIDES:
        return "long"
    if fallback_text in SHORT_SIDES:
        return "short"
    return None


def _normalize_position_mode(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().upper()
    if normalized in {"ONE_WAY", "ONEWAY"}:
        return "COMBINED"
    if normalized == "HEDGE":
        return "SEPARATED"
    return normalized or None


def _normalize_margin_type(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().upper()
    return normalized or None


def normalize_position(position: dict[str, Any]) -> dict[str, Any]:
    quantity = _to_decimal(
        _pick(position, "quantity", "qty", "size", "position_size", "positionAmt", "contracts")
    )
    side = _normalize_side(_pick(position, "side", "positionSide", "direction"), quantity)
    entry_price = _to_decimal(_pick(position, "entry_price", "entryPrice", "avgEntryPrice", "average_open_price"))
    mark_price = _to_decimal(_pick(position, "mark_price", "markPrice", "current_price", "last_price"))
    leverage = _to_decimal(_pick(position, "leverage", "lev"))
    notional = _to_decimal(_pick(position, "notional", "position_value", "value", "mark_value"))
    if notional is None and quantity is not None and mark_price is not None:
        notional = abs(quantity) * mark_price
    unrealized_pnl = _to_decimal(_pick(position, "unrealized_pnl", "unrealizedPnl", "upl", "floating_profit"))
    if unrealized_pnl is None:
        unrealized_pnl = _compute_unrealized_pnl(side, quantity, entry_price, mark_price)

    return {
        "symbol": str(_pick(position, "symbol", "instId", "instrument", "market") or "UNKNOWN"),
        "side": side,
        "quantity": _decimal_to_float(abs(quantity) if quantity is not None else None),
        "entry_price": _decimal_to_float(entry_price),
        "mark_price": _decimal_to_float(mark_price),
        "notional": _decimal_to_float(notional),
        "leverage": _decimal_to_float(leverage),
        "unrealized_pnl": _decimal_to_float(unrealized_pnl),
    }


def analyze_snapshot(payload: Any) -> dict[str, Any]:
    positions = [normalize_position(item) for item in _extract_list(payload, SNAPSHOT_POSITION_KEYS)]
    status_context = _status_context(payload)
    account_snapshot = dict(payload.get("account_snapshot") or {}) if isinstance(payload, dict) else {}
    equity = _extract_balance(payload, "equity", "total_equity", "account_equity", "balance", "margin_balance")
    available_balance = _extract_balance(payload, "available_balance", "availableBalance", "free_collateral", "available")
    if equity is None and account_snapshot:
        equity = _extract_balance(account_snapshot, "equity", "total_equity", "account_equity", "balance", "margin_balance")
    if available_balance is None and account_snapshot:
        available_balance = _extract_balance(account_snapshot, "available_balance", "availableBalance", "free_collateral", "available")

    long_notional = Decimal("0")
    short_notional = Decimal("0")
    gross_notional = Decimal("0")
    net_notional = Decimal("0")
    high_leverage_symbols: list[str] = []
    largest_position: dict[str, Any] | None = None
    largest_notional = Decimal("0")

    normalized_positions: list[dict[str, Any]] = []
    for position in positions:
        notional = _to_decimal(position["notional"]) or Decimal("0")
        leverage = _to_decimal(position["leverage"])
        side = str(position["side"])

        gross_notional += abs(notional)
        if side == "short":
            short_notional += abs(notional)
            net_notional -= abs(notional)
        else:
            long_notional += abs(notional)
            net_notional += abs(notional)

        if leverage is not None and leverage >= Decimal("20"):
            high_leverage_symbols.append(str(position["symbol"]))

        if abs(notional) >= largest_notional:
            largest_notional = abs(notional)
            largest_position = dict(position)

        normalized_positions.append(position)

    if normalized_positions:
        if equity is None:
            status_context["partial"] = True
            _merge_reason_code(status_context["degraded_reasons"], "snapshot_missing_equity")
        if available_balance is None:
            status_context["partial"] = True
            _merge_reason_code(status_context["degraded_reasons"], "snapshot_missing_available_balance")
        for position in normalized_positions:
            if position.get("mark_price") is None and position.get("notional") is None:
                status_context["partial"] = True
                _merge_reason_code(status_context["degraded_reasons"], "snapshot_position_missing_mark_price")
            if position.get("leverage") is None:
                status_context["partial"] = True
                _merge_reason_code(status_context["degraded_reasons"], "snapshot_position_missing_leverage")

    gross_leverage_estimate = _ratio(gross_notional, equity)
    free_balance_ratio = _ratio(available_balance, equity)

    risk_flags: list[dict[str, str]] = []
    if normalized_positions:
        if gross_notional > 0 and largest_position is not None:
            largest_share = _ratio(largest_notional, gross_notional) or Decimal("0")
            largest_position["share_of_gross"] = _decimal_to_float(largest_share)
            if _concentration_alert_is_material(
                largest_notional=largest_notional,
                total_notional=gross_notional,
                leverage_estimate=gross_leverage_estimate,
            ):
                risk_flags.append(
                    {
                        "code": "concentration",
                        "severity": "warning",
                        "message": f"{largest_position['symbol']} represents {round(float(largest_share * 100), 2)}% of gross notional.",
                    }
                )
        if gross_leverage_estimate is not None and gross_leverage_estimate >= Decimal("5"):
            risk_flags.append(
                {
                    "code": "gross_leverage",
                    "severity": "warning",
                    "message": f"Estimated gross leverage is {round(float(gross_leverage_estimate), 2)}x equity.",
                }
            )
        if free_balance_ratio is not None and free_balance_ratio < Decimal("0.2"):
            risk_flags.append(
                {
                    "code": "low_free_balance",
                    "severity": "warning",
                    "message": f"Free balance ratio is only {round(float(free_balance_ratio * 100), 2)}% of equity.",
                }
            )
        if high_leverage_symbols:
            risk_flags.append(
                {
                    "code": "high_position_leverage",
                    "severity": "warning",
                    "message": f"High leverage positions detected on {', '.join(sorted(high_leverage_symbols))}.",
                }
            )
    else:
        risk_flags.append(
            {
                "code": "no_positions",
                "severity": "info",
                "message": "The snapshot does not contain any open positions.",
            }
        )

    direction = "flat"
    if net_notional > 0:
        direction = "net_long"
    elif net_notional < 0:
        direction = "net_short"

    summary_lines = [
        f"Positions: {len(normalized_positions)}",
        f"Gross notional: {round(float(gross_notional), 4)}",
        f"Net direction: {direction}",
    ]
    if equity is not None:
        summary_lines.append(f"Equity: {round(float(equity), 4)}")
    if available_balance is not None:
        summary_lines.append(f"Available balance: {round(float(available_balance), 4)}")
    if largest_position is not None:
        share = largest_position.get("share_of_gross")
        if share is not None:
            summary_lines.append(
                f"Largest position: {largest_position['symbol']} ({round(float(share) * 100, 2)}% of gross)"
            )

    result = _attach_payload_context({
        "positions_count": len(normalized_positions),
        "equity": _decimal_to_float(equity),
        "available_balance": _decimal_to_float(available_balance),
        "gross_notional": _decimal_to_float(gross_notional),
        "net_notional": _decimal_to_float(net_notional),
        "long_notional": _decimal_to_float(long_notional),
        "short_notional": _decimal_to_float(short_notional),
        "gross_leverage_estimate": _decimal_to_float(gross_leverage_estimate),
        "free_balance_ratio": _decimal_to_float(free_balance_ratio),
        "largest_position": largest_position,
        "positions": normalized_positions,
        "risk_flags": risk_flags,
        "summary_lines": summary_lines,
        **status_context,
    }, payload)
    return _attach_standard_disclaimer(result)


def normalize_fill(fill: dict[str, Any]) -> dict[str, Any]:
    quantity = _to_decimal(_pick(fill, "quantity", "qty", "size"))
    price = _to_decimal(_pick(fill, "price", "avg_price", "fill_price"))
    notional = _to_decimal(_pick(fill, "notional", "turnover", "value"))
    if notional is None and quantity is not None and price is not None:
        notional = abs(quantity) * price

    return {
        "account_scope": str(_pick(fill, "account_scope", "accountScope") or ""),
        "market": str(_pick(fill, "market") or ""),
        "order_id": str(_pick(fill, "order_id", "orderId") or ""),
        "symbol": str(_pick(fill, "symbol", "instId", "instrument", "market") or "UNKNOWN"),
        "side": str(_pick(fill, "side", "direction") or "unknown").lower(),
        "position_side": _normalize_position_side(
            _pick(fill, "position_side", "positionSide"),
            market=_pick(fill, "market"),
            fallback_side=_pick(fill, "side", "direction"),
        ),
        "position_mode": _normalize_position_mode(_pick(fill, "position_mode", "positionMode", "separatedMode")),
        "margin_type": _normalize_margin_type(_pick(fill, "margin_type", "marginType")),
        "quantity": _decimal_to_float(abs(quantity) if quantity is not None else None),
        "price": _decimal_to_float(price),
        "notional": _decimal_to_float(notional),
        "realized_pnl": _decimal_to_float(_to_decimal(_pick(fill, "realized_pnl", "realizedPnl", "pnl", "profit", "closedPnl"))),
        "fee": _decimal_to_float(_to_decimal(_pick(fill, "fee", "fees", "commission"))),
        "commission_asset": str(_pick(fill, "commission_asset", "commissionAsset") or ""),
        "is_maker": _coerce_bool(_pick(fill, "is_maker", "maker")),
        "time": _coerce_time(_pick(fill, "time", "tradeTime")),
    }


def normalize_bill(bill: dict[str, Any], *, payload_market: Any = None) -> dict[str, Any]:
    market = str(_pick(bill, "market") or payload_market or "").strip().lower()
    return {
        "market": market,
        "symbol": str(_pick(bill, "symbol") or ""),
        "type": str(_pick(bill, "type", "incomeType", "bizType") or "unknown").strip().lower(),
        "amount": _decimal_to_float(_to_decimal(_pick(bill, "amount", "income", "deltaAmount"))),
        "fee": _decimal_to_float(_to_decimal(_pick(bill, "fee", "fillFee", "fees"))),
        "time": _coerce_time(_pick(bill, "time", "cTime")),
    }


def normalize_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_scope": str(_pick(order, "account_scope", "accountScope") or ""),
        "market": str(_pick(order, "market") or ""),
        "symbol": str(_pick(order, "symbol") or "UNKNOWN"),
        "status": str(_pick(order, "status", "algoStatus") or "unknown"),
        "side": str(_pick(order, "side") or "unknown").lower(),
        "position_side": _normalize_position_side(
            _pick(order, "position_side", "positionSide"),
            market=_pick(order, "market"),
            fallback_side=_pick(order, "side"),
        ),
        "position_mode": _normalize_position_mode(_pick(order, "position_mode", "positionMode", "separatedMode")),
        "margin_type": _normalize_margin_type(_pick(order, "margin_type", "marginType")),
        "order_type": str(_pick(order, "order_type", "orderType", "type") or "unknown").lower(),
        "quantity": _decimal_to_float(_to_decimal(_pick(order, "quantity", "origQty", "qty"))),
        "executed_qty": _decimal_to_float(_to_decimal(_pick(order, "executed_qty", "executedQty"))),
        "price": _decimal_to_float(_to_decimal(_pick(order, "price", "avgPrice"))),
        "avg_price": _decimal_to_float(_to_decimal(_pick(order, "avg_price", "avgPrice", "price"))),
        "reduce_only": _coerce_bool(_pick(order, "reduce_only", "reduceOnly")),
        "close_position": _coerce_bool(_pick(order, "close_position", "closePosition")),
        "working_type": str(_pick(order, "working_type", "workingType") or ""),
        "tp_trigger_price": _decimal_to_float(_to_decimal(_pick(order, "tp_trigger_price", "tpTriggerPrice"))),
        "sl_trigger_price": _decimal_to_float(_to_decimal(_pick(order, "sl_trigger_price", "slTriggerPrice"))),
        "time": _coerce_time(_pick(order, "time", "created_time")),
        "update_time": _coerce_time(_pick(order, "update_time", "updateTime")),
        "order_id": str(_pick(order, "order_id", "orderId", "actualOrderId", "algoId") or ""),
    }


def _is_canceled_protective_order(order: dict[str, Any]) -> bool:
    if str(order.get("status") or "").upper() != "CANCELED":
        return False
    order_type = str(order.get("order_type") or "").lower()
    if order_type in PROTECTIVE_ORDER_TYPES:
        return True
    has_linked_protection = (
        _pick(order, "tp_trigger_price", "tpTriggerPrice") not in (None, "")
        or _pick(order, "sl_trigger_price", "slTriggerPrice") not in (None, "")
    )
    return has_linked_protection and bool(order.get("reduce_only") or order.get("close_position"))


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


def _summarize_bill_adjustments(payload: Any) -> dict[str, Any]:
    payload_market = payload.get("market") if isinstance(payload, dict) else None
    bills = [
        normalize_bill(item, payload_market=payload_market)
        for item in _extract_list(payload, BILL_KEYS)
    ]
    adjustment_total = Decimal("0")
    adjustment_count = 0
    unclassified_types: set[str] = set()

    for bill in bills:
        amount = _to_decimal(bill.get("amount"))
        if amount is None or amount == 0:
            continue
        classification = _classify_bill_adjustment(bill)
        if classification == "include":
            adjustment_total += amount
            adjustment_count += 1
        elif classification == "unknown":
            unclassified_types.add(str(bill.get("type") or "unknown"))

    return {
        "bill_adjustment_total": _decimal_to_float(adjustment_total),
        "bill_adjustment_count": adjustment_count,
        "unclassified_bill_types": sorted(unclassified_types),
    }


def _extract_price_series(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("price_series")
    if not isinstance(rows, list):
        return []

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(_pick(row, "symbol") or "").strip().upper()
        open_time = _coerce_time(_pick(row, "open_time", "openTime", "time"))
        close_time = _coerce_time(_pick(row, "close_time", "closeTime"))
        high = _to_decimal(_pick(row, "high"))
        low = _to_decimal(_pick(row, "low"))
        close = _to_decimal(_pick(row, "close", "price", "lastPrice"))
        if not symbol or open_time is None or close_time is None:
            continue
        normalized_rows.append(
            {
                "symbol": symbol,
                "open_time": open_time,
                "close_time": close_time,
                "high": high,
                "low": low,
                "close": close,
            }
        )

    normalized_rows.sort(key=lambda item: (int(item["open_time"]), int(item["close_time"])))
    return normalized_rows


def _group_price_series_by_symbol(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        grouped.setdefault(symbol, []).append(row)
    return grouped


def _recent_price_range_ratio(price_rows: list[dict[str, Any]], *, before_time: int | None) -> Decimal | None:
    if before_time is None:
        return None
    ratios: list[Decimal] = []
    for row in price_rows:
        close_time = _coerce_time(row.get("close_time"))
        high = _to_decimal(row.get("high"))
        low = _to_decimal(row.get("low"))
        close = _to_decimal(row.get("close"))
        if (
            close_time is None
            or close_time > before_time
            or high is None
            or low is None
            or close in (None, Decimal("0"))
        ):
            continue
        range_value = high - low
        if range_value < 0:
            continue
        ratios.append(range_value / close)
    if not ratios:
        return None
    return _median(ratios[-PRICE_CONTEXT_LOOKBACK_CANDLES:])


def _window_price_range_ratio(
    price_rows: list[dict[str, Any]],
    *,
    start_time: int | None,
    end_time: int | None,
) -> Decimal | None:
    if start_time is None or end_time is None:
        return None
    overlapping_rows = [
        row
        for row in price_rows
        if (_coerce_time(row.get("close_time")) or 0) >= start_time
        and (_coerce_time(row.get("open_time")) or 0) <= end_time
    ]
    if not overlapping_rows:
        return None
    highs = [_to_decimal(row.get("high")) for row in overlapping_rows if _to_decimal(row.get("high")) is not None]
    lows = [_to_decimal(row.get("low")) for row in overlapping_rows if _to_decimal(row.get("low")) is not None]
    closes = [_to_decimal(row.get("close")) for row in overlapping_rows if _to_decimal(row.get("close")) not in (None, Decimal("0"))]
    if not highs or not lows or not closes:
        return None
    window_range = max(highs) - min(lows)
    if window_range < 0:
        return None
    return window_range / closes[-1]


def _best_event_window(
    timestamps: list[int],
    window_ms: int,
) -> tuple[int, int | None, int | None]:
    if not timestamps:
        return 0, None, None

    best = 0
    best_start: int | None = None
    best_end: int | None = None
    left = 0
    for right, current in enumerate(timestamps):
        while current - timestamps[left] > window_ms:
            left += 1
        current_count = right - left + 1
        if current_count > best:
            best = current_count
            best_start = timestamps[left]
            best_end = current
    return best, best_start, best_end


def _high_frequency_price_context(
    episodes: list[dict[str, Any]],
    price_rows_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    window_start: int | None,
    window_end: int | None,
) -> dict[str, Any] | None:
    if window_start is None or window_end is None:
        return None

    window_symbols = {
        str(item.get("symbol") or "").strip().upper()
        for item in episodes
        if window_start <= (_coerce_time(item.get("open_time")) or 0) <= window_end
    }
    window_symbols.discard("")
    if len(window_symbols) != 1:
        return None

    symbol = next(iter(window_symbols))
    price_rows = price_rows_by_symbol.get(symbol, [])
    if not price_rows:
        return None

    baseline_ratio = _recent_price_range_ratio(price_rows, before_time=window_start)
    window_ratio = _window_price_range_ratio(price_rows, start_time=window_start, end_time=window_end)
    if baseline_ratio is None or window_ratio is None:
        return None

    if window_ratio >= max(
        baseline_ratio * PRICE_CONTEXT_HIGH_FREQUENCY_VOL_MULTIPLIER,
        PRICE_CONTEXT_HIGH_FREQUENCY_HIGH_VOL_MIN_RATIO,
    ):
        status = "high_volatility"
    elif window_ratio <= max(
        baseline_ratio * Decimal("3"),
        PRICE_CONTEXT_HIGH_FREQUENCY_QUIET_RATIO,
    ):
        status = "quiet_market"
    else:
        status = "normal"

    return {
        "status": status,
        "symbol": symbol,
        "window_ratio": window_ratio,
        "baseline_ratio": baseline_ratio,
    }


def _has_meaningful_market_reset(
    previous: dict[str, Any],
    current: dict[str, Any],
    price_rows: list[dict[str, Any]],
) -> bool | None:
    previous_close_time = _coerce_time(previous.get("close_time"))
    previous_close_price = _to_decimal(previous.get("close_price"))
    current_open_price = _to_decimal(current.get("open_price"))
    if previous_close_price in (None, Decimal("0")) or current_open_price is None:
        return None
    baseline = _recent_price_range_ratio(price_rows, before_time=previous_close_time)
    if baseline is None:
        return None
    move_ratio = abs(current_open_price - previous_close_price) / previous_close_price
    threshold = baseline * PRICE_RESET_VOL_MULTIPLIER
    if threshold < PRICE_CONTEXT_MIN_RESET_RATIO:
        threshold = PRICE_CONTEXT_MIN_RESET_RATIO
    return move_ratio >= threshold


def _has_meaningful_directional_reset(
    previous: dict[str, Any],
    current: dict[str, Any],
    price_rows: list[dict[str, Any]],
) -> bool | None:
    previous_close_time = _coerce_time(previous.get("close_time"))
    previous_close_price = _to_decimal(previous.get("close_price"))
    current_open_price = _to_decimal(current.get("open_price"))
    position_side = str(current.get("position_side") or "").strip().lower()
    if previous_close_price in (None, Decimal("0")) or current_open_price is None:
        return None
    baseline = _recent_price_range_ratio(price_rows, before_time=previous_close_time)
    if baseline is None:
        return None

    if position_side == "short":
        directional_move = previous_close_price - current_open_price
    else:
        directional_move = current_open_price - previous_close_price
    if directional_move <= 0:
        return False

    move_ratio = directional_move / previous_close_price
    threshold = baseline * PRICE_RESET_VOL_MULTIPLIER
    if threshold < PRICE_CONTEXT_MIN_RESET_RATIO:
        threshold = PRICE_CONTEXT_MIN_RESET_RATIO
    return move_ratio >= threshold


def _episode_post_close_follow_through_ratio(
    episode: dict[str, Any],
    price_rows: list[dict[str, Any]],
) -> Decimal | None:
    close_time = _coerce_time(episode.get("close_time"))
    close_price = _to_decimal(episode.get("close_price"))
    position_side = str(episode.get("position_side") or "").strip().lower()
    if close_time is None or close_price in (None, Decimal("0")):
        return None

    horizon_end = close_time + (PRICE_CONTEXT_FORWARD_HOURS * HOUR_MS)
    future_rows = [
        row
        for row in price_rows
        if (_coerce_time(row.get("close_time")) or 0) > close_time
        and (_coerce_time(row.get("open_time")) or 0) < horizon_end
    ]
    if not future_rows:
        return None

    if position_side == "short":
        lows = [_to_decimal(row.get("low")) for row in future_rows if _to_decimal(row.get("low")) is not None]
        if not lows:
            return None
        favorable_move = close_price - min(lows)
    else:
        highs = [_to_decimal(row.get("high")) for row in future_rows if _to_decimal(row.get("high")) is not None]
        if not highs:
            return None
        favorable_move = max(highs) - close_price

    if favorable_move < 0:
        favorable_move = Decimal("0")
    return favorable_move / close_price


def _episode_key(fill: dict[str, Any], order: dict[str, Any], payload_market: Any) -> tuple[str, str, str, str, str]:
    market = str(fill.get("market") or order.get("market") or payload_market or "unknown").lower()
    account_scope = str(fill.get("account_scope") or order.get("account_scope") or "").strip()
    if not account_scope and market in {"futures", "spot"}:
        account_scope = f"personal_{market}"
    if not account_scope:
        account_scope = "personal"
    symbol = str(fill.get("symbol") or order.get("symbol") or "UNKNOWN")
    position_side = _normalize_position_side(
        fill.get("position_side") or order.get("position_side"),
        market=market,
        fallback_side=fill.get("side") or order.get("side"),
    ) or "net"
    position_mode = str(fill.get("position_mode") or order.get("position_mode") or "UNKNOWN")
    return account_scope, market, symbol, position_side, position_mode


def _new_episode(
    *,
    key: tuple[str, str, str, str, str],
    fill: dict[str, Any],
    order: dict[str, Any],
    fill_time: int | None,
) -> dict[str, Any]:
    account_scope, market, symbol, position_side, position_mode = key
    return {
        "episode_id": f"{market}-{symbol}-{fill_time or 0}",
        "account_scope": account_scope,
        "market": market,
        "symbol": symbol,
        "position_mode": position_mode,
        "margin_type": str(fill.get("margin_type") or order.get("margin_type") or "UNKNOWN"),
        "position_side": position_side,
        "open_time": fill_time,
        "close_time": None,
        "open_price": _to_decimal(fill.get("price")) or _to_decimal(order.get("avg_price")) or _to_decimal(order.get("price")),
        "close_price": None,
        "entry_count": 0,
        "exit_count": 0,
        "max_position_notional": Decimal("0"),
        "realized_pnl": Decimal("0"),
        "fees": Decimal("0"),
        "entry_quality_features": {},
        "exit_quality_features": {},
        "behavior_features": {},
        "_open_quantity": Decimal("0"),
        "_partial": False,
        "_realized_pnl_complete": True,
        "_fee_complete": True,
    }


def _infer_fill_action(fill: dict[str, Any], order: dict[str, Any]) -> str:
    if order.get("reduce_only") or order.get("close_position"):
        return "exit"

    market = str(fill.get("market") or order.get("market") or "").lower()
    side = str(fill.get("side") or order.get("side") or "").lower()
    position_side = _normalize_position_side(
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


def _finalize_episode(episode: dict[str, Any]) -> dict[str, Any]:
    open_time = _coerce_time(episode.get("open_time"))
    close_time = _coerce_time(episode.get("close_time"))
    holding_minutes = None
    if open_time is not None and close_time is not None and close_time >= open_time:
        holding_minutes = (Decimal(close_time - open_time) / Decimal("60000"))

    realized_pnl = episode["realized_pnl"]
    fees = episode["fees"]
    realized_pnl_complete = bool(episode.get("_realized_pnl_complete", True))
    fee_complete = bool(episode.get("_fee_complete", True))
    net_pnl_complete = realized_pnl_complete and fee_complete
    is_partial = bool(episode.get("_partial")) or int(episode["entry_count"]) == 0 or int(episode["exit_count"]) == 0
    return {
        "episode_id": episode["episode_id"],
        "account_scope": episode["account_scope"],
        "market": episode["market"],
        "symbol": episode["symbol"],
        "position_mode": episode["position_mode"],
        "margin_type": episode["margin_type"],
        "position_side": episode["position_side"],
        "open_time": open_time,
        "close_time": close_time,
        "open_price": _decimal_to_float(_to_decimal(episode.get("open_price"))),
        "close_price": _decimal_to_float(_to_decimal(episode.get("close_price"))),
        "holding_minutes": _decimal_to_float(holding_minutes),
        "entry_count": int(episode["entry_count"]),
        "exit_count": int(episode["exit_count"]),
        "max_position_notional": _decimal_to_float(episode["max_position_notional"]),
        "realized_pnl": _decimal_to_float(realized_pnl) if realized_pnl_complete else None,
        "fees": _decimal_to_float(fees) if fee_complete else None,
        "net_pnl": _decimal_to_float(realized_pnl - fees) if net_pnl_complete else None,
        "net_pnl_complete": net_pnl_complete,
        "partial": is_partial,
        "entry_quality_features": dict(episode["entry_quality_features"]),
        "exit_quality_features": dict(episode["exit_quality_features"]),
        "behavior_features": dict(episode["behavior_features"]),
    }


def _merge_reason_code(target: list[str], reason: str) -> None:
    if reason and reason not in target:
        target.append(reason)


def _reconstruct_trade_episodes(
    payload: Any,
    orders: list[dict[str, Any]],
    fills: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    payload_market = payload.get("market") if isinstance(payload, dict) else None
    orders_by_id = {order["order_id"]: order for order in orders if order.get("order_id")}
    active: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    saw_partial_episode = False
    partial_reasons: list[str] = []

    sorted_fills = sorted(
        fills,
        key=lambda item: (_coerce_time(item.get("time")) or 0, str(item.get("order_id") or "")),
    )

    for fill in sorted_fills:
        order = orders_by_id.get(str(fill.get("order_id") or ""), {})
        fill_time = _coerce_time(fill.get("time")) or _coerce_time(order.get("update_time")) or _coerce_time(order.get("time"))
        key = _episode_key(fill, order, payload_market)
        episode = active.get(key)
        action = _infer_fill_action(fill, order)

        if episode is None and action == "entry":
            episode = _new_episode(key=key, fill=fill, order=order, fill_time=fill_time)
            active[key] = episode
        elif episode is None:
            episode = _new_episode(key=key, fill=fill, order=order, fill_time=fill_time)
            episode["_partial"] = True
            active[key] = episode
            _merge_reason_code(partial_reasons, "replay_carry_in_detected")

        quantity = _to_decimal(fill.get("quantity")) or Decimal("0")
        price = _to_decimal(fill.get("price")) or Decimal("0")
        notional = _to_decimal(fill.get("notional"))
        if notional is None and quantity and price:
            notional = abs(quantity) * price
        raw_realized_pnl = _to_decimal(fill.get("realized_pnl"))
        raw_fee = _to_decimal(fill.get("fee"))
        realized_pnl = raw_realized_pnl or Decimal("0")
        fee = raw_fee or Decimal("0")

        if action == "exit" and raw_realized_pnl is None:
            episode["_realized_pnl_complete"] = False
        if raw_fee is None:
            episode["_fee_complete"] = False

        episode["realized_pnl"] += realized_pnl
        episode["fees"] += fee

        if action == "entry":
            episode["entry_count"] += 1
            episode["_open_quantity"] += abs(quantity)
            if episode.get("open_time") is None:
                episode["open_time"] = fill_time
            if episode.get("open_price") is None and price > 0:
                episode["open_price"] = price
            current_notional = abs(episode["_open_quantity"]) * price if price else (notional or Decimal("0"))
            if current_notional > episode["max_position_notional"]:
                episode["max_position_notional"] = current_notional
        else:
            episode["exit_count"] += 1
            episode["close_time"] = fill_time
            if price > 0:
                episode["close_price"] = price
            episode["_open_quantity"] = max(Decimal("0"), episode["_open_quantity"] - abs(quantity))
            if episode["max_position_notional"] <= 0 and notional is not None:
                episode["max_position_notional"] = abs(notional)
            if episode["_open_quantity"] <= POSITION_EPSILON:
                finalized = _finalize_episode(episode)
                if finalized.get("partial"):
                    saw_partial_episode = True
                closed.append(finalized)
                active.pop(key, None)

    partial = bool(active) or saw_partial_episode
    if active:
        _merge_reason_code(partial_reasons, "replay_open_episode_detected")
    return closed, partial, partial_reasons


def _build_replay_metrics(
    episodes: list[dict[str, Any]],
    *,
    bill_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bill_summary = dict(bill_summary or {})
    bill_adjustment_total = _to_decimal(bill_summary.get("bill_adjustment_total")) or Decimal("0")
    bill_adjustment_count = int(bill_summary.get("bill_adjustment_count") or 0)
    if not episodes:
        return {
            "win_rate": None,
            "profit_factor": None,
            "average_holding_minutes": None,
            "average_win": None,
            "average_loss": None,
            "episode_net_pnl": None,
            "bill_adjustment_total": _decimal_to_float(bill_adjustment_total),
            "bill_adjustment_count": bill_adjustment_count,
            "net_pnl": _decimal_to_float(bill_adjustment_total) if bill_adjustment_count else None,
        }

    outcome_episodes = [
        item for item in episodes
        if _to_decimal(item.get("net_pnl")) is not None
    ]
    net_pnls = [_to_decimal(item.get("net_pnl")) or Decimal("0") for item in outcome_episodes]
    wins = [value for value in net_pnls if value > 0]
    losses = [abs(value) for value in net_pnls if value < 0]
    holds = [_to_decimal(item.get("holding_minutes")) for item in episodes if _to_decimal(item.get("holding_minutes")) is not None]

    win_rate = Decimal(len(wins)) / Decimal(len(net_pnls)) if net_pnls else None
    profit_factor = None
    if losses:
        profit_factor = _ratio(sum(wins, Decimal("0")), sum(losses, Decimal("0")))
    average_holding_minutes = sum(holds, Decimal("0")) / Decimal(len(holds)) if holds else None
    average_win = sum(wins, Decimal("0")) / Decimal(len(wins)) if wins else None
    average_loss = sum(losses, Decimal("0")) / Decimal(len(losses)) if losses else None
    episode_net_pnl = sum(net_pnls, Decimal("0")) if net_pnls else None

    return {
        "win_rate": _decimal_to_float(win_rate),
        "profit_factor": _decimal_to_float(profit_factor),
        "average_holding_minutes": _decimal_to_float(average_holding_minutes),
        "average_win": _decimal_to_float(average_win),
        "average_loss": _decimal_to_float(average_loss),
        "episode_net_pnl": _decimal_to_float(episode_net_pnl),
        "bill_adjustment_total": _decimal_to_float(bill_adjustment_total),
        "bill_adjustment_count": bill_adjustment_count,
        "net_pnl": _decimal_to_float(episode_net_pnl + bill_adjustment_total) if episode_net_pnl is not None else None,
    }


def _summarize_replay_patterns(tags: list[str]) -> str:
    if not tags:
        return "Replay shows mixed execution quality."
    if tags[0] == "no_closed_episodes":
        return "Replay sample has no closed trade episodes yet."
    if tags[0] == "no_clear_pattern":
        return "Replay shows mixed execution quality."

    phrases_by_tag = {
        "high_trade_frequency": "elevated trading frequency",
        "repeated_reentry": "repeated re-entry",
        "position_size_escalation": "position size escalation",
        "low_win_large_loss": "weak payoff quality",
        "protection_churn": "protective order churn",
        "take_profit_too_early": "profits clipped too early",
    }
    phrases: list[str] = []
    for tag in tags:
        phrase = phrases_by_tag.get(tag)
        if phrase and phrase not in phrases:
            phrases.append(phrase)
        if len(phrases) == 2:
            break

    if not phrases:
        return "Replay shows mixed execution quality."
    if len(phrases) == 1:
        return f"Replay shows {phrases[0]}."
    return f"Replay shows {phrases[0]} and {phrases[1]}."


def analyze_fills(payload: Any) -> dict[str, Any]:
    fills = [normalize_fill(item) for item in _extract_list(payload, FILL_KEYS)]
    status_context = _status_context(payload)
    realized_pnl = Decimal("0")
    fees = Decimal("0")
    turnover = Decimal("0")
    buy_volume = Decimal("0")
    sell_volume = Decimal("0")
    pnl_samples = 0
    winning_samples = 0
    symbols: set[str] = set()

    for fill in fills:
        symbols.add(fill["symbol"])
        quantity = _to_decimal(fill["quantity"]) or Decimal("0")
        notional = _to_decimal(fill["notional"]) or Decimal("0")
        pnl = _to_decimal(fill["realized_pnl"])
        fee_value = _to_decimal(fill["fee"])
        if pnl is None:
            status_context["partial"] = True
            _merge_reason_code(status_context["degraded_reasons"], "fills_missing_realized_pnl")
        if fee_value is None:
            status_context["partial"] = True
            _merge_reason_code(status_context["degraded_reasons"], "fills_missing_fee")
        fee = fee_value or Decimal("0")
        side = str(fill["side"])

        turnover += abs(notional)
        fees += fee
        if side in LONG_SIDES:
            buy_volume += abs(quantity)
        elif side in SHORT_SIDES:
            sell_volume += abs(quantity)

        if pnl is not None:
            realized_pnl += pnl
            pnl_samples += 1
            if pnl > 0:
                winning_samples += 1

    net_after_fees = realized_pnl - fees
    win_rate = None
    if pnl_samples:
        win_rate = Decimal(winning_samples) / Decimal(pnl_samples)

    summary_lines = [
        f"Fills: {len(fills)}",
        f"Turnover: {round(float(turnover), 4)}",
        f"Realized PnL after fees: {round(float(net_after_fees), 4)}",
    ]
    if pnl_samples:
        summary_lines.append(f"Fill win rate: {round(float(win_rate or Decimal('0')) * 100, 2)}%")

    result = _attach_payload_context({
        "fills_count": len(fills),
        "symbols": sorted(symbols),
        "buy_volume": _decimal_to_float(buy_volume),
        "sell_volume": _decimal_to_float(sell_volume),
        "turnover": _decimal_to_float(turnover),
        "realized_pnl": _decimal_to_float(realized_pnl),
        "fees": _decimal_to_float(fees),
        "net_realized_after_fees": _decimal_to_float(net_after_fees),
        "win_rate": _decimal_to_float(win_rate),
        "fills": fills,
        "summary_lines": summary_lines,
        **status_context,
    }, payload)
    return _attach_standard_disclaimer(result)


def _windowed_event_count(timestamps: list[int], window_ms: int) -> int:
    best, _, _ = _best_event_window(timestamps, window_ms)
    return best


def analyze_replay(payload: Any) -> dict[str, Any]:
    orders = [normalize_order(item) for item in _extract_list(payload, ORDER_KEYS)]
    fills = [normalize_fill(item) for item in _extract_list(payload, FILL_KEYS)]
    price_rows_by_symbol = _group_price_series_by_symbol(_extract_price_series(payload))
    payload_partial = bool(payload.get("partial")) if isinstance(payload, dict) else False
    degraded_reasons = list(payload.get("degraded_reasons") or []) if isinstance(payload, dict) else []
    constraints = list(payload.get("constraints") or []) if isinstance(payload, dict) else []
    bill_summary = _summarize_bill_adjustments(payload)
    episodes, reconstructed_partial, reconstruction_reasons = _reconstruct_trade_episodes(payload, orders, fills)
    complete_episodes = [item for item in episodes if not bool(item.get("partial"))]
    outcome_incomplete = any(_to_decimal(item.get("net_pnl")) is None for item in complete_episodes)
    for reason in reconstruction_reasons:
        _merge_reason_code(degraded_reasons, reason)
    if outcome_incomplete:
        _merge_reason_code(degraded_reasons, "replay_episode_pnl_unavailable")
    if bill_summary.get("unclassified_bill_types"):
        _merge_reason_code(degraded_reasons, "replay_bill_types_unclassified")
    metrics = _build_replay_metrics(complete_episodes, bill_summary=bill_summary)
    partial = payload_partial or reconstructed_partial or outcome_incomplete

    tags: list[str] = []
    evidence: list[str] = []
    advice: list[str] = []
    high_frequency_suppressed_reason: str | None = None
    reentry_suppressed_reason: str | None = None
    size_escalation_suppressed_reason: str | None = None
    take_profit_suppressed_reason: str | None = None

    if not complete_episodes:
        tags.append("no_closed_episodes")
        evidence.append("No closed trade episodes were reconstructed from the payload.")
        advice.append("Collect more closed trades before drawing a stronger conclusion.")
    else:
        episode_open_times = sorted(
            ts for ts in (_coerce_time(item.get("open_time")) for item in complete_episodes) if ts is not None
        )
        burst_count, burst_window_start, burst_window_end = _best_event_window(episode_open_times, WINDOW_60_MIN_MS)
        frequency_context = _high_frequency_price_context(
            complete_episodes,
            price_rows_by_symbol,
            window_start=burst_window_start,
            window_end=burst_window_end,
        )
        burst_threshold = HIGH_FREQUENCY_DEFAULT_THRESHOLD
        if frequency_context and frequency_context.get("status") == "high_volatility":
            burst_threshold = HIGH_FREQUENCY_ELEVATED_VOL_THRESHOLD

        if burst_count >= burst_threshold:
            tags.append("high_trade_frequency")
            if frequency_context and frequency_context.get("status") == "quiet_market":
                evidence.append(
                    f"{burst_count} trade episodes opened inside a 60 minute window while {frequency_context['symbol']} moved only {round(float(frequency_context['window_ratio']) * 100, 2)}% on nearby hourly candles."
                )
            else:
                evidence.append(f"{burst_count} trade episodes opened inside a 60 minute window.")
            advice.append("Slow the trading pace and require a stricter setup before re-entering.")
        elif (
            frequency_context
            and frequency_context.get("status") == "high_volatility"
            and burst_count >= HIGH_FREQUENCY_DEFAULT_THRESHOLD
        ):
            high_frequency_suppressed_reason = (
                f"{burst_count} trade episodes opened inside a 60 minute window, but the burst happened during a high-volatility move in {frequency_context['symbol']} ({round(float(frequency_context['window_ratio']) * 100, 2)}% on nearby hourly candles), so the high-frequency tag was not escalated."
            )

        reentry_count = 0
        size_escalation_count = 0
        reentry_market_reset_count = 0
        size_directional_reset_count = 0
        for previous, current in zip(complete_episodes, complete_episodes[1:]):
            previous_close = _coerce_time(previous.get("close_time"))
            current_open = _coerce_time(current.get("open_time"))
            if (
                previous["symbol"] == current["symbol"]
                and previous["position_side"] == current["position_side"]
                and previous_close is not None
                and current_open is not None
                and 0 <= current_open - previous_close <= WINDOW_30_MIN_MS
            ):
                market_reset = _has_meaningful_market_reset(
                    previous,
                    current,
                    price_rows_by_symbol.get(str(current.get("symbol") or "").strip().upper(), []),
                )
                if market_reset is True:
                    reentry_market_reset_count += 1
                else:
                    reentry_count += 1

            previous_net = _to_decimal(previous.get("net_pnl")) or Decimal("0")
            previous_size = _to_decimal(previous.get("max_position_notional")) or Decimal("0")
            current_size = _to_decimal(current.get("max_position_notional")) or Decimal("0")
            if previous_net < 0 and previous_size > 0 and current_size >= previous_size * Decimal("1.25"):
                directional_reset = _has_meaningful_directional_reset(
                    previous,
                    current,
                    price_rows_by_symbol.get(str(current.get("symbol") or "").strip().upper(), []),
                )
                if directional_reset is True:
                    size_directional_reset_count += 1
                else:
                    size_escalation_count += 1

        if reentry_count >= 2:
            tags.append("repeated_reentry")
            evidence.append(f"{reentry_count} re-entries happened within 30 minutes of the previous close.")
            advice.append("Add a cooling-off period before reopening the same trade idea.")
        elif reentry_market_reset_count > 0 and (reentry_count + reentry_market_reset_count) >= 2:
            reentry_suppressed_reason = (
                f"{reentry_market_reset_count} quick re-entry transition(s) were treated as a market reset instead of repeated re-entry."
            )

        if size_escalation_count >= 1:
            tags.append("position_size_escalation")
            evidence.append(f"Position size escalated after losses in {size_escalation_count} consecutive transition(s).")
            advice.append("Freeze sizing after a losing episode and only scale once the setup quality recovers.")
        elif size_directional_reset_count > 0:
            size_escalation_suppressed_reason = (
                f"{size_directional_reset_count} larger post-loss entry transition(s) followed a directional reset, so position size escalation was not escalated."
            )

        win_rate = _to_decimal(metrics.get("win_rate"))
        average_win = _to_decimal(metrics.get("average_win"))
        average_loss = _to_decimal(metrics.get("average_loss"))
        if win_rate is not None and average_win is not None and average_loss is not None:
            if win_rate < Decimal("0.5") and average_loss > average_win:
                tags.append("low_win_large_loss")
                evidence.append(
                    f"Episode win rate is {round(float(win_rate) * 100, 2)}% while average loss ({round(float(average_loss), 4)}) exceeds average win ({round(float(average_win), 4)})."
                )
                advice.append("Tighten downside first; the replay shows losses are still too expensive.")

        canceled_protective_orders = sum(1 for order in orders if _is_canceled_protective_order(order))
        if canceled_protective_orders >= 2:
            tags.append("protection_churn")
            evidence.append(f"{canceled_protective_orders} protective conditional order(s) were canceled before execution.")
            advice.append("Review whether protective exits are being moved or removed under pressure.")

        winning_holds = [
            _to_decimal(item.get("holding_minutes"))
            for item in complete_episodes
            if (_to_decimal(item.get("net_pnl")) or Decimal("0")) > 0 and _to_decimal(item.get("holding_minutes")) is not None
        ]
        losing_holds = [
            _to_decimal(item.get("holding_minutes"))
            for item in complete_episodes
            if (_to_decimal(item.get("net_pnl")) or Decimal("0")) < 0 and _to_decimal(item.get("holding_minutes")) is not None
        ]
        median_win_hold = _median([value for value in winning_holds if value is not None])
        median_loss_hold = _median([value for value in losing_holds if value is not None])
        if (
            median_win_hold is not None
            and median_loss_hold is not None
            and median_loss_hold > 0
            and median_win_hold < (median_loss_hold * Decimal("0.5"))
            and average_win is not None
            and average_loss is not None
            and average_win < average_loss
        ):
            contextual_wins = 0
            contextual_followthrough = 0
            for item in complete_episodes:
                if (_to_decimal(item.get("net_pnl")) or Decimal("0")) <= 0:
                    continue
                symbol_rows = price_rows_by_symbol.get(str(item.get("symbol") or "").strip().upper(), [])
                follow_through_ratio = _episode_post_close_follow_through_ratio(item, symbol_rows)
                baseline_ratio = _recent_price_range_ratio(symbol_rows, before_time=_coerce_time(item.get("close_time")))
                if follow_through_ratio is None or baseline_ratio is None:
                    continue
                contextual_wins += 1
                threshold = baseline_ratio * PRICE_FOLLOW_THROUGH_VOL_MULTIPLIER
                if threshold < PRICE_CONTEXT_MIN_FOLLOW_THROUGH_RATIO:
                    threshold = PRICE_CONTEXT_MIN_FOLLOW_THROUGH_RATIO
                if follow_through_ratio >= threshold:
                    contextual_followthrough += 1

            if contextual_wins == 0 or contextual_followthrough >= max(1, (contextual_wins + 1) // 2):
                tags.append("take_profit_too_early")
                if contextual_wins > 0:
                    evidence.append(
                        f"Winning trades are being closed materially faster than losing trades, and {contextual_followthrough}/{contextual_wins} contextual winner exit(s) still had favorable follow-through."
                    )
                else:
                    evidence.append("Winning trades are being closed materially faster than losing trades.")
                advice.append("Define one exit rule for winners so profits are not clipped too early.")
            elif contextual_wins > 0:
                take_profit_suppressed_reason = (
                    f"Fast winner exits did not show enough post-exit favorable follow-through ({contextual_followthrough}/{contextual_wins}), so the take-profit-too-early tag was not escalated."
                )

        for suppressed_reason in (
            high_frequency_suppressed_reason,
            reentry_suppressed_reason,
            size_escalation_suppressed_reason,
            take_profit_suppressed_reason,
        ):
            if suppressed_reason:
                evidence.append(suppressed_reason)

        if not tags:
            tags.append("no_clear_pattern")
            evidence.append("No dominant replay pattern crossed the configured thresholds.")
            advice.append("Collect more closed trades before drawing a stronger conclusion.")

    if outcome_incomplete:
        evidence.append("Some closed episodes were missing realized PnL or fee context, so payoff metrics were skipped.")
        advice.append("Treat payoff-based replay conclusions as incomplete until episode outcome fields are available.")

    if partial:
        evidence.append("Some replay legs were still open or lacked enough context to form a closed episode.")
        advice.append("Treat the replay as partial until more closed episodes are available.")

    summary = _summarize_replay_patterns(tags)

    return _attach_payload_context(
        _attach_standard_disclaimer({
            "summary": summary,
            "top_pattern": tags[0],
            "behavior_tags": tags[:5],
            "evidence": evidence[:5],
            "advice": advice[:5],
            "trade_episodes": episodes,
            "episode_count": len(episodes),
            "sample_quality": "full" if len(complete_episodes) >= 20 else "limited" if len(complete_episodes) >= 10 else "minimal",
            "metrics": metrics,
            "quant_reports": dict(metrics),
            "closed_trade_count": int(payload.get("closed_trade_count") or len(complete_episodes)),
            "partial": partial,
            "degraded_reasons": degraded_reasons,
            "constraints": constraints,
        }),
        payload,
    )


def _episode_target_with_status(episode: dict[str, Any]) -> str:
    symbol = str(episode.get("symbol") or "UNKNOWN")
    position_side = str(episode.get("position_side") or "net")
    target = f"{symbol} {position_side}".strip()
    if bool(episode.get("partial")):
        if int(episode.get("entry_count") or 0) == 0 and int(episode.get("exit_count") or 0) > 0:
            return f"{target} (partial carry-in)"
        if int(episode.get("exit_count") or 0) == 0 and int(episode.get("entry_count") or 0) > 0:
            return f"{target} (open episode)"
        return f"{target} (partial)"
    return target


def _build_episode_highlights(episodes: list[dict[str, Any]]) -> list[str]:
    if not episodes:
        return ["No closed trade episodes were reconstructed from the payload."]

    highlights: list[str] = []
    seen_episode_ids: set[str] = set()

    def add_highlight(episode: dict[str, Any], message: str) -> None:
        episode_id = str(episode.get("episode_id") or "")
        if episode_id in seen_episode_ids:
            return
        seen_episode_ids.add(episode_id)
        highlights.append(message)

    def episode_target(episode: dict[str, Any]) -> str:
        return _episode_target_with_status(episode)

    worst_episode = min(
        episodes,
        key=lambda item: _to_decimal(item.get("net_pnl")) or Decimal("0"),
    )
    worst_net = _to_decimal(worst_episode.get("net_pnl")) or Decimal("0")
    if worst_net < 0:
        add_highlight(
            worst_episode,
            f"Largest loss episode: {episode_target(worst_episode)} net {round(float(worst_net), 4)}.",
        )

    best_episode = max(
        episodes,
        key=lambda item: _to_decimal(item.get("net_pnl")) or Decimal("0"),
    )
    best_net = _to_decimal(best_episode.get("net_pnl")) or Decimal("0")
    if best_net > 0:
        add_highlight(
            best_episode,
            f"Best episode: {episode_target(best_episode)} net {round(float(best_net), 4)}.",
        )

    longest_hold_episode = max(
        episodes,
        key=lambda item: _to_decimal(item.get("holding_minutes")) or Decimal("0"),
    )
    longest_hold = _to_decimal(longest_hold_episode.get("holding_minutes")) or Decimal("0")
    if longest_hold > 0:
        add_highlight(
            longest_hold_episode,
            f"Longest hold: {episode_target(longest_hold_episode)} for {_format_duration_minutes(longest_hold)}.",
        )

    if not highlights:
        highlights.append(f"Closed episodes reviewed: {len(episodes)}.")
    return highlights[:5]


def review_trades(payload: Any) -> dict[str, Any]:
    replay_result = analyze_replay(payload)
    pattern_snapshot = {
        "top_pattern": replay_result.get("top_pattern"),
        "behavior_tags": list(replay_result.get("behavior_tags") or []),
        "evidence": list(replay_result.get("evidence") or []),
        "advice": list(replay_result.get("advice") or []),
    }
    episodes = list(replay_result.get("trade_episodes") or [])

    return _attach_payload_context(
        _attach_standard_disclaimer({
            "review_type": "trade_review",
            "summary": replay_result.get("summary"),
            "episode_count": replay_result.get("episode_count", 0),
            "closed_trade_count": replay_result.get("closed_trade_count", 0),
            "sample_quality": replay_result.get("sample_quality"),
            "episode_highlights": _build_episode_highlights(episodes),
            "episodes": episodes,
            "metrics": dict(replay_result.get("metrics") or {}),
            "pattern_snapshot": pattern_snapshot,
            "partial": bool(replay_result.get("partial")),
            "degraded_reasons": list(replay_result.get("degraded_reasons") or []),
            "constraints": list(replay_result.get("constraints") or []),
        }),
        payload,
    )


def analyze_profile(payload: Any) -> dict[str, Any]:
    metrics = payload.get("metrics") if isinstance(payload, dict) else {}
    if not isinstance(metrics, dict):
        metrics = {}
    selected_period = str(payload.get("selected_period") or payload.get("period") or "30d")
    closed_trade_count = int(payload.get("closed_trade_count") or 0)
    partial = bool(payload.get("partial")) if isinstance(payload, dict) else False
    degraded_reasons = list(payload.get("degraded_reasons") or []) if isinstance(payload, dict) else []
    constraints = list(payload.get("constraints") or []) if isinstance(payload, dict) else []
    replay_analysis = payload.get("replay_analysis") if isinstance(payload, dict) else {}
    if not isinstance(replay_analysis, dict):
        replay_analysis = {}
    if not replay_analysis and isinstance(payload, dict) and (payload.get("orders") or payload.get("fills")):
        replay_analysis = analyze_replay(payload)
    partial = partial or bool(replay_analysis.get("partial"))
    for reason in replay_analysis.get("degraded_reasons") or []:
        _merge_reason_code(degraded_reasons, str(reason))
    for constraint in replay_analysis.get("constraints") or []:
        if isinstance(constraint, dict) and constraint not in constraints:
            constraints.append(constraint)
    derived_metrics = _derive_profile_metrics_from_replay_payload(payload, replay_analysis)
    has_replay_rows = isinstance(payload, dict) and bool(payload.get("orders") or payload.get("fills") or payload.get("bills"))
    if derived_metrics:
        metrics = dict(metrics)
        for key, value in derived_metrics.items():
            if has_replay_rows:
                metrics[key] = value
            elif metrics.get(key) in (None, ""):
                metrics[key] = value
    replay_tags = list(replay_analysis.get("behavior_tags") or [])
    sample_quality = str(payload.get("sample_quality") or ("full" if closed_trade_count >= 20 else "limited" if closed_trade_count >= 10 else "minimal"))

    median_hold_ms = _to_decimal(metrics.get("median_hold_ms"))
    active_day_trade_average = _to_decimal(metrics.get("active_day_trade_average"))
    risk_score = _to_decimal(metrics.get("risk_score"))
    win_rate = _to_decimal(metrics.get("win_rate"))
    profit_factor = _to_decimal(metrics.get("profit_factor"))

    holding_style = "short_term" if median_hold_ms is not None and median_hold_ms < EIGHT_HOURS_MS else "trend" if median_hold_ms is not None else None
    frequency_style = "high_frequency" if active_day_trade_average is not None and active_day_trade_average >= Decimal("5") else "low_frequency" if active_day_trade_average is not None else None
    risk_style = "aggressive" if risk_score is not None and risk_score >= Decimal("0.65") else "steady" if risk_score is not None else None
    edge_style = "entries" if win_rate is not None and win_rate >= Decimal("0.5") else "risk_control" if win_rate is not None else None
    persona_confidence_downgraded = partial and sample_quality == "full"
    if persona_confidence_downgraded:
        risk_style = None
        edge_style = None

    strengths: list[str] = []
    weaknesses: list[str] = []
    if active_day_trade_average is not None and active_day_trade_average >= Decimal("5"):
        strengths.append("Maintains a consistent idea generation pace.")
    if median_hold_ms is not None and median_hold_ms >= EIGHT_HOURS_MS:
        strengths.append("Lets positions breathe instead of cutting them instantly.")
    if "high_trade_frequency" in replay_tags:
        if frequency_style == "low_frequency":
            weaknesses.append("Short bursts of high trading frequency are dragging down selectivity.")
        else:
            weaknesses.append("High trading frequency is dragging down selectivity.")
    if (
        any(tag in replay_tags for tag in ("losses_outsize_wins", "low_win_large_loss", "poor_risk_reward"))
        or (profit_factor is not None and profit_factor < Decimal("1"))
    ):
        weaknesses.append("Loss size still dominates payoff quality.")
    if "take_profit_too_early" in replay_tags:
        weaknesses.append("Winning trades are still being clipped too early.")
    if not strengths:
        if partial:
            strengths.append("Sample is large enough for descriptive review, but the replay is still partial.")
        else:
            strengths.append("Sample is stable enough to classify recent trading behavior.")
    if not weaknesses:
        weaknesses.append("No dominant weakness crossed the current thresholds.")
    if partial:
        weaknesses.insert(0, "Replay sample is partial, so risk and edge labels stay downgraded.")

    if sample_quality == "minimal":
        observations = [
            "Sample size is still too small for a stable persona classification.",
            "Use the current metrics as directional observations instead of a firm trading profile.",
        ]
        result = _attach_payload_context({
            "selected_period": selected_period,
            "sample_quality": sample_quality,
            "profile_tier": "basic",
            "persona": None,
            "observations": observations,
            "metrics": metrics,
            "warning": "This analysis cannot predict future market direction.",
            "partial": partial,
            "degraded_reasons": degraded_reasons,
            "constraints": constraints,
        }, payload)
        return _attach_standard_disclaimer(result)

    result = _attach_payload_context({
        "selected_period": selected_period,
        "sample_quality": sample_quality,
        "profile_tier": "full" if sample_quality == "full" and not partial else "weak",
        "persona": {
            "holding_style": holding_style,
            "frequency_style": frequency_style,
            "risk_style": risk_style,
            "edge_style": edge_style,
        },
        "strengths": strengths[:3],
        "weaknesses": weaknesses[:3],
        "metrics": metrics,
        "warning": "This analysis cannot predict future market direction.",
        "partial": partial,
        "degraded_reasons": degraded_reasons,
        "constraints": constraints,
    }, payload)
    return _attach_standard_disclaimer(result)


def _derive_profile_metrics_from_replay_payload(
    payload: Any,
    replay_analysis: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    replay_metrics = dict(replay_analysis.get("metrics") or {})
    trade_episodes = [
        item
        for item in replay_analysis.get("trade_episodes", [])
        if isinstance(item, dict) and not bool(item.get("partial"))
    ]
    fills = [normalize_fill(item) for item in _extract_list(payload, FILL_KEYS)]
    bill_summary = _summarize_bill_adjustments(payload)

    derived: dict[str, Any] = {}

    episode_net_pnls = [
        _to_decimal(episode.get("net_pnl"))
        for episode in trade_episodes
        if _to_decimal(episode.get("net_pnl")) is not None
    ]
    wins = [value for value in episode_net_pnls if value is not None and value > 0]
    losses = [abs(value) for value in episode_net_pnls if value is not None and value < 0]
    if episode_net_pnls:
        derived["win_rate"] = _decimal_to_float(Decimal(len(wins)) / Decimal(len(episode_net_pnls)))
    elif replay_metrics.get("win_rate") is not None:
        derived["win_rate"] = replay_metrics["win_rate"]

    if losses:
        derived["profit_factor"] = _decimal_to_float(sum(wins, Decimal("0")) / sum(losses, Decimal("0")))
    elif replay_metrics.get("profit_factor") is not None:
        derived["profit_factor"] = replay_metrics["profit_factor"]

    if wins:
        derived["avg_win"] = _decimal_to_float(sum(wins, Decimal("0")) / Decimal(len(wins)))
    elif replay_metrics.get("average_win") is not None:
        derived["avg_win"] = replay_metrics["average_win"]

    if losses:
        derived["avg_loss"] = _decimal_to_float(sum(losses, Decimal("0")) / Decimal(len(losses)))
    elif replay_metrics.get("average_loss") is not None:
        derived["avg_loss"] = replay_metrics["average_loss"]

    episode_net_pnl = sum(episode_net_pnls, Decimal("0")) if episode_net_pnls else None
    if episode_net_pnl is not None:
        derived["episode_net_pnl"] = _decimal_to_float(episode_net_pnl)
    elif replay_metrics.get("episode_net_pnl") is not None:
        derived["episode_net_pnl"] = replay_metrics["episode_net_pnl"]

    bill_adjustment_total = _to_decimal(bill_summary.get("bill_adjustment_total")) or Decimal("0")
    bill_adjustment_count = int(bill_summary.get("bill_adjustment_count") or 0)
    if bill_adjustment_count or bill_adjustment_total != 0:
        derived["bill_adjustment_total"] = _decimal_to_float(bill_adjustment_total)
        if episode_net_pnl is not None:
            derived["net_pnl"] = _decimal_to_float(episode_net_pnl + bill_adjustment_total)
    elif replay_metrics.get("bill_adjustment_total") is not None:
        derived["bill_adjustment_total"] = replay_metrics["bill_adjustment_total"]
        if replay_metrics.get("net_pnl") is not None:
            derived["net_pnl"] = replay_metrics["net_pnl"]

    holding_samples_ms = [
        _to_decimal(episode.get("holding_minutes")) * Decimal("60000")
        for episode in trade_episodes
        if _to_decimal(episode.get("holding_minutes")) is not None
    ]
    median_hold_ms = _median(holding_samples_ms)
    if median_hold_ms is not None:
        derived["median_hold_ms"] = _decimal_to_float(median_hold_ms)

    active_days = {
        time.strftime("%Y-%m-%d", time.gmtime(trade_time / 1000))
        for trade_time in (
            _coerce_time(episode.get("close_time")) or _coerce_time(episode.get("open_time"))
            for episode in trade_episodes
        )
        if trade_time is not None
    }
    if not active_days:
        active_days = {
            time.strftime("%Y-%m-%d", time.gmtime(fill_time / 1000))
            for fill_time in (_coerce_time(fill.get("time")) for fill in fills)
            if fill_time is not None
        }
    if active_days:
        closed_trade_count = len(trade_episodes) or int(payload.get("closed_trade_count") or 0)
        derived["active_day_trade_average"] = _decimal_to_float(
            Decimal(closed_trade_count) / Decimal(len(active_days))
        )

    win_rate = _to_decimal(derived.get("win_rate"))
    profit_factor = _to_decimal(derived.get("profit_factor"))
    active_day_trade_average = _to_decimal(derived.get("active_day_trade_average"))
    median_hold_ms_value = _to_decimal(derived.get("median_hold_ms"))

    risk_score = _compute_profile_risk_score(
        win_rate=win_rate,
        profit_factor=profit_factor,
        active_day_trade_average=active_day_trade_average,
        median_hold_ms=median_hold_ms_value,
    )
    if risk_score is not None:
        derived["risk_score"] = _decimal_to_float(risk_score)

    return {key: value for key, value in derived.items() if value is not None}


def _build_alert(
    *,
    alert_type: str,
    level: str,
    target: str,
    reason: str,
    suggestion: str,
) -> dict[str, str]:
    return {
        "type": alert_type,
        "level": level,
        "target": target,
        "reason": reason,
        "suggestion": suggestion,
    }


def _position_protection_key(entry: dict[str, Any], *, market: str) -> tuple[str, str]:
    symbol = str(_pick(entry, "symbol") or "").upper()
    position_side = _normalize_position_side(
        _pick(entry, "position_side", "positionSide"),
        market=market,
        fallback_side=_pick(entry, "side"),
    ) or "net"
    return symbol, position_side


def _has_explicit_position_side(entry: dict[str, Any], *, market: str) -> bool:
    return (
        _normalize_position_side(
            _pick(entry, "position_side", "positionSide"),
            market=market,
        )
        is not None
    )


def _position_mode(entry: dict[str, Any]) -> str | None:
    return _normalize_position_mode(_pick(entry, "position_mode", "positionMode", "separatedMode"))


def _remaining_order_quantity(entry: dict[str, Any]) -> Decimal | None:
    quantity = _to_decimal(_pick(entry, "quantity", "origQty", "qty"))
    if quantity is None:
        return None
    executed_qty = _to_decimal(_pick(entry, "executed_qty", "executedQty")) or Decimal("0")
    return max(Decimal("0"), abs(quantity) - abs(executed_qty))


def _position_quantity(entry: dict[str, Any]) -> Decimal | None:
    quantity = _to_decimal(_pick(entry, "quantity", "qty", "size", "position_size", "positionAmt"))
    if quantity is None:
        return None
    return abs(quantity)


def _build_hedged_symbols(positions: list[dict[str, Any]], *, market: str) -> set[str]:
    position_sides_by_symbol: dict[str, set[str]] = {}
    for position in positions:
        symbol, position_side = _position_protection_key(position, market=market)
        if not symbol:
            continue
        position_sides_by_symbol.setdefault(symbol, set()).add(position_side)
    return {
        symbol
        for symbol, sides in position_sides_by_symbol.items()
        if len({side for side in sides if side != "net"}) > 1
    }


def _conditional_order_matches_bucket(
    order: dict[str, Any],
    *,
    market: str,
    target: tuple[str, str],
    allow_symbol_level: bool,
) -> bool:
    symbol = str(_pick(order, "symbol") or "").upper()
    if symbol != target[0]:
        return False
    if (
        allow_symbol_level
        and not _has_explicit_position_side(order, market=market)
        and _position_mode(order) != "SEPARATED"
    ):
        return True
    return _position_protection_key(order, market=market) == target


def _bucket_protection_state(
    *,
    market: str,
    target: tuple[str, str],
    positions: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
    conditional_orders: list[dict[str, Any]],
    preview_quantity: Decimal = Decimal("0"),
    preview_has_take_profit: bool = False,
    preview_has_stop_loss: bool = False,
    allow_symbol_level: bool = False,
) -> dict[str, Any]:
    required_qty = Decimal("0")
    quantity_complete = True

    for position in positions:
        if _position_protection_key(position, market=market) != target:
            continue
        quantity = _position_quantity(position)
        if quantity is None:
            quantity_complete = False
            continue
        required_qty += quantity

    reserved_close_qty = Decimal("0")
    for order in open_orders:
        if _position_protection_key(order, market=market) != target:
            continue
        if _infer_fill_action(order, order) == "entry":
            quantity = _remaining_order_quantity(order)
            if quantity is None:
                quantity_complete = False
                continue
            required_qty += quantity
            continue
        quantity = _remaining_order_quantity(order)
        if quantity is not None:
            reserved_close_qty += quantity

    required_qty += abs(preview_quantity)
    take_profit_covered_qty = abs(preview_quantity) if preview_has_take_profit else Decimal("0")
    stop_loss_covered_qty = abs(preview_quantity) if preview_has_stop_loss else Decimal("0")
    take_profit_full = False
    stop_loss_full = False

    for order in conditional_orders:
        if not _conditional_order_matches_bucket(
            order,
            market=market,
            target=target,
            allow_symbol_level=allow_symbol_level,
        ):
            continue
        quantity = _remaining_order_quantity(order)
        if _coerce_bool(_pick(order, "close_position", "closePosition")):
            if _pick(order, "tp_trigger_price", "tpTriggerPrice") not in (None, ""):
                take_profit_full = True
            if _pick(order, "sl_trigger_price", "slTriggerPrice") not in (None, ""):
                stop_loss_full = True
            continue
        if quantity is None:
            continue
        if _pick(order, "tp_trigger_price", "tpTriggerPrice") not in (None, ""):
            take_profit_covered_qty += quantity
        if _pick(order, "sl_trigger_price", "slTriggerPrice") not in (None, ""):
            stop_loss_covered_qty += quantity

    if quantity_complete:
        if take_profit_full:
            take_profit_covered_qty = required_qty
        if stop_loss_full:
            stop_loss_covered_qty = required_qty
        take_profit_covered_qty = max(Decimal("0"), take_profit_covered_qty - reserved_close_qty)
        stop_loss_covered_qty = max(Decimal("0"), stop_loss_covered_qty - reserved_close_qty)
        has_take_profit = required_qty > 0 and take_profit_covered_qty + POSITION_EPSILON >= required_qty
        has_stop_loss = required_qty > 0 and stop_loss_covered_qty + POSITION_EPSILON >= required_qty
        return {
            "required_qty": _decimal_to_float(required_qty),
            "take_profit_covered_qty": _decimal_to_float(take_profit_covered_qty),
            "stop_loss_covered_qty": _decimal_to_float(stop_loss_covered_qty),
            "has_take_profit": has_take_profit,
            "has_stop_loss": has_stop_loss,
            "quantity_complete": True,
        }

    return {
        "required_qty": None,
        "take_profit_covered_qty": None,
        "stop_loss_covered_qty": None,
        "has_take_profit": preview_has_take_profit or take_profit_full or take_profit_covered_qty > 0,
        "has_stop_loss": preview_has_stop_loss or stop_loss_full or stop_loss_covered_qty > 0,
        "quantity_complete": False,
    }


def _entry_order_notional(
    order: dict[str, Any],
    *,
    reference_price: Decimal | None,
) -> Decimal:
    if _infer_fill_action(order, order) != "entry":
        return Decimal("0")
    quantity = _remaining_order_quantity(order)
    if quantity in (None, Decimal("0")):
        return Decimal("0")
    price = _to_decimal(_pick(order, "price", "avg_price", "avgPrice")) or reference_price or Decimal("0")
    return abs(quantity) * price


def analyze_order_risk(payload: Any) -> dict[str, Any]:
    return _attach_payload_context(
        _attach_standard_disclaimer(risk_review_core.analyze_order_risk(payload)),
        payload,
    )


def analyze_account_risk(payload: Any) -> dict[str, Any]:
    return _attach_payload_context(
        _attach_standard_disclaimer(risk_review_core.analyze_account_risk(payload)),
        payload,
    )


def _append_status_sections(
    lines: list[str],
    *,
    partial: bool,
    partial_message: str | None,
    degraded_reasons: list[str],
    constraints: list[dict[str, Any]],
) -> None:
    if partial or degraded_reasons:
        lines.append("")
        lines.append("Partial Analysis")
        if partial and partial_message:
            lines.append(f"- {partial_message}")
        for reason in degraded_reasons:
            lines.append(f"- degraded: {reason}")

    if constraints:
        lines.append("")
        lines.append("Applied Filters")
        for constraint in constraints:
            code = str(constraint.get("code") or "constraint")
            message = str(constraint.get("message") or "").strip()
            if message:
                lines.append(f"- {code}: {message}")
            else:
                lines.append(f"- {code}")


def _format_duration_ms(value: Any) -> str:
    milliseconds = _to_decimal(value)
    if milliseconds is None:
        return str(value)
    total_seconds = max(0, int(round(float(milliseconds) / 1000)))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts[:2])


def _format_duration_minutes(value: Any) -> str:
    minutes = _to_decimal(value)
    if minutes is None:
        return str(value)
    return _format_duration_ms(minutes * Decimal("60000"))


def _format_order_preview_line(order_preview: Any) -> str | None:
    if not isinstance(order_preview, dict) or not order_preview:
        return None

    parts: list[str] = []
    for key in ("market", "symbol", "side", "position_side", "order_type"):
        value = str(order_preview.get(key) or "").strip()
        if value:
            parts.append(value)
    quantity = order_preview.get("quantity")
    price = order_preview.get("price")
    if quantity not in (None, ""):
        parts.append(f"qty={quantity}")
    if price not in (None, ""):
        parts.append(f"price={price}")
    if not parts:
        return None
    return f"Order Preview: {' '.join(parts)}"


def _render_text(result: dict[str, Any]) -> str:
    disclaimer = result.get("disclaimer") if isinstance(result, dict) else STANDARD_ANALYSIS_DISCLAIMER
    if result.get("review_type") == "trade_review":
        lines = ["Trade Review", str(result.get("summary") or "")]

        lines.append("")
        lines.append("Episode Highlights")
        for item in result.get("episode_highlights", []):
            lines.append(f"- {item}")

        episodes = list(result.get("episodes") or [])
        if episodes:
            lines.append("")
            lines.append("Key Episodes")
            for episode in episodes[:3]:
                target = _episode_target_with_status(episode)
                net_pnl = episode.get("net_pnl")
                holding_minutes = episode.get("holding_minutes")
                lines.append(
                    f"- {target}: net_pnl={net_pnl}, holding={_format_duration_minutes(holding_minutes)}, entries={episode.get('entry_count')}, exits={episode.get('exit_count')}"
                )

        pattern_snapshot = dict(result.get("pattern_snapshot") or {})
        lines.append("")
        lines.append("Pattern Snapshot")
        if pattern_snapshot.get("top_pattern"):
            lines.append(f"- Top Pattern: {pattern_snapshot['top_pattern']}")
        if pattern_snapshot.get("behavior_tags"):
            lines.append(f"- Behavior Tags: {', '.join(pattern_snapshot['behavior_tags'])}")
        for item in pattern_snapshot.get("evidence", []):
            lines.append(f"- Evidence: {item}")
        for item in pattern_snapshot.get("advice", []):
            lines.append(f"- Advice: {item}")

        metrics = result.get("metrics") or {}
        lines.append("")
        lines.append("Key Metrics")
        lines.append(f"- Episode Count: {result.get('episode_count', 0)}")
        if metrics.get("win_rate") is not None:
            lines.append(f"- Win Rate: {round(float(metrics['win_rate']) * 100, 2)}%")
        if metrics.get("profit_factor") is not None:
            lines.append(f"- Profit Factor: {round(float(metrics['profit_factor']), 4)}")
        if metrics.get("average_holding_minutes") is not None:
            lines.append(f"- Avg Holding Time: {_format_duration_minutes(metrics['average_holding_minutes'])}")
        if metrics.get("episode_net_pnl") is not None:
            lines.append(f"- Episode Net PnL: {metrics['episode_net_pnl']}")
        if metrics.get("bill_adjustment_total") not in (None, 0, 0.0):
            lines.append(f"- Bill Adjustments: {metrics['bill_adjustment_total']}")
        if metrics.get("net_pnl") is not None:
            lines.append(f"- Net PnL: {metrics['net_pnl']}")

        degraded_reasons = list(result.get("degraded_reasons") or [])
        constraints = list(result.get("constraints") or [])
        _append_status_sections(
            lines,
            partial=bool(result.get("partial")),
            partial_message="Trade review dataset is partial and should be interpreted with caution.",
            degraded_reasons=degraded_reasons,
            constraints=constraints,
        )
        _prepend_environment_text(lines, result)
        _append_standard_disclaimer(lines, disclaimer)
        return "\n".join(lines)

    if "trade_episodes" in result:
        lines = ["Replay Summary", str(result.get("summary") or ""), ""]
        lines.append("Behavior Tags")
        for tag in result.get("behavior_tags", []):
            lines.append(f"- {tag}")
        lines.append("")
        lines.append("Evidence")
        for item in result.get("evidence", []):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("Advice")
        for item in result.get("advice", []):
            lines.append(f"- {item}")
        metrics = result.get("metrics") or {}
        lines.append("")
        lines.append("Key Metrics")
        lines.append(f"- Episode Count: {result.get('episode_count', 0)}")
        if metrics.get("win_rate") is not None:
            lines.append(f"- Win Rate: {round(float(metrics['win_rate']) * 100, 2)}%")
        if metrics.get("profit_factor") is not None:
            lines.append(f"- Profit Factor: {round(float(metrics['profit_factor']), 4)}")
        if metrics.get("average_holding_minutes") is not None:
            lines.append(f"- Avg Holding Time: {_format_duration_minutes(metrics['average_holding_minutes'])}")
        if metrics.get("episode_net_pnl") is not None:
            lines.append(f"- Episode Net PnL: {metrics['episode_net_pnl']}")
        if metrics.get("bill_adjustment_total") not in (None, 0, 0.0):
            lines.append(f"- Bill Adjustments: {metrics['bill_adjustment_total']}")
        if metrics.get("net_pnl") is not None:
            lines.append(f"- Net PnL: {metrics['net_pnl']}")
        degraded_reasons = list(result.get("degraded_reasons") or [])
        constraints = list(result.get("constraints") or [])
        _append_status_sections(
            lines,
            partial=bool(result.get("partial")),
            partial_message="Replay dataset is partial and should be interpreted with caution.",
            degraded_reasons=degraded_reasons,
            constraints=constraints,
        )
        _prepend_environment_text(lines, result)
        _append_standard_disclaimer(lines, disclaimer)
        return "\n".join(lines)

    if "profile_tier" in result:
        lines = ["Profile Summary"]
        selected_period = result.get("selected_period")
        if selected_period:
            lines.append(f"Selected Period: {selected_period}")
        sample_quality = result.get("sample_quality")
        if sample_quality:
            lines.append(f"Sample Quality: {sample_quality}")
        profile_tier = result.get("profile_tier")
        if profile_tier:
            lines.append(f"Profile Tier: {profile_tier}")

        persona = result.get("persona") or {}
        if persona:
            lines.append("")
            lines.append("Persona")
            for key in ("holding_style", "frequency_style", "risk_style", "edge_style"):
                value = persona.get(key)
                if value:
                    lines.append(f"- {key}: {value}")

        observations = list(result.get("observations") or [])
        if observations:
            lines.append("")
            lines.append("Observations")
            for item in observations:
                lines.append(f"- {item}")

        strengths = list(result.get("strengths") or [])
        if strengths:
            lines.append("")
            lines.append("Strengths")
            for item in strengths:
                lines.append(f"- {item}")

        weaknesses = list(result.get("weaknesses") or [])
        if weaknesses:
            lines.append("")
            lines.append("Weaknesses")
            for item in weaknesses:
                lines.append(f"- {item}")

        metrics = result.get("metrics") or {}
        if metrics:
            lines.append("")
            lines.append("Key Metrics")
            metric_labels = {
                "win_rate": "Win Rate",
                "profit_factor": "Profit Factor",
                "risk_score": "Risk Score",
                "median_hold_ms": "Median Hold Time",
                "active_day_trade_average": "Active Day Trade Average",
                "avg_win": "Avg Win",
                "avg_loss": "Avg Loss",
                "episode_net_pnl": "Episode Net PnL",
                "bill_adjustment_total": "Bill Adjustments",
                "net_pnl": "Net PnL",
            }
            for key, label in metric_labels.items():
                value = metrics.get(key)
                if value is None:
                    continue
                if key == "win_rate":
                    lines.append(f"- {label}: {round(float(value) * 100, 2)}%")
                elif key == "median_hold_ms":
                    lines.append(f"- {label}: {_format_duration_ms(value)}")
                else:
                    lines.append(f"- {label}: {value}")

        if result.get("warning"):
            lines.append("")
            lines.append(str(result["warning"]))

        degraded_reasons = list(result.get("degraded_reasons") or [])
        constraints = list(result.get("constraints") or [])
        _append_status_sections(
            lines,
            partial=bool(result.get("partial")),
            partial_message="Profile dataset is partial and should be interpreted with caution.",
            degraded_reasons=degraded_reasons,
            constraints=constraints,
        )
        _prepend_environment_text(lines, result)
        _append_standard_disclaimer(lines, disclaimer)
        return "\n".join(lines)

    lines: list[str] = []
    order_preview_line = _format_order_preview_line(result.get("order_preview"))
    if order_preview_line:
        lines.append(order_preview_line)
    lines.extend(result.get("summary_lines", []))
    if result.get("summary"):
        lines.append(str(result["summary"]))
    for flag in result.get("risk_flags", []):
        lines.append(f"{flag['severity'].upper()}: {flag['message']}")
    for alert in result.get("alerts", []):
        lines.append(f"{alert['level'].upper()}: {alert['reason']}")
    if result.get("next_action_hint"):
        if lines:
            lines.append("")
        lines.append(str(result["next_action_hint"]))
    degraded_reasons = list(result.get("degraded_reasons") or [])
    constraints = list(result.get("constraints") or [])
    partial_message = None
    if "confirmation_required" in result or "order_preview" in result:
        partial_message = "Order-risk dataset is partial and should be interpreted with caution."
    elif "alerts" in result or "summary" in result:
        partial_message = "Account-risk dataset is partial and should be interpreted with caution."
    _append_status_sections(
        lines,
        partial=bool(result.get("partial")),
        partial_message=partial_message,
        degraded_reasons=degraded_reasons,
        constraints=constraints,
    )
    _prepend_environment_text(lines, result)
    _append_standard_disclaimer(lines, disclaimer)
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only WEEX snapshot and fill analysis CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("analyze-snapshot", "Analyze an account or positions snapshot."),
        ("analyze-fills", "Analyze fill and realized PnL data."),
        ("analyze-replay", "Analyze normalized replay data."),
        ("review-trades", "Review normalized replay data with trade episodes."),
        ("analyze-profile", "Analyze normalized profile data."),
        ("analyze-order-risk", "Analyze pre-order risk payloads."),
        ("analyze-account-risk", "Analyze current account-level risk payloads."),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--input", default="-", help="JSON input path or '-' for stdin.")
        command.add_argument("--format", choices=("json", "text"), default="json")
        command.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    payload = _load_json_input(args.input)

    if args.command == "analyze-snapshot":
        result = analyze_snapshot(payload)
    elif args.command == "analyze-fills":
        result = analyze_fills(payload)
    elif args.command == "analyze-replay":
        result = analyze_replay(payload)
    elif args.command == "review-trades":
        result = review_trades(payload)
    elif args.command == "analyze-profile":
        result = analyze_profile(payload)
    elif args.command == "analyze-order-risk":
        result = analyze_order_risk(payload)
    else:
        result = analyze_account_risk(payload)
    result = _attach_payload_context(_attach_standard_disclaimer(result), payload)

    if args.format == "text":
        print(_render_text(result))
        return 0

    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
