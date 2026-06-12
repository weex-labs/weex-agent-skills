#!/usr/bin/env python3
"""Shared WEEX order/account risk review helpers.

This file is the repository source of truth for order-risk and account-risk
analysis. Vendored copies live under each installable skill so standalone
installs do not depend on sibling skill paths.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any


LONG_SIDES = {"long", "buy", "bull"}
SHORT_SIDES = {"short", "sell", "bear"}
WINDOW_60_MIN_MS = 60 * 60 * 1000
POSITION_EPSILON = Decimal("0.00000001")


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


def _merge_degraded_reason(target: list[Any], reason: str) -> None:
    if reason and reason not in target:
        target.append(reason)


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 8)


def _ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in (None, Decimal("0")):
        return None
    return numerator / denominator


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


def _windowed_event_count(timestamps: list[int], window_ms: int) -> int:
    if not timestamps:
        return 0

    best = 0
    left = 0
    for right, current in enumerate(timestamps):
        while current - timestamps[left] > window_ms:
            left += 1
        best = max(best, right - left + 1)
    return best


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


def _bucket_protection_state(
    *,
    market: str,
    target: tuple[str, str],
    positions: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
    conditional_orders: list[dict[str, Any]],
    preview_quantity: Decimal = Decimal("0"),
    preview_action: str = "entry",
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

    preview_abs_qty = abs(preview_quantity)
    if preview_abs_qty:
        if preview_action == "entry":
            required_qty += preview_abs_qty
        else:
            reserved_close_qty += preview_abs_qty
    take_profit_covered_qty = preview_abs_qty if preview_action == "entry" and preview_has_take_profit else Decimal("0")
    stop_loss_covered_qty = preview_abs_qty if preview_action == "entry" and preview_has_stop_loss else Decimal("0")
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
        required_qty = max(Decimal("0"), required_qty - reserved_close_qty)
        if take_profit_full:
            take_profit_covered_qty = required_qty
        if stop_loss_full:
            stop_loss_covered_qty = required_qty
        has_take_profit = required_qty <= 0 or take_profit_covered_qty + POSITION_EPSILON >= required_qty
        has_stop_loss = required_qty <= 0 or stop_loss_covered_qty + POSITION_EPSILON >= required_qty
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
    if not isinstance(payload, dict):
        raise TypeError("order risk payload must be an object")

    order_preview = dict(payload.get("order_preview") or {})
    account_snapshot = dict(payload.get("account_snapshot") or {})
    tp_sl = dict(payload.get("tp_sl") or {})
    positions = [item for item in payload.get("positions", []) if isinstance(item, dict)]
    open_orders = [item for item in payload.get("open_orders", []) if isinstance(item, dict)]
    conditional_orders = [item for item in payload.get("conditional_orders", []) if isinstance(item, dict)]
    recent_orders = [item for item in payload.get("recent_orders", []) if isinstance(item, dict)]
    market_snapshot = dict(payload.get("market_snapshot") or {})
    degraded_reasons = list(payload.get("degraded_reasons") or [])
    constraints = list(payload.get("constraints") or [])
    partial = bool(payload.get("partial"))
    market = str(_pick(order_preview, "market") or "").lower()

    if not order_preview:
        partial = True
        _merge_degraded_reason(degraded_reasons, "order_risk_missing_order_preview")
    if not account_snapshot:
        partial = True
        _merge_degraded_reason(degraded_reasons, "order_risk_missing_account_snapshot")

    quantity = _to_decimal(_pick(order_preview, "quantity")) or Decimal("0")
    order_price = _to_decimal(_pick(order_preview, "price"))
    current_price = _to_decimal(_pick(market_snapshot, "current_price", "mark_price"))
    effective_price = order_price or current_price or Decimal("0")
    order_notional = abs(quantity) * effective_price
    equity = _to_decimal(_pick(account_snapshot, "equity", "balance"))
    available_balance = _to_decimal(_pick(account_snapshot, "available_balance", "availableBalance"))
    target = f"{_pick(order_preview, 'symbol') or 'UNKNOWN'} {str(_pick(order_preview, 'position_side', 'side') or '').lower()}".strip()
    skip_low_free_balance = market == "spot" and "spot_equity_estimate_partial" in degraded_reasons
    hedged_symbols = _build_hedged_symbols(positions, market=market) if market == "futures" else set()

    protection_state = None
    if market == "futures":
        protection_state = _bucket_protection_state(
            market=market,
            target=_position_protection_key(order_preview, market=market),
            positions=positions,
            open_orders=open_orders,
            conditional_orders=conditional_orders,
            preview_quantity=quantity,
            preview_action=_infer_fill_action(order_preview, order_preview),
            preview_has_take_profit=bool(tp_sl.get("has_take_profit")),
            preview_has_stop_loss=bool(tp_sl.get("has_stop_loss")),
            allow_symbol_level=str(_pick(order_preview, "symbol") or "").upper() not in hedged_symbols,
        )
        tp_ok = bool(protection_state.get("has_take_profit"))
        sl_ok = bool(protection_state.get("has_stop_loss"))
    else:
        tp_ok = bool(tp_sl.get("has_take_profit"))
        sl_ok = bool(tp_sl.get("has_stop_loss"))

    alerts: list[dict[str, str]] = []
    if "order_risk_missing_order_preview" in degraded_reasons or "order_risk_missing_account_snapshot" in degraded_reasons:
        alerts.append(
            _build_alert(
                alert_type="order_context_incomplete",
                level="warning",
                target=target or "order",
                reason="The order-risk payload is missing order or account context.",
                suggestion="Provide order_preview and account_snapshot before treating this risk review as complete.",
            )
        )
    if market == "futures" and (not tp_ok or not sl_ok):
        alerts.append(
            _build_alert(
                alert_type="missing_tp_sl",
                level="high",
                target=target,
                reason="The order is missing take-profit or stop-loss protection.",
                suggestion="Add TP/SL before continuing or explicitly accept an unprotected position.",
            )
        )

    if available_balance is not None and available_balance > 0 and order_notional >= (available_balance * Decimal("2")):
        alerts.append(
            _build_alert(
                alert_type="oversized_position",
                level="warning",
                target=target,
                reason="Projected order notional is large relative to available balance.",
                suggestion="Reduce size or split the entry into smaller tranches.",
            )
        )

    if order_price is not None and current_price is not None and current_price > 0:
        price_gap = abs(order_price - current_price) / current_price
        if price_gap >= Decimal("0.03"):
            alerts.append(
                _build_alert(
                    alert_type="limit_price_too_far",
                    level="warning",
                    target=target,
                    reason="Limit price is materially away from the current market price.",
                    suggestion="Recheck the price anchor before confirming the order.",
                )
            )

    recent_timestamps = sorted(ts for ts in (_coerce_time(order.get("time")) for order in recent_orders) if ts is not None)
    if recent_timestamps and _windowed_event_count(recent_timestamps, WINDOW_60_MIN_MS) >= 5:
        alerts.append(
            _build_alert(
                alert_type="high_trade_frequency",
                level="warning",
                target=target,
                reason="Recent trading frequency is high in the current review window.",
                suggestion="Pause and confirm this setup still meets the entry criteria.",
            )
        )

    if not skip_low_free_balance and equity is not None and equity > 0 and available_balance is not None:
        free_balance_ratio = available_balance / equity
        if free_balance_ratio < Decimal("0.2"):
            alerts.append(
                _build_alert(
                    alert_type="low_free_balance",
                    level="warning",
                    target=target,
                    reason="Free balance buffer is already low relative to account equity.",
                    suggestion="Preserve more balance buffer before increasing exposure.",
                )
            )

    position_notionals = [(_to_decimal(position.get("notional")) or Decimal("0")) for position in positions]
    working_entry_notionals = [
        _entry_order_notional(order, reference_price=current_price or order_price)
        for order in open_orders
    ]
    current_total_notional = sum((abs(value) for value in [*position_notionals, *working_entry_notionals]), Decimal("0"))
    resulting_total_notional = current_total_notional + order_notional
    resulting_largest_notional = max((abs(value) for value in [order_notional, *position_notionals, *working_entry_notionals]), default=Decimal("0"))
    resulting_leverage_estimate = _ratio(resulting_total_notional, equity)
    max_existing_leverage = max((_to_decimal(position.get("leverage")) or Decimal("0") for position in positions), default=Decimal("0"))
    concentration_elevated = current_total_notional > 0 and _concentration_alert_is_material(
        largest_notional=resulting_largest_notional,
        total_notional=resulting_total_notional,
        leverage_estimate=resulting_leverage_estimate,
    )
    leverage_elevated = max_existing_leverage >= Decimal("20") and (
        resulting_leverage_estimate is None or resulting_leverage_estimate >= Decimal("0.25")
    )
    if leverage_elevated or concentration_elevated:
        alerts.append(
            _build_alert(
                alert_type="high_leverage_or_concentration",
                level="warning",
                target=target,
                reason="Current leverage or resulting concentration is already elevated.",
                suggestion="Lower leverage, reduce size, or diversify exposure before confirming.",
            )
        )

    deduped_alerts: list[dict[str, str]] = []
    seen_types: set[str] = set()
    for alert in alerts:
        if alert["type"] in seen_types:
            continue
        seen_types.add(alert["type"])
        deduped_alerts.append(alert)

    return {
        "order_preview": order_preview,
        "has_risk": bool(deduped_alerts),
        "alerts": deduped_alerts,
        "confirmation_required": True,
        "next_action_hint": "Review the alerts before continuing with order submission.",
        "tp_sl_review": protection_state,
        "partial": partial,
        "degraded_reasons": degraded_reasons,
        "constraints": constraints,
    }


def analyze_account_risk(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("account risk payload must be an object")

    account_snapshot = dict(payload.get("account_snapshot") or {})
    positions = [item for item in payload.get("positions", []) if isinstance(item, dict)]
    recent_orders = [item for item in payload.get("recent_orders", []) if isinstance(item, dict)]
    conditional_orders = [item for item in payload.get("conditional_orders", []) if isinstance(item, dict)]
    open_orders = [item for item in payload.get("open_orders", []) if isinstance(item, dict)]
    degraded_reasons = list(payload.get("degraded_reasons") or [])
    constraints = list(payload.get("constraints") or [])
    partial = bool(payload.get("partial"))
    market = str(payload.get("market") or "")
    skip_low_free_balance = market == "spot" and "spot_equity_estimate_partial" in degraded_reasons

    equity = _to_decimal(_pick(account_snapshot, "equity", "balance"))
    available_balance = _to_decimal(_pick(account_snapshot, "available_balance", "availableBalance"))

    alerts: list[dict[str, str]] = []
    if not skip_low_free_balance and equity is not None and equity > 0 and available_balance is not None:
        free_balance_ratio = available_balance / equity
        if free_balance_ratio < Decimal("0.2"):
            alerts.append(
                _build_alert(
                    alert_type="low_free_balance",
                    level="warning",
                    target="account",
                    reason="Free balance buffer is already low relative to account equity.",
                    suggestion="Reduce exposure or release margin before adding new risk.",
                )
            )

    position_notionals = [abs(_to_decimal(position.get("notional")) or Decimal("0")) for position in positions]
    total_notional = sum(position_notionals, Decimal("0"))
    if total_notional > 0:
        largest_notional = max(position_notionals)
        gross_leverage_estimate = _ratio(total_notional, equity)
        if _concentration_alert_is_material(
            largest_notional=largest_notional,
            total_notional=total_notional,
            leverage_estimate=gross_leverage_estimate,
        ):
            alerts.append(
                _build_alert(
                    alert_type="concentration_risk",
                    level="warning",
                    target="account",
                    reason="One position dominates the current notional exposure.",
                    suggestion="Reduce concentration before adding more exposure to the same idea.",
                )
            )

    if any((_to_decimal(position.get("leverage")) or Decimal("0")) >= Decimal("20") for position in positions):
        alerts.append(
            _build_alert(
                alert_type="high_position_leverage",
                level="warning",
                target="account",
                reason="At least one open position is running at elevated leverage.",
                suggestion="Lower leverage or trim size before adding new trades.",
            )
        )

    recent_timestamps = sorted(ts for ts in (_coerce_time(order.get("time")) for order in recent_orders) if ts is not None)
    if recent_timestamps and _windowed_event_count(recent_timestamps, WINDOW_60_MIN_MS) >= 5:
        alerts.append(
            _build_alert(
                alert_type="frequent_trading",
                level="warning",
                target="account",
                reason="Trading activity is elevated in the current review window.",
                suggestion="Pause and reassess before taking the next setup.",
            )
        )

    if market == "futures" and positions:
        hedged_symbols = _build_hedged_symbols(positions, market=market)
        protected_symbols = {
            str(_pick(item, "symbol") or "").upper()
            for item in conditional_orders
            if (item.get("tp_trigger_price") not in (None, "") or item.get("sl_trigger_price") not in (None, ""))
            and str(_pick(item, "symbol") or "").upper()
            and not _has_explicit_position_side(item, market=market)
            and _position_mode(item) != "SEPARATED"
            and str(_pick(item, "symbol") or "").upper() not in hedged_symbols
        }
        protected_positions = {
            _position_protection_key(item, market=market)
            for item in conditional_orders
            if (item.get("tp_trigger_price") not in (None, "") or item.get("sl_trigger_price") not in (None, ""))
            and _has_explicit_position_side(item, market=market)
        }
        unprotected: list[str] = []
        for position in positions:
            symbol, position_side = _position_protection_key(position, market=market)
            if not symbol:
                continue
            protection_state = _bucket_protection_state(
                market=market,
                target=(symbol, position_side),
                positions=[position],
                open_orders=open_orders,
                conditional_orders=conditional_orders,
                allow_symbol_level=symbol not in hedged_symbols,
            )
            if protection_state.get("quantity_complete"):
                if protection_state.get("has_take_profit") and protection_state.get("has_stop_loss"):
                    continue
                unprotected.append(f"{symbol} {position_side}".strip())
                continue
            if symbol in protected_symbols or (symbol, position_side) in protected_positions:
                continue
            unprotected.append(f"{symbol} {position_side}".strip())
        if unprotected:
            alerts.append(
                _build_alert(
                    alert_type="missing_position_protection",
                    level="warning",
                    target=", ".join(sorted(set(unprotected))),
                    reason="Some open futures positions do not show active TP/SL protection.",
                    suggestion="Add protective conditional orders or reduce exposure.",
                )
            )

    seen_types: set[str] = set()
    deduped_alerts: list[dict[str, str]] = []
    for alert in alerts:
        if alert["type"] in seen_types:
            continue
        seen_types.add(alert["type"])
        deduped_alerts.append(alert)

    if deduped_alerts:
        summary = "Current account risk needs attention."
    elif partial or degraded_reasons:
        summary = "Partial account risk scan completed with no immediate alerts."
    else:
        summary = "Current account risk looks contained."

    return {
        "summary": summary,
        "has_risk": bool(deduped_alerts),
        "alerts": deduped_alerts,
        "partial": partial,
        "degraded_reasons": degraded_reasons,
        "constraints": constraints,
    }
