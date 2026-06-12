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

import weex_analysis_cli as analysis  # noqa: E402


EXPECTED_STANDARD_DISCLAIMER = (
    "Disclaimer: This result is generated solely from the current input data and is for reference only. "
    "It does not constitute any investment or trading advice. Please make your own independent judgment "
    "based on real-time data, official rules, and your own risk tolerance. Responsibility for related "
    "decisions and execution rests solely with the user."
)


class DisclaimerTests(unittest.TestCase):
    def test_public_analysis_entry_points_attach_standard_disclaimer(self) -> None:
        results = [
            analysis.analyze_snapshot({}),
            analysis.analyze_fills({"fills": []}),
            analysis.analyze_replay({"orders": [], "fills": []}),
            analysis.review_trades({"orders": [], "fills": []}),
            analysis.analyze_profile(
                {
                    "analysis_type": "profile",
                    "selected_period": "30d",
                    "closed_trade_count": 0,
                    "sample_quality": "minimal",
                    "metrics": {},
                }
            ),
            analysis.analyze_order_risk(
                {
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
            ),
            analysis.analyze_account_risk(
                {
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
            ),
        ]

        for result in results:
            self.assertEqual(result["disclaimer"], EXPECTED_STANDARD_DISCLAIMER)

    def test_cli_json_output_includes_standard_disclaimer(self) -> None:
        payload = {
            "equity": 500,
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quantity": 0.005,
                    "entry_price": 60000,
                    "mark_price": 61000,
                }
            ],
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-snapshot", "--input", "-", "--pretty"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["disclaimer"], EXPECTED_STANDARD_DISCLAIMER)

    def test_account_risk_analysis_preserves_and_renders_trading_environment(self) -> None:
        payload = {
            "trading_mode": "demo",
            "environment": {
                "trading_mode": "demo",
                "label": "demo",
                "market": "futures",
                "uses_real_funds": False,
                "notice": "This operation targets WEEX futures demo mode.",
            },
            "account_scope": "sim_futures",
            "mode": "account_scan",
            "market": "futures",
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 900,
                "account_scope": "sim_futures",
            },
            "positions": [],
            "recent_orders": [],
            "conditional_orders": [],
            "open_orders": [],
            "degraded_reasons": ["demo_futures_open_orders_unavailable"],
            "constraints": [],
        }

        result = analysis.analyze_account_risk(payload)
        text = analysis._render_text(result)

        self.assertEqual(result["trading_mode"], "demo")
        self.assertEqual(result["environment"]["trading_mode"], "demo")
        self.assertEqual(result["environment"]["market"], "futures")
        self.assertEqual(result["account_scope"], "sim_futures")
        self.assertIn("Trading Mode: demo trading", text)
        self.assertNotIn("Trading Environment:", text)
        self.assertNotIn("Trading Mode: demo\n", text)
        self.assertIn("Uses Real Funds: false", text)
        self.assertIn("demo_futures_open_orders_unavailable", text)

    def test_text_output_renders_top_level_trading_mode_without_environment_object(self) -> None:
        payload = {
            "trading_mode": "demo",
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "notional": 1000,
                }
            ],
            "equity": 10000,
            "available_balance": 8000,
        }

        result = analysis.analyze_snapshot(payload)
        text = analysis._render_text(result)

        self.assertEqual(result["trading_mode"], "demo")
        self.assertIn("Trading Mode: demo trading", text)

    def test_text_output_uses_chinese_trading_mode_label_when_language_is_zh(self) -> None:
        payload = {
            "language": "zh",
            "trading_mode": "demo",
            "environment": {
                "trading_mode": "demo",
                "market": "futures",
                "uses_real_funds": False,
            },
            "positions": [],
        }

        result = analysis.analyze_snapshot(payload)
        text = analysis._render_text(result)

        self.assertIn("Trading Mode: 模拟盘", text)
        self.assertNotIn("Trading Mode: demo trading", text)


class SnapshotAnalysisTests(unittest.TestCase):
    def test_analyze_snapshot_reports_concentration_and_collateral_risk(self) -> None:
        payload = {
            "equity": 1000,
            "available_balance": 120,
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quantity": 0.02,
                    "entry_price": 60000,
                    "mark_price": 65000,
                    "leverage": 25,
                },
                {
                    "symbol": "ETHUSDT",
                    "side": "short",
                    "quantity": 0.1,
                    "entry_price": 3200,
                    "mark_price": 3100,
                    "leverage": 8,
                },
            ],
        }

        result = analysis.analyze_snapshot(payload)
        codes = {flag["code"] for flag in result["risk_flags"]}

        self.assertEqual(result["positions_count"], 2)
        self.assertAlmostEqual(result["gross_notional"], 1610.0)
        self.assertAlmostEqual(result["long_notional"], 1300.0)
        self.assertAlmostEqual(result["short_notional"], 310.0)
        self.assertAlmostEqual(result["gross_leverage_estimate"], 1.61)
        self.assertAlmostEqual(result["free_balance_ratio"], 0.12)
        self.assertEqual(result["largest_position"]["symbol"], "BTCUSDT")
        self.assertIn("concentration", codes)
        self.assertIn("low_free_balance", codes)
        self.assertIn("high_position_leverage", codes)

    def test_analyze_snapshot_skips_concentration_when_total_exposure_is_tiny_vs_equity(self) -> None:
        payload = {
            "equity": 40115.22652044,
            "available_balance": 39974.28914506,
            "positions": [
                {
                    "symbol": "GOMININGUSDT",
                    "side": "long",
                    "quantity": 460,
                    "notional": 152.4,
                    "mark_price": 152.4 / 460,
                    "leverage": 20,
                }
            ],
        }

        result = analysis.analyze_snapshot(payload)
        codes = {flag["code"] for flag in result["risk_flags"]}

        self.assertAlmostEqual(result["gross_leverage_estimate"], 152.4 / 40115.22652044)
        self.assertNotIn("concentration", codes)
        self.assertIn("high_position_leverage", codes)

    def test_analyze_snapshot_reads_nested_account_snapshot_balances(self) -> None:
        payload = {
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 120,
            },
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quantity": 0.02,
                    "entry_price": 60000,
                    "mark_price": 65000,
                    "leverage": 25,
                }
            ],
        }

        result = analysis.analyze_snapshot(payload)
        codes = {flag["code"] for flag in result["risk_flags"]}

        self.assertEqual(result["positions_count"], 1)
        self.assertAlmostEqual(result["equity"], 1000.0)
        self.assertAlmostEqual(result["available_balance"], 120.0)
        self.assertAlmostEqual(result["gross_leverage_estimate"], 1.3)
        self.assertIn("concentration", codes)
        self.assertIn("low_free_balance", codes)
        self.assertIn("high_position_leverage", codes)

    def test_analyze_snapshot_marks_empty_payload_as_no_positions(self) -> None:
        result = analysis.analyze_snapshot({})

        self.assertEqual(result["positions_count"], 0)
        self.assertEqual(result["risk_flags"][0]["code"], "no_positions")

    def test_analyze_snapshot_preserves_partial_context_from_payload(self) -> None:
        payload = {
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 800,
            },
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quantity": 0.01,
                    "entry_price": 60000,
                    "mark_price": 65000,
                    "leverage": 5,
                }
            ],
            "partial": True,
            "degraded_reasons": ["snapshot_source_partial"],
            "constraints": [
                {
                    "code": "snapshot_filter_applied",
                    "message": "Prepared payload keeps only BTCUSDT positions.",
                }
            ],
        }

        result = analysis.analyze_snapshot(payload)

        self.assertTrue(result["partial"])
        self.assertEqual(result["degraded_reasons"], ["snapshot_source_partial"])
        self.assertEqual(result["constraints"][0]["code"], "snapshot_filter_applied")

    def test_analyze_snapshot_marks_missing_risk_fields_as_partial(self) -> None:
        payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quantity": 0.01,
                }
            ],
        }

        result = analysis.analyze_snapshot(payload)

        self.assertTrue(result["partial"])
        self.assertIn("snapshot_missing_equity", result["degraded_reasons"])
        self.assertIn("snapshot_missing_available_balance", result["degraded_reasons"])
        self.assertIn("snapshot_position_missing_mark_price", result["degraded_reasons"])
        self.assertIn("snapshot_position_missing_leverage", result["degraded_reasons"])


class FillAnalysisTests(unittest.TestCase):
    def test_analyze_fills_aggregates_realized_pnl_and_fees(self) -> None:
        payload = {
            "fills": [
                {
                    "symbol": "ETHUSDT",
                    "side": "buy",
                    "quantity": 0.5,
                    "price": 3000,
                    "realized_pnl": 0,
                    "fee": 1.2,
                },
                {
                    "symbol": "ETHUSDT",
                    "side": "sell",
                    "quantity": 0.5,
                    "price": 3080,
                    "realized_pnl": 40,
                    "fee": 1.4,
                },
                {
                    "symbol": "BTCUSDT",
                    "side": "sell",
                    "quantity": 0.01,
                    "price": 64000,
                    "realized_pnl": -12,
                    "fee": 0.8,
                },
            ]
        }

        result = analysis.analyze_fills(payload)

        self.assertEqual(result["fills_count"], 3)
        self.assertEqual(result["symbols"], ["BTCUSDT", "ETHUSDT"])
        self.assertAlmostEqual(result["turnover"], 3680.0)
        self.assertAlmostEqual(result["realized_pnl"], 28.0)
        self.assertAlmostEqual(result["fees"], 3.4)
        self.assertAlmostEqual(result["net_realized_after_fees"], 24.6)
        self.assertAlmostEqual(result["win_rate"], 0.33333333)

    def test_analyze_fills_marks_missing_realized_pnl_and_fee_as_partial(self) -> None:
        payload = {
            "fills": [
                {
                    "symbol": "BTCUSDT",
                    "side": "sell",
                    "quantity": 0.01,
                    "price": 65000,
                },
                {
                    "symbol": "ETHUSDT",
                    "side": "sell",
                    "quantity": 0.5,
                    "price": 3080,
                    "realized_pnl": 40,
                    "fee": 1.4,
                },
            ]
        }

        result = analysis.analyze_fills(payload)
        text = analysis._render_text(result)

        self.assertTrue(result["partial"])
        self.assertIn("fills_missing_realized_pnl", result["degraded_reasons"])
        self.assertIn("fills_missing_fee", result["degraded_reasons"])
        self.assertIn("Partial Analysis", text)
        self.assertIn("degraded: fills_missing_realized_pnl", text)
        self.assertIn("degraded: fills_missing_fee", text)

    def test_analyze_fills_direct_call_preserves_trading_mode_context(self) -> None:
        payload = {
            "language": "zh",
            "trading_mode": "demo",
            "fills": [
                {
                    "symbol": "BTCUSDT",
                    "side": "sell",
                    "quantity": 0.01,
                    "price": 64000,
                    "realized_pnl": 10,
                    "fee": 0.8,
                }
            ],
        }

        result = analysis.analyze_fills(payload)
        text = analysis._render_text(result)

        self.assertEqual(result["trading_mode"], "demo")
        self.assertIn("Trading Mode: 模拟盘", text)

    def test_analyze_fills_cli_text_uses_fill_win_rate_label(self) -> None:
        payload = {
            "fills": [
                {
                    "symbol": "ETHUSDT",
                    "side": "buy",
                    "quantity": 0.5,
                    "price": 3000,
                    "realized_pnl": 0,
                    "fee": 1.2,
                },
                {
                    "symbol": "ETHUSDT",
                    "side": "sell",
                    "quantity": 0.5,
                    "price": 3080,
                    "realized_pnl": 40,
                    "fee": 1.4,
                },
                {
                    "symbol": "BTCUSDT",
                    "side": "sell",
                    "quantity": 0.01,
                    "price": 64000,
                    "realized_pnl": -12,
                    "fee": 0.8,
                },
            ]
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-fills", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Fill win rate: 33.33%", completed.stdout)
        self.assertIn(EXPECTED_STANDARD_DISCLAIMER, completed.stdout)

    def test_analyze_fills_cli_text_surfaces_partial_context(self) -> None:
        payload = {
            "fills": [
                {
                    "symbol": "BTCUSDT",
                    "side": "sell",
                    "quantity": 0.01,
                    "price": 64000,
                    "realized_pnl": -12,
                    "fee": 0.8,
                }
            ],
            "partial": True,
            "degraded_reasons": ["replay_carry_in_detected"],
            "constraints": [
                {
                    "code": "analysis_symbol_filter_applied",
                    "message": "Prepared payload keeps only symbol(s): BTCUSDT.",
                }
            ],
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-fills", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Partial Analysis", completed.stdout)
        self.assertIn("degraded: replay_carry_in_detected", completed.stdout)
        self.assertIn("Applied Filters", completed.stdout)
        self.assertIn("analysis_symbol_filter_applied", completed.stdout)
        self.assertIn(EXPECTED_STANDARD_DISCLAIMER, completed.stdout)

    def test_cli_reads_stdin_and_pretty_prints_json(self) -> None:
        payload = {
            "equity": 500,
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quantity": 0.005,
                    "entry_price": 60000,
                    "mark_price": 61000,
                }
            ],
        }
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-snapshot", "--input", "-", "--pretty"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["positions_count"], 1)
        self.assertEqual(result["positions"][0]["symbol"], "BTCUSDT")
        self.assertEqual(result["disclaimer"], EXPECTED_STANDARD_DISCLAIMER)


class ReplayAnalysisTests(unittest.TestCase):
    def test_analyze_replay_preserves_trader_environment_context(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "trading_mode": "demo",
            "environment": {
                "trading_mode": "demo",
                "label": "demo",
                "market": "futures",
                "uses_real_funds": False,
                "notice": "This operation targets WEEX futures demo mode.",
            },
            "account_scope": "sim_futures",
            "orders": [],
            "fills": [],
        }

        result = analysis.analyze_replay(payload)
        review = analysis.review_trades(payload)
        text = analysis._render_text(result)
        review_text = analysis._render_text(review)

        self.assertEqual(result["trading_mode"], "demo")
        self.assertEqual(result["environment"]["trading_mode"], "demo")
        self.assertEqual(result["account_scope"], "sim_futures")
        self.assertEqual(review["trading_mode"], "demo")
        self.assertEqual(review["environment"]["trading_mode"], "demo")
        self.assertEqual(review["account_scope"], "sim_futures")
        self.assertIn("Trading Mode: demo trading", text)
        self.assertNotIn("Trading Environment:", text)
        self.assertNotIn("Trading Mode: demo\n", text)
        self.assertIn("Uses Real Funds: false", text)
        self.assertIn("Trading Mode: demo trading", review_text)
        self.assertNotIn("Trading Environment:", review_text)
        self.assertNotIn("Trading Mode: demo\n", review_text)
        self.assertIn("Uses Real Funds: false", review_text)

    def test_analyze_replay_reconstructs_trade_episodes_and_metrics(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "orders": [
                {
                    "order_id": "1-open",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "BUY",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "margin_type": "CROSSED",
                    "quantity": 0.01,
                    "time": 1710000000000,
                    "update_time": 1710000000000,
                },
                {
                    "order_id": "1-close",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "SELL",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "margin_type": "CROSSED",
                    "quantity": 0.01,
                    "reduce_only": True,
                    "time": 1710001800000,
                    "update_time": 1710001800000,
                },
                {
                    "order_id": "2-open",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "BUY",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "margin_type": "CROSSED",
                    "quantity": 0.02,
                    "time": 1710002400000,
                    "update_time": 1710002400000,
                },
                {
                    "order_id": "2-close",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "SELL",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "margin_type": "CROSSED",
                    "quantity": 0.02,
                    "reduce_only": True,
                    "time": 1710004200000,
                    "update_time": 1710004200000,
                },
                {
                    "order_id": "3-open",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "BUY",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "margin_type": "CROSSED",
                    "quantity": 0.04,
                    "time": 1710004800000,
                    "update_time": 1710004800000,
                },
                {
                    "order_id": "3-close",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "SELL",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "margin_type": "CROSSED",
                    "quantity": 0.04,
                    "reduce_only": True,
                    "time": 1710005100000,
                    "update_time": 1710005100000,
                },
            ],
            "fills": [
                {
                    "order_id": "1-open",
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
                    "order_id": "1-close",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "price": 64800,
                    "quantity": 0.01,
                    "realized_pnl": -20,
                    "fee": 1.0,
                    "time": 1710001800000,
                },
                {
                    "order_id": "2-open",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "price": 65200,
                    "quantity": 0.02,
                    "realized_pnl": 0,
                    "fee": 1.2,
                    "time": 1710002400000,
                },
                {
                    "order_id": "2-close",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "price": 65100,
                    "quantity": 0.02,
                    "realized_pnl": -10,
                    "fee": 1.2,
                    "time": 1710004200000,
                },
                {
                    "order_id": "3-open",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "price": 65300,
                    "quantity": 0.04,
                    "realized_pnl": 0,
                    "fee": 1.3,
                    "time": 1710004800000,
                },
                {
                    "order_id": "3-close",
                    "account_scope": "personal_futures",
                    "market": "futures",
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "position_side": "LONG",
                    "position_mode": "COMBINED",
                    "price": 65380,
                    "quantity": 0.04,
                    "realized_pnl": 5,
                    "fee": 1.3,
                    "time": 1710005100000,
                },
            ],
        }

        result = analysis.analyze_replay(payload)
        tags = set(result["behavior_tags"])

        self.assertEqual(result["episode_count"], 3)
        self.assertEqual(len(result["trade_episodes"]), 3)
        self.assertEqual(result["trade_episodes"][0]["position_side"], "long")
        self.assertAlmostEqual(result["trade_episodes"][0]["holding_minutes"], 30.0)
        self.assertAlmostEqual(result["metrics"]["win_rate"], 0.33333333)
        self.assertIn("repeated_reentry", tags)
        self.assertIn("position_size_escalation", tags)
        self.assertIn("low_win_large_loss", tags)
        self.assertGreaterEqual(len(result["evidence"]), 2)
        self.assertGreaterEqual(len(result["advice"]), 2)
        self.assertIn("replay", result["summary"].lower())

    def test_review_trades_cli_renders_text_summary(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "7d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "time": 1710001800000, "update_time": 1710001800000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65200, "realized_pnl": 12, "fee": 1.0, "time": 1710001800000},
            ],
        }

        review_result = analysis.review_trades(payload)

        self.assertEqual(review_result["review_type"], "trade_review")
        self.assertEqual(review_result["episode_count"], 1)
        self.assertIn("episodes", review_result)
        self.assertNotIn("trade_episodes", review_result)
        self.assertEqual(review_result["episodes"][0]["symbol"], "BTCUSDT")
        self.assertIn("pattern_snapshot", review_result)
        self.assertIn("behavior_tags", review_result["pattern_snapshot"])

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "review-trades", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Trade Review", completed.stdout)
        self.assertIn("Episode Highlights", completed.stdout)
        self.assertIn("Pattern Snapshot", completed.stdout)
        self.assertIn(EXPECTED_STANDARD_DISCLAIMER, completed.stdout)

    def test_review_trades_text_output_surfaces_partial_and_constraints(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "all",
            "period": "30d",
            "partial": True,
            "degraded_reasons": ["orders_window_truncated"],
            "constraints": [
                {
                    "code": "spot_symbol_required",
                    "message": "spot history is only collected when symbol is provided.",
                }
            ],
            "orders": [],
            "fills": [],
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "review-trades", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Trade Review", completed.stdout)
        self.assertIn("Partial Analysis", completed.stdout)
        self.assertIn("orders_window_truncated", completed.stdout)
        self.assertIn("spot_symbol_required", completed.stdout)
        self.assertIn(EXPECTED_STANDARD_DISCLAIMER, completed.stdout)

    def test_analyze_replay_preserves_partial_and_degraded_reasons(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "partial": True,
            "degraded_reasons": ["orders_window_truncated"],
            "constraints": [{"code": "spot_symbol_required", "message": "spot history is only collected when symbol is provided."}],
            "orders": [],
            "fills": [],
        }

        result = analysis.analyze_replay(payload)

        self.assertTrue(result["partial"])
        self.assertIn("orders_window_truncated", result["degraded_reasons"])
        self.assertEqual(result["constraints"][0]["code"], "spot_symbol_required")

    def test_analyze_replay_marks_carry_in_episode_as_partial(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
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

        result = analysis.analyze_replay(payload)

        self.assertTrue(result["partial"])
        self.assertEqual(result["episode_count"], 1)
        self.assertTrue(result["trade_episodes"][0]["partial"])
        self.assertEqual(result["trade_episodes"][0]["entry_count"], 0)
        self.assertEqual(result["trade_episodes"][0]["exit_count"], 1)
        self.assertIn("replay_carry_in_detected", result["degraded_reasons"])

    def test_analyze_replay_excludes_partial_episodes_from_payoff_metrics(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
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
                },
                {
                    "order_id": "full-open",
                    "symbol": "ETHUSDT",
                    "status": "FILLED",
                    "side": "BUY",
                    "position_side": "LONG",
                    "quantity": 0.5,
                    "time": 1710005400000,
                    "update_time": 1710005400000,
                },
                {
                    "order_id": "full-close",
                    "symbol": "ETHUSDT",
                    "status": "FILLED",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.5,
                    "reduce_only": True,
                    "time": 1710009000000,
                    "update_time": 1710009000000,
                },
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
                },
                {
                    "order_id": "full-open",
                    "symbol": "ETHUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "quantity": 0.5,
                    "price": 3200,
                    "realized_pnl": 0,
                    "fee": 0.5,
                    "time": 1710005400000,
                },
                {
                    "order_id": "full-close",
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.5,
                    "price": 3250,
                    "realized_pnl": 10,
                    "fee": 0.5,
                    "time": 1710009000000,
                },
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertTrue(result["partial"])
        self.assertEqual(result["episode_count"], 2)
        self.assertAlmostEqual(result["metrics"]["win_rate"], 1.0)
        self.assertAlmostEqual(result["metrics"]["average_win"], 9.0)
        self.assertIsNone(result["metrics"]["average_loss"])

    def test_analyze_replay_marks_missing_episode_pnl_as_partial(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "spot",
            "period": "30d",
            "orders": [
                {
                    "order_id": "spot-open",
                    "market": "spot",
                    "symbol": "ETHUSDT",
                    "status": "FILLED",
                    "side": "BUY",
                    "quantity": 0.5,
                    "time": 1710000000000,
                    "update_time": 1710000000000,
                },
                {
                    "order_id": "spot-close",
                    "market": "spot",
                    "symbol": "ETHUSDT",
                    "status": "FILLED",
                    "side": "SELL",
                    "quantity": 0.5,
                    "time": 1710003600000,
                    "update_time": 1710003600000,
                },
            ],
            "fills": [
                {
                    "order_id": "spot-open",
                    "market": "spot",
                    "symbol": "ETHUSDT",
                    "side": "BUY",
                    "quantity": 0.5,
                    "price": 3000,
                    "fee": 1.0,
                    "time": 1710000000000,
                },
                {
                    "order_id": "spot-close",
                    "market": "spot",
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "quantity": 0.5,
                    "price": 3050,
                    "fee": 1.0,
                    "time": 1710003600000,
                },
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertTrue(result["partial"])
        self.assertIn("replay_episode_pnl_unavailable", result["degraded_reasons"])
        self.assertIsNone(result["trade_episodes"][0]["net_pnl"])
        self.assertIsNone(result["metrics"]["win_rate"])
        self.assertIsNone(result["metrics"]["profit_factor"])

    def test_analyze_replay_uses_insufficient_sample_summary_when_no_closed_episodes_exist(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "spot",
            "period": "30d",
            "orders": [],
            "fills": [],
        }

        result = analysis.analyze_replay(payload)

        self.assertEqual(result["episode_count"], 0)
        self.assertEqual(result["top_pattern"], "no_closed_episodes")
        self.assertIn("no closed trade episodes", result["summary"].lower())
        self.assertIn("no closed trade episodes", result["evidence"][0].lower())

    def test_analyze_replay_summary_mentions_detected_pattern_without_false_reentry_label(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710001800000, "update_time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.02, "time": 1710004200000, "update_time": 1710004200000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.02, "reduce_only": True, "time": 1710006000000, "update_time": 1710006000000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.04, "time": 1710009600000, "update_time": 1710009600000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.04, "reduce_only": True, "time": 1710011400000, "update_time": 1710011400000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 64800, "realized_pnl": -20, "fee": 1.0, "time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.02, "price": 65200, "realized_pnl": 0, "fee": 1.2, "time": 1710004200000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.02, "price": 65100, "realized_pnl": -10, "fee": 1.2, "time": 1710006000000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.04, "price": 65300, "realized_pnl": 0, "fee": 1.3, "time": 1710009600000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.04, "price": 65380, "realized_pnl": 5, "fee": 1.3, "time": 1710011400000},
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertEqual(result["top_pattern"], "position_size_escalation")
        self.assertNotIn("repeated_reentry", result["behavior_tags"])
        self.assertIn("position size", result["summary"].lower())
        self.assertNotIn("re-entry", result["summary"].lower())

    def test_analyze_replay_flags_protection_churn_from_canceled_protective_orders(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710001800000, "update_time": 1710001800000},
                {"order_id": "algo-1", "symbol": "BTCUSDT", "status": "CANCELED", "side": "SELL", "position_side": "LONG", "order_type": "take_profit_market", "reduce_only": True, "close_position": True, "tp_trigger_price": 65500, "time": 1710002000000, "update_time": 1710002100000},
                {"order_id": "algo-2", "symbol": "BTCUSDT", "status": "CANCELED", "side": "SELL", "position_side": "LONG", "order_type": "stop_market", "reduce_only": True, "close_position": True, "sl_trigger_price": 64500, "time": 1710002200000, "update_time": 1710002300000},
                {"order_id": "algo-3", "symbol": "BTCUSDT", "status": "CANCELED", "side": "SELL", "position_side": "LONG", "order_type": "take_profit_market", "reduce_only": True, "close_position": True, "tp_trigger_price": 65600, "time": 1710002400000, "update_time": 1710002500000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65100, "realized_pnl": 10, "fee": 1.0, "time": 1710001800000},
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertIn("protection_churn", result["behavior_tags"])
        self.assertTrue(any("protective" in item.lower() for item in result["evidence"]))

    def test_analyze_replay_skips_high_frequency_tag_during_high_volatility_burst(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710000600000, "update_time": 1710000600000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "SHORT", "quantity": 0.01, "time": 1710001200000, "update_time": 1710001200000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "SHORT", "quantity": 0.01, "reduce_only": True, "time": 1710001800000, "update_time": 1710001800000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710002400000, "update_time": 1710002400000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710003000000, "update_time": 1710003000000},
                {"order_id": "4-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "SHORT", "quantity": 0.01, "time": 1710003540000, "update_time": 1710003540000},
                {"order_id": "4-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "SHORT", "quantity": 0.01, "reduce_only": True, "time": 1710004140000, "update_time": 1710004140000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65040, "realized_pnl": 3, "fee": 1.0, "time": 1710000600000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "side": "SELL", "position_side": "SHORT", "quantity": 0.01, "price": 65800, "realized_pnl": 0, "fee": 1.0, "time": 1710001200000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "side": "BUY", "position_side": "SHORT", "quantity": 0.01, "price": 65740, "realized_pnl": 4, "fee": 1.0, "time": 1710001800000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 66600, "realized_pnl": 0, "fee": 1.0, "time": 1710002400000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 66640, "realized_pnl": 3, "fee": 1.0, "time": 1710003000000},
                {"order_id": "4-open", "symbol": "BTCUSDT", "side": "SELL", "position_side": "SHORT", "quantity": 0.01, "price": 67400, "realized_pnl": 0, "fee": 1.0, "time": 1710003540000},
                {"order_id": "4-close", "symbol": "BTCUSDT", "side": "BUY", "position_side": "SHORT", "quantity": 0.01, "price": 67350, "realized_pnl": 4, "fee": 1.0, "time": 1710004140000},
            ],
            "price_series": [
                {"symbol": "BTCUSDT", "open_time": 1709989200000, "close_time": 1709992799999, "open": 64970, "high": 65005, "low": 64965, "close": 64990},
                {"symbol": "BTCUSDT", "open_time": 1709992800000, "close_time": 1709996399999, "open": 64990, "high": 65010, "low": 64980, "close": 65000},
                {"symbol": "BTCUSDT", "open_time": 1709996400000, "close_time": 1709999999999, "open": 65000, "high": 65020, "low": 64990, "close": 65000},
                {"symbol": "BTCUSDT", "open_time": 1710000000000, "close_time": 1710003599999, "open": 65000, "high": 67500, "low": 64980, "close": 67450},
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertNotIn("high_trade_frequency", result["behavior_tags"])
        self.assertTrue(any("high-volatility" in item.lower() for item in result["evidence"]))

    def test_analyze_replay_strengthens_high_frequency_evidence_in_quiet_market(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710000600000, "update_time": 1710000600000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "SHORT", "quantity": 0.01, "time": 1710001200000, "update_time": 1710001200000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "SHORT", "quantity": 0.01, "reduce_only": True, "time": 1710001800000, "update_time": 1710001800000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710002400000, "update_time": 1710002400000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710003000000, "update_time": 1710003000000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65010, "realized_pnl": 2, "fee": 1.0, "time": 1710000600000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "side": "SELL", "position_side": "SHORT", "quantity": 0.01, "price": 65005, "realized_pnl": 0, "fee": 1.0, "time": 1710001200000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "side": "BUY", "position_side": "SHORT", "quantity": 0.01, "price": 64995, "realized_pnl": 2, "fee": 1.0, "time": 1710001800000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65002, "realized_pnl": 0, "fee": 1.0, "time": 1710002400000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65012, "realized_pnl": 2, "fee": 1.0, "time": 1710003000000},
            ],
            "price_series": [
                {"symbol": "BTCUSDT", "open_time": 1709989200000, "close_time": 1709992799999, "open": 64996, "high": 65004, "low": 64994, "close": 65000},
                {"symbol": "BTCUSDT", "open_time": 1709992800000, "close_time": 1709996399999, "open": 65000, "high": 65005, "low": 64997, "close": 65001},
                {"symbol": "BTCUSDT", "open_time": 1709996400000, "close_time": 1709999999999, "open": 65001, "high": 65004, "low": 64998, "close": 65000},
                {"symbol": "BTCUSDT", "open_time": 1710000000000, "close_time": 1710003599999, "open": 65000, "high": 65015, "low": 64992, "close": 65005},
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertIn("high_trade_frequency", result["behavior_tags"])
        self.assertIn("moved only", result["evidence"][0].lower())

    def test_analyze_replay_skips_size_escalation_when_breakout_resets_the_trade_idea(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710001800000, "update_time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.02, "time": 1710003000000, "update_time": 1710003000000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.02, "reduce_only": True, "time": 1710004800000, "update_time": 1710004800000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 64800, "realized_pnl": -20, "fee": 1.0, "time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.02, "price": 66800, "realized_pnl": 0, "fee": 1.0, "time": 1710003000000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.02, "price": 66900, "realized_pnl": 8, "fee": 1.0, "time": 1710004800000},
            ],
            "price_series": [
                {"symbol": "BTCUSDT", "open_time": 1709989200000, "close_time": 1709992799999, "open": 64992, "high": 65004, "low": 64988, "close": 64998},
                {"symbol": "BTCUSDT", "open_time": 1709992800000, "close_time": 1709996399999, "open": 64998, "high": 65008, "low": 64994, "close": 65000},
                {"symbol": "BTCUSDT", "open_time": 1709996400000, "close_time": 1709999999999, "open": 65000, "high": 65012, "low": 64996, "close": 65002},
                {"symbol": "BTCUSDT", "open_time": 1710000000000, "close_time": 1710003599999, "open": 65002, "high": 66850, "low": 64790, "close": 66800},
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertNotIn("position_size_escalation", result["behavior_tags"])
        self.assertTrue(any("directional reset" in item.lower() for item in result["evidence"]))

    def test_analyze_replay_keeps_size_escalation_in_quiet_market(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710001800000, "update_time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.02, "time": 1710007200000, "update_time": 1710007200000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.02, "reduce_only": True, "time": 1710009000000, "update_time": 1710009000000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 64800, "realized_pnl": -20, "fee": 1.0, "time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.02, "price": 64840, "realized_pnl": 0, "fee": 1.0, "time": 1710007200000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.02, "price": 64910, "realized_pnl": 6, "fee": 1.0, "time": 1710009000000},
            ],
            "price_series": [
                {"symbol": "BTCUSDT", "open_time": 1709989200000, "close_time": 1709992799999, "open": 64992, "high": 65004, "low": 64988, "close": 64998},
                {"symbol": "BTCUSDT", "open_time": 1709992800000, "close_time": 1709996399999, "open": 64998, "high": 65008, "low": 64994, "close": 65000},
                {"symbol": "BTCUSDT", "open_time": 1709996400000, "close_time": 1709999999999, "open": 65000, "high": 65012, "low": 64996, "close": 65002},
                {"symbol": "BTCUSDT", "open_time": 1710000000000, "close_time": 1710003599999, "open": 65002, "high": 65010, "low": 64790, "close": 64820},
                {"symbol": "BTCUSDT", "open_time": 1710003600000, "close_time": 1710007199999, "open": 64820, "high": 64855, "low": 64800, "close": 64835},
                {"symbol": "BTCUSDT", "open_time": 1710007200000, "close_time": 1710010799999, "open": 64835, "high": 64920, "low": 64810, "close": 64890},
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertIn("position_size_escalation", result["behavior_tags"])

    def test_analyze_replay_skips_reentry_tag_when_price_context_shows_market_reset(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710001800000, "update_time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710003000000, "update_time": 1710003000000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710004200000, "update_time": 1710004200000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710005400000, "update_time": 1710005400000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710006600000, "update_time": 1710006600000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65050, "realized_pnl": 5, "fee": 1.0, "time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 66000, "realized_pnl": 0, "fee": 1.0, "time": 1710003000000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 66030, "realized_pnl": 4, "fee": 1.0, "time": 1710004200000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 67000, "realized_pnl": 0, "fee": 1.0, "time": 1710005400000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 67010, "realized_pnl": 3, "fee": 1.0, "time": 1710006600000},
            ],
            "price_series": [
                {"symbol": "BTCUSDT", "open_time": 1709996400000, "close_time": 1709999999999, "open": 64980, "high": 65020, "low": 64970, "close": 65000},
                {"symbol": "BTCUSDT", "open_time": 1710000000000, "close_time": 1710003599999, "open": 65000, "high": 66020, "low": 64990, "close": 66000},
                {"symbol": "BTCUSDT", "open_time": 1710003600000, "close_time": 1710007199999, "open": 66000, "high": 67020, "low": 65990, "close": 67000},
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertNotIn("repeated_reentry", result["behavior_tags"])
        self.assertTrue(any("market reset" in item.lower() for item in result["evidence"]))

    def test_analyze_replay_requires_post_exit_follow_through_for_take_profit_tag_when_price_context_exists(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710001800000, "update_time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710086400000, "update_time": 1710086400000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710088200000, "update_time": 1710088200000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710172800000, "update_time": 1710172800000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710180000000, "update_time": 1710180000000},
                {"order_id": "4-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710259200000, "update_time": 1710259200000},
                {"order_id": "4-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710266400000, "update_time": 1710266400000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65080, "realized_pnl": 6, "fee": 1.0, "time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65100, "realized_pnl": 0, "fee": 1.0, "time": 1710086400000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65170, "realized_pnl": 5, "fee": 1.0, "time": 1710088200000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65200, "realized_pnl": 0, "fee": 1.0, "time": 1710172800000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65080, "realized_pnl": -13, "fee": 1.0, "time": 1710180000000},
                {"order_id": "4-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65300, "realized_pnl": 0, "fee": 1.0, "time": 1710259200000},
                {"order_id": "4-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65160, "realized_pnl": -11, "fee": 1.0, "time": 1710266400000},
            ],
            "price_series": [
                {"symbol": "BTCUSDT", "open_time": 1709996400000, "close_time": 1709999999999, "open": 64980, "high": 65020, "low": 64970, "close": 65000},
                {"symbol": "BTCUSDT", "open_time": 1710000000000, "close_time": 1710003599999, "open": 65000, "high": 65085, "low": 64980, "close": 65040},
                {"symbol": "BTCUSDT", "open_time": 1710082800000, "close_time": 1710086399999, "open": 65090, "high": 65120, "low": 65070, "close": 65100},
                {"symbol": "BTCUSDT", "open_time": 1710086400000, "close_time": 1710089999999, "open": 65100, "high": 65175, "low": 65090, "close": 65110},
                {"symbol": "BTCUSDT", "open_time": 1710172800000, "close_time": 1710176399999, "open": 65200, "high": 65210, "low": 65120, "close": 65140},
                {"symbol": "BTCUSDT", "open_time": 1710176400000, "close_time": 1710179999999, "open": 65140, "high": 65150, "low": 65070, "close": 65090},
                {"symbol": "BTCUSDT", "open_time": 1710255600000, "close_time": 1710259199999, "open": 65290, "high": 65320, "low": 65270, "close": 65300},
                {"symbol": "BTCUSDT", "open_time": 1710259200000, "close_time": 1710262799999, "open": 65300, "high": 65310, "low": 65150, "close": 65180},
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertNotIn("take_profit_too_early", result["behavior_tags"])
        self.assertTrue(any("follow-through" in item.lower() for item in result["evidence"]))

    def test_review_trades_text_output_labels_partial_carry_in_episode(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
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

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "review-trades", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("partial carry-in", completed.stdout.lower())

    def test_analyze_replay_text_output_labels_filters_without_partial_banner(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "constraints": [
                {
                    "code": "analysis_symbol_filter_applied",
                    "message": "Prepared payload keeps only symbol(s): BTCUSDT.",
                }
            ],
            "orders": [],
            "fills": [],
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-replay", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Applied Filters", completed.stdout)
        self.assertIn("analysis_symbol_filter_applied", completed.stdout)
        self.assertNotIn("Partial Analysis", completed.stdout)

    def test_review_trades_text_output_labels_filters_without_partial_banner(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "30d",
            "constraints": [
                {
                    "code": "analysis_symbol_filter_applied",
                    "message": "Prepared payload keeps only symbol(s): BTCUSDT.",
                }
            ],
            "orders": [],
            "fills": [],
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "review-trades", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Applied Filters", completed.stdout)
        self.assertIn("analysis_symbol_filter_applied", completed.stdout)
        self.assertNotIn("Partial Analysis", completed.stdout)

    def test_analyze_replay_text_formats_average_holding_time_human_readably(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "7d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710005400000, "update_time": 1710005400000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65200, "realized_pnl": 12, "fee": 1.0, "time": 1710005400000},
            ],
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-replay", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Avg Holding Time: 1h 30m", completed.stdout)
        self.assertNotIn("Avg Holding Minutes: 90.0", completed.stdout)

    def test_review_trades_text_formats_episode_durations_human_readably(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "period": "7d",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710005400000, "update_time": 1710005400000},
                {"order_id": "2-open", "symbol": "ETHUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.1, "time": 1710010000000, "update_time": 1710010000000},
                {"order_id": "2-close", "symbol": "ETHUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.1, "reduce_only": True, "time": 1710020800000, "update_time": 1710020800000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 1.0, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65200, "realized_pnl": 12, "fee": 1.0, "time": 1710005400000},
                {"order_id": "2-open", "symbol": "ETHUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.1, "price": 3200, "realized_pnl": 0, "fee": 1.0, "time": 1710010000000},
                {"order_id": "2-close", "symbol": "ETHUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.1, "price": 3250, "realized_pnl": 5, "fee": 1.0, "time": 1710020800000},
            ],
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "review-trades", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Longest hold: ETHUSDT long for 3h.", completed.stdout)
        self.assertIn("holding=1h 30m", completed.stdout)
        self.assertNotIn("holding_minutes=90.0", completed.stdout)


class ProfileAnalysisTests(unittest.TestCase):
    def test_analyze_profile_outputs_persona_strengths_weaknesses_and_warning(self) -> None:
        payload = {
            "analysis_type": "profile",
            "selected_period": "90d",
            "closed_trade_count": 24,
            "metrics": {
                "median_hold_ms": 2 * 60 * 60 * 1000,
                "active_day_trade_average": 6,
                "risk_score": 0.82,
                "win_rate": 0.42,
                "profit_factor": 0.78,
            },
            "replay_analysis": {
                "behavior_tags": ["high_trade_frequency", "losses_outsize_wins"],
            },
        }

        result = analysis.analyze_profile(payload)

        self.assertEqual(result["selected_period"], "90d")
        self.assertEqual(result["persona"]["holding_style"], "short_term")
        self.assertEqual(result["persona"]["frequency_style"], "high_frequency")
        self.assertEqual(result["persona"]["risk_style"], "aggressive")
        self.assertGreaterEqual(len(result["strengths"]), 1)
        self.assertGreaterEqual(len(result["weaknesses"]), 1)
        self.assertIn("cannot predict future", result["warning"].lower())

    def test_analyze_profile_direct_call_preserves_trading_mode_context(self) -> None:
        payload = {
            "language": "zh",
            "trading_mode": "live",
            "analysis_type": "profile",
            "selected_period": "90d",
            "closed_trade_count": 24,
            "metrics": {
                "median_hold_ms": 2 * 60 * 60 * 1000,
                "active_day_trade_average": 6,
                "risk_score": 0.82,
                "win_rate": 0.42,
                "profit_factor": 0.78,
            },
        }

        result = analysis.analyze_profile(payload)
        text = analysis._render_text(result)

        self.assertEqual(result["trading_mode"], "live")
        self.assertIn("Trading Mode: 真实盘", text)

    def test_analyze_profile_minimal_sample_returns_basic_stats_only(self) -> None:
        payload = {
            "analysis_type": "profile",
            "selected_period": "30d",
            "closed_trade_count": 4,
            "metrics": {
                "median_hold_ms": 90 * 60 * 1000,
                "active_day_trade_average": 2,
                "risk_score": 0.31,
                "win_rate": 0.5,
                "profit_factor": 1.1,
            },
        }

        result = analysis.analyze_profile(payload)

        self.assertEqual(result["profile_tier"], "basic")
        self.assertIsNone(result["persona"])
        self.assertGreaterEqual(len(result["observations"]), 1)

    def test_analyze_profile_preserves_partial_and_degraded_state(self) -> None:
        payload = {
            "analysis_type": "profile",
            "selected_period": "90d",
            "closed_trade_count": 12,
            "sample_quality": "limited",
            "partial": True,
            "degraded_reasons": ["spot_kline_window_unbounded"],
            "constraints": [
                {
                    "code": "spot_symbol_required",
                    "message": "spot history is only collected when symbol is provided.",
                }
            ],
            "metrics": {
                "median_hold_ms": 2 * 60 * 60 * 1000,
                "active_day_trade_average": 3,
                "risk_score": 0.52,
                "win_rate": 0.48,
                "profit_factor": 0.96,
            },
            "replay_analysis": {
                "behavior_tags": ["high_trade_frequency"],
            },
        }

        result = analysis.analyze_profile(payload)

        self.assertTrue(result["partial"])
        self.assertEqual(result["degraded_reasons"], ["spot_kline_window_unbounded"])
        self.assertEqual(result["constraints"][0]["code"], "spot_symbol_required")

    def test_analyze_profile_downgrades_partial_full_sample_persona(self) -> None:
        payload = {
            "analysis_type": "profile",
            "selected_period": "90d",
            "closed_trade_count": 24,
            "sample_quality": "full",
            "partial": True,
            "degraded_reasons": ["replay_carry_in_detected"],
            "metrics": {
                "median_hold_ms": 6 * 60 * 60 * 1000,
                "active_day_trade_average": 1.8,
                "risk_score": 0.82,
                "win_rate": 0.39,
                "profit_factor": 0.42,
            },
            "replay_analysis": {
                "behavior_tags": ["low_win_large_loss"],
            },
        }

        result = analysis.analyze_profile(payload)

        self.assertEqual(result["profile_tier"], "weak")
        self.assertIsNone(result["persona"]["risk_style"])
        self.assertIsNone(result["persona"]["edge_style"])

    def test_analyze_profile_uses_burst_wording_when_persona_is_low_frequency(self) -> None:
        payload = {
            "analysis_type": "profile",
            "selected_period": "90d",
            "closed_trade_count": 24,
            "metrics": {
                "median_hold_ms": 3 * 60 * 60 * 1000,
                "active_day_trade_average": 0.96,
                "risk_score": 0.72,
                "win_rate": 0.42,
                "profit_factor": 0.78,
            },
            "replay_analysis": {
                "behavior_tags": ["high_trade_frequency"],
            },
        }

        result = analysis.analyze_profile(payload)

        self.assertEqual(result["persona"]["frequency_style"], "low_frequency")
        self.assertIn("burst", result["weaknesses"][0].lower())
        self.assertNotEqual(result["weaknesses"][0], "High trading frequency is dragging down selectivity.")

    def test_analyze_profile_keeps_missing_edge_metrics_unclassified(self) -> None:
        payload = {
            "analysis_type": "profile",
            "selected_period": "90d",
            "closed_trade_count": 24,
            "sample_quality": "limited",
            "partial": True,
            "degraded_reasons": ["replay_episode_pnl_unavailable"],
            "metrics": {
                "median_hold_ms": 9 * 60 * 60 * 1000,
                "active_day_trade_average": 1.5,
                "risk_score": 0.2,
            },
            "replay_analysis": {
                "behavior_tags": [],
            },
        }

        result = analysis.analyze_profile(payload)

        self.assertEqual(result["persona"]["holding_style"], "trend")
        self.assertEqual(result["persona"]["frequency_style"], "low_frequency")
        self.assertEqual(result["persona"]["risk_style"], "steady")
        self.assertIsNone(result["persona"]["edge_style"])
        self.assertNotIn("Loss size still dominates payoff quality.", result["weaknesses"])

    def test_analyze_profile_derives_metrics_from_replay_payload_when_metrics_missing(self) -> None:
        payload = {
            "analysis_type": "profile",
            "selected_period": "30d",
            "closed_trade_count": 2,
            "sample_quality": "limited",
            "balances": [
                {
                    "asset": "USDT",
                    "balance": 1000,
                    "equity": 1000,
                    "available_balance": 120,
                }
            ],
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quantity": 0.01,
                    "notional": 700,
                    "leverage": 5,
                }
            ],
            "orders": [
                {
                    "order_id": "1-open",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "BUY",
                    "position_side": "LONG",
                    "quantity": 0.01,
                    "time": 1710000000000,
                    "update_time": 1710000000000,
                },
                {
                    "order_id": "1-close",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.01,
                    "reduce_only": True,
                    "time": 1710001800000,
                    "update_time": 1710001800000,
                },
                {
                    "order_id": "2-open",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "BUY",
                    "position_side": "LONG",
                    "quantity": 0.02,
                    "time": 1710086400000,
                    "update_time": 1710086400000,
                },
                {
                    "order_id": "2-close",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.02,
                    "reduce_only": True,
                    "time": 1710088200000,
                    "update_time": 1710088200000,
                },
            ],
            "fills": [
                {
                    "order_id": "1-open",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "quantity": 0.01,
                    "price": 65000,
                    "realized_pnl": 0,
                    "fee": 1.0,
                    "time": 1710000000000,
                },
                {
                    "order_id": "1-close",
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.01,
                    "price": 65200,
                    "realized_pnl": 12,
                    "fee": 1.0,
                    "time": 1710001800000,
                },
                {
                    "order_id": "2-open",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "quantity": 0.02,
                    "price": 65300,
                    "realized_pnl": 0,
                    "fee": 1.0,
                    "time": 1710086400000,
                },
                {
                    "order_id": "2-close",
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.02,
                    "price": 65100,
                    "realized_pnl": -6,
                    "fee": 1.0,
                    "time": 1710088200000,
                },
            ],
        }

        result = analysis.analyze_profile(payload)

        self.assertEqual(result["profile_tier"], "weak")
        self.assertAlmostEqual(result["metrics"]["win_rate"], 0.5)
        self.assertAlmostEqual(result["metrics"]["profit_factor"], 1.25)
        self.assertAlmostEqual(result["metrics"]["avg_win"], 10.0)
        self.assertAlmostEqual(result["metrics"]["avg_loss"], 8.0)
        self.assertAlmostEqual(result["metrics"]["median_hold_ms"], 1800000.0)
        self.assertAlmostEqual(result["metrics"]["active_day_trade_average"], 1.0)

    def test_analyze_profile_uses_trade_episode_outcomes_instead_of_partial_exit_fills(self) -> None:
        payload = {
            "analysis_type": "profile",
            "selected_period": "30d",
            "closed_trade_count": 12,
            "sample_quality": "limited",
            "balances": [
                {
                    "asset": "USDT",
                    "balance": 1000,
                    "equity": 1000,
                    "available_balance": 400,
                }
            ],
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quantity": 0.0,
                    "notional": 0.0,
                    "leverage": 3,
                }
            ],
            "orders": [
                {
                    "order_id": "1-open",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "BUY",
                    "position_side": "LONG",
                    "quantity": 1.0,
                    "time": 1710000000000,
                    "update_time": 1710000000000,
                },
                {
                    "order_id": "1-close-a",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.5,
                    "reduce_only": True,
                    "time": 1710000900000,
                    "update_time": 1710000900000,
                },
                {
                    "order_id": "1-close-b",
                    "symbol": "BTCUSDT",
                    "status": "FILLED",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.5,
                    "reduce_only": True,
                    "time": 1710001800000,
                    "update_time": 1710001800000,
                },
            ],
            "fills": [
                {
                    "order_id": "1-open",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "quantity": 1.0,
                    "price": 65000,
                    "realized_pnl": 0,
                    "fee": 0.0,
                    "time": 1710000000000,
                },
                {
                    "order_id": "1-close-a",
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.5,
                    "price": 65200,
                    "realized_pnl": 10,
                    "fee": 0.0,
                    "time": 1710000900000,
                },
                {
                    "order_id": "1-close-b",
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "position_side": "LONG",
                    "quantity": 0.5,
                    "price": 64800,
                    "realized_pnl": -15,
                    "fee": 0.0,
                    "time": 1710001800000,
                },
            ],
        }

        result = analysis.analyze_profile(payload)

        self.assertAlmostEqual(result["metrics"]["win_rate"], 0.0)
        self.assertAlmostEqual(result["metrics"]["profit_factor"], 0.0)
        self.assertIsNone(result["metrics"].get("avg_win"))
        self.assertAlmostEqual(result["metrics"]["avg_loss"], 5.0)
        self.assertAlmostEqual(result["metrics"]["median_hold_ms"], 1800000.0)
        self.assertAlmostEqual(result["metrics"]["active_day_trade_average"], 1.0)

    def test_analyze_profile_ignores_current_account_state_when_deriving_risk_score(self) -> None:
        base_payload = {
            "analysis_type": "profile",
            "selected_period": "30d",
            "closed_trade_count": 12,
            "sample_quality": "limited",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710001800000, "update_time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710086400000, "update_time": 1710086400000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710088200000, "update_time": 1710088200000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710172800000, "update_time": 1710172800000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710176400000, "update_time": 1710176400000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 0.5, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65200, "realized_pnl": 12, "fee": 0.5, "time": 1710001800000},
                {"order_id": "2-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65300, "realized_pnl": 0, "fee": 0.5, "time": 1710086400000},
                {"order_id": "2-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65100, "realized_pnl": -6, "fee": 0.5, "time": 1710088200000},
                {"order_id": "3-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65400, "realized_pnl": 0, "fee": 0.5, "time": 1710172800000},
                {"order_id": "3-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65700, "realized_pnl": 18, "fee": 0.5, "time": 1710176400000},
            ],
        }
        flat_result = analysis.analyze_profile(
            {
                **base_payload,
                "balances": [{"asset": "USDT", "equity": 1000, "available_balance": 950}],
                "positions": [],
            }
        )
        loaded_result = analysis.analyze_profile(
            {
                **base_payload,
                "balances": [{"asset": "USDT", "equity": 1000, "available_balance": 50}],
                "positions": [{"symbol": "BTCUSDT", "side": "long", "quantity": 0.01, "notional": 900, "leverage": 50}],
            }
        )

        self.assertAlmostEqual(flat_result["metrics"]["risk_score"], loaded_result["metrics"]["risk_score"])

    def test_analyze_replay_includes_supported_bill_adjustments_in_net_pnl(self) -> None:
        payload = {
            "analysis_type": "replay",
            "market": "futures",
            "orders": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "time": 1710000000000, "update_time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "status": "FILLED", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "reduce_only": True, "time": 1710001800000, "update_time": 1710001800000},
            ],
            "fills": [
                {"order_id": "1-open", "symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "quantity": 0.01, "price": 65000, "realized_pnl": 0, "fee": 0.5, "time": 1710000000000},
                {"order_id": "1-close", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "quantity": 0.01, "price": 65100, "realized_pnl": 10, "fee": 0.5, "time": 1710001800000},
            ],
            "bills": [
                {"market": "futures", "symbol": "BTCUSDT", "type": "position_funding", "amount": -8.0, "time": 1710000900000},
                {"market": "futures", "symbol": "BTCUSDT", "type": "order_liquidate_fee_income", "amount": -2.0, "time": 1710001801000},
            ],
        }

        result = analysis.analyze_replay(payload)

        self.assertAlmostEqual(result["metrics"]["episode_net_pnl"], 9.0)
        self.assertAlmostEqual(result["metrics"]["bill_adjustment_total"], -10.0)
        self.assertAlmostEqual(result["metrics"]["net_pnl"], -1.0)

    def test_analyze_profile_merges_partial_state_from_recomputed_replay_analysis(self) -> None:
        payload = {
            "analysis_type": "profile",
            "selected_period": "30d",
            "closed_trade_count": 4,
            "sample_quality": "minimal",
            "metrics": {
                "median_hold_ms": 90 * 60 * 1000,
                "active_day_trade_average": 2,
                "risk_score": 0.31,
                "win_rate": 0.25,
                "profit_factor": 0.5,
            },
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

        result = analysis.analyze_profile(payload)

        self.assertTrue(result["partial"])
        self.assertIn("replay_carry_in_detected", result["degraded_reasons"])

    def test_analyze_profile_cli_renders_text_for_minimal_sample(self) -> None:
        payload = {
            "analysis_type": "profile",
            "selected_period": "30d",
            "closed_trade_count": 4,
            "sample_quality": "minimal",
            "metrics": {
                "median_hold_ms": 90 * 60 * 1000,
                "active_day_trade_average": 2,
                "risk_score": 0.31,
                "win_rate": 0.5,
                "profit_factor": 1.1,
            },
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-profile", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Profile Summary", completed.stdout)
        self.assertIn("Median Hold Time: 1h 30m", completed.stdout)
        self.assertIn("Sample size is still too small", completed.stdout)
        self.assertIn("cannot predict future market direction", completed.stdout)
        self.assertIn(EXPECTED_STANDARD_DISCLAIMER, completed.stdout)


class OrderRiskAnalysisTests(unittest.TestCase):
    def test_analyze_order_risk_marks_missing_order_context_as_partial(self) -> None:
        result = analysis.analyze_order_risk({})

        self.assertTrue(result["partial"])
        self.assertTrue(result["has_risk"])
        self.assertIn("order_risk_missing_order_preview", result["degraded_reasons"])
        self.assertIn("order_risk_missing_account_snapshot", result["degraded_reasons"])
        self.assertIn("order_context_incomplete", {alert["type"] for alert in result["alerts"]})

    def test_analyze_order_risk_returns_structured_alerts_and_confirmation_hint(self) -> None:
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
                {"symbol": "BTCUSDT", "notional": 400, "leverage": 10},
                {"symbol": "ETHUSDT", "notional": 500, "leverage": 25},
            ],
            "recent_orders": [
                {"symbol": "BTCUSDT", "time": 1710000000000},
                {"symbol": "BTCUSDT", "time": 1710000600000},
                {"symbol": "BTCUSDT", "time": 1710001200000},
                {"symbol": "BTCUSDT", "time": 1710001800000},
                {"symbol": "BTCUSDT", "time": 1710002400000},
            ],
            "market_snapshot": {
                "current_price": 65000,
            },
        }

        result = analysis.analyze_order_risk(payload)
        alert_types = {alert["type"] for alert in result["alerts"]}

        self.assertTrue(result["has_risk"])
        self.assertTrue(result["confirmation_required"])
        self.assertIn("missing_tp_sl", alert_types)
        self.assertIn("oversized_position", alert_types)
        self.assertIn("limit_price_too_far", alert_types)
        self.assertIn("high_trade_frequency", alert_types)
        self.assertIn("low_free_balance", alert_types)
        self.assertIn("high_leverage_or_concentration", alert_types)
        self.assertEqual(
            result["next_action_hint"],
            "Review the alerts before continuing with order submission.",
        )

    def test_analyze_order_risk_cli_text_includes_preview_confirmation_and_partial_context(self) -> None:
        payload = {
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
                "equity": None,
                "available_balance": None,
            },
            "positions": [],
            "recent_orders": [],
            "market_snapshot": {
                "current_price": 65000,
            },
            "partial": True,
            "degraded_reasons": ["spot_balance_unavailable", "spot_tp_sl_state_unavailable"],
            "constraints": [],
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-order-risk", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Order Preview:", completed.stdout)
        self.assertIn("Review the alerts before continuing with order submission.", completed.stdout)
        self.assertIn("Partial Analysis", completed.stdout)
        self.assertIn("degraded: spot_balance_unavailable", completed.stdout)
        self.assertIn("degraded: spot_tp_sl_state_unavailable", completed.stdout)
        self.assertIn(EXPECTED_STANDARD_DISCLAIMER, completed.stdout)

    def test_analyze_order_risk_cli_pretty_serializes_tp_sl_review(self) -> None:
        payload = {
            "order_preview": {
                "market": "futures",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "LIMIT",
                "quantity": 1.0,
                "price": 100.0,
            },
            "tp_sl": {
                "has_take_profit": False,
                "has_stop_loss": False,
            },
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 900,
            },
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "position_side": "LONG",
                    "quantity": 2.0,
                    "notional": 200.0,
                    "leverage": 5.0,
                }
            ],
            "open_orders": [],
            "conditional_orders": [],
            "recent_orders": [],
            "market_snapshot": {
                "current_price": 100.0,
            },
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-order-risk", "--input", "-", "--pretty"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        parsed = json.loads(completed.stdout)
        self.assertEqual(parsed["tp_sl_review"]["required_qty"], 3.0)
        self.assertEqual(parsed["disclaimer"], EXPECTED_STANDARD_DISCLAIMER)

    def test_analyze_order_risk_marks_spot_tp_sl_as_degraded(self) -> None:
        payload = {
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
            "degraded_reasons": ["spot_tp_sl_state_unavailable"],
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

        result = analysis.analyze_order_risk(payload)

        alert_types = {alert["type"] for alert in result["alerts"]}
        self.assertNotIn("missing_tp_sl", alert_types)
        self.assertNotIn("high_leverage_or_concentration", alert_types)
        self.assertFalse(result["has_risk"])
        self.assertIn("spot_tp_sl_state_unavailable", result["degraded_reasons"])

    def test_analyze_order_risk_skips_low_free_balance_when_spot_equity_is_partial(self) -> None:
        payload = {
            "order_preview": {
                "market": "spot",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": 0.01,
                "price": 85000,
            },
            "tp_sl": {
                "has_take_profit": False,
                "has_stop_loss": False,
            },
            "degraded_reasons": ["spot_equity_estimate_partial"],
            "account_snapshot": {
                "equity": 100000,
                "available_balance": 100,
            },
            "positions": [],
            "recent_orders": [],
            "market_snapshot": {
                "current_price": 65000,
            },
        }

        result = analysis.analyze_order_risk(payload)

        alert_types = {alert["type"] for alert in result["alerts"]}
        self.assertNotIn("low_free_balance", alert_types)
        self.assertIn("limit_price_too_far", alert_types)

    def test_analyze_order_risk_skips_materiality_alert_for_tiny_high_leverage_exposure(self) -> None:
        payload = {
            "order_preview": {
                "market": "futures",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "LIMIT",
                "quantity": 0.01,
                "price": 65000,
            },
            "tp_sl": {
                "has_take_profit": True,
                "has_stop_loss": True,
            },
            "account_snapshot": {
                "equity": 40000,
                "available_balance": 39900,
            },
            "positions": [
                {"symbol": "GOMININGUSDT", "notional": 152.4, "leverage": 20},
            ],
            "recent_orders": [],
            "market_snapshot": {
                "current_price": 65000,
            },
        }

        result = analysis.analyze_order_risk(payload)

        alert_types = {alert["type"] for alert in result["alerts"]}
        self.assertNotIn("high_leverage_or_concentration", alert_types)

    def test_analyze_order_risk_counts_working_entry_orders_in_worst_case_exposure(self) -> None:
        payload = {
            "order_preview": {
                "market": "futures",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "LIMIT",
                "quantity": 1.0,
                "price": 100,
            },
            "tp_sl": {
                "has_take_profit": True,
                "has_stop_loss": True,
            },
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 900,
            },
            "positions": [],
            "recent_orders": [],
            "open_orders": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "BUY",
                    "position_side": "LONG",
                    "quantity": 8.0,
                    "executed_qty": 0.0,
                    "price": 100,
                    "reduce_only": False,
                    "close_position": False,
                }
            ],
            "conditional_orders": [],
            "market_snapshot": {
                "current_price": 100,
            },
        }

        result = analysis.analyze_order_risk(payload)

        alert_types = {alert["type"] for alert in result["alerts"]}
        self.assertTrue(result["has_risk"])
        self.assertIn("high_leverage_or_concentration", alert_types)

    def test_analyze_order_risk_requires_full_tp_sl_coverage_for_resulting_size(self) -> None:
        payload = {
            "order_preview": {
                "market": "futures",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "LIMIT",
                "quantity": 0.1,
                "price": 65000,
            },
            "tp_sl": {
                "has_take_profit": True,
                "has_stop_loss": True,
            },
            "account_snapshot": {
                "equity": 10000,
                "available_balance": 7000,
            },
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "position_side": "LONG",
                    "quantity": 1.0,
                    "notional": 65000,
                    "leverage": 5,
                }
            ],
            "recent_orders": [],
            "open_orders": [],
            "conditional_orders": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "position_side": "LONG",
                    "quantity": 0.1,
                    "executed_qty": 0.0,
                    "tp_trigger_price": 68000,
                    "sl_trigger_price": 62000,
                }
            ],
            "market_snapshot": {
                "current_price": 65000,
            },
        }

        result = analysis.analyze_order_risk(payload)

        alert_types = {alert["type"] for alert in result["alerts"]}
        self.assertIn("missing_tp_sl", alert_types)

    def test_analyze_account_risk_cli_returns_structured_alerts(self) -> None:
        payload = {
            "mode": "account_scan",
            "market": "futures",
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 120,
            },
            "positions": [
                {"symbol": "BTCUSDT", "notional": 700, "leverage": 25},
                {"symbol": "ETHUSDT", "notional": 200, "leverage": 5},
            ],
            "recent_orders": [
                {"symbol": "BTCUSDT", "time": 1710000000000},
                {"symbol": "BTCUSDT", "time": 1710000300000},
                {"symbol": "BTCUSDT", "time": 1710000600000},
                {"symbol": "BTCUSDT", "time": 1710000900000},
                {"symbol": "BTCUSDT", "time": 1710001200000},
            ],
            "conditional_orders": [],
            "open_orders": [],
            "degraded_reasons": [],
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-account-risk", "--input", "-", "--pretty"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["has_risk"])
        self.assertGreaterEqual(len(result["alerts"]), 2)
        self.assertEqual(result["disclaimer"], EXPECTED_STANDARD_DISCLAIMER)

    def test_analyze_account_risk_cli_text_includes_partial_context(self) -> None:
        payload = {
            "mode": "account_scan",
            "market": "spot",
            "account_snapshot": {
                "equity": None,
                "available_balance": None,
            },
            "positions": [],
            "recent_orders": [],
            "conditional_orders": [],
            "open_orders": [],
            "partial": True,
            "degraded_reasons": ["spot_balance_unavailable", "spot_tp_sl_state_unavailable"],
            "constraints": [],
        }

        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "weex_analysis_cli.py"), "analyze-account-risk", "--input", "-", "--format", "text"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Partial account risk scan completed with no immediate alerts.", completed.stdout)
        self.assertIn("Partial Analysis", completed.stdout)
        self.assertIn("degraded: spot_balance_unavailable", completed.stdout)
        self.assertIn("degraded: spot_tp_sl_state_unavailable", completed.stdout)
        self.assertIn(EXPECTED_STANDARD_DISCLAIMER, completed.stdout)

    def test_analyze_account_risk_returns_partial_summary_when_no_alerts_and_degraded(self) -> None:
        payload = {
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
            "partial": False,
            "degraded_reasons": ["spot_tp_sl_state_unavailable"],
            "constraints": [],
        }

        result = analysis.analyze_account_risk(payload)

        self.assertEqual(result["summary"], "Partial account risk scan completed with no immediate alerts.")
        self.assertFalse(result["has_risk"])

    def test_analyze_account_risk_requires_protection_per_position_side_in_hedge_mode(self) -> None:
        payload = {
            "mode": "account_scan",
            "market": "futures",
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 500,
            },
            "positions": [
                {"symbol": "BTCUSDT", "position_side": "LONG", "notional": 120, "leverage": 5},
                {"symbol": "BTCUSDT", "position_side": "SHORT", "notional": 100, "leverage": 5},
            ],
            "recent_orders": [],
            "conditional_orders": [
                {"symbol": "BTCUSDT", "position_side": "LONG", "tp_trigger_price": 71000},
            ],
            "open_orders": [],
            "degraded_reasons": [],
        }

        result = analysis.analyze_account_risk(payload)
        protection_alert = next(
            (alert for alert in result["alerts"] if alert["type"] == "missing_position_protection"),
            None,
        )

        self.assertTrue(result["has_risk"])
        self.assertIsNotNone(protection_alert)
        self.assertIn("short", protection_alert["target"].lower())

    def test_analyze_account_risk_accepts_symbol_level_protection_in_one_way_mode(self) -> None:
        payload = {
            "mode": "account_scan",
            "market": "futures",
            "account_snapshot": {
                "equity": 1000,
                "available_balance": 500,
            },
            "positions": [
                {"symbol": "BTCUSDT", "side": "BUY", "position_mode": "ONE_WAY", "notional": 120, "leverage": 5},
            ],
            "recent_orders": [],
            "conditional_orders": [
                {"symbol": "BTCUSDT", "side": "SELL", "position_mode": "ONE_WAY", "tp_trigger_price": 71000},
            ],
            "open_orders": [],
            "degraded_reasons": [],
        }

        result = analysis.analyze_account_risk(payload)
        alert_types = {alert["type"] for alert in result["alerts"]}

        self.assertNotIn("missing_position_protection", alert_types)

    def test_analyze_account_risk_requires_full_tp_sl_quantity_coverage(self) -> None:
        payload = {
            "mode": "account_scan",
            "market": "futures",
            "account_snapshot": {
                "equity": 10000,
                "available_balance": 9000,
            },
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "position_side": "LONG",
                    "quantity": 1.0,
                    "notional": 100.0,
                    "leverage": 1,
                }
            ],
            "recent_orders": [],
            "conditional_orders": [
                {
                    "symbol": "BTCUSDT",
                    "position_side": "LONG",
                    "quantity": 0.1,
                    "executed_qty": 0.0,
                    "tp_trigger_price": 71000,
                    "sl_trigger_price": 62000,
                }
            ],
            "open_orders": [],
            "degraded_reasons": [],
        }

        result = analysis.analyze_account_risk(payload)

        alert_types = {alert["type"] for alert in result["alerts"]}
        self.assertIn("missing_position_protection", alert_types)

    def test_analyze_account_risk_skips_concentration_when_total_exposure_is_tiny_vs_equity(self) -> None:
        payload = {
            "mode": "account_scan",
            "market": "futures",
            "account_snapshot": {
                "equity": 40115.22652044,
                "available_balance": 39974.28914506,
            },
            "positions": [
                {
                    "symbol": "GOMININGUSDT",
                    "side": "long",
                    "notional": 152.4,
                    "leverage": 20,
                }
            ],
            "recent_orders": [],
            "conditional_orders": [],
            "open_orders": [],
            "degraded_reasons": [],
        }

        result = analysis.analyze_account_risk(payload)
        alert_types = {alert["type"] for alert in result["alerts"]}

        self.assertNotIn("concentration_risk", alert_types)
        self.assertIn("high_position_leverage", alert_types)

    def test_analyze_account_risk_skips_low_free_balance_when_spot_equity_is_partial(self) -> None:
        payload = {
            "mode": "account_scan",
            "market": "spot",
            "account_snapshot": {
                "equity": 100000,
                "available_balance": 100,
            },
            "positions": [],
            "recent_orders": [],
            "conditional_orders": [],
            "open_orders": [],
            "degraded_reasons": ["spot_equity_estimate_partial"],
        }

        result = analysis.analyze_account_risk(payload)

        alert_types = {alert["type"] for alert in result["alerts"]}
        self.assertNotIn("low_free_balance", alert_types)


if __name__ == "__main__":
    unittest.main()
