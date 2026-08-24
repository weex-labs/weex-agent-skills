#!/usr/bin/env python3
"""Preview order risk and enforce confirmation before live WEEX order submission."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from contextlib import ExitStack, nullcontext
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import weex_trade_risk_review as analysis
from weex_auto_trade_amount import ValuationUnavailable, estimate_order_amount
from weex_auto_trade_state import StateConflictError
from weex_order_intent_state import (
    build_intent,
    clear_intent,
    intent_is_expired,
    load_intent,
    save_intent,
)
from weex_profile_language import resolve_language
from weex_trade_data_aggregator import AggregationInputError, TradeDataAggregator


CONFIRMATION_PROMPTS = {
    "zh": {
        "reply_text": "确认",
        "reply_instruction": "如果你接受上述风险并要继续，请回复：确认",
    },
    "en": {
        "reply_text": "confirm",
        "reply_instruction": "If you accept the risks and want to continue, reply: confirm",
    },
}
AUTO_TRADE_AUTHORIZATION_HINTS = {
    "zh": (
        "如需取消二次确认功能，可申请自动交易授权。授权后，在指定交易类型、交易对、"
        "单笔金额和有效期范围内，下单无需逐笔确认。发送“申请自动交易授权”即可开始配置。"
    ),
    "en": (
        "To disable per-order confirmation, you can request automated trading authorization. "
        "After authorization, orders within the specified trade types, symbols, single-order amount, "
        'and validity period can be placed without per-order confirmation. Send "Request automated '
        'trading authorization" to start configuration.'
    ),
}
TRADING_MODES = ("live", "demo")
DEFAULT_TRADING_MODE = "live"
AUTO_TRADE_OPERATION_POLICY = {
    "spot.order.place_order": {"module": "SPOT", "kind": "SINGLE", "max_legs": 1},
    "spot.order.bulk_order": {"module": "SPOT", "kind": "BATCH", "max_legs": 10},
    "transaction.place_order": {"module": "FUTURES", "kind": "SINGLE", "max_legs": 1},
    "transaction.place_orders_batch": {"module": "FUTURES", "kind": "BATCH", "max_legs": 5},
    "transaction.place_pending_order": {"module": "FUTURES", "kind": "CONDITIONAL", "max_legs": 1},
    "transaction.place_tp_sl_order": {"module": "FUTURES", "kind": "TP_SL", "max_legs": 1},
}
AUTO_TRADE_DEFINITION_FILES = {
    "SPOT": "spot-api-definitions.json",
    "FUTURES": "contract-api-definitions.json",
}
ADVISORY_DEGRADED_REASONS = frozenset({"spot_equity_estimate_partial"})
AUTO_TRADE_RAW_CREDENTIAL_KEYS = frozenset(
    {
        "apikey",
        "apisecret",
        "secret",
        "passphrase",
        "apipassphrase",
        "password",
        "vaultpassword",
    }
)


def _parse_order_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid --order-json payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("--order-json must decode to a JSON object.")
    return payload


def _parse_tp_sl_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid --tp-sl-json payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("--tp-sl-json must decode to a JSON object.")
    return payload


def resolve_official_auto_trade_operation(operation_key: str) -> dict[str, Any] | None:
    """Resolve an allowlisted official operation without using caller-reported module or URL."""
    policy = AUTO_TRADE_OPERATION_POLICY.get(str(operation_key or "").strip())
    if policy is None:
        return None
    definitions_path = (
        Path(__file__).resolve().parents[1]
        / "references"
        / AUTO_TRADE_DEFINITION_FILES[policy["module"]]
    )
    try:
        payload = json.loads(definitions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    definitions = payload.get("definitions")
    if not isinstance(definitions, list):
        return None
    definition = next(
        (item for item in definitions if isinstance(item, dict) and item.get("key") == operation_key),
        None,
    )
    if (
        definition is None
        or definition.get("method") != "POST"
        or definition.get("requires_auth") is not True
        or definition.get("permission") != "TRADE"
    ):
        return None
    body_fields = definition.get("body_fields")
    if not isinstance(body_fields, list) or any(not isinstance(item, str) for item in body_fields):
        return None
    if operation_key == "spot.order.bulk_order":
        allowed_order_fields = {"symbol"}
        allowed_order_fields.update(
            item.removeprefix("orderList[].")
            for item in body_fields
            if item.startswith("orderList[].")
        )
    elif operation_key == "transaction.place_orders_batch":
        place_order_definition = next(
            (
                item
                for item in definitions
                if isinstance(item, dict) and item.get("key") == "transaction.place_order"
            ),
            None,
        )
        place_order_fields = (
            place_order_definition.get("body_fields")
            if isinstance(place_order_definition, dict)
            else None
        )
        if not isinstance(place_order_fields, list) or any(
            not isinstance(item, str) for item in place_order_fields
        ):
            return None
        allowed_order_fields = set(place_order_fields)
    else:
        allowed_order_fields = set(body_fields)
    if not allowed_order_fields:
        return None
    return {
        "operation_key": operation_key,
        **policy,
        "allowed_order_fields": frozenset(allowed_order_fields),
    }


def _blocking_reasons_from_risk_payload(
    payload: dict[str, Any],
    analysis_output: dict[str, Any],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    if not isinstance(payload.get("partial"), bool) or payload.get("partial") is True:
        reasons.append({"code": "RISK_DATA_INCOMPLETE", "message": "risk payload is incomplete"})
    degraded = payload.get("degraded_reasons")
    if not isinstance(degraded, list):
        reasons.append(
            {"code": "RISK_DATA_INCOMPLETE", "message": "risk degradation metadata is missing"}
        )
    else:
        reasons.extend(
            {"code": "RISK_DATA_DEGRADED", "message": str(item)}
            for item in degraded
            if str(item).strip() and str(item) not in ADVISORY_DEGRADED_REASONS
        )
    constraints = payload.get("constraints")
    if not isinstance(constraints, list):
        reasons.append(
            {"code": "RISK_DATA_INCOMPLETE", "message": "risk constraint metadata is missing"}
        )
    else:
        for item in constraints:
            if isinstance(item, dict):
                code = str(item.get("code") or "HARD_CHECK_FAILED")
                message = str(item.get("message") or code)
            else:
                code = "HARD_CHECK_FAILED"
                message = str(item)
            if message.strip():
                reasons.append({"code": code, "message": message})
    explicit = analysis_output.get("blocking_reasons")
    if isinstance(explicit, list):
        for item in explicit:
            if isinstance(item, dict):
                reasons.append(
                    {
                        "code": str(item.get("code") or "HARD_CHECK_FAILED"),
                        "message": str(item.get("message") or item.get("reason") or "hard check failed"),
                    }
                )
            elif str(item).strip():
                reasons.append({"code": "HARD_CHECK_FAILED", "message": str(item)})
    if analysis_output.get("partial") is True:
        reasons.append(
            {"code": "RISK_DATA_INCOMPLETE", "message": "risk analysis output is partial"}
        )
    analysis_degraded = analysis_output.get("degraded_reasons")
    if isinstance(analysis_degraded, list):
        reasons.extend(
            {"code": "RISK_DATA_DEGRADED", "message": str(item)}
            for item in analysis_degraded
            if str(item).strip() and str(item) not in ADVISORY_DEGRADED_REASONS
        )
    deduplicated: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for reason in reasons:
        identity = (reason["code"], reason["message"])
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(reason)
    return deduplicated


def _request_fingerprint(operation_key: str, orders: list[dict[str, Any]]) -> str:
    caller_id_fields = {"newClientOrderId", "clientAlgoId"}
    normalized_orders = [
        {key: value for key, value in order.items() if key not in caller_id_fields}
        for order in orders
    ]
    encoded = json.dumps(
        {"operation_key": operation_key, "orders": normalized_orders},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_decimal_text(value: Any) -> str:
    decimal_value = Decimal(str(value))
    text = format(decimal_value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _legacy_replay_legs(
    operation: dict[str, Any], orders: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rebuild the caller-determinable fields retained before request digests existed."""
    legs: list[dict[str, Any]] = []
    for index, order in enumerate(orders):
        kind = operation["kind"]
        if kind == "CONDITIONAL":
            leg_type = "CONDITIONAL"
        elif kind == "TP_SL":
            leg_type = str(order.get("planType") or "").upper()
        else:
            leg_type = "PRIMARY" if len(orders) == 1 else "BATCH_CHILD"
        if kind == "TP_SL":
            position_side = str(order.get("positionSide") or "").upper()
            side = "SELL" if position_side == "LONG" else "BUY"
            execute_price = order.get("executePrice")
            price = (
                None
                if execute_price in (None, "", "0") or Decimal(str(execute_price)) == 0
                else _normalized_decimal_text(execute_price)
            )
        else:
            side = str(order.get("side") or "").upper()
            conditional_type = str(order.get("type") or "").upper()
            if kind == "CONDITIONAL" and conditional_type in {
                "STOP_MARKET",
                "TAKE_PROFIT_MARKET",
            }:
                price = None
            else:
                raw_price = order.get("price")
                price = (
                    None
                    if raw_price in (None, "")
                    else _normalized_decimal_text(raw_price)
                )
        legs.append(
            {
                "leg_id": f"leg-{index}",
                "leg_index": index,
                "leg_type": leg_type,
                "module": operation["module"],
                "symbol": str(order.get("symbol") or "").upper(),
                "side": side,
                "order_type": str(
                    order.get("type") or order.get("orderType") or order.get("planType") or ""
                ).upper(),
                "quantity": _normalized_decimal_text(order.get("quantity")),
                "price": price,
            }
        )
    return legs


def _positive_decimal_field(order: dict[str, Any], field: str) -> bool:
    try:
        value = Decimal(str(order.get(field)))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return value.is_finite() and value > 0


def _spot_quantity_blocking_reason(
    facts: dict[str, Any],
    quantity_value: Any,
) -> dict[str, str] | None:
    symbol_facts = facts.get("symbol") if isinstance(facts, dict) else None
    if not isinstance(symbol_facts, dict):
        return {
            "code": "SPOT_PRODUCT_RULES_UNAVAILABLE",
            "message": "official spot product quantity rules are unavailable",
        }
    try:
        quantity = Decimal(str(quantity_value))
        step_size = Decimal(str(symbol_facts.get("stepSize")))
        minimum = Decimal(str(symbol_facts.get("minTradeAmount")))
        maximum = Decimal(str(symbol_facts.get("maxTradeAmount")))
    except (InvalidOperation, TypeError, ValueError):
        return {
            "code": "SPOT_PRODUCT_RULES_UNAVAILABLE",
            "message": "official spot product quantity rules are unavailable",
        }
    if any(
        not value.is_finite() or value <= 0
        for value in (quantity, step_size, minimum, maximum)
    ) or maximum < minimum:
        return {
            "code": "SPOT_PRODUCT_RULES_UNAVAILABLE",
            "message": "official spot product quantity rules are invalid",
        }
    if quantity < minimum:
        return {
            "code": "SPOT_QUANTITY_BELOW_MINIMUM",
            "message": "spot order quantity is below minTradeAmount",
        }
    if quantity > maximum:
        return {
            "code": "SPOT_QUANTITY_ABOVE_MAXIMUM",
            "message": "spot order quantity is above maxTradeAmount",
        }
    try:
        if quantity % step_size != 0:
            return {
                "code": "SPOT_QUANTITY_STEP_MISMATCH",
                "message": "spot order quantity is not an integer multiple of stepSize",
            }
    except InvalidOperation:
        return {
            "code": "SPOT_PRODUCT_RULES_UNAVAILABLE",
            "message": "official spot product quantity rules cannot be applied",
        }
    return None


def _finite_nonnegative_decimal(value: Any) -> Decimal | None:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not decimal_value.is_finite() or decimal_value < 0:
        return None
    return decimal_value


def _validate_official_order_semantics(
    operation: dict[str, Any],
    order: dict[str, Any],
) -> list[dict[str, str]]:
    def reason(message: str) -> list[dict[str, str]]:
        return [{"code": "HARD_CHECK_FAILED", "message": message}]

    if not str(order.get("symbol") or "").strip():
        return reason("symbol is required")
    side = str(order.get("side") or "").upper()
    if operation["kind"] != "TP_SL" and side not in {"BUY", "SELL"}:
        return reason("side must be BUY or SELL")
    if operation["kind"] != "TP_SL" and not _positive_decimal_field(order, "quantity"):
        return reason("quantity must be greater than zero")

    kind = operation["kind"]
    if kind == "TP_SL":
        try:
            quantity = Decimal(str(order.get("quantity")))
        except (InvalidOperation, TypeError, ValueError):
            return reason("quantity must be numeric")
        if not quantity.is_finite() or quantity < 0:
            return reason("quantity must be greater than or equal to zero")
        if str(order.get("planType") or "").upper() not in {"TAKE_PROFIT", "STOP_LOSS"}:
            return reason("planType must be TAKE_PROFIT or STOP_LOSS")
        if str(order.get("positionSide") or "").upper() not in {"LONG", "SHORT"}:
            return reason("positionSide must be LONG or SHORT")
        if not _positive_decimal_field(order, "triggerPrice"):
            return reason("triggerPrice must be greater than zero")
        trigger_type = str(order.get("triggerPriceType") or "CONTRACT_PRICE").upper()
        if trigger_type not in {"CONTRACT_PRICE", "MARK_PRICE"}:
            return reason("triggerPriceType is invalid")
        execute_price = order.get("executePrice", "0")
        try:
            execute_decimal = Decimal(str(execute_price))
        except (InvalidOperation, TypeError, ValueError):
            return reason("executePrice must be numeric")
        if not execute_decimal.is_finite() or execute_decimal < 0:
            return reason("executePrice must be greater than or equal to zero")
        return []

    if operation["module"] == "FUTURES" and str(
        order.get("positionSide") or ""
    ).upper() not in {"LONG", "SHORT"}:
        return reason("positionSide must be LONG or SHORT")

    order_type = str(order.get("type") or "").upper()
    if kind == "CONDITIONAL":
        if order_type not in {"STOP", "TAKE_PROFIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            return reason("conditional order type is invalid")
        if not _positive_decimal_field(order, "triggerPrice"):
            return reason("triggerPrice must be greater than zero")
        if order_type in {"STOP", "TAKE_PROFIT"} and not _positive_decimal_field(order, "price"):
            return reason("conditional limit order price is required")
        return []

    if order_type not in {"LIMIT", "MARKET"}:
        return reason("type must be LIMIT or MARKET")
    if order_type == "LIMIT":
        if not _positive_decimal_field(order, "price"):
            return reason("limit price is required")
        time_in_force = str(order.get("timeInForce") or "").upper()
        allowed = {"GTC", "IOC", "FOK"}
        if operation["module"] == "FUTURES":
            allowed.add("POST_ONLY")
        if time_in_force not in allowed:
            return reason("timeInForce is required for LIMIT orders")
    return []


def _manual_fallback(
    *,
    code: str,
    blocking_reasons: list[dict[str, str]],
    advisory_alerts: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "MANUAL_CONFIRMATION_REQUIRED",
        "error": {"code": code},
        "advisory_alerts": advisory_alerts or [],
        "blocking_reasons": blocking_reasons,
        "next_action": "PREVIEW_AND_CONFIRM_ORDER_MANUALLY",
    }


def _contains_raw_credentials(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if normalized in AUTO_TRADE_RAW_CREDENTIAL_KEYS or _contains_raw_credentials(child):
                return True
    elif isinstance(value, list):
        return any(_contains_raw_credentials(child) for child in value)
    return False


def _state_operation_lock(state: Any):
    lock_factory = getattr(state, "operation_lock", None)
    if not callable(lock_factory):
        return nullcontext()
    candidate = lock_factory()
    if not hasattr(candidate, "__enter__") or not hasattr(candidate, "__exit__"):
        return nullcontext()
    return candidate


def submit_authorized_order(
    *,
    state: Any,
    operation_key: str,
    strategy_id: str,
    authorization_id: str,
    idempotency_key: str,
    orders: list[dict[str, Any]],
    risk_payload_provider: Any,
    risk_evaluator: Any,
    facts_provider: Any,
    submitter: Any,
    confirm_live: bool,
    now: Any = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Run an authorized order while restore and other state operations are excluded."""
    if confirm_live is not True:
        return _submit_authorized_order_unlocked(
            state=state,
            operation_key=operation_key,
            strategy_id=strategy_id,
            authorization_id=authorization_id,
            idempotency_key=idempotency_key,
            orders=orders,
            risk_payload_provider=risk_payload_provider,
            risk_evaluator=risk_evaluator,
            facts_provider=facts_provider,
            submitter=submitter,
            confirm_live=confirm_live,
            now=now,
            now_ms=now_ms,
        )
    stack = ExitStack()
    try:
        stack.enter_context(_state_operation_lock(state))
    except StateConflictError:
        return _manual_fallback(
            code="STATE_CONFLICT",
            blocking_reasons=[
                {
                    "code": "STATE_CONFLICT",
                    "message": "automated-trading operation lock is unavailable",
                }
            ],
        )
    with stack:
        return _submit_authorized_order_unlocked(
            state=state,
            operation_key=operation_key,
            strategy_id=strategy_id,
            authorization_id=authorization_id,
            idempotency_key=idempotency_key,
            orders=orders,
            risk_payload_provider=risk_payload_provider,
            risk_evaluator=risk_evaluator,
            facts_provider=facts_provider,
            submitter=submitter,
            confirm_live=confirm_live,
            now=now,
            now_ms=now_ms,
        )


def _submit_authorized_order_unlocked(
    *,
    state: Any,
    operation_key: str,
    strategy_id: str,
    authorization_id: str,
    idempotency_key: str,
    orders: list[dict[str, Any]],
    risk_payload_provider: Any,
    risk_evaluator: Any,
    facts_provider: Any,
    submitter: Any,
    confirm_live: bool,
    now: Any = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Run the deterministic authorized path with injected official-data and REST boundaries."""
    if confirm_live is not True:
        return _manual_fallback(
            code="LIVE_CONFIRMATION_REQUIRED",
            blocking_reasons=[{"code": "LIVE_CONFIRMATION_REQUIRED", "message": "--confirm-live is required"}],
        )
    operation = resolve_official_auto_trade_operation(operation_key)
    if operation is None:
        return _manual_fallback(
            code="UNSUPPORTED_OPERATION",
            blocking_reasons=[{"code": "UNSUPPORTED_OPERATION", "message": "operation is not in the official auto-trade catalog"}],
        )
    if not isinstance(orders, list) or not orders or any(not isinstance(item, dict) for item in orders):
        return _manual_fallback(
            code="HARD_CHECK_FAILED",
            blocking_reasons=[{"code": "HARD_CHECK_FAILED", "message": "orders must be a non-empty array"}],
        )
    if _contains_raw_credentials(orders):
        code = "RAW_CREDENTIALS_NOT_ALLOWED"
        return _manual_fallback(
            code=code,
            blocking_reasons=[
                {
                    "code": code,
                    "message": "raw credentials are not accepted by automated-trading guards",
                }
            ],
        )
    if any(set(order) - operation["allowed_order_fields"] for order in orders):
        code = "UNSUPPORTED_ORDER_FIELDS"
        return _manual_fallback(
            code=code,
            blocking_reasons=[
                {
                    "code": code,
                    "message": "order contains fields outside the official operation schema",
                }
            ],
        )
    if len(orders) > operation["max_legs"]:
        code = "BATCH_LEG_LIMIT_EXCEEDED"
        return _manual_fallback(
            code=code,
            blocking_reasons=[
                {
                    "code": code,
                    "message": "order leg count exceeds the official operation limit",
                }
            ],
        )
    if operation["kind"] != "BATCH" and len(orders) != 1:
        return _manual_fallback(
            code="HARD_CHECK_FAILED",
            blocking_reasons=[{"code": "HARD_CHECK_FAILED", "message": "single-order operation received multiple legs"}],
        )
    attached_fields = (
        {"tpTriggerPrice", "slTriggerPrice"}
        if operation_key == "transaction.place_order"
        else (
            {"presetTakeProfitPrice", "presetStopLossPrice"}
            if operation_key == "transaction.place_pending_order"
            else set()
        )
    )
    if attached_fields and any(
        order.get(field) not in (None, "") for order in orders for field in attached_fields
    ):
        code = "LEG_MAPPING_UNAVAILABLE"
        return _manual_fallback(
            code=code,
            blocking_reasons=[
                {
                    "code": code,
                    "message": "attached TP/SL child orders cannot be mapped to independent exchange legs",
                }
            ],
        )
    if operation_key == "spot.order.bulk_order" and len(
        {str(order.get("symbol") or "").upper() for order in orders}
    ) != 1:
        code = "SPOT_BATCH_SYMBOL_MISMATCH"
        return _manual_fallback(
            code=code,
            blocking_reasons=[
                {
                    "code": code,
                    "message": "official Spot batch orders must share one envelope symbol",
                }
            ],
        )
    semantic_reasons: list[dict[str, str]] = []
    for raw_order in orders:
        semantic_reasons.extend(_validate_official_order_semantics(operation, raw_order))
    if semantic_reasons:
        return _manual_fallback(
            code=semantic_reasons[0]["code"],
            blocking_reasons=semantic_reasons,
        )
    if operation["kind"] == "TP_SL":
        raw_quantity = orders[0].get("quantity")
        try:
            full_position = raw_quantity in (None, "") or Decimal(str(raw_quantity)) == 0
        except InvalidOperation:
            full_position = False
        if full_position:
            code = "FULL_POSITION_REQUIRES_MANUAL_CONFIRMATION"
            return _manual_fallback(
                code=code,
                blocking_reasons=[
                    {
                        "code": code,
                        "message": "full-position TP/SL has no deterministic quantity for authorization valuation",
                    }
                ],
            )

    request_fingerprint = _request_fingerprint(operation_key, orders)
    replay_reader = getattr(type(state), "get_submission_group_by_idempotency", None)
    if callable(replay_reader):
        try:
            existing_group = state.get_submission_group_by_idempotency(
                authorization_id=authorization_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                legacy_legs=_legacy_replay_legs(operation, orders),
            )
        except StateConflictError:
            return _manual_fallback(
                code="STATE_CONFLICT",
                blocking_reasons=[
                    {
                        "code": "STATE_CONFLICT",
                        "message": "prior automated-trading submission state is inconsistent",
                    }
                ],
            )
        except ValueError:
            return _manual_fallback(
                code="IDEMPOTENCY_CONFLICT",
                blocking_reasons=[
                    {
                        "code": "IDEMPOTENCY_CONFLICT",
                        "message": "idempotency key is already bound to a different request",
                    }
                ],
            )
        if existing_group is not None:
            existing_statuses = {item["usage_status"] for item in existing_group["legs"]}
            existing_status = (
                next(iter(existing_statuses))
                if len(existing_statuses) == 1
                else "SUBMISSION_GROUP_PARTIAL"
            )
            return {
                "ok": existing_status == "ACCEPTED",
                "status": existing_status,
                "advisory_alerts": [],
                "blocking_reasons": [],
                "legs": existing_group["legs"],
                "next_action": "INSPECT_EXISTING_USAGE",
            }

    prepared: list[dict[str, Any]] = []
    advisory_alerts: list[Any] = []
    blocking_reasons: list[dict[str, str]] = []
    for index, raw_order in enumerate(orders):
        if operation["kind"] == "CONDITIONAL":
            leg_type = "CONDITIONAL"
        elif operation["kind"] == "TP_SL":
            leg_type = str(raw_order.get("planType") or "").upper()
            if leg_type not in {"TAKE_PROFIT", "STOP_LOSS"}:
                blocking_reasons.append(
                    {"code": "HARD_CHECK_FAILED", "message": "TP/SL planType is invalid"}
                )
                continue
        else:
            leg_type = "PRIMARY" if len(orders) == 1 else "BATCH_CHILD"
        leg = {
            "leg_id": f"leg-{index}",
            "leg_index": index,
            "leg_type": leg_type,
            "module": operation["module"],
            "order": dict(raw_order),
        }
        try:
            risk_payload = risk_payload_provider(leg)
            if not isinstance(risk_payload, dict):
                raise ValueError("risk payload is unavailable")
            analysis_output = risk_evaluator(risk_payload)
            if not isinstance(analysis_output, dict):
                raise ValueError("risk analysis output is unavailable")
        except Exception:
            blocking_reasons.append(
                {"code": "RISK_DATA_UNAVAILABLE", "message": "risk preview could not be completed"}
            )
            continue
        alerts = analysis_output.get("alerts")
        leg_advisories: list[dict[str, str]] = []
        if isinstance(alerts, list):
            advisory_alerts.extend(alerts)
            for alert in alerts:
                if not isinstance(alert, dict):
                    continue
                normalized_alert = {
                    key: str(alert[key])
                    for key in ("type", "level", "code", "reason", "suggestion")
                    if alert.get(key) not in (None, "")
                }
                if normalized_alert:
                    leg_advisories.append(normalized_alert)
        blocking_reasons.extend(_blocking_reasons_from_risk_payload(risk_payload, analysis_output))
        if blocking_reasons:
            continue
        try:
            facts = facts_provider(leg)
            if operation["module"] == "SPOT":
                quantity_reason = _spot_quantity_blocking_reason(
                    facts, raw_order.get("quantity")
                )
                if quantity_reason is not None:
                    blocking_reasons.append(quantity_reason)
                    continue
            valuation_order = dict(raw_order)
            record_order_type = str(
                raw_order.get("type")
                or raw_order.get("orderType")
                or raw_order.get("planType")
                or ""
            )
            if operation["kind"] == "CONDITIONAL":
                conditional_type = str(raw_order.get("type") or "").upper()
                if conditional_type in {"STOP", "TAKE_PROFIT"}:
                    valuation_order["type"] = "LIMIT"
                elif conditional_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
                    valuation_order["type"] = "MARKET"
                    valuation_order.pop("price", None)
                else:
                    raise ValuationUnavailable("unsupported conditional order type")
            elif operation["kind"] == "TP_SL":
                position_side = str(raw_order.get("positionSide") or "").upper()
                if position_side not in {"LONG", "SHORT"}:
                    raise ValuationUnavailable("unsupported TP/SL position side")
                valuation_order["side"] = "SELL" if position_side == "LONG" else "BUY"
                execute_price = raw_order.get("executePrice")
                if execute_price in (None, "", "0"):
                    valuation_order["type"] = "MARKET"
                    valuation_order.pop("price", None)
                else:
                    valuation_order["type"] = "LIMIT"
                    valuation_order["price"] = str(execute_price)
            if operation["module"] == "FUTURES" and isinstance(facts, dict):
                side = str(valuation_order.get("side") or "").upper()
                position_side = str(valuation_order.get("positionSide") or "").upper()
                reduce_only = (side, position_side) in {
                    ("SELL", "LONG"),
                    ("BUY", "SHORT"),
                }
                if reduce_only and facts.get("reduce_only_proven") is not True:
                    blocking_reasons.append(
                        {
                            "code": "REDUCE_ONLY_UNPROVEN",
                            "message": "official position facts do not prove reduce-only semantics",
                        }
                    )
                    continue
                valuation_order["reduceOnly"] = reduce_only
                if valuation_order.get("marginType") in (None, ""):
                    symbol_facts = facts.get("symbol")
                    if isinstance(symbol_facts, dict):
                        valuation_order["marginType"] = symbol_facts.get("marginType")
            valuation = estimate_order_amount(
                market=operation["module"],
                order=valuation_order,
                facts=facts,
                now_ms=now_ms,
            )
        except (ValuationUnavailable, Exception):
            blocking_reasons.append(
                {"code": "VALUATION_UNAVAILABLE", "message": "official conservative valuation is unavailable"}
            )
            continue
        if operation["module"] == "SPOT":
            account_snapshot = risk_payload.get("account_snapshot")
            symbol_facts = facts.get("symbol") if isinstance(facts, dict) else None
            base_asset = (
                str(symbol_facts.get("baseAsset") or "").strip().upper()
                if isinstance(symbol_facts, dict)
                else ""
            )
            quote_asset = (
                str(symbol_facts.get("quoteAsset") or "").strip().upper()
                if isinstance(symbol_facts, dict)
                else ""
            )
            if not isinstance(account_snapshot, dict) or not base_asset or not quote_asset:
                blocking_reasons.append(
                    {
                        "code": "AVAILABLE_BALANCE_UNAVAILABLE",
                        "message": "spot base and quote asset balance facts are unavailable",
                    }
                )
                continue
            side = str(valuation_order.get("side") or "").upper()
            if side == "BUY":
                snapshot_quote_asset = str(
                    account_snapshot.get("quote_asset") or ""
                ).strip().upper()
                available_balance = _finite_nonnegative_decimal(
                    account_snapshot.get("quote_available_balance_u")
                )
                estimated_amount_u = _finite_nonnegative_decimal(
                    valuation.get("estimated_amount_u")
                )
                if snapshot_quote_asset != quote_asset or available_balance is None:
                    blocking_reasons.append(
                        {
                            "code": "AVAILABLE_BALANCE_UNAVAILABLE",
                            "message": "spot quote available balance is unavailable",
                        }
                    )
                    continue
                if estimated_amount_u is None:
                    blocking_reasons.append(
                        {
                            "code": "VALUATION_UNAVAILABLE",
                            "message": "official conservative valuation is unavailable",
                        }
                    )
                    continue
                if available_balance < estimated_amount_u:
                    blocking_reasons.append(
                        {
                            "code": "INSUFFICIENT_AVAILABLE_BALANCE",
                            "message": "spot quote available balance is below the conservative order amount",
                        }
                    )
                    continue
            elif side == "SELL":
                snapshot_base_asset = str(
                    account_snapshot.get("base_asset") or ""
                ).strip().upper()
                base_available_quantity = _finite_nonnegative_decimal(
                    account_snapshot.get("base_available_quantity")
                )
                order_quantity = _finite_nonnegative_decimal(
                    valuation_order.get("quantity")
                )
                if snapshot_base_asset != base_asset or base_available_quantity is None:
                    blocking_reasons.append(
                        {
                            "code": "BASE_ASSET_BALANCE_UNAVAILABLE",
                            "message": "spot base asset available quantity is unavailable",
                        }
                    )
                    continue
                if order_quantity is None or base_available_quantity < order_quantity:
                    blocking_reasons.append(
                        {
                            "code": "INSUFFICIENT_BASE_ASSET_BALANCE",
                            "message": "spot base asset available quantity is below the sell quantity",
                        }
                    )
                    continue
            else:
                blocking_reasons.append(
                    {
                        "code": "HARD_CHECK_FAILED",
                        "message": "spot order side is invalid",
                    }
                )
                continue
        prepared.append(
            {
                **leg,
                "valuation": valuation,
                "record_side": str(valuation_order.get("side") or ""),
                "record_order_type": record_order_type,
                "record_quantity": str(valuation_order.get("quantity") or ""),
                "record_price": (
                    None
                    if valuation_order.get("price") in (None, "")
                    else str(valuation_order["price"])
                ),
                "client_order_field": (
                    "clientAlgoId"
                    if operation["kind"] in {"CONDITIONAL", "TP_SL"}
                    else "newClientOrderId"
                ),
                "advisory_alerts": leg_advisories,
                "risk_rule_version": str(
                    analysis_output.get("rule_version")
                    or analysis_output.get("version")
                    or "unknown"
                ),
                "risk_input_timestamp": (
                    None
                    if risk_payload.get("generated_at") in (None, "")
                    else str(risk_payload["generated_at"])
                ),
            }
        )

    if blocking_reasons or len(prepared) != len(orders):
        return _manual_fallback(
            code=blocking_reasons[0]["code"] if blocking_reasons else "HARD_CHECK_FAILED",
            blocking_reasons=blocking_reasons,
            advisory_alerts=advisory_alerts,
        )

    try:
        group = state.prepare_submission_group(
            strategy_id=strategy_id,
            authorization_id=authorization_id,
            idempotency_key=idempotency_key,
            legs=[
                {
                    "leg_id": item["leg_id"],
                    "leg_index": item["leg_index"],
                    "leg_type": item["leg_type"],
                    "module": item["module"],
                    "symbol": str(item["order"].get("symbol") or ""),
                    "estimated_amount_u": item["valuation"]["estimated_amount_u"],
                    "valuation_source": item["valuation"]["valuation_source"],
                    "side": item["record_side"],
                    "order_type": item["record_order_type"],
                    "quantity": item["record_quantity"],
                    "price": item["record_price"],
                    "advisory_alerts": item["advisory_alerts"],
                    "risk_rule_version": item["risk_rule_version"],
                    "risk_input_timestamp": item["risk_input_timestamp"],
                }
                for item in prepared
            ],
            request_fingerprint=request_fingerprint,
            now=now,
        )
        reservation_results = group["legs"]
        order_records = group["legs"]
        for item, order_record in zip(prepared, order_records):
            raw_order = item["order"]
            outgoing_order = dict(raw_order)
            outgoing_order.pop("newClientOrderId", None)
            outgoing_order.pop("clientAlgoId", None)
            outgoing_order[item["client_order_field"]] = order_record["client_order_id"]
            item["order"] = outgoing_order
            item["usage_id"] = order_record["usage_id"]
            item["client_order_id"] = order_record["client_order_id"]
    except StateConflictError:
        code = "STATE_CONFLICT"
        return _manual_fallback(
            code=code,
            blocking_reasons=[
                {
                    "code": code,
                    "message": "automated-trading authorization state is unavailable or inconsistent",
                }
            ],
            advisory_alerts=advisory_alerts,
        )
    except ValueError as exc:
        code = str(exc) if str(exc).isupper() else "HARD_CHECK_FAILED"
        return _manual_fallback(
            code=code,
            blocking_reasons=[{"code": code, "message": "authorization, scope, or quota check failed"}],
            advisory_alerts=advisory_alerts,
        )

    if group["replayed"]:
        existing_statuses = {item["usage_status"] for item in order_records}
        existing_status = (
            next(iter(existing_statuses))
            if len(existing_statuses) == 1
            else "SUBMISSION_GROUP_PARTIAL"
        )
        return {
            "ok": True,
            "status": existing_status,
            "advisory_alerts": advisory_alerts,
            "blocking_reasons": [],
            "legs": order_records,
            "next_action": "INSPECT_EXISTING_USAGE",
        }

    try:
        submission_results = submitter(operation_key, prepared)
        if not isinstance(submission_results, list):
            raise RuntimeError("submission result is not a leg array")
    except Exception:
        submission_results = [
            {"leg_id": item["leg_id"], "status": "REVIEW_REQUIRED"} for item in prepared
        ]

    expected_by_leg_id = {item["leg_id"]: item for item in prepared}
    expected_by_client_order_id = {item["client_order_id"]: item for item in prepared}
    candidates_by_leg: dict[str, list[dict[str, Any]]] = {
        item["leg_id"]: [] for item in prepared
    }
    for downstream in submission_results:
        if not isinstance(downstream, dict):
            continue
        mapped_by_leg = expected_by_leg_id.get(downstream.get("leg_id"))
        downstream_client_order_id = downstream.get("client_order_id")
        if downstream_client_order_id in (None, ""):
            downstream_client_order_id = downstream.get("clientOrderId")
        mapped_by_client = expected_by_client_order_id.get(downstream_client_order_id)
        if mapped_by_leg is not None and mapped_by_client is not None and mapped_by_leg is not mapped_by_client:
            continue
        mapped = (
            mapped_by_client
            if operation["kind"] == "BATCH"
            else (mapped_by_leg or mapped_by_client)
        )
        if mapped is not None:
            candidates_by_leg[mapped["leg_id"]].append(downstream)
    if (
        operation["kind"] == "TP_SL"
        and len(prepared) == 1
        and len(submission_results) == 1
        and isinstance(submission_results[0], dict)
        and not candidates_by_leg[prepared[0]["leg_id"]]
    ):
        candidates_by_leg[prepared[0]["leg_id"]].append(submission_results[0])
    final_legs: list[dict[str, Any]] = []
    for item, reservation, order_record in zip(prepared, reservation_results, order_records):
        mapped_candidates = candidates_by_leg[item["leg_id"]]
        downstream = mapped_candidates[0] if len(mapped_candidates) == 1 else {}
        raw_outcome = downstream.get("status")
        if raw_outcome in (None, ""):
            if downstream.get("success") is True:
                raw_outcome = "ACCEPTED"
            elif downstream.get("success") is False:
                raw_outcome = "RELEASED"
        outcome = str(raw_outcome or "REVIEW_REQUIRED").upper()
        weex_order_id = downstream.get("weex_order_id")
        if weex_order_id in (None, ""):
            weex_order_id = downstream.get("orderId")
        if outcome == "ACCEPTED" and not weex_order_id:
            outcome = "REVIEW_REQUIRED"
        if outcome not in {"ACCEPTED", "RELEASED", "REVIEW_REQUIRED"}:
            outcome = "REVIEW_REQUIRED"
        rejection_evidence = (
            downstream.get("error_code")
            or downstream.get("errorCode")
            or downstream.get("rejectionCode")
        )
        rejection_message = (
            downstream.get("error_message")
            or downstream.get("errorMessage")
            or downstream.get("rejectionMessage")
        )
        rejection_code_text = _bounded_auto_trade_error_text(
            rejection_evidence, max_length=128
        )
        rejection_message_text = _bounded_auto_trade_error_text(
            rejection_message, max_length=512
        )
        if outcome == "RELEASED" and (weex_order_id or not rejection_evidence):
            outcome = "REVIEW_REQUIRED"
        if weex_order_id:
            order_record = state.record_order(
                usage_id=reservation["usage_id"],
                weex_order_id=str(weex_order_id),
                side=item["record_side"],
                order_type=item["record_order_type"],
                quantity=item["record_quantity"],
                price=item["record_price"],
                now=now,
            )
        settled = state.settle_usage(
            usage_id=reservation["usage_id"],
            outcome=outcome,
            error_code=(rejection_code_text if outcome == "RELEASED" else None),
            error_message=(rejection_message_text if outcome == "RELEASED" else None),
            now=now,
        )
        final_leg = {
            **settled,
            "leg_id": item["leg_id"],
            "client_order_id": order_record["client_order_id"],
            "weex_order_id": weex_order_id,
            "estimated_amount_u": item["valuation"]["estimated_amount_u"],
        }
        if outcome == "RELEASED":
            final_leg["error_code"] = rejection_code_text
            if rejection_message_text is not None:
                final_leg["error_message"] = rejection_message_text
        final_legs.append(final_leg)
    statuses = {item["status"] for item in final_legs}
    status = next(iter(statuses)) if len(statuses) == 1 else "SUBMISSION_GROUP_PARTIAL"
    return {
        "ok": status in {"ACCEPTED", "RELEASED"},
        "status": status,
        "advisory_alerts": advisory_alerts,
        "blocking_reasons": [],
        "legs": final_legs,
        "next_action": (
            "NONE" if status == "ACCEPTED" else "INSPECT_AND_RECONCILE_MANUALLY"
        ),
    }


def _output_json(payload: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


def _output_error(error: str, pretty: bool) -> None:
    _output_json({"ok": False, "error": error}, pretty)


def _normalize_trading_mode(raw: Any) -> str:
    mode = str(raw or DEFAULT_TRADING_MODE).strip().lower()
    if mode not in TRADING_MODES:
        raise AggregationInputError(f"invalid_trading_mode: expected one of {', '.join(TRADING_MODES)}")
    return mode


def _bounded_auto_trade_error_text(value: Any, *, max_length: int) -> str | None:
    if value in (None, ""):
        return None
    normalized = " ".join(str(value).split())
    return normalized[:max_length] or None


def _arg_value(args: argparse.Namespace, name: str, default: Any = None) -> Any:
    return vars(args).get(name, default)


def _environment_for_mode(trading_mode: str, market: str) -> dict[str, Any]:
    mode = _normalize_trading_mode(trading_mode)
    normalized_market = str(market or "").strip().lower()
    if mode == "demo":
        if normalized_market != "futures":
            raise AggregationInputError("demo_spot_unsupported: demo trading_mode is only supported for futures")
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
        "market": normalized_market or "unknown",
        "uses_real_funds": True,
        "notice": f"This operation targets real WEEX {normalized_market or 'trading'} trading.",
    }


def _environment_from_payload_or_mode(payload: dict[str, Any], trading_mode: str, market: str) -> dict[str, Any]:
    environment = payload.get("environment")
    if isinstance(environment, dict) and environment.get("trading_mode"):
        return dict(environment)
    return _environment_for_mode(trading_mode, market)


def _merge_environment_context(
    result: dict[str, Any],
    *,
    trading_mode: str,
    environment: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(result)
    updated["trading_mode"] = trading_mode
    updated["environment"] = environment
    return updated


def _user_facing_trading_mode_label(environment: dict[str, Any], *, language: str) -> str:
    mode = _normalize_trading_mode(environment.get("trading_mode"))
    if language == "zh":
        return "模拟盘" if mode == "demo" else "真实盘"
    return "demo trading" if mode == "demo" else "real trading"


def _confirmation_environment_label(environment: dict[str, Any], *, language: str) -> str:
    mode = _normalize_trading_mode(environment.get("trading_mode"))
    if language == "zh":
        return "模拟盘" if mode == "demo" else "真实盘"
    return _user_facing_trading_mode_label(environment, language=language)


def _other_confirmation_environment_label(environment: dict[str, Any], *, language: str) -> str:
    mode = _normalize_trading_mode(environment.get("trading_mode"))
    if language == "zh":
        return "真实盘" if mode == "demo" else "模拟盘"
    return "real trading" if mode == "demo" else "demo trading"


def _switch_reply_text(environment: dict[str, Any], *, language: str) -> str:
    other_mode = _other_confirmation_environment_label(environment, language=language)
    if language == "zh":
        return f"切换到{other_mode}"
    return f"switch to {other_mode}"


def _query_environment_prefix(environment: dict[str, Any], *, language: str | None = None) -> str:
    resolved_language = resolve_language(language)
    mode = _confirmation_environment_label(environment, language=resolved_language)
    if resolved_language == "zh":
        return f"当前交易环境：{mode}"
    return f"Current trading mode: {mode}"


def _format_value(value: Any, *, missing: str = "未返回") -> str:
    if value is None or value == "":
        return missing
    return str(value)


def _normalize_upper_text(value: Any) -> str:
    return str(value or "").strip().upper()


def _zh_market_label(market: Any) -> str:
    normalized = str(market or "").strip().lower()
    if normalized == "futures":
        return "合约"
    if normalized == "spot":
        return "现货"
    return normalized or "交易"


def _en_market_label(market: Any) -> str:
    normalized = str(market or "").strip().lower()
    if normalized == "futures":
        return "futures"
    if normalized == "spot":
        return "spot"
    return normalized or "trading"


def _zh_order_type_label(order_type: Any) -> str:
    normalized = _normalize_upper_text(order_type)
    if normalized == "MARKET":
        return "市价"
    if normalized == "LIMIT":
        return "限价"
    return normalized or "订单"


def _en_order_type_label(order_type: Any) -> str:
    normalized = _normalize_upper_text(order_type)
    if normalized == "MARKET":
        return "market"
    if normalized == "LIMIT":
        return "limit"
    return normalized.lower() or "order"


def _zh_order_action(order_preview: dict[str, Any]) -> str:
    side = _normalize_upper_text(order_preview.get("side"))
    position_side = _normalize_upper_text(order_preview.get("position_side") or order_preview.get("positionSide"))
    if position_side == "LONG" and side == "BUY":
        return "开多"
    if position_side == "SHORT" and side == "SELL":
        return "开空"
    if position_side == "LONG" and side == "SELL":
        return "平多"
    if position_side == "SHORT" and side == "BUY":
        return "平空"
    if side == "BUY":
        return "买入"
    if side == "SELL":
        return "卖出"
    return "下单"


def _en_order_action(order_preview: dict[str, Any]) -> str:
    side = _normalize_upper_text(order_preview.get("side"))
    position_side = _normalize_upper_text(order_preview.get("position_side") or order_preview.get("positionSide"))
    if position_side == "LONG" and side == "BUY":
        return "open long"
    if position_side == "SHORT" and side == "SELL":
        return "open short"
    if position_side == "LONG" and side == "SELL":
        return "close long"
    if position_side == "SHORT" and side == "BUY":
        return "close short"
    if side == "BUY":
        return "buy"
    if side == "SELL":
        return "sell"
    return "place order"


def _is_full_position_tp_sl(order_preview: dict[str, Any]) -> bool:
    if not order_preview.get("planType"):
        return False
    quantity = order_preview.get("quantity")
    if quantity is None or str(quantity).strip() == "":
        return True
    try:
        return Decimal(str(quantity)) == 0
    except (InvalidOperation, ValueError):
        return False


def _format_zh_order_summary(preview_context: dict[str, Any] | None) -> str:
    order_preview = (preview_context or {}).get("order_preview")
    if not isinstance(order_preview, dict) or not order_preview:
        return "订单：详情请以上方风险预览为准。"
    symbol = _format_value(order_preview.get("symbol"))
    market = _zh_market_label(order_preview.get("market"))
    order_type = _zh_order_type_label(order_preview.get("order_type") or order_preview.get("orderType"))
    action = _zh_order_action(order_preview)
    if _is_full_position_tp_sl(order_preview):
        quantity = "全部仓位（quantity 为 0 或省略）"
    else:
        quantity = _format_value(order_preview.get("quantity") or order_preview.get("size"))
    price = order_preview.get("price")
    price_text = "" if price in (None, "") else f"，价格 {_format_value(price)}"
    return f"订单：{symbol} {market}，{order_type}{action}，数量 {quantity}{price_text}。"


def _format_en_order_summary(preview_context: dict[str, Any] | None) -> str:
    order_preview = (preview_context or {}).get("order_preview")
    if not isinstance(order_preview, dict) or not order_preview:
        return "Order: see the risk preview above for details."
    symbol = _format_value(order_preview.get("symbol"), missing="not returned")
    market = _en_market_label(order_preview.get("market"))
    order_type = _en_order_type_label(order_preview.get("order_type") or order_preview.get("orderType"))
    action = _en_order_action(order_preview)
    if _is_full_position_tp_sl(order_preview):
        quantity = "the full position (quantity is 0 or omitted)"
    else:
        quantity = _format_value(order_preview.get("quantity") or order_preview.get("size"), missing="not returned")
    price = order_preview.get("price")
    price_text = "" if price in (None, "") else f", price {_format_value(price, missing='not returned')}"
    return f"Order: {symbol} {market}, {order_type} {action}, quantity {quantity}{price_text}."


def _alert_level_is_high(alert: dict[str, Any]) -> bool:
    return str(alert.get("level") or "").strip().lower() == "high" or alert.get("type") == "missing_tp_sl"


def _alert_reason_zh(alert: dict[str, Any]) -> str:
    alert_type = alert.get("type")
    if alert_type == "missing_tp_sl":
        return "这笔订单没有止盈或止损保护，需要你明确接受无保护仓位风险后才能继续。"
    if alert_type in {"high_trade_frequency", "frequent_trading"}:
        return "近期交易频率偏高，请暂停并确认这次入场仍满足交易条件。"
    if alert_type == "high_leverage_or_concentration":
        return "当前杠杆或集中度已经偏高，确认前应降低杠杆、减小仓位或分散风险。"
    reason = str(alert.get("reason") or alert.get("suggestion") or alert.get("type") or "请先复核上方风险提示。").strip()
    if reason[-1:] not in "。！？.!?":
        reason += "。"
    return reason


def _alert_reason_en(alert: dict[str, Any]) -> str:
    alert_type = alert.get("type")
    if alert_type == "missing_tp_sl":
        return "The order has no take-profit or stop-loss protection. Continue only if you explicitly accept an unprotected position."
    if alert_type in {"high_trade_frequency", "frequent_trading"}:
        return "Recent trading frequency is high. Pause and confirm this setup still meets the entry criteria."
    if alert_type == "high_leverage_or_concentration":
        return "Current leverage or resulting concentration is already elevated. Lower leverage, reduce size, or diversify exposure before confirming."
    reason = str(alert.get("reason") or alert.get("suggestion") or alert.get("type") or "Review the risk alert above before continuing.").strip()
    if reason[-1:] not in ".!?。！？":
        reason += "."
    return reason


def _alert_label_zh(alert: dict[str, Any]) -> str:
    labels = {
        "missing_tp_sl": "缺少止盈止损",
        "high_trade_frequency": "频繁交易",
        "frequent_trading": "频繁交易",
        "high_leverage_or_concentration": "高杠杆或集中度",
        "low_free_balance": "可用余额偏低",
        "oversized_position": "仓位偏大",
        "limit_price_too_far": "限价偏离",
    }
    return labels.get(str(alert.get("type") or ""), "风险提示")


def _alert_label_en(alert: dict[str, Any]) -> str:
    labels = {
        "missing_tp_sl": "Missing TP/SL",
        "high_trade_frequency": "Frequent trading",
        "frequent_trading": "Frequent trading",
        "high_leverage_or_concentration": "High leverage or concentration",
        "low_free_balance": "Low free balance",
        "oversized_position": "Oversized position",
        "limit_price_too_far": "Limit price deviation",
    }
    return labels.get(str(alert.get("type") or ""), "Risk alert")


def _format_zh_alert_summary(preview_context: dict[str, Any] | None) -> str:
    alerts = [alert for alert in (preview_context or {}).get("alerts", []) if isinstance(alert, dict)]
    if not alerts:
        return "风险提示：未发现高风险提示。"
    alert = next((candidate for candidate in alerts if _alert_level_is_high(candidate)), alerts[0])
    prefix = "高风险提示" if _alert_level_is_high(alert) else "风险提示"
    lines = [f"{prefix}：{_alert_reason_zh(alert)}"]
    remaining_alerts = [candidate for candidate in alerts if candidate is not alert]
    if remaining_alerts:
        lines.append("其他风险提示：")
        for candidate in remaining_alerts:
            lines.append(f"- {_alert_label_zh(candidate)}：{_alert_reason_zh(candidate)}")
    return "\n".join(lines)


def _format_en_alert_summary(preview_context: dict[str, Any] | None) -> str:
    alerts = [alert for alert in (preview_context or {}).get("alerts", []) if isinstance(alert, dict)]
    if not alerts:
        return "Risk alert: no high-risk alerts were detected."
    alert = next((candidate for candidate in alerts if _alert_level_is_high(candidate)), alerts[0])
    prefix = "High-risk alert" if _alert_level_is_high(alert) else "Risk alert"
    lines = [f"{prefix}: {_alert_reason_en(alert)}"]
    remaining_alerts = [candidate for candidate in alerts if candidate is not alert]
    if remaining_alerts:
        lines.append("Other risk alerts:")
        for candidate in remaining_alerts:
            lines.append(f"- {_alert_label_en(candidate)}: {_alert_reason_en(candidate)}")
    return "\n".join(lines)


def _build_zh_confirmation_instruction(
    *,
    environment: dict[str, Any],
    preview_context: dict[str, Any] | None,
    include_mode_switch: bool,
    auto_trade_authorization_hint: str | None,
    reply_text: str,
) -> tuple[str, str | None]:
    mode = _confirmation_environment_label(environment, language="zh")
    uses_real_funds = bool(environment.get("uses_real_funds"))
    funds_line = "本次操作将使用真实资金，请谨慎确认。" if uses_real_funds else "本次操作不会使用真实资金。"
    confirm_line = (
        f"如果确认使用真实资金提交这笔订单，请回复：{reply_text}"
        if uses_real_funds
        else f"如果确认提交到{mode}，请回复：{reply_text}"
    )
    lines = [
        f"当前交易环境：{mode}",
        funds_line,
        "",
        f"{mode}风险预览已生成，订单尚未提交。",
        "",
        _format_zh_order_summary(preview_context),
        _format_zh_alert_summary(preview_context),
        "",
        confirm_line,
    ]
    switch_text = None
    if include_mode_switch:
        switch_text = _switch_reply_text(environment, language="zh")
        other_mode = _other_confirmation_environment_label(environment, language="zh")
        lines.extend(["", f"如果需要切换为{other_mode}，请回复：{switch_text}。"])
    if auto_trade_authorization_hint is not None:
        lines.extend(["", auto_trade_authorization_hint])
    return "\n".join(lines), switch_text


def _build_en_confirmation_instruction(
    *,
    environment: dict[str, Any],
    preview_context: dict[str, Any] | None,
    include_mode_switch: bool,
    auto_trade_authorization_hint: str | None,
    reply_text: str,
) -> tuple[str, str | None]:
    mode = _confirmation_environment_label(environment, language="en")
    uses_real_funds = bool(environment.get("uses_real_funds"))
    funds_line = "This operation uses real funds. Confirm carefully." if uses_real_funds else "This operation does not use real funds."
    preview_line = f"{mode.capitalize()} risk preview generated; order has not been submitted."
    confirm_line = (
        f"To submit this order with real funds, reply: {reply_text}"
        if uses_real_funds
        else f"To submit this order to {mode}, reply: {reply_text}"
    )
    lines = [
        f"Trading mode: {mode}",
        funds_line,
        "",
        preview_line,
        "",
        _format_en_order_summary(preview_context),
        _format_en_alert_summary(preview_context),
        "",
        confirm_line,
    ]
    switch_text = None
    if include_mode_switch:
        switch_text = _switch_reply_text(environment, language="en")
        other_mode = _other_confirmation_environment_label(environment, language="en")
        lines.extend(["", f"To switch to {other_mode}, reply: {switch_text}."])
    if auto_trade_authorization_hint is not None:
        lines.extend(["", auto_trade_authorization_hint])
    return "\n".join(lines), switch_text


def _build_user_confirmation(
    language: str | None,
    *,
    environment: dict[str, Any] | None = None,
    preview_context: dict[str, Any] | None = None,
    include_mode_switch: bool = False,
    include_auto_trade_authorization_hint: bool = False,
) -> dict[str, str]:
    resolved_language = resolve_language(language)
    prompt = CONFIRMATION_PROMPTS[resolved_language]
    auto_trade_authorization_hint = (
        AUTO_TRADE_AUTHORIZATION_HINTS[resolved_language]
        if include_auto_trade_authorization_hint
        else None
    )
    reply_instruction = prompt["reply_instruction"]
    switch_text = None
    if environment is not None:
        if resolved_language == "zh":
            reply_instruction, switch_text = _build_zh_confirmation_instruction(
                environment=environment,
                preview_context=preview_context,
                include_mode_switch=include_mode_switch,
                auto_trade_authorization_hint=auto_trade_authorization_hint,
                reply_text=prompt["reply_text"],
            )
        else:
            reply_instruction, switch_text = _build_en_confirmation_instruction(
                environment=environment,
                preview_context=preview_context,
                include_mode_switch=include_mode_switch,
                auto_trade_authorization_hint=auto_trade_authorization_hint,
                reply_text=prompt["reply_text"],
            )
    result = {
        "language": resolved_language,
        "reply_text": prompt["reply_text"],
        "reply_instruction": reply_instruction,
    }
    if switch_text is not None:
        result["switch_reply_text"] = switch_text
    return result


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        raise AggregationInputError(f"{key} is required")
    return str(value).strip()


def _positive_decimal_text(payload: dict[str, Any], key: str) -> str:
    value = _required_text(payload, key)
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise AggregationInputError(f"{key} must be numeric") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise AggregationInputError(f"{key} must be > 0")
    return value


def _normalize_tp_sl_order(raw_order: dict[str, Any]) -> dict[str, str]:
    client_algo_id = _required_text(raw_order, "clientAlgoId")
    if len(client_algo_id) > 36 or re.fullmatch(r"[\.\:\/A-Za-z0-9_-]{1,36}", client_algo_id) is None:
        raise AggregationInputError("clientAlgoId must be 1-36 allowed characters")

    plan_type = _required_text(raw_order, "planType").upper()
    if plan_type not in {"TAKE_PROFIT", "STOP_LOSS"}:
        raise AggregationInputError("planType must be TAKE_PROFIT or STOP_LOSS")

    position_side = _required_text(raw_order, "positionSide").upper()
    if position_side not in {"LONG", "SHORT"}:
        raise AggregationInputError("positionSide must be LONG or SHORT")

    trigger_price_type = str(raw_order.get("triggerPriceType") or "CONTRACT_PRICE").strip().upper()
    if trigger_price_type not in {"CONTRACT_PRICE", "MARK_PRICE"}:
        raise AggregationInputError("triggerPriceType must be CONTRACT_PRICE or MARK_PRICE")

    normalized = {
        "symbol": _required_text(raw_order, "symbol").upper(),
        "clientAlgoId": client_algo_id,
        "planType": plan_type,
        "triggerPrice": _positive_decimal_text(raw_order, "triggerPrice"),
        "executePrice": str(raw_order.get("executePrice", "0")).strip() or "0",
        "positionSide": position_side,
        "triggerPriceType": trigger_price_type,
    }
    quantity = raw_order.get("quantity")
    if quantity is not None and str(quantity).strip() != "":
        quantity_text = str(quantity).strip()
        try:
            decimal_quantity = Decimal(quantity_text)
        except (InvalidOperation, ValueError) as exc:
            raise AggregationInputError("quantity must be numeric") from exc
        if not decimal_quantity.is_finite() or decimal_quantity < 0:
            raise AggregationInputError("quantity must be >= 0")
        normalized["quantity"] = quantity_text
    return normalized


def _build_contract_client(profile_name: str) -> tuple[Any, Any]:
    import weex_contract_api as contract_api

    contract_api.refresh_agent_records(command="trade-guard.contract")
    contract_api.ensure_private_runtime_ready(command="trade-guard.contract", auto_setup=True, language=None)
    profile = contract_api.resolve_runtime_profile(requested_profile=profile_name, allow_invalid_default=False)
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


def _build_spot_client(profile_name: str) -> tuple[Any, Any]:
    import weex_spot_api as spot_api

    spot_api.refresh_agent_records(command="trade-guard.spot")
    spot_api.ensure_private_runtime_ready(command="trade-guard.spot", auto_setup=True, language=None)
    profile = spot_api.resolve_runtime_profile(requested_profile=profile_name, allow_invalid_default=False)
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


def _position_identity(position: dict[str, Any], *keys: str) -> str | None:
    value = next(
        (position.get(key) for key in keys if position.get(key) not in (None, "")),
        None,
    )
    if value in (None, ""):
        raw = position.get("raw")
        if isinstance(raw, dict):
            value = next(
                (raw.get(key) for key in keys if raw.get(key) not in (None, "")),
                None,
            )
    return None if value in (None, "") else str(value).strip()


def _validate_exact_position_close(
    raw_order: dict[str, Any],
    account_payload: dict[str, Any],
) -> int:
    requested_id = _position_identity(raw_order, "position_id", "positionId")
    if requested_id is None:
        raise AggregationInputError("exact position close requires position_id")
    try:
        numeric_position_id = int(requested_id)
    except (TypeError, ValueError) as exc:
        raise AggregationInputError("position_id must be a positive integer") from exc
    if numeric_position_id <= 0:
        raise AggregationInputError("position_id must be a positive integer")
    if account_payload.get("partial") is not False or account_payload.get("degraded_reasons"):
        raise AggregationInputError("fresh account data is incomplete for exact position close")

    positions = account_payload.get("positions")
    if not isinstance(positions, list):
        raise AggregationInputError("fresh account positions are unavailable for exact position close")
    matches = [
        position
        for position in positions
        if isinstance(position, dict)
        and _position_identity(position, "position_id", "positionId", "id") == requested_id
    ]
    if len(matches) != 1:
        reason = "not found" if not matches else "ambiguous"
        raise AggregationInputError(f"exact position_id is {reason} in fresh account positions")

    position = matches[0]
    requested_symbol = str(raw_order.get("symbol") or "").strip().upper()
    actual_symbol = str(position.get("symbol") or "").strip().upper()
    if not requested_symbol or requested_symbol != actual_symbol:
        raise AggregationInputError("exact position close symbol does not match the fresh position")
    requested_position_side = str(
        raw_order.get("position_side") or raw_order.get("positionSide") or ""
    ).strip().upper()
    actual_position_side = str(
        position.get("position_side") or position.get("positionSide") or position.get("side") or ""
    ).strip().upper()
    if requested_position_side not in {"LONG", "SHORT"} or requested_position_side != actual_position_side:
        raise AggregationInputError("exact position close side does not match the fresh position")
    expected_order_side = "SELL" if requested_position_side == "LONG" else "BUY"
    if str(raw_order.get("side") or "").strip().upper() != expected_order_side:
        raise AggregationInputError("exact position close order side is not directionally closing")
    order_type = str(raw_order.get("order_type") or raw_order.get("type") or "").strip().upper()
    if order_type != "MARKET":
        raise AggregationInputError("exact position close requires MARKET order_type")
    try:
        requested_quantity = Decimal(str(raw_order.get("quantity") or "").strip())
        position_quantity = Decimal(str(position.get("quantity") or "").strip())
    except InvalidOperation as exc:
        raise AggregationInputError("exact position close quantity is unavailable") from exc
    if requested_quantity <= 0 or position_quantity <= 0 or requested_quantity != position_quantity:
        raise AggregationInputError(
            "exact position close quantity must equal the full separated position quantity"
        )
    return numeric_position_id


def _submit_order(
    *,
    market: str,
    profile_name: str,
    trading_mode: str,
    raw_order: dict[str, Any],
) -> dict[str, Any]:
    normalized_market = str(market).strip().lower()
    mode = _normalize_trading_mode(trading_mode)
    if mode == "demo" and normalized_market != "futures":
        raise AggregationInputError("demo_spot_unsupported: demo order submission is only supported for futures")
    if normalized_market == "futures":
        position_side = raw_order.get("position_side") or raw_order.get("positionSide")
        order_type = raw_order.get("order_type") or raw_order.get("type")
        if not position_side:
            raise AggregationInputError("futures order requires positionSide")
        if not order_type:
            raise AggregationInputError("futures order requires type")
        requested_position_id = _position_identity(raw_order, "position_id", "positionId")
        if requested_position_id is not None:
            if mode != "live":
                raise AggregationInputError("exact position close is only supported for live futures")
            fresh_account = TradeDataAggregator().collect_account_risk_payload(
                profile_name=profile_name,
                market="futures",
                trading_mode=mode,
                symbol=str(raw_order.get("symbol") or ""),
            )
            position_id = _validate_exact_position_close(raw_order, fresh_account)
            contract_api, client = _build_contract_client(profile_name)
            endpoint_key = contract_api.find_endpoint_key_by_doc_suffix("ClosePositions")
            normalized_symbol = contract_api.normalize_contract_trade_symbol(str(raw_order["symbol"]))
            body = {"symbol": normalized_symbol, "positionId": position_id}
            _, payload = contract_api.execute_endpoint_payload(
                client=client,
                endpoint_key=endpoint_key,
                query={},
                body=body,
                dry_run=False,
                confirm_live=True,
                trading_mode=mode,
                pretty=False,
            )
            return payload
        contract_api, client = _build_contract_client(profile_name)
        endpoint_key = (
            "sim.transaction.place_order"
            if mode == "demo"
            else "transaction.place_order"
        )
        endpoint = contract_api.ENDPOINTS[endpoint_key]
        normalized_symbol = (
            contract_api.normalize_contract_demo_trade_symbol(str(raw_order["symbol"]))
            if mode == "demo"
            else contract_api.normalize_contract_trade_symbol(str(raw_order["symbol"]))
        )
        body = {
            "symbol": normalized_symbol,
            "side": str(raw_order["side"]).upper(),
            "positionSide": str(position_side).upper(),
            "type": str(order_type).upper(),
            "quantity": raw_order["quantity"],
            "price": raw_order.get("price"),
            "timeInForce": raw_order.get("time_in_force") or raw_order.get("timeInForce"),
            "newClientOrderId": raw_order.get("new_client_order_id")
            or raw_order.get("newClientOrderId")
            or contract_api.generate_client_oid(),
            "tpTriggerPrice": raw_order.get("tp_trigger_price") or raw_order.get("tpTriggerPrice"),
            "slTriggerPrice": raw_order.get("sl_trigger_price") or raw_order.get("slTriggerPrice"),
            "TpWorkingType": raw_order.get("tp_working_type") or raw_order.get("TpWorkingType"),
            "SlWorkingType": raw_order.get("sl_working_type") or raw_order.get("SlWorkingType"),
        }
        body = {key: value for key, value in body.items() if value not in (None, "")}
        contract_api.validate_endpoint_trading_mode(endpoint, mode)
        prepared = client.prepare_request(endpoint, query={}, body=body)
        response = client.send(prepared)
    elif normalized_market == "spot":
        if mode != "live":
            raise AggregationInputError("demo_spot_unsupported: demo order submission is only supported for futures")
        order_type = raw_order.get("order_type") or raw_order.get("type")
        if not order_type:
            raise AggregationInputError("spot order requires type")
        spot_api, client = _build_spot_client(profile_name)
        endpoint = spot_api.ENDPOINTS[spot_api.find_endpoint_key_by_doc_suffix("PlaceOrder")]
        body = {
            "symbol": spot_api.normalize_spot_symbol(str(raw_order["symbol"])),
            "side": str(raw_order["side"]).upper(),
            "type": str(order_type).upper(),
            "quantity": raw_order["quantity"],
            "price": raw_order.get("price"),
            "timeInForce": raw_order.get("time_in_force") or raw_order.get("timeInForce"),
            "newClientOrderId": raw_order.get("new_client_order_id")
            or raw_order.get("newClientOrderId")
            or spot_api.generate_client_order_id(),
        }
        body = {key: value for key, value in body.items() if value not in (None, "")}
        prepared = client.prepare_request(endpoint, query={}, body=body)
        response = client.send(prepared)
    else:
        raise AggregationInputError(f"Unsupported market for live order submission: {market}")

    if not response.get("ok"):
        raise AggregationInputError(f"{mode} order submission failed: {response.get('error')}")
    return response.get("data") if isinstance(response.get("data"), dict) else {"result": response.get("data")}


def _submit_live_order(*, market: str, profile_name: str, raw_order: dict[str, Any]) -> dict[str, Any]:
    return _submit_order(
        market=market,
        profile_name=profile_name,
        trading_mode="live",
        raw_order=raw_order,
    )


def _submit_live_auto_fallback_order(intent: dict[str, Any]) -> dict[str, Any]:
    from weex_auto_trade_runtime import OfficialAutoTradeRuntime, OfficialRequestUncertain

    operation_key = str(intent.get("auto_fallback_operation_key") or "").strip()
    operation = resolve_official_auto_trade_operation(operation_key)
    orders = intent.get("auto_fallback_orders")
    if operation is None:
        raise AggregationInputError("auto fallback operation is not in the official catalog")
    if not isinstance(orders, list) or not orders or any(not isinstance(item, dict) for item in orders):
        raise AggregationInputError("auto fallback orders must be a non-empty array")
    if len(orders) > operation["max_legs"] or (
        operation["kind"] != "BATCH" and len(orders) != 1
    ):
        raise AggregationInputError("auto fallback order count does not match the official operation")
    if any(set(order) - operation["allowed_order_fields"] for order in orders):
        raise AggregationInputError("auto fallback order contains unsupported fields")
    if operation_key == "spot.order.bulk_order" and len(
        {str(order.get("symbol") or "").upper() for order in orders}
    ) != 1:
        raise AggregationInputError("auto fallback Spot batch orders must share one symbol")
    if any(_validate_official_order_semantics(operation, order) for order in orders):
        raise AggregationInputError("auto fallback order failed official semantic validation")
    intent_market = str(intent.get("market") or "").upper()
    if intent_market != operation["module"]:
        raise AggregationInputError("auto fallback market does not match the official operation")

    prepared: list[dict[str, Any]] = []
    for index, raw_order in enumerate(orders):
        order = dict(raw_order)
        client_field = (
            "clientAlgoId"
            if operation["kind"] in {"CONDITIONAL", "TP_SL"}
            else "newClientOrderId"
        )
        client_order_id = str(order.get(client_field) or "").strip()
        if not client_order_id:
            digest = hashlib.sha256(
                f"{intent.get('intent_id')}:{index}".encode("utf-8")
            ).hexdigest()[:24]
            client_order_id = "mnl_" + digest
            order[client_field] = client_order_id
        prepared.append(
            {
                "leg_id": f"leg-{index}",
                "leg_index": index,
                "client_order_id": client_order_id,
                "order": order,
            }
        )

    runtime = OfficialAutoTradeRuntime(profile_name=str(intent["profile_name"]))
    try:
        results = runtime.submitter(operation_key, prepared)
    except OfficialRequestUncertain:
        return {
            "ok": False,
            "status": "REVIEW_REQUIRED",
            "error": {"code": "SUBMISSION_STATE_UNCERTAIN"},
            "results": [],
            "next_action": "INSPECT_AND_RECONCILE_MANUALLY",
        }
    if not isinstance(results, list):
        raise AggregationInputError("auto fallback submission result is not an order array")
    statuses = {str(item.get("status") or "REVIEW_REQUIRED").upper() for item in results}
    if statuses == {"ACCEPTED"}:
        status = "ACCEPTED"
    elif "REVIEW_REQUIRED" in statuses:
        status = "REVIEW_REQUIRED"
    elif statuses == {"RELEASED"}:
        status = "RELEASED"
    else:
        status = "SUBMISSION_GROUP_PARTIAL"
    return {
        "ok": status == "ACCEPTED",
        "status": status,
        "results": results,
        "next_action": (
            "NONE" if status == "ACCEPTED" else "INSPECT_RESULT_BEFORE_ANY_NEW_ORDER"
        ),
    }


def _submit_live_tp_sl_order(*, profile_name: str, raw_order: dict[str, Any]) -> dict[str, Any]:
    contract_api, client = _build_contract_client(profile_name)
    endpoint = contract_api.ENDPOINTS[contract_api.find_endpoint_key_by_doc_suffix("PlaceTpSlOrder")]
    normalized = _normalize_tp_sl_order(raw_order)
    normalized["symbol"] = contract_api.normalize_contract_trade_symbol(normalized["symbol"])
    prepared = client.prepare_request(endpoint, query={}, body=normalized)
    response = client.send(prepared)
    if not response.get("ok"):
        raise AggregationInputError(f"Live TP/SL submission failed: {response.get('error')}")
    return response.get("data") if isinstance(response.get("data"), dict) else {"result": response.get("data")}


def cmd_preview_order(args: argparse.Namespace, *, now_ms: int | None = None) -> int:
    raw_order = _parse_order_json(args.order_json)
    trading_mode = _normalize_trading_mode(_arg_value(args, "trading_mode", DEFAULT_TRADING_MODE))
    trade_aggregator = TradeDataAggregator()
    risk_payload = trade_aggregator.collect_order_risk_payload(
        profile_name=args.profile,
        market=args.market,
        trading_mode=trading_mode,
        raw_order=raw_order,
    )
    if _position_identity(raw_order, "position_id", "positionId") is not None:
        _validate_exact_position_close(raw_order, risk_payload)
    environment = _environment_from_payload_or_mode(risk_payload, trading_mode, args.market)
    analysis_output = analysis.analyze_order_risk(risk_payload)
    analysis_output = _merge_environment_context(
        analysis_output,
        trading_mode=trading_mode,
        environment=environment,
    )
    analysis_output["user_environment_prefix"] = _query_environment_prefix(
        environment,
        language=_arg_value(args, "language", None),
    )
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    intent = build_intent(
        profile_name=args.profile,
        market=args.market,
        trading_mode=trading_mode,
        environment=environment,
        order_preview=analysis_output.get("order_preview") or risk_payload.get("order_preview", {}),
        raw_order=raw_order,
        analysis_output=analysis_output,
        now_ms=current_ms,
        ttl_seconds=args.ttl_seconds,
    )
    save_intent(intent)
    response = dict(analysis_output)
    response["intent_id"] = intent["intent_id"]
    response["expires_at"] = intent["expires_at"]
    response["risk_signature"] = intent["risk_signature"]
    confirmation_context = dict(response)
    confirmation_context.setdefault("order_preview", risk_payload.get("order_preview", {}))
    response["user_confirmation"] = _build_user_confirmation(
        _arg_value(args, "language", None),
        environment=environment,
        preview_context=confirmation_context,
        include_mode_switch=True,
        include_auto_trade_authorization_hint=True,
    )
    _output_json(response, args.pretty)
    return 0


def cmd_preview_tp_sl(args: argparse.Namespace, *, now_ms: int | None = None) -> int:
    trading_mode = _normalize_trading_mode(_arg_value(args, "trading_mode", DEFAULT_TRADING_MODE))
    if trading_mode != "live":
        _output_json({"ok": False, "error": "demo_tp_sl_unsupported: demo TP/SL preview is not supported."}, args.pretty)
        return 1
    tp_sl_order = _normalize_tp_sl_order(_parse_tp_sl_json(args.tp_sl_json))
    trade_aggregator = TradeDataAggregator()
    risk_payload = trade_aggregator.collect_account_risk_payload(
        profile_name=args.profile,
        market="futures",
        trading_mode=trading_mode,
        symbol=tp_sl_order["symbol"],
    )
    environment = _environment_from_payload_or_mode(risk_payload, trading_mode, "futures")
    analysis_output = analysis.analyze_account_risk(risk_payload)
    analysis_output = _merge_environment_context(
        analysis_output,
        trading_mode=trading_mode,
        environment=environment,
    )
    analysis_output["user_environment_prefix"] = _query_environment_prefix(
        environment,
        language=_arg_value(args, "language", None),
    )
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    intent = build_intent(
        profile_name=args.profile,
        market="futures",
        trading_mode=trading_mode,
        environment=environment,
        order_preview=tp_sl_order,
        raw_order=tp_sl_order,
        analysis_output=analysis_output,
        now_ms=current_ms,
        ttl_seconds=args.ttl_seconds,
        intent_type="tp_sl_order",
        tp_sl_order=tp_sl_order,
    )
    save_intent(intent)
    response = dict(analysis_output)
    response["intent_type"] = "tp_sl_order"
    response["tp_sl_order"] = tp_sl_order
    response["intent_id"] = intent["intent_id"]
    response["expires_at"] = intent["expires_at"]
    response["risk_signature"] = intent["risk_signature"]
    response["user_confirmation"] = _build_user_confirmation(
        _arg_value(args, "language", None),
        environment=environment,
    )
    _output_json(response, args.pretty)
    return 0


def _confirm_flags_match_mode(args: argparse.Namespace, trading_mode: str) -> bool:
    confirm_live = bool(_arg_value(args, "confirm_live", False))
    confirm_demo = bool(_arg_value(args, "confirm_demo", False))
    if confirm_live and confirm_demo:
        return False
    if trading_mode == "demo":
        return confirm_demo and not confirm_live
    return confirm_live and not confirm_demo


def cmd_confirm_order(args: argparse.Namespace, *, now_ms: int | None = None) -> int:
    intent = load_intent()
    if intent is None:
        _output_json({"ok": False, "error": "No pending order intent was found."}, args.pretty)
        return 1
    if intent.get("intent_type", "order") != "order":
        _output_json({"ok": False, "error": "Pending intent is not a regular order. Use confirm-tp-sl for TP/SL intents."}, args.pretty)
        return 1
    if args.intent_id and args.intent_id != intent.get("intent_id"):
        _output_json({"ok": False, "error": "Intent id does not match the saved pending order."}, args.pretty)
        return 1
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if intent_is_expired(intent, now_ms=current_ms):
        clear_intent()
        _output_json({"ok": False, "error": "Pending order intent has expired. Generate a new preview first."}, args.pretty)
        return 1
    intent_mode = _normalize_trading_mode(intent.get("trading_mode", DEFAULT_TRADING_MODE))
    requested_mode = _normalize_trading_mode(_arg_value(args, "trading_mode", intent_mode))
    if requested_mode != intent_mode:
        _output_json(
            {
                "ok": False,
                "error": "intent_trading_mode_mismatch: requested trading mode does not match the saved pending order.",
                "requested_trading_mode": requested_mode,
                "intent_trading_mode": intent_mode,
            },
            args.pretty,
        )
        return 1
    if not _confirm_flags_match_mode(args, intent_mode):
        required_flag = "--confirm-demo" if intent_mode == "demo" else "--confirm-live"
        _output_json(
            {
                "ok": False,
                "error": f"confirm_flag_mode_mismatch: confirm-order for {intent_mode} requires {required_flag}.",
                "trading_mode": intent_mode,
            },
            args.pretty,
        )
        return 1
    if not args.intent_id or not args.risk_signature:
        _output_json(
            {
                "ok": False,
                "error": "confirm-order requires both --intent-id and --risk-signature from preview-order.",
            },
            args.pretty,
        )
        return 1
    if args.risk_signature and args.risk_signature != intent.get("risk_signature"):
        _output_json({"ok": False, "error": "Risk signature does not match the saved pending order."}, args.pretty)
        return 1

    if intent_mode == "live":
        if intent.get("auto_fallback_operation_key") is not None:
            execution_payload = _submit_live_auto_fallback_order(intent)
        else:
            execution_payload = _submit_live_order(
                market=str(intent["market"]),
                profile_name=str(intent["profile_name"]),
                raw_order=dict(intent["raw_order"]),
            )
    else:
        execution_payload = _submit_order(
            market=str(intent["market"]),
            profile_name=str(intent["profile_name"]),
            trading_mode=intent_mode,
            raw_order=dict(intent["raw_order"]),
        )
    clear_intent()
    environment = intent.get("environment")
    if not isinstance(environment, dict):
        environment = _environment_for_mode(intent_mode, str(intent["market"]))
    response = {"ok": True, **execution_payload, "environment": environment, "trading_mode": intent_mode}
    response["user_environment_prefix"] = _query_environment_prefix(
        environment,
        language=_arg_value(args, "language", None),
    )
    _output_json(response, args.pretty)
    return 0


def cmd_confirm_tp_sl(args: argparse.Namespace, *, now_ms: int | None = None) -> int:
    intent = load_intent()
    if intent is None:
        _output_json({"ok": False, "error": "No pending TP/SL intent was found."}, args.pretty)
        return 1
    if intent.get("intent_type") != "tp_sl_order":
        _output_json({"ok": False, "error": "Pending intent is not a TP/SL order."}, args.pretty)
        return 1
    if args.intent_id and args.intent_id != intent.get("intent_id"):
        _output_json({"ok": False, "error": "Intent id does not match the saved pending TP/SL order."}, args.pretty)
        return 1
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if intent_is_expired(intent, now_ms=current_ms):
        clear_intent()
        _output_json({"ok": False, "error": "Pending TP/SL intent has expired. Generate a new preview first."}, args.pretty)
        return 1
    intent_mode = _normalize_trading_mode(intent.get("trading_mode", DEFAULT_TRADING_MODE))
    requested_mode = _normalize_trading_mode(_arg_value(args, "trading_mode", intent_mode))
    if intent_mode != "live" or requested_mode != intent_mode:
        _output_json({"ok": False, "error": "demo_tp_sl_unsupported: TP/SL confirmation is only supported for live futures."}, args.pretty)
        return 1
    if not _confirm_flags_match_mode(args, intent_mode):
        _output_json({"ok": False, "error": "confirm-tp-sl still requires --confirm-live before sending a real TP/SL order."}, args.pretty)
        return 1
    if not args.intent_id or not args.risk_signature:
        _output_json(
            {
                "ok": False,
                "error": "confirm-tp-sl requires both --intent-id and --risk-signature from preview-tp-sl.",
            },
            args.pretty,
        )
        return 1
    if args.risk_signature and args.risk_signature != intent.get("risk_signature"):
        _output_json({"ok": False, "error": "Risk signature does not match the saved pending TP/SL order."}, args.pretty)
        return 1

    tp_sl_order = intent.get("tp_sl_order")
    if not isinstance(tp_sl_order, dict):
        _output_json({"ok": False, "error": "Pending TP/SL intent is missing tp_sl_order."}, args.pretty)
        return 1

    execution_payload = _submit_live_tp_sl_order(
        profile_name=str(intent["profile_name"]),
        raw_order=dict(tp_sl_order),
    )
    clear_intent()
    environment = intent.get("environment")
    if not isinstance(environment, dict):
        environment = _environment_for_mode(intent_mode, "futures")
    response = {"ok": True, **execution_payload, "environment": environment, "trading_mode": intent_mode}
    response["user_environment_prefix"] = _query_environment_prefix(
        environment,
        language=_arg_value(args, "language", None),
    )
    _output_json(response, args.pretty)
    return 0


def cmd_account_scan(args: argparse.Namespace) -> int:
    trading_mode = _normalize_trading_mode(_arg_value(args, "trading_mode", DEFAULT_TRADING_MODE))
    trade_aggregator = TradeDataAggregator()
    payload = trade_aggregator.collect_account_risk_payload(
        profile_name=args.profile,
        market=args.market,
        trading_mode=trading_mode,
        symbol=args.symbol,
    )
    environment = _environment_from_payload_or_mode(payload, trading_mode, args.market)
    analysis_output = analysis.analyze_account_risk(payload)
    analysis_output = _merge_environment_context(
        analysis_output,
        trading_mode=trading_mode,
        environment=environment,
    )
    analysis_output["user_environment_prefix"] = _query_environment_prefix(
        environment,
        language=_arg_value(args, "language", None),
    )
    _output_json(analysis_output, args.pretty)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview order risk and confirm WEEX orders.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser("preview-order", help="Preview risk before placing an order.")
    preview.add_argument("--profile", required=True, help="Saved profile name.")
    preview.add_argument("--market", required=True, choices=("futures", "spot"))
    preview.add_argument("--trading-mode", choices=TRADING_MODES, default=DEFAULT_TRADING_MODE)
    preview.add_argument("--order-json", required=True, help="JSON order payload.")
    preview.add_argument("--ttl-seconds", type=int, default=300, help="Intent TTL in seconds.")
    preview.add_argument("--language", choices=("zh", "en"), default=None, help="Language for human confirmation prompt.")
    preview.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    preview_tp_sl = subparsers.add_parser(
        "preview-tp-sl",
        help="Preview a real trading only futures TP/SL conditional order; demo TP/SL is not supported.",
        description="Preview risk before placing a futures TP/SL conditional order. This flow is real trading only; demo TP/SL is not supported.",
    )
    preview_tp_sl.add_argument("--profile", required=True, help="Saved profile name.")
    preview_tp_sl.add_argument("--trading-mode", choices=TRADING_MODES, default=DEFAULT_TRADING_MODE, help="TP/SL trading mode; real trading only because demo TP/SL is not supported.")
    preview_tp_sl.add_argument("--tp-sl-json", required=True, help="JSON TP/SL conditional order payload.")
    preview_tp_sl.add_argument("--ttl-seconds", type=int, default=300, help="Intent TTL in seconds.")
    preview_tp_sl.add_argument("--language", choices=("zh", "en"), default=None, help="Language for human confirmation prompt.")
    preview_tp_sl.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    confirm = subparsers.add_parser("confirm-order", help="Submit the last previewed order.")
    confirm.add_argument("--intent-id", default=None, help="Optional explicit intent id to confirm.")
    confirm.add_argument("--risk-signature", default=None, help="Risk signature returned by preview-order.")
    confirm.add_argument("--trading-mode", choices=TRADING_MODES, default=DEFAULT_TRADING_MODE)
    confirm.add_argument("--confirm-live", action="store_true", help="Required before sending a real order.")
    confirm.add_argument("--confirm-demo", action="store_true", help="Required before sending a demo futures order.")
    confirm.add_argument("--language", choices=("zh", "en"), default=None, help="Language for user-facing environment prefix.")
    confirm.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    confirm_tp_sl = subparsers.add_parser(
        "confirm-tp-sl",
        help="Submit the last previewed real trading futures TP/SL conditional order; demo TP/SL is not supported.",
        description="Submit the last previewed futures TP/SL conditional order. This flow is real trading only; demo TP/SL is not supported.",
    )
    confirm_tp_sl.add_argument("--intent-id", default=None, help="Optional explicit intent id to confirm.")
    confirm_tp_sl.add_argument("--risk-signature", default=None, help="Risk signature returned by preview-tp-sl.")
    confirm_tp_sl.add_argument("--trading-mode", choices=TRADING_MODES, default=DEFAULT_TRADING_MODE)
    confirm_tp_sl.add_argument("--confirm-live", action="store_true", help="Required before sending a real TP/SL order.")
    confirm_tp_sl.add_argument("--confirm-demo", action="store_true", help="Rejected because demo TP/SL is not supported.")
    confirm_tp_sl.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    account_scan = subparsers.add_parser("account-scan", help="Review current account-level risk without an order preview.")
    account_scan.add_argument("--profile", required=True, help="Saved profile name.")
    account_scan.add_argument("--market", required=True, choices=("futures", "spot"))
    account_scan.add_argument("--trading-mode", choices=TRADING_MODES, default=DEFAULT_TRADING_MODE)
    account_scan.add_argument("--symbol", default=None, help="Optional trading pair focus.")
    account_scan.add_argument("--language", choices=("zh", "en"), default=None, help="Language for user-facing environment prefix.")
    account_scan.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preview-order":
            return cmd_preview_order(args)
        if args.command == "preview-tp-sl":
            return cmd_preview_tp_sl(args)
        if args.command == "confirm-order":
            return cmd_confirm_order(args)
        if args.command == "confirm-tp-sl":
            return cmd_confirm_tp_sl(args)
        if args.command == "account-scan":
            return cmd_account_scan(args)
        raise SystemExit(f"Unsupported command: {args.command}")
    except AggregationInputError as exc:
        _output_error(str(exc), bool(getattr(args, "pretty", False)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
