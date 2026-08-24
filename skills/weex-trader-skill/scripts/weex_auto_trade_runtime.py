#!/usr/bin/env python3
"""Official WEEX data and REST boundaries for automated-trading authorization."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

import weex_trade_risk_review as risk_review
from weex_trade_data_aggregator import TradeDataAggregator


RULE_VERSION = "weex_trade_risk_review.v1"


class OfficialRequestRejected(RuntimeError):
    """A non-transient official response proves the request was rejected."""

    def __init__(
        self, *, status: int, error_code: str, error_message: str | None = None
    ) -> None:
        super().__init__("official WEEX request was explicitly rejected")
        self.status = status
        self.error_code = error_code
        self.error_message = error_message


class OfficialRequestUncertain(RuntimeError):
    """The transport or upstream response cannot prove whether a write was accepted."""


class OfficialReadRequestFailed(RuntimeError):
    """An official read failed with a preserved, sanitized exchange error code."""

    def __init__(
        self,
        *,
        error_code: str | None,
        error_message: str | None = None,
    ) -> None:
        super().__init__("official WEEX read request failed")
        self.error_code = error_code
        self.error_message = error_message


class OfficialApiBoundary:
    """Build saved-profile clients and reject any endpoint/purpose mismatch."""

    def __init__(self, *, profile_name: str) -> None:
        self.profile_name = profile_name
        self._clients: dict[tuple[str, bool], tuple[Any, Any]] = {}

    def _client(self, module: str, *, private: bool) -> tuple[Any, Any]:
        cache_key = (module, private)
        if cache_key in self._clients:
            return self._clients[cache_key]
        if module == "SPOT":
            import weex_spot_api as api_module

            if private:
                api_module.ensure_private_runtime_ready(
                    command="auto-trade.spot", auto_setup=True, language=None
                )
            profile = api_module.resolve_runtime_profile(
                requested_profile=self.profile_name,
                allow_invalid_default=False,
            )
            api_module.require_private_profile(profile)
            base_url = (
                profile.spot_base_url
                or os.getenv("WEEX_SPOT_API_BASE")
                or os.getenv("WEEX_API_BASE")
                or api_module.DEFAULT_BASE_URL
            )
            client = api_module.WeexSpotClient(
                base_url=base_url,
                timeout=float(os.getenv("WEEX_API_TIMEOUT", api_module.DEFAULT_TIMEOUT)),
                locale=os.getenv("WEEX_LOCALE") or api_module.DEFAULT_LOCALE,
                api_key=None,
                api_secret=None,
                api_passphrase=None,
                profile_name=profile.name if private else None,
            )
        elif module == "FUTURES":
            import weex_contract_api as api_module

            if private:
                api_module.ensure_private_runtime_ready(
                    command="auto-trade.contract", auto_setup=True, language=None
                )
            profile = api_module.resolve_runtime_profile(
                requested_profile=self.profile_name,
                allow_invalid_default=False,
            )
            api_module.require_private_profile(profile)
            base_url = (
                profile.contract_base_url
                or os.getenv("WEEX_CONTRACT_API_BASE")
                or os.getenv("WEEX_API_BASE")
                or api_module.DEFAULT_BASE_URL
            )
            client = api_module.WeexContractClient(
                base_url=base_url,
                timeout=float(os.getenv("WEEX_API_TIMEOUT", api_module.DEFAULT_TIMEOUT)),
                locale=os.getenv("WEEX_LOCALE") or api_module.DEFAULT_LOCALE,
                api_key=None,
                api_secret=None,
                api_passphrase=None,
                profile_name=profile.name if private else None,
            )
        else:
            raise ValueError("unsupported official product module")
        self._clients[cache_key] = (api_module, client)
        return api_module, client

    def call(
        self,
        *,
        module: str,
        endpoint_key: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        public: bool = False,
        mutating: bool = False,
    ) -> Any:
        if public and mutating:
            raise ValueError("mutating requests cannot use a public client")
        api_module, client = self._client(module, private=not public)
        endpoint = api_module.ENDPOINTS.get(endpoint_key)
        if endpoint is None:
            raise ValueError("endpoint is not in the bundled official catalog")
        is_trade = (
            api_module.is_mutating(endpoint)
            if module == "SPOT"
            else bool(endpoint.mutating)
        )
        if mutating != is_trade:
            raise ValueError("official endpoint purpose does not match the requested boundary")
        if module == "FUTURES":
            api_module.validate_endpoint_trading_mode(endpoint, "live")
        prepared = client.prepare_request(endpoint, query=query or {}, body=body or {})
        response = client.send(prepared)
        if not response.get("ok"):
            if mutating:
                status = response.get("status")
                error_payload = response.get("error")
                error_code = _official_error_code(error_payload)
                if _is_definitive_http_rejection(status, error_code):
                    raise OfficialRequestRejected(
                        status=status,
                        error_code=error_code,
                        error_message=_official_error_message(error_payload),
                    )
                raise OfficialRequestUncertain(
                    "official WEEX write result is transport or upstream uncertain"
                )
            error_payload = response.get("error")
            raise OfficialReadRequestFailed(
                error_code=_official_error_code(error_payload),
                error_message=_official_error_message(error_payload),
            )
        return response.get("data")


class OfficialAutoTradeRuntime:
    """Provide the guard with official risk, valuation, and submission collaborators."""

    def __init__(
        self,
        *,
        profile_name: str,
        api: Any | None = None,
        risk_aggregator: Any | None = None,
    ) -> None:
        self.profile_name = profile_name
        self.api = api or OfficialApiBoundary(profile_name=profile_name)
        self.risk_aggregator = risk_aggregator or TradeDataAggregator()

    def risk_payload_provider(self, leg: dict[str, Any]) -> dict[str, Any]:
        module = _required_module(leg.get("module"))
        order = _required_mapping(leg.get("order"), "order")
        leg_type = str(leg.get("leg_type") or "")
        if leg_type in {"TAKE_PROFIT", "STOP_LOSS"}:
            payload = self.risk_aggregator.collect_account_risk_payload(
                profile_name=self.profile_name,
                market="futures",
                trading_mode="live",
                symbol=_required_text(order.get("symbol"), "symbol"),
            )
            payload["_auto_analysis_type"] = "account"
        else:
            payload = self.risk_aggregator.collect_order_risk_payload(
                profile_name=self.profile_name,
                market=module.lower(),
                trading_mode="live",
                raw_order=order,
            )
            payload["_auto_analysis_type"] = "order"
        if module == "SPOT":
            degraded = list(payload.get("degraded_reasons") or [])
            capability_note = "spot_tp_sl_state_unavailable"
            if capability_note in degraded:
                payload["degraded_reasons"] = [
                    item for item in degraded if item != capability_note
                ]
                notes = list(payload.get("capability_notes") or [])
                if capability_note not in notes:
                    notes.append(capability_note)
                payload["capability_notes"] = notes
        payload["generated_at"] = _utc_now_text()
        return payload

    def risk_evaluator(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("_auto_analysis_type") == "account":
            result = risk_review.analyze_account_risk(payload)
        else:
            result = risk_review.analyze_order_risk(payload)
        return {**result, "rule_version": RULE_VERSION}

    def facts_provider(self, leg: dict[str, Any]) -> dict[str, Any]:
        module = _required_module(leg.get("module"))
        order = _required_mapping(leg.get("order"), "order")
        if module == "SPOT":
            return self._spot_facts(order)
        return self._futures_facts(order)

    def _spot_facts(self, order: dict[str, Any]) -> dict[str, Any]:
        symbol = _required_text(order.get("symbol"), "symbol").upper()
        product = self.api.call(
            module="SPOT",
            endpoint_key="spot.config.get_product_info",
            query={"symbol": symbol},
            public=True,
        )
        symbol_facts = _find_symbol(product, symbol)
        symbol_facts["makerFeeRate"] = _maximum_decimal_text(
            symbol_facts.get("makerFeeRate")
        )
        symbol_facts["takerFeeRate"] = _maximum_decimal_text(
            symbol_facts.get("takerFeeRate")
        )
        facts: dict[str, Any] = {
            "timestamp_ms": _now_ms(),
            "symbol": symbol_facts,
            "degraded_reasons": _symbol_degradations(symbol_facts),
        }
        if _needs_depth(order):
            depth = self.api.call(
                module="SPOT",
                endpoint_key="spot.market.get_depth_data",
                query={"symbol": symbol, "limit": 200},
                public=True,
            )
            facts["depth"] = _normalize_depth(depth, received_at_ms=_now_ms())
        quote_asset = _required_text(symbol_facts.get("quoteAsset"), "quoteAsset").upper()
        facts["conversion_rates"] = self._conversion_rates(quote_asset)
        return facts

    def _futures_facts(self, order: dict[str, Any]) -> dict[str, Any]:
        symbol = _required_text(order.get("symbol"), "symbol").upper()
        exchange_info = self.api.call(
            module="FUTURES",
            endpoint_key="market.get_contract_info",
            query={"symbol": symbol},
            public=True,
        )
        symbol_facts = _find_symbol(exchange_info, symbol)
        symbol_facts["contractVal"] = _maximum_decimal_text(
            symbol_facts.get("contractVal")
        )
        config_payload = self.api.call(
            module="FUTURES",
            endpoint_key="account.get_symbol_config",
            query={"symbol": symbol},
        )
        symbol_config = _find_symbol(config_payload, symbol)
        commission = _unwrap_mapping(
            self.api.call(
                module="FUTURES",
                endpoint_key="account.get_commission_rate",
                query={"symbol": symbol},
            )
        )
        symbol_facts.update(symbol_config)
        symbol_facts["makerFeeRate"] = _maximum_decimal_text(
            symbol_facts.get("makerFeeRate"),
            commission.get("makerCommissionRate"),
        )
        symbol_facts["takerFeeRate"] = _maximum_decimal_text(
            symbol_facts.get("takerFeeRate"),
            commission.get("takerCommissionRate"),
        )
        facts: dict[str, Any] = {
            "timestamp_ms": _now_ms(),
            "symbol": symbol_facts,
            "degraded_reasons": _symbol_degradations(symbol_facts),
            "reduce_only_proven": False,
        }
        if _needs_depth(order):
            depth = self.api.call(
                module="FUTURES",
                endpoint_key="market.get_depth_data",
                query={"symbol": symbol, "limit": 200},
                public=True,
            )
            facts["depth"] = _normalize_depth(depth, received_at_ms=_now_ms())

        side = str(order.get("side") or "").upper()
        position_side = str(order.get("positionSide") or "").upper()
        if not side and str(order.get("planType") or "").upper() in {
            "TAKE_PROFIT",
            "STOP_LOSS",
        }:
            if position_side == "LONG":
                side = "SELL"
            elif position_side == "SHORT":
                side = "BUY"
        if (side, position_side) in {("SELL", "LONG"), ("BUY", "SHORT")}:
            positions = self.api.call(
                module="FUTURES",
                endpoint_key="account.get_all_positions",
                query={},
            )
            facts["reduce_only_proven"] = _position_covers_order(
                positions,
                symbol=symbol,
                position_side=position_side,
                quantity=order.get("quantity"),
            )
        assets = {
            _required_text(symbol_facts.get("quoteAsset"), "quoteAsset").upper(),
            _required_text(symbol_facts.get("marginAsset"), "marginAsset").upper(),
        }
        rates: dict[str, Any] = {}
        for asset in assets:
            rates.update(self._conversion_rates(asset))
        facts["conversion_rates"] = rates
        return facts

    def _conversion_rates(self, asset: str) -> dict[str, Any]:
        if asset == "USDT":
            return {}
        rates: dict[str, Any] = {}
        for symbol in (f"{asset}USDT", f"USDT{asset}"):
            try:
                product = self.api.call(
                    module="SPOT",
                    endpoint_key="spot.config.get_product_info",
                    query={"symbol": symbol},
                    public=True,
                )
                metadata = _find_symbol(product, symbol)
                ticker = _unwrap_mapping(
                    self.api.call(
                        module="SPOT",
                        endpoint_key="spot.market.get_ticker_info",
                        query={"symbol": symbol},
                        public=True,
                    )
                )
                rates[symbol] = {
                    "price": _required_text(
                        _pick(ticker, "price", "lastPrice", "close"), "conversion price"
                    ),
                    "timestamp_ms": _now_ms(),
                    "tradable": not _symbol_degradations(metadata),
                }
            except Exception:
                continue
        return rates

    def submitter(
        self,
        operation_key: str,
        prepared_legs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not prepared_legs:
            raise ValueError("prepared legs are required")
        module = _operation_module(operation_key)
        orders = [_required_mapping(item.get("order"), "order") for item in prepared_legs]
        if operation_key == "spot.order.bulk_order":
            symbol = _required_text(orders[0].get("symbol"), "symbol").upper()
            body = {
                "symbol": symbol,
                "orderList": [
                    {key: value for key, value in order.items() if key != "symbol"}
                    for order in orders
                ],
            }
        elif operation_key == "transaction.place_orders_batch":
            body = {"batchOrders": orders}
        else:
            if len(orders) != 1:
                raise ValueError("single official operation received multiple legs")
            body = orders[0]
        try:
            payload = self.api.call(
                module=module,
                endpoint_key=operation_key,
                query={},
                body=body,
                mutating=True,
            )
        except OfficialRequestRejected as exc:
            results = []
            for item in prepared_legs:
                result = {
                    "leg_id": item["leg_id"],
                    "client_order_id": item["client_order_id"],
                    "status": "RELEASED",
                    "success": False,
                    "errorCode": exc.error_code,
                }
                if exc.error_message is not None:
                    result["errorMessage"] = exc.error_message
                results.append(result)
            return results
        return _normalize_submission_results(
            operation_key=operation_key,
            payload=payload,
            prepared_legs=prepared_legs,
        )


def query_official_order_facts(
    *,
    order: dict[str, Any],
    profile_name: str,
    api: Any | None = None,
) -> dict[str, Any]:
    """Query only official order/trade endpoints and return normalized facts."""
    module = _required_module(order.get("module"))
    symbol = _required_text(order.get("symbol"), "symbol").upper()
    order_id = _required_text(order.get("weex_order_id"), "weex_order_id")
    boundary = api or OfficialApiBoundary(profile_name=profile_name)
    leg_type = str(order.get("leg_type") or "PRIMARY").upper()
    if module == "FUTURES" and leg_type in {
        "CONDITIONAL",
        "TAKE_PROFIT",
        "STOP_LOSS",
    }:
        plan = _query_futures_plan_order(
            boundary=boundary,
            symbol=symbol,
            plan_order_id=order_id,
        )
        plan_status = _optional_text(_pick(plan, "algoStatus", "status"))
        actual_order_id = _optional_text(_pick(plan, "actualOrderId", "actual_order_id"))
        if actual_order_id in {None, "0"}:
            return {
                "reconciliation_status": "PARTIAL",
                "exchange_status": plan_status,
                "executed_quantity": None,
                "executed_quote_amount": None,
                "fee_amount": None,
                "fee_asset": None,
                "reconciliation_source": "WEEX_FUTURES_PLAN_ORDER",
            }
        return _query_active_order_facts(
            boundary=boundary,
            module="FUTURES",
            symbol=symbol,
            order_id=actual_order_id,
            source="WEEX_FUTURES_PLAN_ORDER_AND_TRADES",
        )
    if module == "SPOT":
        return _query_spot_order_facts(
            boundary=boundary,
            symbol=symbol,
            order_id=order_id,
            client_order_id=_optional_text(order.get("client_order_id")),
        )
    return _query_active_order_facts(
        boundary=boundary,
        module=module,
        symbol=symbol,
        order_id=order_id,
        source="WEEX_FUTURES_ORDER_AND_TRADES",
    )


def query_official_usage_resolution(
    *,
    order: dict[str, Any],
    profile_name: str,
    api: Any | None = None,
) -> dict[str, Any]:
    """Resolve an uncertain submission using a bound, read-only WEEX order lookup.

    A missing futures order ID cannot be queried by client ID through the official
    API, so that case deliberately raises and remains REVIEW_REQUIRED at the
    facade.  Spot supports ``origClientOrderId`` and can prove a missing order
    only after the detail and complete history queries return no match.
    """
    module = _required_module(order.get("module"))
    symbol = _required_text(order.get("symbol"), "symbol").upper()
    local_order_id = _optional_text(order.get("weex_order_id"))
    client_order_id = _required_text(order.get("client_order_id"), "client_order_id")
    boundary = api or OfficialApiBoundary(profile_name=profile_name)
    leg_type = str(order.get("leg_type") or "PRIMARY").upper()

    detail: dict[str, Any] | None = None
    if module == "SPOT":
        try:
            query: dict[str, Any] = {"orderId": local_order_id} if local_order_id else {
                "origClientOrderId": client_order_id
            }
            detail = _unwrap_mapping(
                boundary.call(
                    module="SPOT",
                    endpoint_key="spot.order.order_details",
                    query=query,
                )
            )
        except OfficialReadRequestFailed as exc:
            if exc.error_code != "-2200":
                raise
            detail = _query_spot_resolution_history(
                boundary=boundary,
                symbol=symbol,
                order_id=local_order_id,
                client_order_id=client_order_id,
            )
    elif local_order_id and leg_type in {"CONDITIONAL", "TAKE_PROFIT", "STOP_LOSS"}:
        # The plan-order list API does not provide a stable not-found error;
        # absence or malformed pagination is therefore kept unresolved.
        detail = _query_futures_plan_order(
            boundary=boundary,
            symbol=symbol,
            plan_order_id=local_order_id,
        )
    elif local_order_id:
        try:
            detail = _unwrap_mapping(
                boundary.call(
                    module="FUTURES",
                    endpoint_key="transaction.get_single_order_info",
                    query={"orderId": local_order_id},
                )
            )
        except OfficialReadRequestFailed as exc:
            if exc.error_code != "-2200":
                raise
            detail = None
    else:
        raise ValueError("OFFICIAL_FUTURES_CLIENT_ID_QUERY_UNSUPPORTED")

    if detail is None:
        return _resolution_facts(order, outcome="RELEASED", source=f"WEEX_{module}_ORDER_NOT_FOUND")

    order_id_keys = (
        ("algoId", "orderId", "order_id")
        if module == "FUTURES" and leg_type in {"CONDITIONAL", "TAKE_PROFIT", "STOP_LOSS"}
        else ("orderId", "order_id", "algoId")
    )
    returned_order_id = _optional_text(_pick(detail, *order_id_keys))
    if local_order_id is not None and returned_order_id != local_order_id:
        raise ValueError("official order query returned a different order")
    returned_client_id = _optional_text(
        _pick(detail, "clientOrderId", "client_order_id", "clientAlgoId", "origClientOrderId")
    )
    if returned_client_id != client_order_id:
        raise ValueError("official order query returned a different client order")
    returned_symbol = _optional_text(_pick(detail, "symbol"))
    if returned_symbol is None or returned_symbol.upper() != symbol:
        raise ValueError("official order query returned a different symbol")
    _validate_resolution_order_fields(detail, order, leg_type=leg_type)
    resolved_order_id = returned_order_id or local_order_id
    if resolved_order_id is None:
        raise ValueError("official order query did not return an order ID")
    return _resolution_facts(
        order,
        outcome="ACCEPTED",
        source=f"WEEX_{module}_ORDER_ACCEPTED",
        weex_order_id=resolved_order_id,
    )


def _validate_resolution_order_fields(
    detail: dict[str, Any],
    order: dict[str, Any],
    *,
    leg_type: str,
) -> None:
    """Require the official response to describe the locally recorded order."""
    returned_side = _optional_text(_pick(detail, "side"))
    if returned_side is None or returned_side.upper() != str(order.get("side") or "").upper():
        raise ValueError("official order query returned a different side")
    returned_quantity = _pick(detail, "origQty", "quantity", "qty", "executedQty")
    if not _same_decimal_value(returned_quantity, order.get("quantity")):
        raise ValueError("official order query returned a different quantity")
    returned_type = _optional_text(_pick(detail, "type", "orderType", "order_type"))
    expected_type = str(order.get("order_type") or "").upper()
    if returned_type is None and leg_type not in {"CONDITIONAL", "TAKE_PROFIT", "STOP_LOSS"}:
        raise ValueError("official order query did not return an order type")
    if returned_type is not None and returned_type.upper() != expected_type:
        raise ValueError("official order query returned a different order type")
    expected_price = order.get("price")
    returned_price = _pick(detail, "price", "executePrice")
    if expected_price is not None and not _same_decimal_value(returned_price, expected_price):
        raise ValueError("official order query returned a different price")


def _same_decimal_value(left: Any, right: Any) -> bool:
    left_value = _decimal(left)
    right_value = _decimal(right)
    return left_value is not None and right_value is not None and left_value == right_value


def _resolution_facts(
    order: dict[str, Any],
    *,
    outcome: str,
    source: str,
    weex_order_id: str | None = None,
) -> dict[str, Any]:
    fields = (
        "usage_id",
        "strategy_id",
        "authorization_id",
        "submission_group_id",
        "leg_id",
        "client_order_id",
        "module",
        "symbol",
        "side",
        "order_type",
        "quantity",
        "price",
    )
    facts = {key: order.get(key) for key in fields}
    facts.update(
        {
            "outcome": outcome,
            "evidence_source": source,
            "weex_order_id": weex_order_id,
        }
    )
    return facts


def _query_spot_resolution_history(
    *,
    boundary: Any,
    symbol: str,
    order_id: str | None,
    client_order_id: str,
) -> dict[str, Any] | None:
    page = 1
    limit = 1000
    seen_order_ids: set[str] = set()
    matches: list[dict[str, Any]] = []
    while True:
        history_payload = boundary.call(
            module="SPOT",
            endpoint_key="spot.order.history_orders",
            query={"symbol": symbol, "limit": limit, "page": page},
        )
        if not _has_rows_container(history_payload, "items", "orders"):
            raise ValueError("official spot history query is incomplete")
        rows = _extract_rows(
            history_payload,
            "items",
            "orders",
        )
        for item in rows:
            item_order_id = str(_pick(item, "orderId", "order_id") or "")
            item_client_id = str(_pick(item, "clientOrderId", "client_order_id") or "")
            is_target = (
                (order_id is not None and item_order_id == order_id)
                or (order_id is None and item_client_id == client_order_id)
            )
            if not is_target:
                continue
            if item_client_id != client_order_id:
                raise ValueError("official order query returned a different client order")
            if str(_pick(item, "symbol") or "").upper() != symbol:
                raise ValueError("official order query returned a different symbol")
            matches.append(dict(item))
        page_order_ids = {
            str(_pick(item, "orderId", "order_id") or "")
            for item in rows
            if _pick(item, "orderId", "order_id") not in (None, "")
        }
        if rows and not page_order_ids:
            raise ValueError("official spot history query is incomplete")
        new_order_ids = page_order_ids - seen_order_ids
        seen_order_ids.update(page_order_ids)
        if len(rows) < limit:
            break
        if not new_order_ids:
            raise ValueError("official spot history query pagination did not advance")
        page += 1
    if len(matches) > 1:
        raise ValueError("official spot history query returned non-unique order")
    return matches[0] if matches else None


def _has_rows_container(payload: Any, *keys: str) -> bool:
    raw = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if isinstance(raw, list):
        return True
    return isinstance(raw, dict) and any(isinstance(raw.get(key), list) for key in keys)


def _query_spot_order_facts(
    *,
    boundary: Any,
    symbol: str,
    order_id: str,
    client_order_id: str | None,
) -> dict[str, Any]:
    try:
        return _query_active_order_facts(
            boundary=boundary,
            module="SPOT",
            symbol=symbol,
            order_id=order_id,
            source="WEEX_SPOT_ORDER_AND_TRADES",
        )
    except OfficialReadRequestFailed as exc:
        if exc.error_code != "-2200":
            raise
    detail = _query_spot_history_order(
        boundary=boundary,
        symbol=symbol,
        order_id=order_id,
        client_order_id=client_order_id,
    )
    return _query_order_and_trade_facts(
        boundary=boundary,
        module="SPOT",
        symbol=symbol,
        order_id=order_id,
        detail=detail,
        source="WEEX_SPOT_HISTORY_ORDER_AND_TRADES",
    )


def _query_spot_history_order(
    *,
    boundary: Any,
    symbol: str,
    order_id: str,
    client_order_id: str | None,
) -> dict[str, Any]:
    page = 1
    limit = 1000
    seen_order_ids: set[str] = set()
    while True:
        rows = _extract_rows(
            boundary.call(
                module="SPOT",
                endpoint_key="spot.order.history_orders",
                query={"symbol": symbol, "limit": limit, "page": page},
            ),
            "items",
            "orders",
        )
        matches = [
            item
            for item in rows
            if str(_pick(item, "orderId", "order_id") or "") == order_id
            and (
                client_order_id is None
                or str(_pick(item, "clientOrderId", "client_order_id") or "")
                == client_order_id
            )
        ]
        if len(matches) == 1:
            return dict(matches[0])
        if len(matches) > 1:
            break
        page_order_ids = {
            str(_pick(item, "orderId", "order_id") or "")
            for item in rows
            if _pick(item, "orderId", "order_id") not in (None, "")
        }
        new_order_ids = page_order_ids - seen_order_ids
        seen_order_ids.update(page_order_ids)
        if len(rows) < limit or not new_order_ids:
            break
        page += 1
    raise ValueError("official spot history query returned no unique matching order")


def _query_active_order_facts(
    *,
    boundary: Any,
    module: str,
    symbol: str,
    order_id: str,
    source: str,
) -> dict[str, Any]:
    if module == "SPOT":
        detail_key = "spot.order.order_details"
        detail_query = {"orderId": order_id}
    else:
        detail_key = "transaction.get_single_order_info"
        detail_query = {"orderId": order_id}
    detail = _unwrap_mapping(
        boundary.call(
            module=module,
            endpoint_key=detail_key,
            query=detail_query,
        )
    )
    if str(_pick(detail, "orderId", "order_id") or "") != order_id:
        raise ValueError("official order query returned a different order")
    return _query_order_and_trade_facts(
        boundary=boundary,
        module=module,
        symbol=symbol,
        order_id=order_id,
        detail=detail,
        source=source,
    )


def _query_order_and_trade_facts(
    *,
    boundary: Any,
    module: str,
    symbol: str,
    order_id: str,
    detail: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    trades_key = (
        "spot.order.transaction_details"
        if module == "SPOT"
        else "transaction.get_trade_details"
    )
    trades_query = {"symbol": symbol, "orderId": order_id, "limit": 100}
    trades = _extract_rows(
        boundary.call(
            module=module,
            endpoint_key=trades_key,
            query=trades_query,
        ),
        "items",
        "trades",
        "fills",
    )
    matched_trades = [
        item
        for item in trades
        if str(_pick(item, "orderId", "order_id") or "") == order_id
    ]
    exchange_status = _optional_text(_pick(detail, "status", "orderStatus"))
    executed_quantity = _optional_decimal_text(
        _pick(detail, "executedQty", "executed_quantity")
    )
    executed_quote_amount = _optional_decimal_text(
        _pick(
            detail,
            "cummulativeQuoteQty",
            "cumulativeQuoteQty",
            "cumQuote",
            "executed_quote_amount",
        )
    )
    trade_coverage_complete = _trade_rows_cover_order(
        matched_trades,
        executed_quantity=executed_quantity,
        executed_quote_amount=executed_quote_amount,
    )
    fee_amount, fee_asset = (
        _reliable_fee(matched_trades)
        if trade_coverage_complete
        else (None, None)
    )
    complete = all(
        item is not None
        for item in (
            exchange_status,
            executed_quantity,
            executed_quote_amount,
            fee_amount,
            fee_asset,
        )
    )
    return {
        "reconciliation_status": "COMPLETE" if complete else "PARTIAL",
        "exchange_status": exchange_status,
        "executed_quantity": executed_quantity,
        "executed_quote_amount": executed_quote_amount,
        "fee_amount": fee_amount,
        "fee_asset": fee_asset,
        "reconciliation_source": source,
    }


def _query_futures_plan_order(
    *,
    boundary: Any,
    symbol: str,
    plan_order_id: str,
) -> dict[str, Any]:
    current = _extract_rows(
        boundary.call(
            module="FUTURES",
            endpoint_key="transaction.get_current_pending_orders",
            query={"symbol": symbol, "limit": 100},
        ),
        "items",
        "orders",
    )
    matches = [
        item
        for item in current
        if str(_pick(item, "algoId", "orderId") or "") == plan_order_id
    ]
    if not matches:
        historical = _extract_rows(
            boundary.call(
                module="FUTURES",
                endpoint_key="transaction.get_historical_pending_orders",
                query={"symbol": symbol, "limit": 1000},
            ),
            "items",
            "orders",
        )
        matches = [
            item
            for item in historical
            if str(_pick(item, "algoId", "orderId") or "") == plan_order_id
        ]
    if len(matches) != 1:
        raise ValueError("official plan-order query returned no unique matching order")
    return dict(matches[0])


def _normalize_submission_results(
    *,
    operation_key: str,
    payload: Any,
    prepared_legs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw = payload
    if isinstance(raw, dict) and "data" in raw and not any(
        key in raw for key in ("orderId", "orderList", "success", "errorCode")
    ):
        raw = raw["data"]
    if operation_key == "spot.order.bulk_order" and isinstance(raw, dict):
        rows = raw.get("orderList")
    elif isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        rows = [raw]
    else:
        rows = []
    normalized: list[dict[str, Any]] = []
    is_batch = operation_key in {
        "spot.order.bulk_order",
        "transaction.place_orders_batch",
    }
    for index, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        result = dict(row)
        client_id = _pick(result, "clientOrderId", "clientAlgoId")
        if client_id not in (None, ""):
            result["client_order_id"] = str(client_id)
        if not is_batch and len(prepared_legs) == 1:
            result["leg_id"] = prepared_legs[0]["leg_id"]
        order_id = _pick(result, "orderId", "weex_order_id")
        error_code = _pick(result, "errorCode", "error_code", "rejectionCode")
        error_message = _pick(
            result, "errorMessage", "error_message", "rejectionMessage", "message"
        )
        if error_code not in (None, ""):
            result["error_code"] = _sanitized_official_error_text(
                error_code, max_length=128
            )
        if error_message not in (None, ""):
            result["error_message"] = _sanitized_official_error_text(
                error_message, max_length=512
            )
        if order_id not in (None, "") and result.get("success") is not False:
            result["status"] = "ACCEPTED"
        elif result.get("success") is False and error_code not in (None, ""):
            result["status"] = "RELEASED"
        else:
            result["status"] = "REVIEW_REQUIRED"
        normalized.append(result)
    if not normalized and len(prepared_legs) == 1:
        return [{"leg_id": prepared_legs[0]["leg_id"], "status": "REVIEW_REQUIRED"}]
    return normalized


def _find_symbol(payload: Any, symbol: str) -> dict[str, Any]:
    rows = _extract_rows(payload, "symbols", "items")
    if not rows and isinstance(payload, dict) and str(payload.get("symbol") or "").upper() == symbol:
        rows = [payload]
    matches = [item for item in rows if str(item.get("symbol") or "").upper() == symbol]
    if len(matches) != 1:
        raise ValueError("official symbol metadata is unavailable or ambiguous")
    return dict(matches[0])


def _extract_rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    raw = payload
    if isinstance(raw, dict) and "data" in raw:
        raw = raw["data"]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in keys:
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _unwrap_mapping(payload: Any) -> dict[str, Any]:
    raw = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if not isinstance(raw, dict):
        raise ValueError("official response is not an object")
    return dict(raw)


def _normalize_depth(payload: Any, *, received_at_ms: int) -> dict[str, Any]:
    depth = _unwrap_mapping(payload)
    bids = depth.get("bids")
    asks = depth.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list):
        raise ValueError("official depth response is incomplete")
    return {
        "timestamp_ms": received_at_ms,
        "limit": 200,
        "bids": bids,
        "asks": asks,
    }


def _symbol_degradations(symbol: dict[str, Any]) -> list[str]:
    status = str(symbol.get("status") or "").upper()
    if status and status != "TRADING":
        return ["symbol_not_tradable"]
    if symbol.get("enableTrade") is False:
        return ["symbol_not_tradable"]
    return []


def _position_covers_order(
    payload: Any,
    *,
    symbol: str,
    position_side: str,
    quantity: Any,
) -> bool:
    required = _decimal(quantity)
    if required is None or required <= 0:
        return False
    total = Decimal("0")
    for item in _extract_rows(payload, "items", "positions"):
        if str(item.get("symbol") or "").upper() != symbol:
            continue
        if str(_pick(item, "side", "positionSide") or "").upper() != position_side:
            continue
        size = _decimal(_pick(item, "size", "quantity", "positionAmt"))
        if size is not None and size > 0:
            total += size
    return total >= required


def _reliable_fee(trades: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    if not trades:
        return None, None
    assets: set[str] = set()
    amounts: list[Decimal] = []
    for trade in trades:
        amount = _decimal(_pick(trade, "commission", "fee", "feeAmount"))
        asset = _optional_text(_pick(trade, "commissionAsset", "feeAsset"))
        if amount is None or amount < 0 or asset is None:
            return None, None
        amounts.append(amount)
        assets.add(asset.upper())
    if len(assets) != 1:
        return None, None
    with localcontext() as context:
        context.prec = max(28, sum(len(item.as_tuple().digits) for item in amounts) + 8)
        total = sum(amounts, Decimal("0"))
    return _decimal_text(total), next(iter(assets))


def _trade_rows_cover_order(
    trades: list[dict[str, Any]],
    *,
    executed_quantity: str | None,
    executed_quote_amount: str | None,
) -> bool:
    expected_quantity = _decimal(executed_quantity)
    expected_quote = _decimal(executed_quote_amount)
    if expected_quantity is None or expected_quote is None or not trades:
        return False
    quantities: list[Decimal] = []
    quotes: list[Decimal] = []
    for trade in trades:
        quantity = _decimal(_pick(trade, "qty", "quantity", "executedQty"))
        quote = _decimal(_pick(trade, "quoteQty", "quoteAmount", "executedQuoteQty"))
        if quantity is None or quantity < 0 or quote is None or quote < 0:
            return False
        quantities.append(quantity)
        quotes.append(quote)
    return _exact_decimal_sum(quantities) == expected_quantity and _exact_decimal_sum(
        quotes
    ) == expected_quote


def _exact_decimal_sum(values: list[Decimal]) -> Decimal:
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
        return sum(values, Decimal("0"))


def _maximum_decimal_text(*values: Any) -> str:
    parsed = [item for item in (_decimal(value) for value in values) if item is not None]
    if not parsed:
        raise ValueError("official commission rate is unavailable")
    return _decimal_text(max(parsed))


def _optional_decimal_text(value: Any) -> str | None:
    parsed = _decimal(value)
    if parsed is None or parsed < 0:
        return None
    return _decimal_text(parsed)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _needs_depth(order: dict[str, Any]) -> bool:
    order_type = str(order.get("type") or "").upper()
    if order_type in {"MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"}:
        return True
    if str(order.get("planType") or "").upper() in {"TAKE_PROFIT", "STOP_LOSS"}:
        return str(order.get("executePrice") or "0") in {"", "0"}
    return False


def _operation_module(operation_key: str) -> str:
    if operation_key.startswith("spot."):
        return "SPOT"
    if operation_key.startswith("transaction."):
        return "FUTURES"
    raise ValueError("unsupported official operation")


def _required_module(value: Any) -> str:
    module = str(value or "").upper()
    if module not in {"SPOT", "FUTURES"}:
        raise ValueError("unsupported official product module")
    return module


def _required_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping[key]
    return None


def _official_error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("code", "errorCode", "error_code"):
        value = payload.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list, tuple, set)):
            return str(value)
    return None


def _official_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("msg", "message", "errorMessage", "error_message"):
        value = payload.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list, tuple, set)):
            return _sanitized_official_error_text(value, max_length=512)
    return None


def _sanitized_official_error_text(value: Any, *, max_length: int) -> str:
    text = " ".join(str(value).split())
    return text[:max_length]


def _is_definitive_http_rejection(status: Any, error_code: str | None) -> bool:
    if isinstance(status, bool) or not isinstance(status, int) or error_code is None:
        return False
    return 400 <= status < 500 and status not in {408, 409, 425, 429}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "OfficialApiBoundary",
    "OfficialAutoTradeRuntime",
    "OfficialReadRequestFailed",
    "OfficialRequestRejected",
    "OfficialRequestUncertain",
    "query_official_order_facts",
    "query_official_usage_resolution",
]
