#!/usr/bin/env python3
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import weex_trade_data_aggregator as aggregator  # noqa: E402


class WindowSplitTests(unittest.TestCase):
    def test_split_time_range_chunks_ranges_larger_than_ninety_days(self) -> None:
        end_ms = 105 * aggregator.DAY_MS

        windows = aggregator.split_time_range(0, end_ms, max_span_days=90)

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].start_ms, 0)
        self.assertEqual(windows[0].end_ms, (90 * aggregator.DAY_MS) - 1)
        self.assertEqual(windows[1].start_ms, 90 * aggregator.DAY_MS)
        self.assertEqual(windows[1].end_ms, end_ms)


class ReplayCollectionTests(unittest.TestCase):
    def test_collect_replay_payload_rejects_spot_market_without_symbol(self) -> None:
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=mock.Mock())

        with self.assertRaises(aggregator.AggregationInputError) as exc_info:
            trade_aggregator.collect_replay_payload(
                profile_name="demo",
                market="spot",
                period="7d",
                symbol=None,
            )

        self.assertIn("symbol", str(exc_info.exception))
        self.assertIn("spot", str(exc_info.exception))

    def test_collect_replay_payload_normalizes_futures_history_and_account_state(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = {
            "asset": "USDT",
            "balance": "1000",
            "availableBalance": "620",
            "unrealizePnl": "30",
        }
        fetcher.fetch_futures_positions.return_value = [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "marginType": "CROSSED",
                "separatedMode": "COMBINED",
                "size": "0.01",
                "openValue": "650",
                "leverage": "10",
                "createdTime": 1710000000000,
                "updatedTime": 1710003600000,
            }
        ]
        fetcher.fetch_futures_orders.return_value = [
            {
                "symbol": "BTCUSDT",
                "orderId": 11,
                "clientOrderId": "abc",
                "side": "BUY",
                "positionSide": "LONG",
                "marginType": "CROSSED",
                "separatedMode": "COMBINED",
                "type": "LIMIT",
                "status": "FILLED",
                "origQty": "0.01",
                "executedQty": "0.01",
                "cumQuote": "650",
                "avgPrice": "65000",
                "price": "65000",
                "time": 1710000000000,
                "updateTime": 1710003600000,
            }
        ]
        fetcher.fetch_futures_fills.return_value = [
            {
                "id": 21,
                "orderId": 11,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "positionSide": "LONG",
                "marginType": "CROSSED",
                "separatedMode": "COMBINED",
                "price": "65000",
                "qty": "0.01",
                "quoteQty": "650",
                "realizedPnl": "12",
                "commission": "0.5",
                "time": 1710003600000,
            }
        ]
        fetcher.fetch_futures_bills.return_value = {
            "items": [
                {
                    "billId": 31,
                    "asset": "USDT",
                    "symbol": "BTCUSDT",
                    "income": "12",
                    "incomeType": "position_close_long",
                    "fillFee": "0.5",
                    "time": 1710003600000,
                }
            ]
        }
        fetcher.fetch_futures_klines.return_value = [
            [1710000000000, "64000", "66000", "63500", "65000", "100", 1710003599999, "6500000", 120, "55", "3575000"]
        ]
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="futures",
            period="7d",
            symbol="BTCUSDT",
        )

        self.assertEqual(result["market"], "futures")
        self.assertEqual(result["closed_trade_count"], 1)
        self.assertEqual(result["balances"][0]["account_scope"], "personal_futures")
        self.assertEqual(result["balances"][0]["available_balance"], 620.0)
        self.assertEqual(result["positions"][0]["account_scope"], "personal_futures")
        self.assertEqual(result["positions"][0]["symbol"], "BTCUSDT")
        self.assertEqual(result["positions"][0]["margin_type"], "CROSSED")
        self.assertEqual(result["positions"][0]["position_mode"], "COMBINED")
        self.assertEqual(result["orders"][0]["account_scope"], "personal_futures")
        self.assertEqual(result["orders"][0]["status"], "FILLED")
        self.assertEqual(result["orders"][0]["margin_type"], "CROSSED")
        self.assertEqual(result["orders"][0]["position_mode"], "COMBINED")
        self.assertEqual(result["fills"][0]["account_scope"], "personal_futures")
        self.assertEqual(result["fills"][0]["realized_pnl"], 12.0)
        self.assertEqual(result["fills"][0]["margin_type"], "CROSSED")
        self.assertEqual(result["fills"][0]["position_mode"], "COMBINED")
        self.assertEqual(result["bills"][0]["type"], "position_close_long")
        self.assertEqual(result["price_series"][0]["close"], 65000.0)
        self.assertEqual(result["price_series"][0]["symbol"], "BTCUSDT")

    def test_collect_replay_payload_includes_futures_historical_pending_orders_for_discipline_review(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = []
        fetcher.fetch_futures_positions.return_value = []
        fetcher.fetch_futures_orders.return_value = []
        fetcher.fetch_futures_fills.return_value = []
        fetcher.fetch_futures_bills.return_value = []
        fetcher.fetch_futures_historical_pending_orders.return_value = {
            "orders": [
                {
                    "algoId": 91,
                    "actualOrderId": 901,
                    "symbol": "BTCUSDT",
                    "orderType": "TAKE_PROFIT_MARKET",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "algoStatus": "CANCELED",
                    "quantity": "0.01",
                    "tpTriggerPrice": "70000",
                    "closePosition": True,
                    "reduceOnly": True,
                    "createTime": 1710001000000,
                    "updateTime": 1710002000000,
                }
            ]
        }
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="futures",
            period="7d",
            symbol=None,
        )

        matching_orders = [
            order
            for order in result["orders"]
            if order["symbol"] == "BTCUSDT" and order["order_type"] == "take_profit_market"
        ]

        self.assertEqual(len(matching_orders), 1)
        self.assertEqual(matching_orders[0]["status"], "CANCELED")
        self.assertEqual(matching_orders[0]["order_id"], "901")
        self.assertEqual(matching_orders[0]["tp_trigger_price"], 70000.0)

    def test_normalize_conditional_order_uses_algo_id_when_actual_order_id_is_zero(self) -> None:
        normalized = aggregator._normalize_orders(
            [
                {
                    "algoId": 91,
                    "actualOrderId": 0,
                    "symbol": "BTCUSDT",
                    "orderType": "TAKE_PROFIT_MARKET",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "algoStatus": "CANCELED",
                    "quantity": "0.01",
                    "reduceOnly": False,
                    "closePosition": False,
                    "createTime": 1710001000000,
                    "updateTime": 1710002000000,
                }
            ],
            "futures",
        )[0]

        self.assertEqual(normalized["order_id"], "91")
        self.assertEqual(normalized["time"], 1710001000000)
        self.assertFalse(normalized["reduce_only"])
        self.assertFalse(normalized["close_position"])

    def test_normalize_conditional_order_preserves_client_algo_id(self) -> None:
        normalized = aggregator._normalize_orders(
            [
                {
                    "algoId": 91,
                    "actualOrderId": 0,
                    "clientAlgoId": "mon_price_demo",
                    "symbol": "BTCUSDT",
                    "orderType": "TAKE_PROFIT_MARKET",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "algoStatus": "NOT_TRIGGER",
                    "quantity": "0.01",
                    "tpTriggerPrice": "70000",
                    "createTime": 1710001000000,
                }
            ],
            "futures",
        )[0]

        self.assertEqual(normalized["client_order_id"], "mon_price_demo")

    def test_normalize_order_parses_string_false_boolean_fields(self) -> None:
        normalized = aggregator._normalize_orders(
            [
                {
                    "orderId": 11,
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "reduceOnly": "false",
                    "closePosition": "false",
                    "origQty": "0.01",
                    "executedQty": "0",
                    "time": 1710000000000,
                }
            ],
            "futures",
        )[0]

        self.assertFalse(normalized["reduce_only"])
        self.assertFalse(normalized["close_position"])

    def test_collect_replay_payload_normalizes_spot_symbol_specific_data(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_spot_balance.return_value = {
            "balances": [
                {
                    "asset": "USDT",
                    "free": "520",
                    "locked": "10",
                }
            ]
        }
        fetcher.fetch_spot_orders.return_value = [
            {
                "symbol": "ETHUSDT",
                "orderId": 41,
                "clientOrderId": "spot-1",
                "side": "BUY",
                "type": "LIMIT",
                "status": "FILLED",
                "origQty": "0.5",
                "executedQty": "0.5",
                "cummulativeQuoteQty": "1500",
                "price": "3000",
                "time": 1710000000000,
                "updateTime": 1710003600000,
            }
        ]
        fetcher.fetch_spot_fills.return_value = [
            {
                "id": 51,
                "orderId": 41,
                "symbol": "ETHUSDT",
                "price": "3000",
                "qty": "0.5",
                "quoteQty": "1500",
                "commission": "1.2",
                "time": 1710003600000,
                "isBuyer": True,
            }
        ]
        fetcher.fetch_spot_bills.return_value = {
            "items": [
                {
                    "billId": 61,
                    "coinName": "USDT",
                    "bizType": "trade_out",
                    "deltaAmount": "-1500",
                    "afterAmount": "520",
                    "fees": "1.2",
                    "cTime": "1710003600000",
                }
            ]
        }
        fetcher.fetch_spot_klines.return_value = [
            [1710000000000, "2950", "3050", "2940", "3000", "90", 1710003599999, "270000", 80]
        ]
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="spot",
            period="7d",
            symbol="ETHUSDT",
        )

        self.assertEqual(result["market"], "spot")
        self.assertEqual(result["balances"][0]["account_scope"], "personal_spot")
        self.assertEqual(result["balances"][0]["asset"], "USDT")
        self.assertEqual(result["balances"][0]["balance"], 530.0)
        self.assertEqual(result["balances"][0]["available_balance"], 520.0)
        self.assertEqual(result["orders"][0]["account_scope"], "personal_spot")
        self.assertEqual(result["orders"][0]["order_id"], "41")
        self.assertIsNone(result["orders"][0]["margin_type"])
        self.assertIsNone(result["orders"][0]["position_mode"])
        self.assertEqual(result["fills"][0]["account_scope"], "personal_spot")
        self.assertEqual(result["fills"][0]["symbol"], "ETHUSDT")
        self.assertEqual(result["fills"][0]["side"], "buy")
        self.assertIsNone(result["fills"][0]["margin_type"])
        self.assertIsNone(result["fills"][0]["position_mode"])
        self.assertEqual(result["bills"][0]["type"], "trade_out")
        self.assertEqual(result["price_series"][0]["close"], 3000.0)

    def test_collect_replay_payload_marks_spot_episode_pnl_as_unavailable(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_spot_balance.return_value = {
            "balances": [
                {
                    "asset": "USDT",
                    "free": "520",
                    "locked": "10",
                }
            ]
        }
        fetcher.fetch_spot_orders.return_value = [
            {
                "symbol": "ETHUSDT",
                "orderId": 41,
                "clientOrderId": "spot-open",
                "side": "BUY",
                "type": "LIMIT",
                "status": "FILLED",
                "origQty": "0.5",
                "executedQty": "0.5",
                "price": "3000",
                "time": 1710000000000,
                "updateTime": 1710000000000,
            },
            {
                "symbol": "ETHUSDT",
                "orderId": 42,
                "clientOrderId": "spot-close",
                "side": "SELL",
                "type": "LIMIT",
                "status": "FILLED",
                "origQty": "0.5",
                "executedQty": "0.5",
                "price": "3050",
                "time": 1710003600000,
                "updateTime": 1710003600000,
            },
        ]
        fetcher.fetch_spot_fills.return_value = [
            {
                "id": 51,
                "orderId": 41,
                "symbol": "ETHUSDT",
                "price": "3000",
                "qty": "0.5",
                "quoteQty": "1500",
                "commission": "1.2",
                "time": 1710000000000,
                "isBuyer": True,
            },
            {
                "id": 52,
                "orderId": 42,
                "symbol": "ETHUSDT",
                "price": "3050",
                "qty": "0.5",
                "quoteQty": "1525",
                "commission": "1.2",
                "time": 1710003600000,
                "isBuyer": False,
            },
        ]
        fetcher.fetch_spot_bills.return_value = {"items": []}
        fetcher.fetch_spot_klines.return_value = []
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="spot",
            period="7d",
            symbol="ETHUSDT",
        )

        self.assertTrue(result["partial"])
        self.assertEqual(result["closed_trade_count"], 1)
        self.assertIn("replay_episode_pnl_unavailable", result["degraded_reasons"])

    def test_collect_replay_payload_uses_degraded_mode_for_all_market_without_symbol(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = {
            "asset": "USDT",
            "balance": "1000",
            "availableBalance": "600",
        }
        fetcher.fetch_futures_positions.return_value = []
        fetcher.fetch_futures_orders.return_value = []
        fetcher.fetch_futures_fills.return_value = []
        fetcher.fetch_futures_bills.return_value = {"items": []}
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="all",
            period="7d",
            symbol=None,
        )

        self.assertEqual(result["market"], "all")
        self.assertTrue(result["partial"])
        self.assertEqual(result["balances"][0]["account_scope"], "personal_futures")
        self.assertIn(
            {"code": "spot_symbol_required", "message": "spot history is only collected when symbol is provided."},
            result["constraints"],
        )
        self.assertIn("spot_history_skipped_without_symbol", result["degraded_reasons"])
        fetcher.fetch_spot_balance.assert_not_called()
        fetcher.fetch_spot_orders.assert_not_called()
        fetcher.fetch_spot_fills.assert_not_called()
        fetcher.fetch_spot_bills.assert_not_called()
        fetcher.fetch_spot_klines.assert_not_called()

    def test_collect_replay_payload_propagates_partial_and_degraded_reasons(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = {
            "asset": "USDT",
            "balance": "1000",
            "availableBalance": "600",
        }
        fetcher.fetch_futures_positions.return_value = []
        fetcher.fetch_futures_orders.return_value = {
            "items": [],
            "_meta": {
                "partial": True,
                "degraded_reasons": ["orders_window_truncated"],
            },
        }
        fetcher.fetch_futures_fills.return_value = []
        fetcher.fetch_futures_bills.return_value = {"items": []}
        fetcher.fetch_futures_klines.return_value = []
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="futures",
            period="7d",
            symbol="BTCUSDT",
        )

        self.assertTrue(result["partial"])
        self.assertIn("orders_window_truncated", result["degraded_reasons"])

    def test_collect_replay_payload_counts_closed_episode_instead_of_entry_fill(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = {
            "asset": "USDT",
            "balance": "1000",
            "availableBalance": "600",
        }
        fetcher.fetch_futures_positions.return_value = []
        fetcher.fetch_futures_orders.return_value = [
            {
                "symbol": "BTCUSDT",
                "orderId": 101,
                "side": "BUY",
                "positionSide": "LONG",
                "status": "FILLED",
                "origQty": "0.01",
                "executedQty": "0.01",
                "price": "65000",
                "time": 1710000000000,
                "updateTime": 1710000000000,
            },
            {
                "symbol": "BTCUSDT",
                "orderId": 102,
                "side": "SELL",
                "positionSide": "LONG",
                "status": "FILLED",
                "reduceOnly": True,
                "origQty": "0.01",
                "executedQty": "0.01",
                "price": "65100",
                "time": 1710001800000,
                "updateTime": 1710001800000,
            },
        ]
        fetcher.fetch_futures_fills.return_value = [
            {
                "id": 201,
                "orderId": 101,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "positionSide": "LONG",
                "price": "65000",
                "qty": "0.01",
                "quoteQty": "650",
                "realizedPnl": "0",
                "commission": "0.5",
                "time": 1710000000000,
            },
            {
                "id": 202,
                "orderId": 102,
                "symbol": "BTCUSDT",
                "side": "SELL",
                "positionSide": "LONG",
                "price": "65100",
                "qty": "0.01",
                "quoteQty": "651",
                "realizedPnl": "10",
                "commission": "0.5",
                "time": 1710001800000,
            },
        ]
        fetcher.fetch_futures_bills.return_value = {"items": []}
        fetcher.fetch_futures_klines.return_value = []
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="futures",
            period="7d",
            symbol="BTCUSDT",
        )

        self.assertEqual(result["closed_trade_count"], 1)

    def test_collect_replay_payload_does_not_count_entry_only_history_as_closed_trade(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_spot_balance.return_value = {
            "balances": [
                {
                    "asset": "USDT",
                    "free": "520",
                    "locked": "10",
                }
            ]
        }
        fetcher.fetch_spot_orders.return_value = [
            {
                "symbol": "ETHUSDT",
                "orderId": 41,
                "clientOrderId": "spot-1",
                "side": "BUY",
                "type": "LIMIT",
                "status": "FILLED",
                "origQty": "0.5",
                "executedQty": "0.5",
                "cummulativeQuoteQty": "1500",
                "price": "3000",
                "time": 1710000000000,
                "updateTime": 1710003600000,
            }
        ]
        fetcher.fetch_spot_fills.return_value = [
            {
                "id": 51,
                "orderId": 41,
                "symbol": "ETHUSDT",
                "price": "3000",
                "qty": "0.5",
                "quoteQty": "1500",
                "commission": "1.2",
                "time": 1710003600000,
                "isBuyer": True,
            }
        ]
        fetcher.fetch_spot_bills.return_value = {"items": []}
        fetcher.fetch_spot_klines.return_value = []
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="spot",
            period="7d",
            symbol="ETHUSDT",
        )

        self.assertEqual(result["closed_trade_count"], 0)

    def test_collect_replay_payload_marks_exit_only_history_as_partial_carry_in(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = {
            "asset": "USDT",
            "balance": "1000",
            "availableBalance": "600",
        }
        fetcher.fetch_futures_positions.return_value = []
        fetcher.fetch_futures_orders.return_value = [
            {
                "symbol": "BTCUSDT",
                "orderId": 11,
                "clientOrderId": "carry-in-close",
                "side": "SELL",
                "positionSide": "LONG",
                "type": "MARKET",
                "status": "FILLED",
                "reduceOnly": True,
                "origQty": "0.01",
                "executedQty": "0.01",
                "cumQuote": "650",
                "avgPrice": "65000",
                "price": "0",
                "time": 1710000000000,
                "updateTime": 1710000000000,
            }
        ]
        fetcher.fetch_futures_fills.return_value = [
            {
                "id": 21,
                "orderId": 11,
                "symbol": "BTCUSDT",
                "side": "SELL",
                "positionSide": "LONG",
                "price": "65000",
                "qty": "0.01",
                "quoteQty": "650",
                "realizedPnl": "12",
                "commission": "0.5",
                "time": 1710000000000,
            }
        ]
        fetcher.fetch_futures_bills.return_value = {"items": []}
        fetcher.fetch_futures_klines.return_value = []
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="futures",
            period="7d",
            symbol="BTCUSDT",
        )

        self.assertTrue(result["partial"])
        self.assertEqual(result["closed_trade_count"], 1)
        self.assertIn("replay_carry_in_detected", result["degraded_reasons"])

    def test_collect_replay_payload_degrades_when_spot_balance_endpoint_is_unavailable(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_spot_balance.side_effect = aggregator.AggregationInputError(
            "Spot request failed for spot.account.get_account_balance: {'code': -1000, 'msg': 'An unknown error occurred.'}"
        )
        fetcher.fetch_spot_orders.return_value = []
        fetcher.fetch_spot_fills.return_value = []
        fetcher.fetch_spot_bills.return_value = {"items": []}
        fetcher.fetch_spot_klines.return_value = [
            [1710000000000, "64000", "66000", "63500", "65000", "90", 1710003599999, "270000", 80]
        ]
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="spot",
            period="7d",
            symbol="BTCUSDT",
        )

        self.assertTrue(result["partial"])
        self.assertEqual(result["balances"], [])
        self.assertEqual(result["price_series"][0]["close"], 65000.0)
        self.assertIn("spot_balance_unavailable", result["degraded_reasons"])

    def test_collect_replay_payload_degrades_when_spot_klines_are_rate_limited(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_spot_balance.return_value = {
            "balances": [
                {"asset": "USDT", "free": "100", "locked": "0"},
            ]
        }
        fetcher.fetch_spot_orders.return_value = []
        fetcher.fetch_spot_fills.return_value = []
        fetcher.fetch_spot_bills.return_value = {"items": []}
        fetcher.fetch_spot_klines.side_effect = aggregator.AggregationInputError(
            "Spot request failed for spot.market.get_k_line_data: {'code': 429, 'data': {}, 'msg': 'Rate limit exceeded.'}"
        )
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="spot",
            period="30d",
            symbol="BTCUSDT",
        )

        self.assertTrue(result["partial"])
        self.assertEqual(result["price_series"], [])
        self.assertIn("spot_kline_unavailable", result["degraded_reasons"])

    def test_collect_replay_payload_degrades_when_spot_bills_are_rate_limited(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_spot_balance.return_value = {
            "balances": [
                {"asset": "USDT", "free": "100", "locked": "0"},
            ]
        }
        fetcher.fetch_spot_orders.return_value = []
        fetcher.fetch_spot_fills.return_value = []
        fetcher.fetch_spot_bills.side_effect = aggregator.AggregationInputError(
            "Spot request failed for spot.account.get_bill_records: {'code': -1059, 'msg': 'Too many high-frequency order requests in current window'}"
        )
        fetcher.fetch_spot_klines.return_value = [
            [1710000000000, "64000", "66000", "63500", "65000", "90", 1710003599999, "270000", 80]
        ]
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_replay_payload(
            profile_name="demo",
            market="spot",
            period="30d",
            symbol="BTCUSDT",
        )

        self.assertTrue(result["partial"])
        self.assertEqual(result["bills"], [])
        self.assertIn("spot_bills_unavailable", result["degraded_reasons"])

    def test_collect_order_risk_payload_reads_live_pending_orders_for_futures_tp_sl(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = {
            "asset": "USDT",
            "balance": "1000",
            "availableBalance": "500",
        }
        fetcher.fetch_futures_positions.return_value = [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "size": "0.01",
                "openValue": "650",
                "leverage": "10",
            }
        ]
        fetcher.fetch_futures_orders.return_value = []
        fetcher.fetch_futures_pending_orders.return_value = [
            {
                "symbol": "BTCUSDT",
                "positionSide": "LONG",
                "origQty": "0.01",
                "executedQty": "0",
                "tpTriggerPrice": "68000",
                "slTriggerPrice": "62000",
            }
        ]
        fetcher.fetch_futures_klines.return_value = [
            [1710000000000, "64000", "66000", "63500", "65000", "100", 1710003599999, "6500000", 120]
        ]
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_order_risk_payload(
            profile_name="demo",
            market="futures",
            raw_order={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "LIMIT",
                "quantity": 0.01,
                "price": 65000,
            },
        )

        self.assertFalse(result["tp_sl"]["has_take_profit"])
        self.assertFalse(result["tp_sl"]["has_stop_loss"])
        self.assertAlmostEqual(result["tp_sl"]["required_covered_qty"], 0.02)
        self.assertAlmostEqual(result["tp_sl"]["take_profit_covered_qty"], 0.01)
        self.assertAlmostEqual(result["tp_sl"]["stop_loss_covered_qty"], 0.01)

    def test_fetch_futures_open_orders_paginates_all_pages(self) -> None:
        fetcher = aggregator.WeexApiFetcher()

        def fake_send_contract_request(**kwargs):
            query = kwargs["query"]
            page = query["page"]
            if page == 0:
                return {
                    "items": [
                        {
                            "orderId": 100 + index,
                            "clientOrderId": f"page0-{index}",
                            "symbol": "BTCUSDT",
                            "time": 1710000000000 + index,
                        }
                        for index in range(aggregator.FUTURES_OPEN_ORDER_LIMIT)
                    ]
                }
            if page == 1:
                return {
                    "items": [
                        {
                            "orderId": 200,
                            "clientOrderId": "page1-0",
                            "symbol": "BTCUSDT",
                            "time": 1710000100000,
                        }
                    ]
                }
            return {"items": []}

        with mock.patch.object(fetcher, "_send_contract_request", side_effect=fake_send_contract_request) as send_mock:
            rows = fetcher.fetch_futures_open_orders(profile_name="demo", symbol="BTCUSDT")

        self.assertEqual(len(rows), aggregator.FUTURES_OPEN_ORDER_LIMIT + 1)
        self.assertEqual(rows[-1]["orderId"], 200)
        self.assertEqual([call.kwargs["query"]["page"] for call in send_mock.call_args_list], [0, 1])

    def test_collect_order_risk_payload_keeps_partial_live_tp_sl_as_partial_coverage(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = {
            "asset": "USDT",
            "balance": "1000",
            "availableBalance": "500",
        }
        fetcher.fetch_futures_positions.return_value = [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "size": "1.0",
                "openValue": "65000",
                "leverage": "10",
            }
        ]
        fetcher.fetch_futures_orders.return_value = []
        fetcher.fetch_futures_open_orders.return_value = []
        fetcher.fetch_futures_pending_orders.return_value = [
            {
                "symbol": "BTCUSDT",
                "positionSide": "LONG",
                "origQty": "0.1",
                "executedQty": "0",
                "tpTriggerPrice": "68000",
                "slTriggerPrice": "62000",
            }
        ]
        fetcher.fetch_futures_klines.return_value = [
            [1710000000000, "64000", "66000", "63500", "65000", "100", 1710003599999, "6500000", 120]
        ]
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_order_risk_payload(
            profile_name="demo",
            market="futures",
            raw_order={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "LIMIT",
                "quantity": 0.1,
                "price": 65000,
            },
        )

        self.assertFalse(result["tp_sl"]["has_take_profit"])
        self.assertFalse(result["tp_sl"]["has_stop_loss"])
        self.assertAlmostEqual(result["tp_sl"]["required_covered_qty"], 1.1)
        self.assertAlmostEqual(result["tp_sl"]["take_profit_covered_qty"], 0.1)
        self.assertAlmostEqual(result["tp_sl"]["stop_loss_covered_qty"], 0.1)

    def test_collect_order_risk_payload_degrades_when_spot_balance_endpoint_is_unavailable(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_spot_balance.side_effect = aggregator.AggregationInputError(
            "Spot request failed for spot.account.get_account_balance: {'code': -1000, 'msg': 'An unknown error occurred.'}"
        )
        fetcher.fetch_spot_orders.return_value = []
        fetcher.fetch_spot_open_orders.return_value = []
        fetcher.fetch_spot_latest_price.return_value = {
            "symbol": "BTCUSDT",
            "lastPrice": "65000",
        }
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_order_risk_payload(
            profile_name="demo",
            market="spot",
            raw_order={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": 0.01,
                "price": 65000,
            },
        )

        self.assertTrue(result["partial"])
        self.assertIsNone(result["account_snapshot"]["equity"])
        self.assertIsNone(result["account_snapshot"]["available_balance"])
        self.assertEqual(result["market_snapshot"]["current_price"], 65000.0)
        self.assertIn("spot_balance_unavailable", result["degraded_reasons"])
        self.assertIn("spot_tp_sl_state_unavailable", result["degraded_reasons"])

    def test_collect_order_risk_payload_degrades_when_futures_market_snapshot_lookup_fails(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = {
            "asset": "USDT",
            "balance": "1000",
            "availableBalance": "500",
        }
        fetcher.fetch_futures_positions.return_value = []
        fetcher.fetch_futures_orders.return_value = []
        fetcher.fetch_futures_open_orders.return_value = []
        fetcher.fetch_futures_pending_orders.return_value = []
        fetcher.fetch_futures_klines.side_effect = aggregator.AggregationInputError(
            "Contract request failed for market.get_history_klines: {'code': -1142, 'msg': \"Parameter 'symbol' is invalid.\"}"
        )
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_order_risk_payload(
            profile_name="demo",
            market="futures",
            raw_order={
                "symbol": "GOMININGUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "LIMIT",
                "quantity": 50,
                "price": 0.3313,
            },
        )

        self.assertIsNone(result["market_snapshot"]["current_price"])
        self.assertIn("futures_market_snapshot_unavailable", result["degraded_reasons"])

    def test_collect_order_risk_payload_estimates_spot_equity_and_uses_latest_price(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_spot_balance.return_value = {
            "balances": [
                {"asset": "USDT", "free": "100", "locked": "20"},
                {"asset": "BTC", "free": "1.0", "locked": "0.1"},
            ]
        }
        fetcher.fetch_spot_orders.return_value = []
        fetcher.fetch_spot_open_orders.return_value = []
        fetcher.fetch_spot_latest_price.side_effect = lambda *, symbol: {
            "BTCUSDT": {"symbol": "BTCUSDT", "lastPrice": "50000"},
            "ETHUSDT": {"symbol": "ETHUSDT", "lastPrice": "3000"},
        }[symbol]
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_order_risk_payload(
            profile_name="demo",
            market="spot",
            raw_order={
                "symbol": "ETHUSDT",
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": 0.01,
                "price": 2900,
            },
        )

        self.assertAlmostEqual(result["account_snapshot"]["equity"], 55120.0)
        self.assertAlmostEqual(result["account_snapshot"]["available_balance"], 100.0)
        self.assertEqual(result["market_snapshot"]["current_price"], 3000.0)
        fetcher.fetch_spot_klines.assert_not_called()

    def test_collect_account_risk_payload_without_symbol_uses_primary_futures_position_price_anchor(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = {
            "asset": "USDT",
            "balance": "1000",
            "availableBalance": "500",
        }
        fetcher.fetch_futures_positions.return_value = [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "size": "0.01",
                "openValue": "300",
                "leverage": "5",
                "unrealizePnl": "51.2",
            },
            {
                "symbol": "ETHUSDT",
                "side": "LONG",
                "size": "0.5",
                "openValue": "900",
                "leverage": "4",
            },
        ]
        fetcher.fetch_futures_orders.return_value = []
        fetcher.fetch_futures_open_orders.return_value = []
        fetcher.fetch_futures_pending_orders.return_value = []
        fetcher.fetch_futures_latest_price.return_value = {
            "symbol": "ETHUSDT",
            "price": "3150",
        }
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_account_risk_payload(
            profile_name="demo",
            market="futures",
            symbol=None,
        )

        self.assertIsNone(result["symbol"])
        self.assertEqual(result["market_snapshot"]["current_price"], 3150.0)
        self.assertEqual(result["positions"][0]["unrealized_pnl"], 51.2)
        fetcher.fetch_futures_latest_price.assert_called_once_with(symbol="ETHUSDT")

    def test_collect_account_risk_payload_estimates_price_from_position_when_market_lookup_fails(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_futures_balance.return_value = {
            "asset": "USDT",
            "balance": "1000",
            "availableBalance": "500",
        }
        fetcher.fetch_futures_positions.return_value = [
            {
                "symbol": "GOMININGUSDT",
                "side": "LONG",
                "size": "460",
                "openValue": "152.4",
                "leverage": "20",
            }
        ]
        fetcher.fetch_futures_orders.return_value = []
        fetcher.fetch_futures_open_orders.return_value = []
        fetcher.fetch_futures_pending_orders.return_value = []
        fetcher.fetch_futures_latest_price.side_effect = aggregator.AggregationInputError(
            "Contract request failed for market.get_symbol_price: {'code': -1142, 'msg': \"Parameter 'symbol' is invalid.\"}"
        )
        fetcher.fetch_futures_klines.side_effect = aggregator.AggregationInputError(
            "Contract request failed for market.get_history_klines: {'code': -1142, 'msg': \"Parameter 'symbol' is invalid.\"}"
        )
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_account_risk_payload(
            profile_name="demo",
            market="futures",
            symbol=None,
        )

        self.assertAlmostEqual(result["market_snapshot"]["current_price"], 152.4 / 460.0)
        self.assertIn("futures_market_snapshot_estimated_from_position", result["degraded_reasons"])
        self.assertNotIn("futures_market_snapshot_unavailable", result["degraded_reasons"])

    def test_collect_account_risk_payload_degrades_when_spot_balance_endpoint_is_unavailable(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_spot_balance.side_effect = aggregator.AggregationInputError(
            "Spot request failed for spot.account.get_account_balance: {'code': -1000, 'msg': 'An unknown error occurred.'}"
        )
        fetcher.fetch_spot_orders.return_value = []
        fetcher.fetch_spot_open_orders.return_value = []
        fetcher.fetch_spot_latest_price.return_value = {
            "symbol": "BTCUSDT",
            "lastPrice": "65000",
        }
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_account_risk_payload(
            profile_name="demo",
            market="spot",
            symbol="BTCUSDT",
        )

        self.assertTrue(result["partial"])
        self.assertIsNone(result["account_snapshot"]["equity"])
        self.assertIsNone(result["account_snapshot"]["available_balance"])
        self.assertEqual(result["market_snapshot"]["current_price"], 65000.0)
        self.assertIn("spot_balance_unavailable", result["degraded_reasons"])
        self.assertIn("spot_tp_sl_state_unavailable", result["degraded_reasons"])

    def test_collect_account_risk_payload_estimates_spot_positions_from_balances(self) -> None:
        fetcher = mock.Mock()
        fetcher.fetch_spot_balance.return_value = {
            "balances": [
                {"asset": "USDT", "availableBalance": "1000", "locked": "0"},
                {"asset": "BTC", "availableBalance": "0.1", "locked": "0"},
                {"asset": "ETH", "availableBalance": "1.0", "locked": "0"},
            ]
        }
        fetcher.fetch_spot_orders.return_value = []
        fetcher.fetch_spot_open_orders.return_value = []
        fetcher.fetch_spot_latest_price.side_effect = lambda *, symbol: {
            "BTCUSDT": {"symbol": "BTCUSDT", "lastPrice": "65000"},
            "ETHUSDT": {"symbol": "ETHUSDT", "lastPrice": "3000"},
        }[symbol]
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=fetcher)

        result = trade_aggregator.collect_account_risk_payload(
            profile_name="demo",
            market="spot",
            symbol=None,
        )

        self.assertAlmostEqual(result["account_snapshot"]["equity"], 10500.0)
        self.assertAlmostEqual(result["account_snapshot"]["available_balance"], 10500.0)
        self.assertIsNone(result["market_snapshot"]["current_price"])
        self.assertEqual(
            result["positions"],
            [
                {
                    "account_scope": "personal_spot",
                    "market": "spot",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "margin_type": None,
                    "position_mode": "COMBINED",
                    "quantity": 0.1,
                    "notional": 6500.0,
                    "leverage": 1.0,
                    "created_time": 0,
                    "updated_time": 0,
                },
                {
                    "account_scope": "personal_spot",
                    "market": "spot",
                    "symbol": "ETHUSDT",
                    "side": "long",
                    "margin_type": None,
                    "position_mode": "COMBINED",
                    "quantity": 1.0,
                    "notional": 3000.0,
                    "leverage": 1.0,
                    "created_time": 0,
                    "updated_time": 0,
                },
            ],
        )

    def test_fetch_futures_orders_paginates_until_page_is_exhausted(self) -> None:
        fetcher = aggregator.WeexApiFetcher()
        first_page = [{"orderId": index, "symbol": "BTCUSDT"} for index in range(1000)]
        second_page = [{"orderId": 1001, "symbol": "BTCUSDT"}]

        with mock.patch.object(
            fetcher,
            "_send_contract_request",
            side_effect=[
                first_page,
                second_page,
                [],
            ],
        ) as send_mock:
            payload = fetcher.fetch_futures_orders(
                profile_name="demo",
                start_ms=10,
                end_ms=20,
                symbol="BTCUSDT",
            )

        self.assertEqual(len(payload), 1001)
        self.assertEqual(payload[-1], {"orderId": 1001, "symbol": "BTCUSDT"})
        self.assertEqual(send_mock.call_count, 2)

    def test_fetch_futures_fills_splits_requests_into_seven_day_windows(self) -> None:
        fetcher = aggregator.WeexApiFetcher()

        def fake_send_contract_request(*, profile_name: str, endpoint_key: str, query: dict[str, object]) -> list[dict[str, object]]:
            self.assertEqual(endpoint_key, "transaction.get_trade_details")
            self.assertLessEqual(
                int(query["endTime"]) - int(query["startTime"]),
                (7 * aggregator.DAY_MS) - 1,
            )
            return []

        with mock.patch.object(
            fetcher,
            "_send_contract_request",
            side_effect=fake_send_contract_request,
        ) as send_mock:
            payload = fetcher.fetch_futures_fills(
                profile_name="demo",
                start_ms=0,
                end_ms=30 * aggregator.DAY_MS,
                symbol="BTCUSDT",
            )

        self.assertEqual(payload, [])
        self.assertGreater(send_mock.call_count, 1)

    def test_fetch_spot_fills_splits_large_windows_before_requesting_history(self) -> None:
        fetcher = aggregator.WeexApiFetcher()
        queries: list[dict[str, object]] = []

        def fake_send_spot_request(*, profile_name: str, endpoint_key: str, query: dict[str, object], **_: object) -> dict[str, object]:
            self.assertEqual(profile_name, "demo")
            self.assertEqual(endpoint_key, "spot.order.transaction_details")
            queries.append(dict(query))
            return {"items": []}

        with mock.patch.object(
            fetcher,
            "_send_spot_request",
            side_effect=fake_send_spot_request,
        ):
            payload = fetcher.fetch_spot_fills(
                profile_name="demo",
                start_ms=0,
                end_ms=(181 * aggregator.DAY_MS) - 1,
                symbol="BTCUSDT",
            )

        self.assertEqual(payload, [])
        self.assertGreater(len(queries), 1)
        for query in queries:
            self.assertLessEqual(
                int(query["endTime"]) - int(query["startTime"]),
                (90 * aggregator.DAY_MS) - 1,
            )

    def test_fetch_spot_bills_splits_large_windows_before_requesting_history(self) -> None:
        fetcher = aggregator.WeexApiFetcher()
        bodies: list[dict[str, object]] = []

        def fake_send_spot_request(
            *,
            profile_name: str,
            endpoint_key: str,
            query: dict[str, object],
            body: dict[str, object] | None = None,
            **_: object,
        ) -> dict[str, object]:
            self.assertEqual(profile_name, "demo")
            self.assertEqual(endpoint_key, "spot.account.get_bill_records")
            self.assertEqual(query, {})
            self.assertIsNotNone(body)
            bodies.append(dict(body or {}))
            return {"items": []}

        with mock.patch.object(
            fetcher,
            "_send_spot_request",
            side_effect=fake_send_spot_request,
        ):
            payload = fetcher.fetch_spot_bills(
                profile_name="demo",
                start_ms=0,
                end_ms=(181 * aggregator.DAY_MS) - 1,
                symbol="BTCUSDT",
            )

        self.assertEqual(payload, [])
        self.assertGreater(len(bodies), 1)
        for body in bodies:
            self.assertLessEqual(
                int(body["before"]) - int(body["after"]),
                (90 * aggregator.DAY_MS) - 1,
            )


class ProfileCollectionTests(unittest.TestCase):
    def test_collect_profile_payload_falls_back_until_sample_is_not_minimal(self) -> None:
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=mock.Mock())
        replay_payloads = [
            {
                "period": "30d",
                "market": "futures",
                "closed_trade_count": 5,
                "fills": [{"symbol": "BTCUSDT"}],
            },
            {
                "period": "90d",
                "market": "futures",
                "closed_trade_count": 12,
                "fills": [{"symbol": "ETHUSDT"}],
            },
        ]

        with mock.patch.object(
            trade_aggregator,
            "collect_replay_payload",
            side_effect=replay_payloads,
        ) as collect_mock:
            result = trade_aggregator.collect_profile_payload(
                profile_name="demo",
                market="futures",
            )

        self.assertEqual(collect_mock.call_count, 2)
        self.assertEqual(result["selected_period"], "90d")
        self.assertTrue(result["fallback_applied"])
        self.assertEqual(result["closed_trade_count"], 12)
        self.assertEqual(result["fills"][0]["symbol"], "ETHUSDT")
        self.assertEqual(result["sample_quality"], "limited")

    def test_collect_profile_payload_ignores_carry_in_only_counts_for_sample_gate(self) -> None:
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=mock.Mock())
        replay_payloads = [
            {
                "period": "30d",
                "market": "futures",
                "closed_trade_count": 10,
                "reconstructed_closed_trade_count": 0,
                "partial": True,
                "degraded_reasons": ["replay_carry_in_detected"],
                "fills": [{"symbol": "BTCUSDT"}],
            },
            {
                "period": "90d",
                "market": "futures",
                "closed_trade_count": 12,
                "reconstructed_closed_trade_count": 12,
                "fills": [{"symbol": "ETHUSDT"}],
            },
        ]

        with mock.patch.object(
            trade_aggregator,
            "collect_replay_payload",
            side_effect=replay_payloads,
        ) as collect_mock:
            result = trade_aggregator.collect_profile_payload(
                profile_name="demo",
                market="futures",
            )

        self.assertEqual(collect_mock.call_count, 2)
        self.assertEqual(result["selected_period"], "90d")
        self.assertEqual(result["closed_trade_count"], 12)
        self.assertEqual(result["sample_quality"], "limited")

    def test_collect_profile_payload_returns_last_window_when_all_candidates_are_minimal(self) -> None:
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=mock.Mock())
        replay_payloads = [
            {"period": "30d", "market": "futures", "closed_trade_count": 2, "fills": []},
            {"period": "90d", "market": "futures", "closed_trade_count": 4, "fills": []},
            {"period": "180d", "market": "futures", "closed_trade_count": 5, "fills": []},
            {"period": "360d", "market": "futures", "closed_trade_count": 6, "fills": [{"symbol": "ETHUSDT"}]},
        ]

        with mock.patch.object(
            trade_aggregator,
            "collect_replay_payload",
            side_effect=replay_payloads,
        ) as collect_mock:
            result = trade_aggregator.collect_profile_payload(
                profile_name="demo",
                market="futures",
            )

        self.assertEqual(collect_mock.call_count, 4)
        self.assertEqual(result["selected_period"], "360d")
        self.assertTrue(result["fallback_applied"])
        self.assertEqual(result["closed_trade_count"], 6)
        self.assertEqual(result["sample_quality"], "minimal")

    def test_collect_profile_payload_keeps_sample_quality_but_leaves_metrics_to_analysis_skill(self) -> None:
        trade_aggregator = aggregator.TradeDataAggregator(fetcher=mock.Mock())
        replay_payloads = [
            {
                "period": "30d",
                "market": "futures",
                "closed_trade_count": 12,
                "orders": [
                    {"order_id": "o1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                    {"order_id": "o1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "reduce_only": True, "quantity": 0.01, "time": 1710001800000, "update_time": 1710001800000},
                    {"order_id": "o2-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.02, "time": 1710086400000, "update_time": 1710086400000},
                    {"order_id": "o2-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "reduce_only": True, "quantity": 0.02, "time": 1710088200000, "update_time": 1710088200000},
                    {"order_id": "o3-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.03, "time": 1710172800000, "update_time": 1710172800000},
                    {"order_id": "o3-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "reduce_only": True, "quantity": 0.03, "time": 1710176400000, "update_time": 1710176400000},
                ],
                "fills": [
                    {"order_id": "o1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 0, "time": 1710000000000},
                    {"order_id": "o1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65200, "realized_pnl": 12, "fee": 0, "time": 1710001800000},
                    {"order_id": "o2-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.02, "price": 65300, "realized_pnl": 0, "fee": 0, "time": 1710086400000},
                    {"order_id": "o2-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.02, "price": 65100, "realized_pnl": -6, "fee": 0, "time": 1710088200000},
                    {"order_id": "o3-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.03, "price": 65400, "realized_pnl": 0, "fee": 0, "time": 1710172800000},
                    {"order_id": "o3-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.03, "price": 65700, "realized_pnl": 18, "fee": 0, "time": 1710176400000},
                ],
                "positions": [],
                "balances": [],
                "bills": [],
                "price_series": [],
                "constraints": [],
            }
        ]

        with mock.patch.object(
            trade_aggregator,
            "collect_replay_payload",
            side_effect=replay_payloads,
        ):
            result = trade_aggregator.collect_profile_payload(
                profile_name="demo",
                market="futures",
            )

        self.assertEqual(result["sample_quality"], "limited")
        self.assertEqual(result["selected_period"], "30d")
        self.assertNotIn("metrics", result)

class AggregatorCliTests(unittest.TestCase):
    def test_cmd_collect_replay_pretty_prints_json(self) -> None:
        args = mock.Mock(
            profile="demo",
            market="futures",
            period="7d",
            symbol="BTCUSDT",
            focus=None,
            pretty=True,
        )
        payload = {"analysis_type": "replay", "market": "futures", "closed_trade_count": 1}

        aggregator_instance = mock.Mock()
        aggregator_instance.collect_replay_payload.return_value = payload

        with mock.patch.object(aggregator, "TradeDataAggregator", return_value=aggregator_instance):
            stream = io.StringIO()
            with mock.patch.object(sys, "stdout", stream):
                exit_code = aggregator.cmd_collect_replay(args)

        self.assertEqual(exit_code, 0)
        self.assertIn('"analysis_type": "replay"', stream.getvalue())

    def test_cmd_collect_profile_uses_selected_period_payload(self) -> None:
        args = mock.Mock(
            profile="demo",
            market="futures",
            symbol=None,
            pretty=False,
        )
        payload = {"analysis_type": "profile", "selected_period": "90d", "fallback_applied": True}

        aggregator_instance = mock.Mock()
        aggregator_instance.collect_profile_payload.return_value = payload

        with mock.patch.object(aggregator, "TradeDataAggregator", return_value=aggregator_instance):
            stream = io.StringIO()
            with mock.patch.object(sys, "stdout", stream):
                exit_code = aggregator.cmd_collect_profile(args)

        self.assertEqual(exit_code, 0)
        self.assertIn('"selected_period": "90d"', stream.getvalue())

    def test_cmd_collect_order_risk_pretty_prints_raw_payload(self) -> None:
        args = mock.Mock(
            profile="demo",
            market="futures",
            order_json='{"symbol":"BTCUSDT","side":"BUY","position_side":"LONG","order_type":"LIMIT","quantity":0.01,"price":65000}',
            pretty=True,
        )
        payload = {"order_preview": {"symbol": "BTCUSDT"}, "market_snapshot": {"current_price": 65000}}

        aggregator_instance = mock.Mock()
        aggregator_instance.collect_order_risk_payload.return_value = payload

        with mock.patch.object(aggregator, "TradeDataAggregator", return_value=aggregator_instance):
            stream = io.StringIO()
            with mock.patch.object(sys, "stdout", stream):
                exit_code = aggregator.cmd_collect_order_risk(args)

        self.assertEqual(exit_code, 0)
        self.assertIn('"order_preview"', stream.getvalue())
        self.assertIn('"current_price": 65000', stream.getvalue())

    def test_cmd_collect_account_risk_pretty_prints_raw_payload(self) -> None:
        args = mock.Mock(
            profile="demo",
            market="futures",
            symbol="BTCUSDT",
            pretty=True,
        )
        payload = {"market": "futures", "account_snapshot": {"equity": 1000}}

        aggregator_instance = mock.Mock()
        aggregator_instance.collect_account_risk_payload.return_value = payload

        with mock.patch.object(aggregator, "TradeDataAggregator", return_value=aggregator_instance):
            stream = io.StringIO()
            with mock.patch.object(sys, "stdout", stream):
                exit_code = aggregator.cmd_collect_account_risk(args)

        self.assertEqual(exit_code, 0)
        self.assertIn('"account_snapshot"', stream.getvalue())
        self.assertIn('"market": "futures"', stream.getvalue())

    def test_main_returns_structured_json_when_aggregation_fails(self) -> None:
        aggregator_instance = mock.Mock()
        aggregator_instance.collect_account_risk_payload.side_effect = aggregator.AggregationInputError(
            "Spot request failed for spot.account.get_account_balance: {'code': -1000, 'msg': 'An unknown error occurred.'}"
        )

        with mock.patch.object(aggregator, "TradeDataAggregator", return_value=aggregator_instance):
            stream = io.StringIO()
            with mock.patch.object(sys, "stdout", stream):
                exit_code = aggregator.main(["collect-account-risk", "--profile", "demo", "--market", "spot"])

        self.assertEqual(exit_code, 1)
        self.assertIn('"ok": false', stream.getvalue().lower())
        self.assertIn("spot.account.get_account_balance", stream.getvalue())


class ApiFetcherTests(unittest.TestCase):
    def test_fetch_futures_orders_uses_contract_order_history_endpoint(self) -> None:
        fetcher = aggregator.WeexApiFetcher()

        with mock.patch.object(fetcher, "_send_contract_request", return_value=[]) as send_mock:
            fetcher.fetch_futures_orders(
                profile_name="demo",
                start_ms=10,
                end_ms=20,
                symbol="BTCUSDT",
            )

        send_mock.assert_called_once_with(
            profile_name="demo",
            endpoint_key="transaction.get_order_history",
            query={
                "symbol": "BTCUSDT",
                "startTime": 10,
                "endTime": 20,
                "limit": 1000,
                "page": 0,
            },
        )

    def test_fetch_spot_balance_uses_spot_account_balance_endpoint(self) -> None:
        fetcher = aggregator.WeexApiFetcher()

        with mock.patch.object(fetcher, "_send_spot_request", return_value={"balances": []}) as send_mock:
            payload = fetcher.fetch_spot_balance(profile_name="demo")

        self.assertEqual(payload, {"balances": []})
        send_mock.assert_called_once_with(
            profile_name="demo",
            endpoint_key="spot.account.get_account_balance",
            query={},
        )

    def test_fetch_spot_latest_price_uses_spot_ticker_price_endpoint(self) -> None:
        fetcher = aggregator.WeexApiFetcher()

        with mock.patch.object(fetcher, "_send_spot_request", return_value={"symbol": "BTCUSDT", "lastPrice": "65000"}) as send_mock:
            payload = fetcher.fetch_spot_latest_price(symbol="BTCUSDT")

        self.assertEqual(payload, {"symbol": "BTCUSDT", "lastPrice": "65000"})
        send_mock.assert_called_once_with(
            profile_name="",
            endpoint_key="spot.market.get_ticker_info",
            query={"symbol": "BTCUSDT"},
            public=True,
        )

    def test_fetch_futures_latest_price_uses_contract_symbol_price_endpoint(self) -> None:
        fetcher = aggregator.WeexApiFetcher()

        with mock.patch.object(fetcher, "_send_contract_request", return_value={"symbol": "BTCUSDT", "price": "65000"}) as send_mock:
            payload = fetcher.fetch_futures_latest_price(symbol="BTCUSDT")

        self.assertEqual(payload, {"symbol": "BTCUSDT", "price": "65000"})
        send_mock.assert_called_once_with(
            profile_name="",
            endpoint_key="market.get_symbol_price",
            query={"symbol": "BTCUSDT", "priceType": "MARK"},
            public=True,
        )

    def test_fetch_spot_orders_retries_with_conservative_limit_after_stg_unknown_error(self) -> None:
        fetcher = aggregator.WeexApiFetcher()
        row = {
            "symbol": "BTCUSDT",
            "orderId": 41,
            "clientOrderId": "spot-1",
            "time": 1710000000000,
        }

        with mock.patch.object(
            fetcher,
            "_send_spot_request",
            side_effect=[
                aggregator.AggregationInputError(
                    "Spot request failed for spot.order.history_orders: {'code': -1000, 'msg': 'An unknown error occurred.'}"
                ),
                {"items": [row]},
            ],
        ) as send_mock:
            payload = fetcher.fetch_spot_orders(
                profile_name="demo",
                start_ms=10,
                end_ms=20,
                symbol="BTCUSDT",
            )

        self.assertEqual(payload, [row])
        self.assertEqual(send_mock.call_count, 2)
        self.assertEqual(send_mock.call_args_list[0].kwargs["query"]["limit"], 1000)
        self.assertEqual(send_mock.call_args_list[1].kwargs["query"]["limit"], 100)

    def test_fetch_spot_orders_splits_large_windows_before_requesting_history(self) -> None:
        fetcher = aggregator.WeexApiFetcher()
        queries: list[dict[str, object]] = []

        def fake_send_spot_request(*, profile_name: str, endpoint_key: str, query: dict[str, object], **_: object) -> dict[str, object]:
            self.assertEqual(profile_name, "demo")
            self.assertEqual(endpoint_key, "spot.order.history_orders")
            queries.append(dict(query))
            return {"items": []}

        with mock.patch.object(fetcher, "_send_spot_request", side_effect=fake_send_spot_request):
            payload = fetcher.fetch_spot_orders(
                profile_name="demo",
                start_ms=0,
                end_ms=(181 * aggregator.DAY_MS) - 1,
                symbol="BTCUSDT",
            )

        self.assertEqual(payload, [])
        self.assertGreater(len(queries), 1)
        for query in queries:
            self.assertLessEqual(
                int(query["endTime"]) - int(query["startTime"]),
                (90 * aggregator.DAY_MS) - 1,
            )


if __name__ == "__main__":
    unittest.main()
