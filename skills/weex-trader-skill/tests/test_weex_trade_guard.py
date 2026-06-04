#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
ANALYSIS_SCRIPTS = ROOT.parent / "weex-analysis-skill" / "scripts"
if str(ANALYSIS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_SCRIPTS))

import weex_order_intent_state as intent_state  # noqa: E402
import weex_analysis_cli as analysis_skill  # noqa: E402
import weex_trade_guard as trade_guard  # noqa: E402


EXPECTED_STANDARD_DISCLAIMER = (
    "Disclaimer: This result is generated solely from the current input data and is for reference only. "
    "It does not constitute any investment or trading advice. Please make your own independent judgment "
    "based on real-time data, official rules, and your own risk tolerance. Responsibility for related "
    "decisions and execution rests solely with the user."
)


class OrderIntentStateTests(unittest.TestCase):
    def test_save_load_and_expiry_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                intent = intent_state.build_intent(
                    profile_name="demo",
                    market="futures",
                    order_preview={"symbol": "BTCUSDT"},
                    raw_order={"symbol": "BTCUSDT"},
                    analysis_output={"alerts": []},
                    now_ms=1000,
                    ttl_seconds=300,
                )
                intent_state.save_intent(intent)

                loaded = intent_state.load_intent()

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["intent_id"], intent["intent_id"])
        self.assertFalse(intent_state.intent_is_expired(intent, now_ms=300999))
        self.assertTrue(intent_state.intent_is_expired(intent, now_ms=301001))


class TradeGuardTests(unittest.TestCase):
    def test_trader_local_risk_review_adds_standard_disclaimer(self) -> None:
        order_payload = {
            "order_preview": {
                "market": "spot",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": 0.01,
                "price": 65000,
            },
            "tp_sl": {
                "has_take_profit": False,
                "has_stop_loss": False,
            },
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 500,
            },
            "positions": [],
            "recent_orders": [],
            "market_snapshot": {
                "current_price": 65000,
            },
        }
        account_payload = {
            "mode": "account_scan",
            "market": "spot",
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 1000,
            },
            "positions": [],
            "recent_orders": [],
            "conditional_orders": [],
            "open_orders": [],
            "degraded_reasons": [],
        }

        order_result = trade_guard.analysis.analyze_order_risk(order_payload)
        account_result = trade_guard.analysis.analyze_account_risk(account_payload)

        self.assertEqual(order_result["disclaimer"], EXPECTED_STANDARD_DISCLAIMER)
        self.assertEqual(account_result["disclaimer"], EXPECTED_STANDARD_DISCLAIMER)

    def test_trader_and_analysis_skill_agree_on_order_risk_output(self) -> None:
        payload = {
            "order_preview": {
                "market": "futures",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "LIMIT",
                "quantity": 0.05,
                "price": 68000,
            },
            "tp_sl": {
                "has_take_profit": False,
                "has_stop_loss": False,
            },
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 120,
            },
            "positions": [
                {"symbol": "BTCUSDT", "market": "futures", "notional": 400, "leverage": 10},
                {"symbol": "ETHUSDT", "market": "futures", "notional": 500, "leverage": 25},
            ],
            "recent_orders": [
                {"symbol": "BTCUSDT", "time": 1710000000000},
                {"symbol": "BTCUSDT", "time": 1710000600000},
                {"symbol": "BTCUSDT", "time": 1710001200000},
                {"symbol": "BTCUSDT", "time": 1710001800000},
                {"symbol": "BTCUSDT", "time": 1710002400000},
            ],
            "open_orders": [],
            "conditional_orders": [],
            "market_snapshot": {
                "current_price": 65000,
            },
        }

        trader_result = trade_guard.analysis.analyze_order_risk(payload)
        analysis_result = analysis_skill.analyze_order_risk(payload)

        self.assertEqual(trader_result, analysis_result)

    def test_trader_and_analysis_skill_agree_on_account_risk_output(self) -> None:
        payload = {
            "mode": "account_scan",
            "market": "futures",
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 120,
            },
            "positions": [
                {"symbol": "BTCUSDT", "market": "futures", "notional": 750, "leverage": 25},
                {"symbol": "ETHUSDT", "market": "futures", "notional": 200, "leverage": 8},
            ],
            "recent_orders": [
                {"symbol": "BTCUSDT", "time": 1710000000000},
                {"symbol": "BTCUSDT", "time": 1710000600000},
                {"symbol": "BTCUSDT", "time": 1710001200000},
                {"symbol": "BTCUSDT", "time": 1710001800000},
                {"symbol": "BTCUSDT", "time": 1710002400000},
            ],
            "conditional_orders": [],
            "open_orders": [],
            "degraded_reasons": [],
            "constraints": [],
        }

        trader_result = trade_guard.analysis.analyze_account_risk(payload)
        analysis_result = analysis_skill.analyze_account_risk(payload)

        self.assertEqual(trader_result, analysis_result)

    def test_preview_order_saves_intent_and_returns_analysis_output(self) -> None:
        args = mock.Mock(
            profile="demo",
            market="futures",
            order_json=json.dumps(
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "order_type": "LIMIT",
                    "quantity": 0.01,
                    "price": 65000,
                }
            ),
            pretty=True,
            ttl_seconds=300,
        )
        risk_payload = {
            "order_preview": {
                "symbol": "BTCUSDT",
                "market": "futures",
            }
        }
        analysis_payload = {
            "has_risk": True,
            "alerts": [{"type": "missing_tp_sl"}],
            "confirmation_required": True,
            "next_action_hint": "continue order",
        }
        aggregator_instance = mock.Mock()
        aggregator_instance.collect_order_risk_payload.return_value = risk_payload

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(trade_guard, "TradeDataAggregator", return_value=aggregator_instance):
                    with mock.patch.object(trade_guard.analysis, "analyze_order_risk", return_value=analysis_payload):
                        stream = io.StringIO()
                        with mock.patch.object(sys, "stdout", stream):
                            exit_code = trade_guard.cmd_preview_order(args, now_ms=1000)
                        saved_intent = intent_state.load_intent()

        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(saved_intent)
        self.assertEqual(saved_intent["profile_name"], "demo")
        self.assertIn("risk_signature", saved_intent)
        self.assertIn('"risk_signature"', stream.getvalue())
        self.assertIn('"confirmation_required": true', stream.getvalue().lower())

    def test_preview_order_adds_chinese_reply_confirmation_prompt(self) -> None:
        args = mock.Mock(
            profile="demo",
            market="futures",
            language="zh",
            order_json=json.dumps(
                {
                    "symbol": "ETHUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "order_type": "MARKET",
                    "quantity": 0.01,
                }
            ),
            pretty=True,
            ttl_seconds=300,
        )
        risk_payload = {"order_preview": {"symbol": "ETHUSDT", "market": "futures"}}
        analysis_payload = {
            "has_risk": True,
            "alerts": [{"type": "missing_tp_sl"}],
            "confirmation_required": True,
            "next_action_hint": "continue order",
        }
        aggregator_instance = mock.Mock()
        aggregator_instance.collect_order_risk_payload.return_value = risk_payload

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(trade_guard, "TradeDataAggregator", return_value=aggregator_instance):
                    with mock.patch.object(trade_guard.analysis, "analyze_order_risk", return_value=analysis_payload):
                        stream = io.StringIO()
                        with mock.patch.object(sys, "stdout", stream):
                            exit_code = trade_guard.cmd_preview_order(args, now_ms=1000)

        payload = json.loads(stream.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["user_confirmation"]["language"], "zh")
        self.assertEqual(payload["user_confirmation"]["reply_text"], "确认")
        self.assertIn("回复：确认", payload["user_confirmation"]["reply_instruction"])
        self.assertNotIn("确认下单", payload["user_confirmation"]["reply_instruction"])

    def test_preview_order_adds_english_reply_confirmation_prompt(self) -> None:
        args = mock.Mock(
            profile="demo",
            market="futures",
            language="en",
            order_json=json.dumps(
                {
                    "symbol": "ETHUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "order_type": "MARKET",
                    "quantity": 0.01,
                }
            ),
            pretty=True,
            ttl_seconds=300,
        )
        risk_payload = {"order_preview": {"symbol": "ETHUSDT", "market": "futures"}}
        analysis_payload = {
            "has_risk": True,
            "alerts": [{"type": "missing_tp_sl"}],
            "confirmation_required": True,
            "next_action_hint": "continue order",
        }
        aggregator_instance = mock.Mock()
        aggregator_instance.collect_order_risk_payload.return_value = risk_payload

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(trade_guard, "TradeDataAggregator", return_value=aggregator_instance):
                    with mock.patch.object(trade_guard.analysis, "analyze_order_risk", return_value=analysis_payload):
                        stream = io.StringIO()
                        with mock.patch.object(sys, "stdout", stream):
                            exit_code = trade_guard.cmd_preview_order(args, now_ms=1000)

        payload = json.loads(stream.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["user_confirmation"]["language"], "en")
        self.assertEqual(payload["user_confirmation"]["reply_text"], "confirm")
        self.assertIn("reply: confirm", payload["user_confirmation"]["reply_instruction"])
        self.assertNotIn("确认", payload["user_confirmation"]["reply_instruction"])

    def test_confirm_order_rejects_expired_intent(self) -> None:
        args = mock.Mock(intent_id=None, risk_signature=None, confirm_live=True, pretty=False)

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                intent = intent_state.build_intent(
                    profile_name="demo",
                    market="futures",
                    order_preview={"symbol": "BTCUSDT"},
                    raw_order={"symbol": "BTCUSDT"},
                    analysis_output={"alerts": []},
                    now_ms=1000,
                    ttl_seconds=300,
                )
                intent_state.save_intent(intent)
                stream = io.StringIO()
                with mock.patch.object(sys, "stdout", stream):
                    with mock.patch.object(trade_guard, "_submit_live_order") as submit_mock:
                        exit_code = trade_guard.cmd_confirm_order(args, now_ms=1000 + (301 * 1000))

        self.assertEqual(exit_code, 1)
        self.assertIn("expired", stream.getvalue().lower())
        submit_mock.assert_not_called()

    def test_confirm_order_executes_live_order_when_intent_is_valid(self) -> None:
        args = mock.Mock(intent_id="intent-1", risk_signature="sig-1", confirm_live=True, pretty=True)
        execution_payload = {"ok": True, "order_id": "9001"}

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                intent = intent_state.build_intent(
                    profile_name="demo",
                    market="futures",
                    order_preview={"symbol": "BTCUSDT"},
                    raw_order={
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "position_side": "LONG",
                        "order_type": "LIMIT",
                        "quantity": 0.01,
                        "price": 65000,
                        "time_in_force": "GTC",
                    },
                    analysis_output={"alerts": []},
                    now_ms=1000,
                    ttl_seconds=300,
                )
                intent["intent_id"] = "intent-1"
                intent["risk_signature"] = "sig-1"
                intent_state.save_intent(intent)
                stream = io.StringIO()
                with mock.patch.object(trade_guard, "_submit_live_order", return_value=execution_payload) as submit_mock:
                    with mock.patch.object(sys, "stdout", stream):
                        exit_code = trade_guard.cmd_confirm_order(args, now_ms=1000 + (60 * 1000))
                remaining_intent = intent_state.load_intent()

        self.assertEqual(exit_code, 0)
        submit_mock.assert_called_once()
        self.assertIsNone(remaining_intent)
        self.assertIn('"order_id": "9001"', stream.getvalue())

    def test_submit_live_order_passes_new_client_order_id_for_futures(self) -> None:
        prepared_bodies: list[dict[str, object]] = []
        fake_contract_api = mock.Mock()
        fake_contract_api.ENDPOINTS = {"place": {"path": "/capi/v3/order"}}
        fake_contract_api.find_endpoint_key_by_doc_suffix.return_value = "place"
        fake_contract_api.normalize_contract_trade_symbol.side_effect = lambda symbol: symbol.upper()
        fake_client = mock.Mock()

        def prepare_request(endpoint: dict[str, object], query: dict[str, object], body: dict[str, object]) -> dict[str, object]:
            prepared_bodies.append(body)
            return {"endpoint": endpoint, "query": query, "body": body}

        fake_client.prepare_request.side_effect = prepare_request
        fake_client.send.return_value = {"ok": True, "data": {"orderId": "1001"}}

        with mock.patch.object(trade_guard, "_build_contract_client", return_value=(fake_contract_api, fake_client)):
            result = trade_guard._submit_live_order(
                market="futures",
                profile_name="demo",
                raw_order={
                    "symbol": "btcusdt",
                    "side": "SELL",
                    "position_side": "LONG",
                    "order_type": "MARKET",
                    "quantity": "0.01",
                    "new_client_order_id": "monitor-mon1-close",
                },
            )

        self.assertEqual(result["orderId"], "1001")
        self.assertEqual(prepared_bodies[0]["newClientOrderId"], "monitor-mon1-close")

    def test_submit_live_order_accepts_camelcase_field_names_for_futures(self) -> None:
        prepared_bodies: list[dict[str, object]] = []
        fake_contract_api = mock.Mock()
        fake_contract_api.ENDPOINTS = {"place": {"path": "/capi/v3/order"}}
        fake_contract_api.find_endpoint_key_by_doc_suffix.return_value = "place"
        fake_contract_api.normalize_contract_trade_symbol.side_effect = lambda symbol: symbol.upper()
        fake_client = mock.Mock()

        def prepare_request(endpoint: dict[str, object], query: dict[str, object], body: dict[str, object]) -> dict[str, object]:
            prepared_bodies.append(body)
            return {"endpoint": endpoint, "query": query, "body": body}

        fake_client.prepare_request.side_effect = prepare_request
        fake_client.send.return_value = {"ok": True, "data": {"orderId": "2001"}}

        with mock.patch.object(trade_guard, "_build_contract_client", return_value=(fake_contract_api, fake_client)):
            result = trade_guard._submit_live_order(
                market="futures",
                profile_name="demo",
                raw_order={
                    "symbol": "btcusdt",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "type": "MARKET",
                    "quantity": "0.01",
                    "timeInForce": "GTC",
                    "newClientOrderId": "monitor-camel-close",
                },
            )

        self.assertEqual(result["orderId"], "2001")
        self.assertEqual(prepared_bodies[0]["positionSide"], "LONG")
        self.assertEqual(prepared_bodies[0]["type"], "MARKET")
        self.assertEqual(prepared_bodies[0]["timeInForce"], "GTC")
        self.assertEqual(prepared_bodies[0]["newClientOrderId"], "monitor-camel-close")

    def test_submit_live_order_accepts_camelcase_field_names_for_spot(self) -> None:
        prepared_bodies: list[dict[str, object]] = []
        fake_spot_api = mock.Mock()
        fake_spot_api.ENDPOINTS = {"place": {"path": "/sapi/v1/order"}}
        fake_spot_api.find_endpoint_key_by_doc_suffix.return_value = "place"
        fake_spot_api.normalize_spot_symbol.side_effect = lambda symbol: symbol.upper()
        fake_client = mock.Mock()

        def prepare_request(endpoint: dict[str, object], query: dict[str, object], body: dict[str, object]) -> dict[str, object]:
            prepared_bodies.append(body)
            return {"endpoint": endpoint, "query": query, "body": body}

        fake_client.prepare_request.side_effect = prepare_request
        fake_client.send.return_value = {"ok": True, "data": {"orderId": "3001"}}

        with mock.patch.object(trade_guard, "_build_spot_client", return_value=(fake_spot_api, fake_client)):
            result = trade_guard._submit_live_order(
                market="spot",
                profile_name="demo",
                raw_order={
                    "symbol": "btcusdt",
                    "side": "BUY",
                    "type": "LIMIT",
                    "quantity": "0.01",
                    "price": "65000",
                    "timeInForce": "GTC",
                },
            )

        self.assertEqual(result["orderId"], "3001")
        self.assertEqual(prepared_bodies[0]["type"], "LIMIT")
        self.assertEqual(prepared_bodies[0]["timeInForce"], "GTC")

    def test_submit_live_order_raises_on_missing_position_side_for_futures(self) -> None:
        with mock.patch.object(trade_guard, "_build_contract_client"):
            with self.assertRaises(trade_guard.AggregationInputError) as exc_info:
                trade_guard._submit_live_order(
                    market="futures",
                    profile_name="demo",
                    raw_order={
                        "symbol": "BTCUSDT",
                        "side": "SELL",
                        "type": "MARKET",
                        "quantity": "0.01",
                    },
                )
        self.assertIn("positionSide", str(exc_info.exception))

    def test_submit_live_order_raises_on_missing_type_for_futures(self) -> None:
        with mock.patch.object(trade_guard, "_build_contract_client"):
            with self.assertRaises(trade_guard.AggregationInputError) as exc_info:
                trade_guard._submit_live_order(
                    market="futures",
                    profile_name="demo",
                    raw_order={
                        "symbol": "BTCUSDT",
                        "side": "SELL",
                        "positionSide": "LONG",
                        "quantity": "0.01",
                    },
                )
        self.assertIn("type", str(exc_info.exception))

    def test_submit_live_order_raises_on_missing_type_for_spot(self) -> None:
        with mock.patch.object(trade_guard, "_build_spot_client"):
            with self.assertRaises(trade_guard.AggregationInputError) as exc_info:
                trade_guard._submit_live_order(
                    market="spot",
                    profile_name="demo",
                    raw_order={
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "quantity": "0.01",
                    },
                )
        self.assertIn("type", str(exc_info.exception))

    def test_preview_tp_sl_saves_intent_and_returns_confirmation_fields(self) -> None:
        args = mock.Mock(
            profile="demo",
            language="zh",
            tp_sl_json=json.dumps(
                {
                    "symbol": "ETHUSDT",
                    "clientAlgoId": "mon_price_demo",
                    "planType": "TAKE_PROFIT",
                    "triggerPrice": "2500",
                    "executePrice": "0",
                    "quantity": "0.2",
                    "positionSide": "SHORT",
                    "triggerPriceType": "CONTRACT_PRICE",
                }
            ),
            pretty=True,
            ttl_seconds=300,
        )
        aggregator_instance = mock.Mock()
        aggregator_instance.collect_account_risk_payload.return_value = {
            "mode": "account_scan",
            "market": "futures",
            "account_snapshot": {"equity": 1000, "available_balance": 500},
            "positions": [],
            "recent_orders": [],
            "conditional_orders": [],
            "open_orders": [],
            "degraded_reasons": [],
            "constraints": [],
        }
        analysis_payload = {
            "has_risk": False,
            "alerts": [],
            "confirmation_required": True,
            "next_action_hint": "confirm tp/sl",
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(trade_guard, "TradeDataAggregator", return_value=aggregator_instance):
                    with mock.patch.object(trade_guard.analysis, "analyze_account_risk", return_value=analysis_payload):
                        stream = io.StringIO()
                        with mock.patch.object(sys, "stdout", stream):
                            exit_code = trade_guard.cmd_preview_tp_sl(args, now_ms=1000)
                        saved_intent = intent_state.load_intent()

        payload = json.loads(stream.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(saved_intent)
        self.assertEqual(saved_intent["intent_type"], "tp_sl_order")
        self.assertEqual(saved_intent["tp_sl_order"]["clientAlgoId"], "mon_price_demo")
        self.assertIn("risk_signature", payload)
        self.assertEqual(payload["user_confirmation"]["reply_text"], "确认")

    def test_confirm_tp_sl_executes_live_tp_sl_when_intent_is_valid(self) -> None:
        args = mock.Mock(intent_id="intent-tpsl", risk_signature="sig-tpsl", confirm_live=True, pretty=True)
        tp_sl_order = {
            "symbol": "ETHUSDT",
            "clientAlgoId": "mon_price_demo",
            "planType": "TAKE_PROFIT",
            "triggerPrice": "2500",
            "executePrice": "0",
            "quantity": "0.2",
            "positionSide": "SHORT",
            "triggerPriceType": "CONTRACT_PRICE",
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                intent = intent_state.build_intent(
                    profile_name="demo",
                    market="futures",
                    order_preview=tp_sl_order,
                    raw_order=tp_sl_order,
                    analysis_output={"alerts": []},
                    now_ms=1000,
                    ttl_seconds=300,
                    intent_type="tp_sl_order",
                    tp_sl_order=tp_sl_order,
                )
                intent["intent_id"] = "intent-tpsl"
                intent["risk_signature"] = "sig-tpsl"
                intent_state.save_intent(intent)
                stream = io.StringIO()
                with mock.patch.object(
                    trade_guard,
                    "_submit_live_tp_sl_order",
                    return_value={"algoId": "7001", "clientAlgoId": "mon_price_demo"},
                ) as submit_mock:
                    with mock.patch.object(sys, "stdout", stream):
                        exit_code = trade_guard.cmd_confirm_tp_sl(args, now_ms=2000)
                remaining_intent = intent_state.load_intent()

        self.assertEqual(exit_code, 0)
        submit_mock.assert_called_once_with(profile_name="demo", raw_order=tp_sl_order)
        self.assertIsNone(remaining_intent)
        self.assertIn('"algoId": "7001"', stream.getvalue())

    def test_confirm_order_requires_intent_id_and_risk_signature(self) -> None:
        args = mock.Mock(intent_id=None, risk_signature=None, confirm_live=True, pretty=False)

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                intent = intent_state.build_intent(
                    profile_name="demo",
                    market="futures",
                    order_preview={"symbol": "BTCUSDT"},
                    raw_order={"symbol": "BTCUSDT"},
                    analysis_output={"alerts": []},
                    now_ms=1000,
                    ttl_seconds=300,
                )
                intent_state.save_intent(intent)
                stream = io.StringIO()
                with mock.patch.object(sys, "stdout", stream):
                    with mock.patch.object(trade_guard, "_submit_live_order") as submit_mock:
                        exit_code = trade_guard.cmd_confirm_order(args, now_ms=2000)

        self.assertEqual(exit_code, 1)
        self.assertIn("intent-id", stream.getvalue().lower())
        self.assertIn("risk-signature", stream.getvalue().lower())
        submit_mock.assert_not_called()

    def test_confirm_order_requires_confirm_live_flag(self) -> None:
        args = mock.Mock(intent_id=None, risk_signature=None, confirm_live=False, pretty=False)

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                intent = intent_state.build_intent(
                    profile_name="demo",
                    market="futures",
                    order_preview={"symbol": "BTCUSDT"},
                    raw_order={"symbol": "BTCUSDT"},
                    analysis_output={"alerts": []},
                    now_ms=1000,
                    ttl_seconds=300,
                )
                intent_state.save_intent(intent)
                stream = io.StringIO()
                with mock.patch.object(sys, "stdout", stream):
                    with mock.patch.object(trade_guard, "_submit_live_order") as submit_mock:
                        exit_code = trade_guard.cmd_confirm_order(args, now_ms=1000 + 1000)

        self.assertEqual(exit_code, 1)
        self.assertIn("confirm-live", stream.getvalue().lower())
        submit_mock.assert_not_called()

    def test_confirm_order_rejects_mismatched_risk_signature(self) -> None:
        args = mock.Mock(intent_id="intent-1", risk_signature="wrong-signature", confirm_live=True, pretty=False)

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                intent = intent_state.build_intent(
                    profile_name="demo",
                    market="futures",
                    order_preview={"symbol": "BTCUSDT"},
                    raw_order={"symbol": "BTCUSDT"},
                    analysis_output={"alerts": []},
                    now_ms=1000,
                    ttl_seconds=300,
                )
                intent["intent_id"] = "intent-1"
                intent_state.save_intent(intent)
                stream = io.StringIO()
                with mock.patch.object(sys, "stdout", stream):
                    with mock.patch.object(trade_guard, "_submit_live_order") as submit_mock:
                        exit_code = trade_guard.cmd_confirm_order(args, now_ms=2000)

        self.assertEqual(exit_code, 1)
        self.assertIn("risk signature", stream.getvalue().lower())
        submit_mock.assert_not_called()

    def test_account_scan_command_returns_analysis_output(self) -> None:
        args = mock.Mock(profile="demo", market="futures", symbol="BTCUSDT", pretty=True)
        aggregator_instance = mock.Mock()
        aggregator_instance.collect_account_risk_payload.return_value = {
            "mode": "account_scan",
            "market": "futures",
            "account_snapshot": {"equity": 1000, "available_balance": 120},
            "positions": [],
            "recent_orders": [],
            "conditional_orders": [],
            "open_orders": [],
            "degraded_reasons": [],
            "constraints": [],
        }
        analysis_payload = {
            "has_risk": True,
            "alerts": [{"type": "low_free_balance"}],
        }

        with mock.patch.object(trade_guard, "TradeDataAggregator", return_value=aggregator_instance):
            with mock.patch.object(trade_guard.analysis, "analyze_account_risk", return_value=analysis_payload):
                stream = io.StringIO()
                with mock.patch.object(sys, "stdout", stream):
                    exit_code = trade_guard.cmd_account_scan(args)

        self.assertEqual(exit_code, 0)
        self.assertIn('"low_free_balance"', stream.getvalue())

    def test_main_returns_structured_json_when_account_scan_aggregation_fails(self) -> None:
        aggregator_instance = mock.Mock()
        aggregator_instance.collect_account_risk_payload.side_effect = trade_guard.AggregationInputError(
            "Spot request failed for spot.account.get_account_balance: {'code': -1000, 'msg': 'An unknown error occurred.'}"
        )

        with mock.patch.object(trade_guard, "TradeDataAggregator", return_value=aggregator_instance):
            stream = io.StringIO()
            with mock.patch.object(sys, "stdout", stream):
                exit_code = trade_guard.main(["account-scan", "--profile", "demo", "--market", "spot"])

        self.assertEqual(exit_code, 1)
        self.assertIn('"ok": false', stream.getvalue().lower())
        self.assertIn("spot.account.get_account_balance", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
