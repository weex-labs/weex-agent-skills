#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import weex_analysis_prepare as prep  # noqa: E402


def _make_replay_payload() -> dict[str, object]:
    return {
        "analysis_type": "replay",
        "market": "futures",
        "period": "30d",
        "symbol": "BTCUSDT",
        "focus": "losses",
        "time_range": {
            "start_ms": 1710000000000,
            "end_ms": 1710001800000,
        },
        "partial": False,
        "degraded_reasons": ["orders_window_truncated"],
        "constraints": [
            {
                "code": "spot_symbol_required",
                "message": "spot history is only collected when symbol is provided.",
            }
        ],
        "orders": [
            {
                "order_id": "btc-open",
                "account_scope": "personal_futures",
                "market": "futures",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "position_mode": "COMBINED",
                "margin_type": "CROSSED",
                "quantity": 0.01,
                "time": 1710000000000,
                "update_time": 1710000000000,
            },
            {
                "order_id": "btc-close",
                "account_scope": "personal_futures",
                "market": "futures",
                "symbol": "BTCUSDT",
                "side": "SELL",
                "position_side": "LONG",
                "position_mode": "COMBINED",
                "margin_type": "CROSSED",
                "quantity": 0.01,
                "reduce_only": True,
                "time": 1710000600000,
                "update_time": 1710000600000,
            },
            {
                "order_id": "eth-open",
                "account_scope": "personal_futures",
                "market": "futures",
                "symbol": "ETHUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "position_mode": "COMBINED",
                "margin_type": "CROSSED",
                "quantity": 0.1,
                "time": 1710001200000,
                "update_time": 1710001200000,
            },
            {
                "order_id": "eth-close",
                "account_scope": "personal_futures",
                "market": "futures",
                "symbol": "ETHUSDT",
                "side": "SELL",
                "position_side": "LONG",
                "position_mode": "COMBINED",
                "margin_type": "CROSSED",
                "quantity": 0.1,
                "reduce_only": True,
                "time": 1710001800000,
                "update_time": 1710001800000,
            },
        ],
        "fills": [
            {
                "order_id": "btc-open",
                "account_scope": "personal_futures",
                "market": "futures",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "position_mode": "COMBINED",
                "price": 65000,
                "quantity": 0.01,
                "realized_pnl": 0,
                "fee": 1.0,
                "time": 1710000000000,
            },
            {
                "order_id": "btc-close",
                "account_scope": "personal_futures",
                "market": "futures",
                "symbol": "BTCUSDT",
                "side": "SELL",
                "position_side": "LONG",
                "position_mode": "COMBINED",
                "price": 65200,
                "quantity": 0.01,
                "realized_pnl": 12,
                "fee": 1.0,
                "time": 1710000600000,
            },
            {
                "order_id": "eth-open",
                "account_scope": "personal_futures",
                "market": "futures",
                "symbol": "ETHUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "position_mode": "COMBINED",
                "price": 3200,
                "quantity": 0.1,
                "realized_pnl": 0,
                "fee": 0.5,
                "time": 1710001200000,
            },
            {
                "order_id": "eth-close",
                "account_scope": "personal_futures",
                "market": "futures",
                "symbol": "ETHUSDT",
                "side": "SELL",
                "position_side": "LONG",
                "position_mode": "COMBINED",
                "price": 3250,
                "quantity": 0.1,
                "realized_pnl": 5,
                "fee": 0.5,
                "time": 1710001800000,
            },
        ],
        "balances": [{"equity": 1000}],
        "positions": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}],
        "bills": [{"id": "bill-1", "symbol": "BTCUSDT", "time": 1710000000000}, {"id": "bill-2", "symbol": "ETHUSDT", "time": 1710001800000}],
        "price_series": [
            {"symbol": "BTCUSDT", "time": 1710000000000, "close": 65000},
            {"symbol": "ETHUSDT", "time": 1710001800000, "close": 3250},
        ],
    }


class PrepareReplayTests(unittest.TestCase):
    def test_prepare_replay_filters_symbols_across_context_collections(self) -> None:
        result = prep.prepare_replay_payload(
            _make_replay_payload(),
            symbols={"BTCUSDT"},
        )

        self.assertEqual(result["analysis_type"], "replay")
        self.assertEqual(result["closed_trade_count"], 1)
        self.assertFalse(result["partial"])
        self.assertEqual(
            [item["symbol"] for item in result["orders"]],
            ["BTCUSDT", "BTCUSDT"],
        )
        self.assertEqual(
            [item["symbol"] for item in result["fills"]],
            ["BTCUSDT", "BTCUSDT"],
        )
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["focus"], "losses")
        self.assertEqual(
            result["time_range"],
            {
                "start_ms": 1710000000000,
                "end_ms": 1710001800000,
            },
        )
        self.assertEqual(result["balances"], [{"equity": 1000}])
        self.assertEqual(result["positions"], [{"symbol": "BTCUSDT"}])
        self.assertEqual(result["bills"], [{"id": "bill-1", "symbol": "BTCUSDT", "time": 1710000000000}])
        self.assertEqual(
            result["price_series"],
            [{"symbol": "BTCUSDT", "time": 1710000000000, "close": 65000}],
        )
        self.assertEqual(result["degraded_reasons"], ["orders_window_truncated"])
        constraint_codes = [item["code"] for item in result["constraints"]]
        self.assertIn("spot_symbol_required", constraint_codes)
        self.assertIn("analysis_symbol_filter_applied", constraint_codes)

    def test_prepare_replay_preserves_trader_environment_context(self) -> None:
        payload = _make_replay_payload()
        payload["trading_mode"] = "demo"
        payload["environment"] = {
            "trading_mode": "demo",
            "label": "demo",
            "market": "futures",
            "uses_real_funds": False,
            "notice": "This operation targets WEEX futures demo mode.",
        }
        payload["account_scope"] = "sim_futures"

        result = prep.prepare_replay_payload(payload, symbols={"BTCUSDT"})

        self.assertEqual(result["trading_mode"], "demo")
        self.assertEqual(result["environment"], payload["environment"])
        self.assertEqual(result["account_scope"], "sim_futures")

    def test_prepare_replay_account_scope_filter_inherits_top_level_scope(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "account_scope": "sim_futures",
            "orders": [
                {
                    "order_id": "btc-open",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "quantity": 0.01,
                    "time": 1710000000000,
                }
            ],
            "fills": [
                {
                    "order_id": "btc-open",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "quantity": 0.01,
                    "price": 65000,
                    "time": 1710000000000,
                }
            ],
        }

        result = prep.prepare_replay_payload(payload, account_scopes={"sim_futures"})

        self.assertEqual([item["order_id"] for item in result["orders"]], ["btc-open"])
        self.assertEqual([item["order_id"] for item in result["fills"]], ["btc-open"])
        self.assertEqual(result["account_scope"], "sim_futures")

    def test_prepare_replay_sets_top_level_symbol_when_single_symbol_filter_is_applied(self) -> None:
        payload = _make_replay_payload()
        payload["symbol"] = None

        result = prep.prepare_replay_payload(
            payload,
            symbols={"BTCUSDT"},
        )

        self.assertEqual(result["symbol"], "BTCUSDT")

    def test_prepare_replay_marks_truncation_as_partial_and_keeps_recent_rows(self) -> None:
        result = prep.prepare_replay_payload(
            _make_replay_payload(),
            max_orders=2,
            max_fills=2,
        )

        self.assertTrue(result["partial"])
        self.assertEqual(
            [item["order_id"] for item in result["orders"]],
            ["eth-open", "eth-close"],
        )
        self.assertEqual(
            [item["order_id"] for item in result["fills"]],
            ["eth-open", "eth-close"],
        )
        self.assertEqual(result["closed_trade_count"], 1)
        self.assertIn("analysis_orders_truncated", result["degraded_reasons"])
        self.assertIn("analysis_fills_truncated", result["degraded_reasons"])

    def test_prepare_replay_time_filter_preserves_overlapping_price_series(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "orders": [],
            "fills": [],
            "price_series": [
                {
                    "symbol": "BTCUSDT",
                    "open_time": 1709996400000,
                    "close_time": 1709999999999,
                    "close": 64980,
                },
                {
                    "symbol": "BTCUSDT",
                    "open_time": 1710000000000,
                    "close_time": 1710003599999,
                    "close": 65000,
                },
                {
                    "symbol": "BTCUSDT",
                    "open_time": 1710003600000,
                    "close_time": 1710007199999,
                    "close": 65100,
                },
                {
                    "symbol": "BTCUSDT",
                    "open_time": 1710007200000,
                    "close_time": 1710010799999,
                    "close": 65200,
                },
            ],
        }

        result = prep.prepare_replay_payload(
            payload,
            start_time_ms=1710000000000,
            end_time_ms=1710007199999,
        )

        self.assertEqual(
            [item["close"] for item in result["price_series"]],
            [65000, 65100],
        )

    def test_prepare_replay_time_filter_preserves_positions_with_normalized_times(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "orders": [],
            "fills": [],
            "positions": [
                {
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quantity": 0.01,
                    "created_time": 1710000000000,
                    "updated_time": 1710003600000,
                },
                {
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "ETHUSDT",
                    "side": "long",
                    "quantity": 0.5,
                    "created_time": 1709990000000,
                    "updated_time": 1709993600000,
                },
            ],
        }

        result = prep.prepare_replay_payload(
            payload,
            start_time_ms=1710000000000,
            end_time_ms=1710007200000,
        )

        self.assertEqual([item["symbol"] for item in result["positions"]], ["BTCUSDT"])

    def test_prepare_replay_surfaces_partial_reconstruction_reasons(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "orders": [
                {
                    "order_id": "carry-close",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.01,
                    "reduce_only": True,
                    "time": 1710001800000,
                    "update_time": 1710001800000,
                }
            ],
            "fills": [
                {
                    "order_id": "carry-close",
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.01,
                    "price": 65200,
                    "realized_pnl": -5,
                    "fee": 1.0,
                    "time": 1710001800000,
                }
            ],
        }

        result = prep.prepare_replay_payload(payload)

        self.assertTrue(result["partial"])
        self.assertIn("replay_carry_in_detected", result["degraded_reasons"])

    def test_prepare_replay_cli_reads_stdin_and_pretty_prints_json(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "weex_analysis_prepare.py"),
                "prepare-replay",
                "--input",
                "-",
                "--symbol",
                "BTCUSDT",
                "--pretty",
            ],
            cwd=ROOT,
            input=json.dumps(_make_replay_payload()),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["closed_trade_count"], 1)
        self.assertEqual(len(result["orders"]), 2)
        self.assertEqual(len(result["fills"]), 2)


if __name__ == "__main__":
    unittest.main()
