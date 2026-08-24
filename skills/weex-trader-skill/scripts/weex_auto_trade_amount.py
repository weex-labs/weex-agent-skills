#!/usr/bin/env python3
"""Deterministic conservative U valuation for WEEX automated orders."""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation, ROUND_UP, localcontext
from typing import Any


DEFAULT_MAX_FACT_AGE_MS = 5_000
U_QUANTUM = Decimal("0.00000001")
OFFICIAL_DEPTH_LIMITS = {15, 200}


class ValuationUnavailable(ValueError):
    """Raised when official facts cannot support a conservative valuation."""

    code = "VALUATION_UNAVAILABLE"


def estimate_order_amount(
    *,
    market: str,
    order: dict[str, Any],
    facts: dict[str, Any],
    now_ms: int | None = None,
    max_fact_age_ms: int = DEFAULT_MAX_FACT_AGE_MS,
) -> dict[str, Any]:
    """Return a conservative estimated U amount from normalized official facts."""
    if market not in {"SPOT", "FUTURES"}:
        raise ValuationUnavailable("unsupported official product module")
    if not isinstance(order, dict) or not isinstance(facts, dict):
        raise ValuationUnavailable("order and facts must be objects")
    if isinstance(max_fact_age_ms, bool) or not isinstance(max_fact_age_ms, int) or max_fact_age_ms <= 0:
        raise ValuationUnavailable("max_fact_age_ms must be a positive integer")
    current_ms = _current_ms(now_ms)
    _require_fresh_timestamp(facts.get("timestamp_ms"), current_ms, max_fact_age_ms, "official facts")
    degraded = facts.get("degraded_reasons")
    if degraded not in (None, []):
        raise ValuationUnavailable("official facts are partial or degraded")

    symbol_facts = facts.get("symbol")
    if not isinstance(symbol_facts, dict):
        raise ValuationUnavailable("official symbol metadata is unavailable")
    symbol = _required_text(order.get("symbol"), "symbol")
    side = _required_enum(order.get("side"), "side", {"BUY", "SELL"})
    order_type = _required_enum(order.get("type"), "type", {"LIMIT", "MARKET"})
    quantity = _positive_decimal(order.get("quantity"), "quantity")

    quote_asset = _required_text(
        _pick(symbol_facts, "quoteAsset", "quote_asset"), "quoteAsset"
    ).upper()
    maker_rate = _nonnegative_decimal(
        _pick(symbol_facts, "makerFeeRate", "maker_fee_rate"), "makerFeeRate"
    )
    taker_rate = _nonnegative_decimal(
        _pick(symbol_facts, "takerFeeRate", "taker_fee_rate"), "takerFeeRate"
    )
    fee_rate = max(maker_rate, taker_rate)

    if market == "SPOT":
        notional_quote = _reference_notional(
            order_type=order_type,
            side=side,
            quantity=quantity,
            price_value=order.get("price"),
            facts=facts,
            current_ms=current_ms,
            max_fact_age_ms=max_fact_age_ms,
            contract_value=Decimal("1"),
        )
        fee_quote = _multiply(notional_quote, fee_rate)
        total_quote = _exact_sum(notional_quote, fee_quote)
        conversion_rate, conversion_source = _asset_to_usdt_rate(
            quote_asset,
            facts=facts,
            current_ms=current_ms,
            max_fact_age_ms=max_fact_age_ms,
        )
        estimated_amount_u = _round_up_u(_multiply(total_quote, conversion_rate))
        source = (
            "SPOT_LIMIT_NOTIONAL_PLUS_FEE_UPPER_BOUND"
            if order_type == "LIMIT"
            else "SPOT_ADVERSE_DEPTH_PLUS_FEE_UPPER_BOUND"
        )
        return _result(
            market=market,
            symbol=symbol,
            quote_asset=quote_asset,
            notional_quote=notional_quote,
            margin_quote=None,
            fee_quote=fee_quote,
            conversion_rate=conversion_rate,
            conversion_source=conversion_source,
            estimated_amount_u=estimated_amount_u,
            valuation_source=source,
            leverage=None,
            margin_asset=None,
            margin_conversion_source=None,
        )

    margin_asset = _required_text(
        _pick(symbol_facts, "marginAsset", "margin_asset"), "marginAsset"
    ).upper()
    _, margin_conversion_source = _asset_to_usdt_rate(
        margin_asset,
        facts=facts,
        current_ms=current_ms,
        max_fact_age_ms=max_fact_age_ms,
    )
    contract_value = _positive_decimal(
        _pick(symbol_facts, "contractVal", "contract_value"), "contractVal"
    )
    # Official order/depth quantities can already be base-asset sized. Use the
    # larger interpretation so subunit contractVal metadata cannot understate quota.
    conservative_quantity_multiplier = max(Decimal("1"), contract_value)
    notional_quote = _reference_notional(
        order_type=order_type,
        side=side,
        quantity=quantity,
        price_value=order.get("price"),
        facts=facts,
        current_ms=current_ms,
        max_fact_age_ms=max_fact_age_ms,
        contract_value=conservative_quantity_multiplier,
    )
    margin_type = _required_enum(
        _pick(order, "marginType", "margin_type"),
        "marginType",
        {"CROSSED", "ISOLATED"},
    )
    leverage = _leverage_for_order(symbol_facts, margin_type=margin_type, side=side)
    fee_quote = _multiply(notional_quote, fee_rate)
    reduce_only = order.get("reduceOnly", order.get("reduce_only", False))
    if not isinstance(reduce_only, bool):
        raise ValuationUnavailable("reduceOnly must be a boolean")
    if reduce_only:
        if facts.get("reduce_only_proven") is not True:
            raise ValuationUnavailable("reduce-only semantics are not proven by official facts")
        margin_quote = Decimal("0")
    else:
        margin_quote = _divide_up(notional_quote, leverage)
    total_quote = _exact_sum(margin_quote, fee_quote)
    conversion_rate, conversion_source = _asset_to_usdt_rate(
        quote_asset,
        facts=facts,
        current_ms=current_ms,
        max_fact_age_ms=max_fact_age_ms,
    )
    estimated_amount_u = _round_up_u(_multiply(total_quote, conversion_rate))
    return _result(
        market=market,
        symbol=symbol,
        quote_asset=quote_asset,
        notional_quote=notional_quote,
        margin_quote=margin_quote,
        fee_quote=fee_quote,
        conversion_rate=conversion_rate,
        conversion_source=conversion_source,
        estimated_amount_u=estimated_amount_u,
        valuation_source=(
            "FUTURES_REDUCE_ONLY_FEE_UPPER_BOUND"
            if reduce_only
            else "FUTURES_ESTIMATED_MARGIN_PLUS_FEE_UPPER_BOUND"
        ),
        leverage=leverage,
        margin_asset=margin_asset,
        margin_conversion_source=margin_conversion_source,
    )


def _reference_notional(
    *,
    order_type: str,
    side: str,
    quantity: Decimal,
    price_value: Any,
    facts: dict[str, Any],
    current_ms: int,
    max_fact_age_ms: int,
    contract_value: Decimal,
) -> Decimal:
    if order_type == "LIMIT":
        price = _positive_decimal(price_value, "price")
        return _multiply(quantity, contract_value, price)
    depth = facts.get("depth")
    if not isinstance(depth, dict):
        raise ValuationUnavailable("official full-depth data is unavailable")
    _require_fresh_timestamp(
        depth.get("timestamp_ms"), current_ms, max_fact_age_ms, "official depth"
    )
    depth_limit = depth.get("limit")
    if isinstance(depth_limit, bool) or not isinstance(depth_limit, int) or depth_limit not in OFFICIAL_DEPTH_LIMITS:
        raise ValuationUnavailable("depth limit must be an official 15 or 200 level response")
    levels = depth.get("asks" if side == "BUY" else "bids")
    if not isinstance(levels, list) or not levels:
        raise ValuationUnavailable("adverse depth side is unavailable")
    remaining = quantity
    notional = Decimal("0")
    for level in levels:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            raise ValuationUnavailable("depth level is malformed")
        price = _positive_decimal(level[0], "depth price")
        available = _positive_decimal(level[1], "depth quantity")
        consumed = min(remaining, available)
        notional = _exact_sum(notional, _multiply(consumed, contract_value, price))
        remaining = _exact_sum(remaining, -consumed)
        if remaining == 0:
            break
    if remaining > 0:
        raise ValuationUnavailable("official depth does not cover the full order quantity")
    return notional


def _asset_to_usdt_rate(
    asset: str,
    *,
    facts: dict[str, Any],
    current_ms: int,
    max_fact_age_ms: int,
) -> tuple[Decimal, str]:
    if asset == "USDT":
        return Decimal("1"), "USDT_IDENTITY"
    rates = facts.get("conversion_rates")
    if not isinstance(rates, dict):
        raise ValuationUnavailable(f"official direct {asset}/USDT conversion is unavailable")
    direct_symbol = f"{asset}USDT"
    reverse_symbol = f"USDT{asset}"
    candidates = []
    if direct_symbol in rates:
        candidates.append((direct_symbol, "DIRECT", rates[direct_symbol]))
    if reverse_symbol in rates:
        candidates.append((reverse_symbol, "REVERSE", rates[reverse_symbol]))
    if not candidates:
        raise ValuationUnavailable(f"official direct {asset}/USDT conversion is unavailable")
    last_error: ValuationUnavailable | None = None
    for symbol, direction, record in candidates:
        try:
            if not isinstance(record, dict) or record.get("tradable") is not True:
                raise ValuationUnavailable("conversion pair is not currently tradable")
            _require_fresh_timestamp(
                record.get("timestamp_ms"), current_ms, max_fact_age_ms, "conversion rate"
            )
            price = _positive_decimal(record.get("price"), "conversion price")
            if direction == "DIRECT":
                return price, f"{symbol}_DIRECT"
            return _divide_up(Decimal("1"), price), f"{symbol}_REVERSE"
        except ValuationUnavailable as exc:
            last_error = exc
    raise last_error or ValuationUnavailable(
        f"official direct {asset}/USDT conversion is unavailable"
    )


def _leverage_for_order(symbol_facts: dict[str, Any], *, margin_type: str, side: str) -> Decimal:
    if margin_type == "CROSSED":
        raw = _pick(symbol_facts, "crossLeverage", "cross_leverage")
        return _positive_decimal(raw, "crossLeverage")
    if side == "BUY":
        raw = _pick(symbol_facts, "isolatedLongLeverage", "isolated_long_leverage")
        return _positive_decimal(raw, "isolatedLongLeverage")
    raw = _pick(symbol_facts, "isolatedShortLeverage", "isolated_short_leverage")
    return _positive_decimal(raw, "isolatedShortLeverage")


def _result(
    *,
    market: str,
    symbol: str,
    quote_asset: str,
    notional_quote: Decimal,
    margin_quote: Decimal | None,
    fee_quote: Decimal,
    conversion_rate: Decimal,
    conversion_source: str,
    estimated_amount_u: Decimal,
    valuation_source: str,
    leverage: Decimal | None,
    margin_asset: str | None,
    margin_conversion_source: str | None,
) -> dict[str, Any]:
    return {
        "estimated": True,
        "market": market,
        "symbol": symbol,
        "quote_asset": quote_asset,
        "notional_quote": _decimal_text(notional_quote),
        "estimated_margin_quote": (
            None if margin_quote is None else _decimal_text(margin_quote)
        ),
        "fee_upper_bound_quote": _decimal_text(fee_quote),
        "conversion_rate_to_usdt": _decimal_text(conversion_rate),
        "conversion_source": conversion_source,
        "margin_asset": margin_asset,
        "margin_conversion_source": margin_conversion_source,
        "leverage": None if leverage is None else _decimal_text(leverage),
        "estimated_amount_u": _decimal_text(estimated_amount_u),
        "valuation_source": valuation_source,
        "disclaimer": (
            "Estimated from current official WEEX facts; this is a conservative authorization "
            "amount, not an exact exchange incremental margin or final expense."
        ),
    }


def _current_ms(value: int | None) -> int:
    if value is None:
        return int(time.time() * 1000)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValuationUnavailable("now_ms must be a non-negative integer")
    return value


def _require_fresh_timestamp(value: Any, now_ms: int, max_age_ms: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValuationUnavailable(f"{label} timestamp is unavailable")
    age = now_ms - value
    if age < 0 or age > max_age_ms:
        raise ValuationUnavailable(f"{label} timestamp is stale")


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValuationUnavailable(f"{field} must be a non-empty string")
    return value.strip()


def _required_enum(value: Any, field: str, allowed: set[str]) -> str:
    text = _required_text(value, field).upper()
    if text not in allowed:
        raise ValuationUnavailable(f"{field} is unsupported")
    return text


def _positive_decimal(value: Any, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ValuationUnavailable(f"{field} must be a Decimal string")
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise ValuationUnavailable(f"{field} is not a valid Decimal string") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValuationUnavailable(f"{field} must be greater than zero")
    return decimal_value


def _nonnegative_decimal(value: Any, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ValuationUnavailable(f"{field} must be a Decimal string")
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise ValuationUnavailable(f"{field} is not a valid Decimal string") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValuationUnavailable(f"{field} must be non-negative")
    return decimal_value


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _round_up_u(value: Decimal) -> Decimal:
    integer_digits = max(len(value.as_tuple().digits) + value.as_tuple().exponent, 1)
    with localcontext() as context:
        context.prec = max(28, integer_digits - U_QUANTUM.as_tuple().exponent + 8)
        context.rounding = ROUND_UP
        return value.quantize(U_QUANTUM)


def _multiply(*values: Decimal) -> Decimal:
    if not values:
        return Decimal("1")
    with localcontext() as context:
        context.prec = max(28, sum(max(len(value.as_tuple().digits), 1) for value in values) + 8)
        context.rounding = ROUND_UP
        result = Decimal("1")
        for value in values:
            result *= value
        return result


def _exact_sum(*values: Decimal) -> Decimal:
    if not values:
        return Decimal("0")
    max_integer_digits = 1
    max_fraction_digits = 0
    for value in values:
        digits = value.as_tuple().digits
        exponent = value.as_tuple().exponent
        max_integer_digits = max(max_integer_digits, len(digits) + exponent, 1)
        max_fraction_digits = max(max_fraction_digits, -exponent, 0)
    with localcontext() as context:
        context.prec = max(
            28,
            max_integer_digits + max_fraction_digits + len(str(len(values))) + 4,
        )
        total = Decimal("0")
        for value in values:
            total += value
        return total


def _divide_up(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = max(
            28,
            len(numerator.as_tuple().digits) + len(denominator.as_tuple().digits) + 32,
        )
        context.rounding = ROUND_UP
        return numerator / denominator


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


__all__ = [
    "DEFAULT_MAX_FACT_AGE_MS",
    "OFFICIAL_DEPTH_LIMITS",
    "ValuationUnavailable",
    "estimate_order_amount",
]
