#!/usr/bin/env python3
from __future__ import annotations

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
SCRIPT = SCRIPTS / "weex_monitor_cli.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import weex_monitor_cli as monitor  # noqa: E402


class MonitorTaskTests(unittest.TestCase):
    def _prepare_and_confirm(
        self,
        task_json: dict[str, object],
        *,
        now_ms: int = 1000,
    ) -> dict[str, object]:
        prepared = monitor.prepare_confirmation(task_json, now_ms=now_ms)
        return monitor.confirm_task(
            prepared["task"],
            confirm_monitor=True,
            confirmation_token=prepared["confirmation_token"],
            now_ms=now_ms + 1,
        )

    def test_symbol_price_monitor_is_no_longer_supported(self) -> None:
        task_json = {
            "task_type": "symbol_price_monitor",
            "profile": "demo",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "last_price",
                "operator": ">",
                "threshold": "70000",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
                "quantity": "0.01",
            },
            "callback": {"type": "current_thread"},
        }

        with self.assertRaisesRegex(monitor.MonitorInputError, "unsupported task_type"):
            monitor.normalize_task(task_json, now_ms=1000)

        parser = monitor.build_parser()
        self.assertIsNone(parser._subparsers._group_actions[0].choices.get("build-price-order"))
        self.assertIsNone(parser._subparsers._group_actions[0].choices.get("submit-price-order"))
        self.assertIsNone(parser._subparsers._group_actions[0].choices.get("reconcile-price-order"))

    def test_position_pnl_monitor_defaults_to_five_seconds_and_rejects_too_fast(self) -> None:
        base_task = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "demo",
            "symbol": "ETHUSDT",
            "position_side": "SHORT",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">=",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "SHORT",
            },
            "callback": {"type": "current_thread"},
        }

        task = monitor.normalize_task(base_task, now_ms=1000)

        self.assertEqual(task["frequency_seconds"], 5)
        self.assertEqual(task["trading_mode"], "demo")
        self.assertEqual(task["environment"]["trading_mode"], "demo")

        too_fast = dict(base_task)
        too_fast["frequency_seconds"] = 2
        with self.assertRaisesRegex(monitor.MonitorInputError, "frequency_seconds"):
            monitor.normalize_task(too_fast, now_ms=1000)

    def test_position_pnl_monitor_requires_explicit_trading_mode(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "symbol": "ETHUSDT",
            "position_side": "SHORT",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">=",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "SHORT",
            },
            "callback": {"type": "current_thread"},
        }

        with self.assertRaisesRegex(monitor.MonitorInputError, "trading_mode is required"):
            monitor.normalize_task(task_json, now_ms=1000)

    def test_position_pnl_monitor_accepts_explicit_demo_trading_mode(self) -> None:
        task = monitor.normalize_task(
            {
                "task_type": "position_pnl_monitor",
                "profile": "demo-profile",
                "trading_mode": "demo",
                "symbol": "BTCSUSDT",
                "position_side": "LONG",
                "condition": {
                    "metric": "unrealized_pnl",
                    "operator": ">",
                    "threshold": "10",
                },
                "action": {
                    "type": "market_close",
                    "target": "LONG",
                },
                "callback": {"type": "current_thread"},
            },
            now_ms=1000,
        )

        self.assertEqual(task["trading_mode"], "demo")
        self.assertEqual(task["environment"]["trading_mode"], "demo")
        self.assertFalse(task["environment"]["uses_real_funds"])

    def test_price_threshold_condition_error_points_to_official_conditional_orders(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo-profile",
            "trading_mode": "demo",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "price",
                "operator": ">",
                "threshold": "70000",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }

        with self.assertRaises(monitor.MonitorInputError) as raised:
            monitor.normalize_task(task_json, now_ms=1000)

        message = str(raised.exception)
        self.assertIn("unrealized_pnl", message)
        self.assertIn("weex-trader-skill", message)
        self.assertIn("official conditional orders", message)

    def test_confirmation_fingerprint_binds_trading_mode(self) -> None:
        base_task = {
            "task_type": "position_pnl_monitor",
            "profile": "demo-profile",
            "trading_mode": "live",
            "symbol": "BTCSUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "10",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        live_task = dict(base_task, trading_mode="live")
        demo_task = dict(base_task, trading_mode="demo")

        self.assertNotEqual(
            monitor._confirmation_fingerprint(live_task),
            monitor._confirmation_fingerprint(demo_task),
        )

    def test_pnl_trigger_builds_directional_market_close_plan_without_live_execution(self) -> None:
        task = monitor.normalize_task(
            {
                "task_type": "position_pnl_monitor",
                "profile": "demo",
                "trading_mode": "live",
                "symbol": "ETHUSDT",
                "position_side": "SHORT",
                "condition": {
                    "metric": "unrealized_pnl",
                    "operator": ">",
                    "threshold": "25",
                },
                "action": {
                    "type": "market_close",
                    "target": "SHORT",
                },
                "callback": {"type": "current_thread"},
            },
            now_ms=1000,
        )
        positions = [
            {
                "symbol": "ETHUSDT",
                "side": "SHORT",
                "size": "0.2",
                "unrealizePnl": "31.5",
            }
        ]

        result = monitor.evaluate_pnl_task(task, positions)

        self.assertTrue(result["triggered"])
        self.assertEqual(result["reason"], "condition_matched")
        self.assertEqual(result["execution_delegate"], "weex-trader-skill")
        self.assertEqual(
            result["close_order"],
            {
                "symbol": "ETHUSDT",
                "side": "BUY",
                "position_side": "SHORT",
                "order_type": "MARKET",
                "quantity": "0.2",
            },
        )

    def test_live_delegate_plan_carries_trading_mode(self) -> None:
        task = monitor.normalize_task(
            {
                "task_type": "position_pnl_monitor",
                "profile": "demo-profile",
                "trading_mode": "demo",
                "symbol": "BTCSUSDT",
                "position_side": "LONG",
                "condition": {
                    "metric": "unrealized_pnl",
                    "operator": ">",
                    "threshold": "10",
                },
                "action": {
                    "type": "market_close",
                    "target": "LONG",
                },
                "callback": {"type": "current_thread"},
            },
            now_ms=1000,
        )
        evaluation_result = {
            "triggered": True,
            "close_order": {
                "symbol": "BTCSUSDT",
                "side": "SELL",
                "position_side": "LONG",
                "order_type": "MARKET",
                "quantity": "0.01",
            },
            "trigger_snapshot": {"unrealized_pnl": "11"},
        }

        plan = monitor.build_live_delegate_plan(task, evaluation_result, purpose="dry-run-trigger")

        self.assertEqual(plan["trading_mode"], "demo")
        self.assertEqual(plan["environment"]["trading_mode"], "demo")
        self.assertEqual(plan["requires_trading_mode_authorization"], True)
        self.assertEqual(plan["requires_real_trading_authorization"], False)
        self.assertEqual(plan["requires_demo_trading_authorization"], True)
        self.assertNotIn("requires_trading_environment_authorization", plan)
        self.assertNotIn("requires_real_trading_environment_authorization", plan)
        self.assertNotIn("requires_simulated_futures_environment_authorization", plan)
        self.assertNotIn("requires_demo_account_authorization", plan)
        self.assertIn("--trading-mode demo", plan["instruction"])

    def test_collect_live_account_payload_delegates_with_task_trading_mode(self) -> None:
        task = monitor.normalize_task(
            {
                "task_type": "position_pnl_monitor",
                "profile": "demo-profile",
                "trading_mode": "demo",
                "symbol": "BTCSUSDT",
                "position_side": "LONG",
                "condition": {
                    "metric": "unrealized_pnl",
                    "operator": ">",
                    "threshold": "10",
                },
                "action": {
                    "type": "market_close",
                    "target": "LONG",
                },
                "callback": {"type": "current_thread"},
            },
            now_ms=1000,
        )

        with mock.patch.object(
            monitor,
            "_run_json_command",
            return_value={"positions": [], "trading_mode": "demo"},
        ) as run_mock:
            payload = monitor._collect_live_account_payload(task)

        command = run_mock.call_args.args[0]
        self.assertEqual(payload["trading_mode"], "demo")
        self.assertIn("--trading-mode", command)
        self.assertIn("demo", command)

    def test_confirm_requires_explicit_monitor_confirmation_before_active_task_is_saved(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "10",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        with tempfile.TemporaryDirectory() as tempdir:
            with self.assertRaisesRegex(monitor.MonitorInputError, "confirm-monitor"):
                with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                    monitor.confirm_task(task_json, confirm_monitor=False, now_ms=1000)
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with self.assertRaisesRegex(monitor.MonitorInputError, "confirmation-token"):
                    monitor.confirm_task(task_json, confirm_monitor=True, now_ms=1000)
                prepared = monitor.prepare_confirmation(task_json, now_ms=1000)
                confirmed = monitor.confirm_task(
                    prepared["task"],
                    confirm_monitor=True,
                    confirmation_token=prepared["confirmation_token"],
                    now_ms=1001,
                )
                tasks = monitor.load_tasks()
                events = monitor.load_events(confirmed["task_id"])

        self.assertEqual(confirmed["status"], "active")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], confirmed["task_id"])
        self.assertEqual(
            [event["event_type"] for event in events],
            ["task_confirmation_rendered", "task_confirmed"],
        )

    def test_load_events_redacts_confirmation_tokens(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "demo",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                prepared = monitor.prepare_confirmation(task_json, now_ms=1000)
                token = prepared["confirmation_token"]
                events = monitor.load_events(prepared["task"]["task_id"])

        rendered_event = events[0]
        self.assertEqual(rendered_event["event_type"], "task_confirmation_rendered")
        self.assertIn("confirmation_token", rendered_event["payload"])
        self.assertNotEqual(rendered_event["payload"]["confirmation_token"], token)
        self.assertEqual(rendered_event["payload"]["confirmation_token"], "<redacted>")

    def test_explicit_pnl_close_quantity_must_be_positive(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "ETHUSDT",
            "position_side": "SHORT",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "25",
            },
            "action": {
                "type": "market_close",
                "target": "SHORT",
                "quantity": "0",
            },
            "callback": {"type": "current_thread"},
        }

        with self.assertRaisesRegex(monitor.MonitorInputError, "action.quantity"):
            monitor.normalize_task(task_json, now_ms=1000)

    def test_monitor_market_scope_is_futures_only(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "market": "spot",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "10",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }

        with self.assertRaisesRegex(monitor.MonitorInputError, "market"):
            monitor.normalize_task(task_json, now_ms=1000)

    def test_confirm_persists_to_sqlite_and_writes_event(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "10",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                confirmed = self._prepare_and_confirm(task_json, now_ms=1000)
                tasks = monitor.load_tasks()
                events = monitor.load_events(confirmed["task_id"])

                self.assertTrue(monitor.db_path().exists())

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "active")
        self.assertEqual([event["event_type"] for event in events], ["task_confirmation_rendered", "task_confirmed"])
        self.assertEqual(events[-1]["payload"]["status"], "active")

    def test_monitor_store_uses_owner_only_permissions(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "10",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir) / "monitor-home"
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": str(home)}, clear=False):
                monitor.prepare_confirmation(task_json, now_ms=1000)

                home_mode = home.stat().st_mode & 0o777
                db_mode = monitor.db_path().stat().st_mode & 0o777

        self.assertEqual(home_mode, 0o700)
        self.assertEqual(db_mode, 0o600)

    def test_confirmation_token_consumption_detects_concurrent_use(self) -> None:
        task = monitor.normalize_task(
            {
                "task_id": "mon_token_race",
                "task_type": "position_pnl_monitor",
                "profile": "demo",
                "trading_mode": "live",
                "symbol": "BTCUSDT",
                "position_side": "LONG",
                "condition": {
                    "metric": "unrealized_pnl",
                    "operator": ">",
                    "threshold": "10",
                },
                "action": {
                    "type": "market_close",
                    "target": "LONG",
                },
                "callback": {"type": "current_thread"},
            },
            now_ms=1000,
        )

        class FakeCursor:
            def __init__(self, rowcount: int) -> None:
                self.rowcount = rowcount

        class FakeConnection:
            def __init__(self) -> None:
                self.update_sql = ""

            def execute(self, sql: str, params: tuple[object, ...] = ()) -> object:
                if sql.lstrip().upper().startswith("SELECT"):
                    return self
                if sql.lstrip().upper().startswith("UPDATE"):
                    self.update_sql = sql
                    if "used_at_ms IS NULL" in sql:
                        return FakeCursor(rowcount=0)
                    return FakeCursor(rowcount=1)
                raise AssertionError(f"unexpected SQL: {sql}")

            def fetchone(self) -> dict[str, object]:
                return {
                    "task_id": task["task_id"],
                    "task_hash": monitor._confirmation_fingerprint(task),
                    "used_at_ms": None,
                }

        fake_conn = FakeConnection()

        with self.assertRaisesRegex(monitor.MonitorInputError, "already been used"):
            monitor._consume_confirmation_token(
                fake_conn,  # type: ignore[arg-type]
                confirmation_token="token-race",
                task=task,
                used_at_ms=2000,
            )
        self.assertIn("used_at_ms IS NULL", fake_conn.update_sql)

    def test_cancel_updates_sqlite_status_and_writes_event(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "10",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                confirmed = self._prepare_and_confirm(task_json, now_ms=1000)
                cancelled = monitor.cancel_task(confirmed["task_id"], now_ms=2000)
                tasks = monitor.load_tasks()
                events = monitor.load_events(confirmed["task_id"])

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(tasks[0]["status"], "cancelled")
        self.assertEqual(
            [event["event_type"] for event in events],
            ["task_confirmation_rendered", "task_confirmed", "task_cancelled"],
        )

    def test_confirmation_text_mentions_task_details_without_internal_flags(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }

        text = monitor.render_confirmation_text(task_json, now_ms=1000)

        self.assertIn("自动化监控", text)
        self.assertIn("BTCUSDT", text)
        self.assertIn("多单", text)
        self.assertIn("未实现盈亏 > 50", text)
        self.assertIn("授权使用真实盘", text)
        self.assertTrue(text.startswith("当前交易环境： 真实盘\n"))
        self.assertIn("资金说明: 会使用真实资金", text)
        self.assertNotIn("盘别:", text)
        self.assertIn("请回复：确认", text)
        self.assertNotIn("确认启动监控", text)
        self.assertNotIn("--confirm-monitor", text)
        self.assertNotIn("--confirm-live", text)

    def test_demo_confirmation_text_labels_demo_market_close_action(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "demo",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": "<",
                "threshold": "0",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
                "quantity": "0.001",
            },
            "callback": {"type": "current_thread"},
        }

        text = monitor.render_confirmation_text(task_json, now_ms=1000)

        self.assertTrue(text.startswith("当前交易环境： 模拟盘\n"))
        self.assertIn("资金说明: 不会使用真实资金", text)
        self.assertNotIn("盘别:", text)
        self.assertNotIn("盘别: demo", text)
        self.assertIn("提交模拟盘市价平多单", text)
        self.assertNotIn("提交真实市价平多单", text)

    def test_demo_confirmation_text_labels_demo_position_snapshot(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "demo",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "1",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        position_snapshot = {
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "quantity": "0.001",
            "unrealized_pnl": "2",
        }

        zh_text = monitor.render_confirmation_text(
            task_json,
            now_ms=1000,
            position_snapshot=position_snapshot,
            language="zh",
        )
        en_text = monitor.render_confirmation_text(
            task_json,
            now_ms=1000,
            position_snapshot=position_snapshot,
            language="en",
        )

        self.assertIn("已匹配模拟盘持仓", zh_text)
        self.assertNotIn("已匹配真实持仓", zh_text)
        self.assertIn("Matched simulated futures position", en_text)
        self.assertNotIn("Matched simulated account position", en_text)
        self.assertNotIn("Matched demo position", en_text)
        self.assertNotIn("Matched live position", en_text)

    def test_demo_dry_run_thread_report_uses_demo_authorization_wording(self) -> None:
        report = monitor.render_thread_report(
            {
                "task_id": "demo-task",
                "live_delegate_plan": {
                    "trading_mode": "demo",
                    "environment": {
                        "trading_mode": "demo",
                        "uses_real_funds": False,
                    },
                },
                "result": {
                    "triggered": True,
                    "trigger_snapshot": {
                        "symbol": "BTCUSDT",
                        "position_side": "LONG",
                        "unrealized_pnl": "-1",
                        "operator": "<",
                        "threshold": "0",
                    },
                    "close_order": {
                        "symbol": "BTCUSDT",
                        "side": "SELL",
                        "position_side": "LONG",
                        "order_type": "MARKET",
                        "quantity": "0.001",
                    },
                },
            }
        )

        self.assertTrue(report.startswith("当前交易环境： 模拟盘\n"))
        self.assertIn("Demo trading authorization is required", report)
        self.assertNotIn("Demo-account authorization is required", report)
        self.assertNotIn("Real-account authorization is required", report)

    def test_english_confirmation_text_uses_simple_localized_reply_word(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": "<",
                "threshold": "0",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }

        text = monitor.render_confirmation_text(task_json, now_ms=1000, language="en")

        self.assertTrue(text.startswith("Current trading mode: real trading\n"))
        self.assertIn("Automated Monitor Confirmation", text)
        self.assertIn("Monitor target: BTCUSDT long position", text)
        self.assertIn("Funds: uses real funds", text)
        self.assertNotIn("Trading environment:", text)
        self.assertNotIn("Trading mode: real account", text)
        self.assertNotIn("Trading mode: real trading (uses real funds)", text)
        self.assertIn("Trigger condition: Unrealized PnL < 0", text)
        self.assertIn("Reply: confirm", text)
        self.assertNotIn("Trading mode: live", text)
        self.assertNotIn("Reply: 确认", text)
        self.assertNotIn("请回复", text)
        self.assertNotIn("--confirm-monitor", text)
        self.assertNotIn("--confirm-live", text)

    def test_confirmation_text_binds_close_quantity_semantics(self) -> None:
        fixed_quantity_task = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">=",
                "threshold": "0",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
                "quantity": "0.05",
            },
            "callback": {"type": "current_thread"},
        }
        matched_size_task = json.loads(json.dumps(fixed_quantity_task))
        del matched_size_task["action"]["quantity"]

        fixed_text = monitor.render_confirmation_text(fixed_quantity_task, now_ms=1000)
        matched_text = monitor.render_confirmation_text(matched_size_task, now_ms=1000)

        self.assertIn("平仓数量: 0.05", fixed_text)
        self.assertIn("平仓数量: 触发时匹配持仓数量", matched_text)

    def test_live_confirmation_text_includes_matched_position_snapshot(self) -> None:
        task_json = {
            "task_id": "mon_live_confirm",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "frequency_seconds": 5,
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "12.34",
                    "entry_price": "78000",
                    "margin_type": "CROSSED",
                    "leverage": "10",
                    "available_quantity": "0.008",
                    "liquidation_price": "65000",
                    "updated_time": "1710000000000",
                }
            ],
            "account_snapshot": {
                "available_balance": "123.45",
            },
            "market_snapshot": {
                "current_price": "78123.4",
            },
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(monitor, "_run_json_command", return_value=account_payload) as runner:
                    prepared = monitor.prepare_live_confirmation(
                        task_json,
                        duration_seconds=3600,
                        now_ms=1000,
                    )
                tasks = monitor.load_tasks()
                events = monitor.load_events(task_json["task_id"])

        runner.assert_called_once()
        self.assertEqual(prepared["task"]["status"], "draft")
        self.assertEqual(prepared["task"]["live_position_confirmation"]["quantity"], "0.01")
        self.assertEqual(prepared["duration_seconds"], 3600.0)
        self.assertIn("已匹配真实持仓", prepared["confirmation_text"])
        self.assertIn("BTCUSDT 多单", prepared["confirmation_text"])
        self.assertIn("持仓数量: 0.01", prepared["confirmation_text"])
        self.assertIn("当前未实现盈亏: 12.34", prepared["confirmation_text"])
        self.assertIn("仓位明细:", prepared["confirmation_text"])
        self.assertIn("开仓均价: 78000", prepared["confirmation_text"])
        self.assertIn("标记/最新价: 78123.4", prepared["confirmation_text"])
        self.assertIn("杠杆: 10", prepared["confirmation_text"])
        self.assertIn("保证金模式: CROSSED", prepared["confirmation_text"])
        self.assertIn("可平数量: 0.008", prepared["confirmation_text"])
        self.assertIn("强平价: 65000", prepared["confirmation_text"])
        self.assertIn("仓位更新时间: 1710000000000", prepared["confirmation_text"])
        self.assertIn("账户可用余额: 123.45", prepared["confirmation_text"])
        self.assertIn("确认快照时间:", prepared["confirmation_text"])
        self.assertIn("授权使用真实盘", prepared["confirmation_text"])
        self.assertTrue(prepared["confirmation_text"].startswith("当前交易环境： 真实盘\n"))
        self.assertNotIn("盘别:", prepared["confirmation_text"])
        self.assertIn("请回复：确认", prepared["confirmation_text"])
        self.assertNotIn("确认启动监控", prepared["confirmation_text"])
        self.assertNotIn("--confirm-live", prepared["confirmation_text"])
        self.assertNotIn("--confirm-monitor", prepared["confirmation_text"])
        self.assertEqual(tasks[0]["live_position_confirmation"]["quantity"], "0.01")
        self.assertEqual(tasks[0]["live_position_confirmation"]["entry_price"], "78000")
        self.assertEqual(tasks[0]["live_position_confirmation"]["current_price"], "78123.4")
        self.assertEqual(events[0]["payload"]["live_position_confirmation"]["quantity"], "0.01")

    def test_live_confirmation_text_warns_when_fixed_close_quantity_differs_from_aggregate_position(self) -> None:
        task_json = {
            "task_id": "mon_live_aggregate_warning",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "demo",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "frequency_seconds": 5,
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "2",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
                "quantity": "0.01",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.022",
                    "unrealized_pnl": "-4.86",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(monitor, "_run_json_command", return_value=account_payload):
                    zh_prepared = monitor.prepare_live_confirmation(
                        task_json,
                        duration_seconds=3600,
                        now_ms=1000,
                        language="zh",
                    )
                    en_prepared = monitor.prepare_live_confirmation(
                        dict(task_json, task_id="mon_live_aggregate_warning_en"),
                        duration_seconds=3600,
                        now_ms=2000,
                        language="en",
                    )

        self.assertIn("聚合持仓未实现盈亏", zh_prepared["confirmation_text"])
        self.assertIn("不是单笔订单独立盈亏", zh_prepared["confirmation_text"])
        self.assertIn("聚合持仓数量 0.022 与固定平仓数量 0.01 不同", zh_prepared["confirmation_text"])
        self.assertIn(
            "已匹配模拟盘持仓: BTCUSDT 多单, 持仓数量: 0.022, "
            "聚合持仓总未实现盈亏: -4.86, "
            "按固定平仓数量 0.01 折算未实现盈亏: -2.20909091",
            zh_prepared["confirmation_text"],
        )
        self.assertNotIn("当前未实现盈亏: -4.86", zh_prepared["confirmation_text"])
        self.assertIn("aggregate position unrealized PnL", en_prepared["confirmation_text"])
        self.assertIn("not isolated single-order PnL", en_prepared["confirmation_text"])
        self.assertIn(
            "aggregate position size 0.022 differs from fixed close quantity 0.01",
            en_prepared["confirmation_text"],
        )
        self.assertIn(
            "Matched simulated futures position: BTCUSDT long position, position size: 0.022, "
            "aggregate total unrealized PnL: -4.86, "
            "unrealized PnL prorated to fixed close quantity 0.01: -2.20909091",
            en_prepared["confirmation_text"],
        )
        self.assertNotIn("current unrealized PnL: -4.86", en_prepared["confirmation_text"])

    def test_live_confirmation_defaults_codex_reporting_to_one_minute(self) -> None:
        task_json = {
            "task_id": "mon_live_report_default",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "frequency_seconds": 5,
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "12.34",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(monitor, "_run_json_command", return_value=account_payload):
                    prepared = monitor.prepare_live_confirmation(
                        task_json,
                        duration_seconds=3600,
                        now_ms=1000,
                    )

        reporting = prepared["reporting"]
        agent_reporting = prepared["agent_reporting"]
        self.assertTrue(reporting["enabled"])
        self.assertEqual(reporting["interval_seconds"], 60)
        self.assertEqual(prepared["task"]["codex_reporting"]["interval_seconds"], 60)
        self.assertEqual(prepared["task"]["agent_reporting"]["interval_seconds"], 60)
        self.assertIn("状态汇报: 每 1 分钟", prepared["confirmation_text"])
        self.assertIn("mon_live_report_default", reporting["heartbeat_prompt"])
        self.assertIn("events --task-id mon_live_report_default", reporting["heartbeat_prompt"])
        self.assertIn("当前交易环境： 真实盘", reporting["heartbeat_prompt"])
        self.assertIn("Start the status report with this exact first line", reporting["heartbeat_prompt"])
        self.assertIn("sanitized summaries", reporting["heartbeat_prompt"])
        self.assertNotIn("include those", reporting["heartbeat_prompt"])
        self.assertIn("Do not output HTML entities", reporting["heartbeat_prompt"])
        self.assertIn("less than", reporting["heartbeat_prompt"])
        self.assertIn("小于", reporting["heartbeat_prompt"])
        self.assertNotIn("&lt;", reporting["heartbeat_prompt"])
        self.assertNotIn("&gt;", reporting["heartbeat_prompt"])
        self.assertIn("codex", agent_reporting["runtimes"])
        self.assertIn("claude_code", agent_reporting["runtimes"])
        self.assertIn("openclaw", agent_reporting["runtimes"])
        self.assertEqual(agent_reporting["runtimes"]["claude_code"]["type"], "claude_code_loop")
        self.assertIn("/loop 1m", agent_reporting["runtimes"]["claude_code"]["loop_command"])
        self.assertIn(
            "events --task-id mon_live_report_default",
            agent_reporting["runtimes"]["claude_code"]["loop_prompt"],
        )
        self.assertIn(
            "Do not output HTML entities",
            agent_reporting["runtimes"]["claude_code"]["loop_prompt"],
        )
        self.assertEqual(agent_reporting["runtimes"]["openclaw"]["type"], "openclaw_cron")
        self.assertEqual(
            agent_reporting["runtimes"]["openclaw"]["create_job_args"][:8],
            [
                "openclaw",
                "cron",
                "add",
                "--name",
                "WEEX monitor mon_live_report_default status",
                "--every",
                "1m",
                "--session",
            ],
        )
        self.assertIn(
            "events --task-id mon_live_report_default",
            agent_reporting["runtimes"]["openclaw"]["message"],
        )
        self.assertIn(
            "Do not output HTML entities",
            agent_reporting["runtimes"]["openclaw"]["message"],
        )

    def test_live_confirmation_accepts_explicit_codex_reporting_interval(self) -> None:
        task_json = {
            "task_id": "mon_live_report_explicit",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "frequency_seconds": 5,
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "12.34",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(monitor, "_run_json_command", return_value=account_payload):
                    prepared = monitor.prepare_live_confirmation(
                        task_json,
                        duration_seconds=3600,
                        reporting_interval_seconds=120,
                        now_ms=1000,
                    )

        self.assertEqual(prepared["reporting"]["interval_seconds"], 120)
        self.assertEqual(prepared["reporting"]["rrule"], "FREQ=MINUTELY;INTERVAL=2")
        self.assertEqual(prepared["agent_reporting"]["runtimes"]["claude_code"]["interval"], "2m")
        self.assertIn("/loop 2m", prepared["agent_reporting"]["runtimes"]["claude_code"]["loop_command"])
        self.assertIn("--every", prepared["agent_reporting"]["runtimes"]["openclaw"]["create_job_args"])
        self.assertIn("2m", prepared["agent_reporting"]["runtimes"]["openclaw"]["create_job_args"])
        self.assertIn("状态汇报: 每 2 分钟", prepared["confirmation_text"])

    def test_live_confirmation_text_marks_missing_position_details_as_not_returned(self) -> None:
        task_json = {
            "task_id": "mon_live_confirm_missing_details",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "frequency_seconds": 5,
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "12.34",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(monitor, "_run_json_command", return_value=account_payload):
                    prepared = monitor.prepare_live_confirmation(
                        task_json,
                        duration_seconds=3600,
                        now_ms=1000,
                    )

        self.assertIn("开仓均价: 未返回", prepared["confirmation_text"])
        self.assertIn("标记/最新价: 未返回", prepared["confirmation_text"])
        self.assertIn("杠杆: 未返回", prepared["confirmation_text"])
        self.assertIn("保证金模式: 未返回", prepared["confirmation_text"])
        self.assertIn("可平数量: 未返回", prepared["confirmation_text"])
        self.assertIn("强平价: 未返回", prepared["confirmation_text"])
        self.assertIn("账户可用余额: 未返回", prepared["confirmation_text"])

    def test_english_live_confirmation_marks_missing_position_details_as_not_returned(self) -> None:
        task_json = {
            "task_id": "mon_live_confirm_missing_details_en",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "frequency_seconds": 5,
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "12.34",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(monitor, "_run_json_command", return_value=account_payload):
                    prepared = monitor.prepare_live_confirmation(
                        task_json,
                        duration_seconds=3600,
                        now_ms=1000,
                        language="en",
                    )

        self.assertIn("entry price: not returned", prepared["confirmation_text"])
        self.assertIn("mark/latest price: not returned", prepared["confirmation_text"])
        self.assertIn("leverage: not returned", prepared["confirmation_text"])
        self.assertIn("margin mode: not returned", prepared["confirmation_text"])
        self.assertIn("closable quantity: not returned", prepared["confirmation_text"])
        self.assertIn("liquidation price: not returned", prepared["confirmation_text"])
        self.assertIn("account available balance: not returned", prepared["confirmation_text"])
        self.assertNotIn("未返回", prepared["confirmation_text"])
        self.assertEqual(prepared["live_position_confirmation"]["entry_price"], "not returned")
        self.assertEqual(prepared["live_position_confirmation"]["account_available_balance"], "not returned")

    def test_live_confirmation_requires_matching_live_position(self) -> None:
        task_json = {
            "task_id": "mon_live_confirm_missing",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(monitor, "_run_json_command", return_value=account_payload):
                    with self.assertRaisesRegex(monitor.MonitorInputError, "live position"):
                        monitor.prepare_live_confirmation(task_json, duration_seconds=3600, now_ms=1000)
                tasks = monitor.load_tasks()

        self.assertEqual(tasks, [])

    def test_confirm_text_cli_persists_draft_and_confirm_requires_token(self) -> None:
        task_json = {
            "task_id": "mon_cli_confirm",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "10",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        with tempfile.TemporaryDirectory() as tempdir:
            env = {**os.environ, "WEEX_MONITOR_SKILL_HOME": tempdir}
            rendered = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "confirm-text",
                    "--task-json",
                    json.dumps(task_json),
                ],
                text=True,
                capture_output=True,
                check=True,
                env=env,
            )
            rendered_payload = json.loads(rendered.stdout)
            self.assertIn("自动化监控", rendered_payload["confirmation_text"])
            self.assertIn("confirmation_token", rendered_payload)
            self.assertEqual(rendered_payload["task"]["status"], "draft")

            rejected = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "confirm",
                    "--task-json",
                    json.dumps(rendered_payload["task"]),
                    "--confirm-monitor",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("confirmation-token", rejected.stderr)
            self.assertIn("confirm-text returned task", rejected.stderr)

            confirmed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "confirm",
                    "--task-json",
                    json.dumps(rendered_payload["task"]),
                    "--confirm-monitor",
                    "--confirmation-token",
                    rendered_payload["confirmation_token"],
                ],
                text=True,
                capture_output=True,
                check=True,
                env=env,
            )
            confirmed_payload = json.loads(confirmed.stdout)

        self.assertEqual(confirmed_payload["status"], "active")

    def test_list_accepts_pretty_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = {**os.environ, "WEEX_MONITOR_SKILL_HOME": tempdir}
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "list", "--pretty"],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), [])

    def test_confirm_text_live_requires_duration_in_help_and_parser(self) -> None:
        help_result = subprocess.run(
            [sys.executable, str(SCRIPT), "confirm-text-live", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--duration-seconds", help_result.stdout)
        self.assertIn("required", help_result.stdout.lower())

        parser = monitor.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["confirm-text-live", "--task-json", "{}"])

    def test_run_loop_missing_mode_error_mentions_confirm_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = {**os.environ, "WEEX_MONITOR_SKILL_HOME": tempdir}
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "run-loop"],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("--confirm-demo", completed.stderr)

    def test_prepare_confirmation_refuses_to_overwrite_non_draft_task(self) -> None:
        task_json = {
            "task_id": "mon_no_overwrite",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "10",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                confirmed = self._prepare_and_confirm(task_json, now_ms=1000)

                with self.assertRaisesRegex(monitor.MonitorInputError, "non-draft"):
                    monitor.prepare_confirmation(task_json, now_ms=2000)

                with mock.patch.object(monitor, "_run_json_command") as runner:
                    with self.assertRaisesRegex(monitor.MonitorInputError, "non-draft"):
                        monitor.prepare_live_confirmation(
                            task_json,
                            duration_seconds=3600,
                            now_ms=3000,
                        )
                completed = dict(confirmed)
                completed["status"] = "completed"
                with monitor._connect() as conn:
                    monitor._upsert_task(conn, completed, updated_at_ms=4000)

                with self.assertRaisesRegex(monitor.MonitorInputError, "non-draft"):
                    monitor.prepare_confirmation(task_json, now_ms=5000)
                tasks = monitor.load_tasks()

        runner.assert_not_called()
        self.assertEqual(confirmed["status"], "active")
        self.assertEqual(tasks[0]["status"], "completed")

    def test_idempotency_key_is_deterministic_and_changes_with_condition(self) -> None:
        task_json = {
            "task_id": "mon_fixed",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        changed = json.loads(json.dumps(task_json))
        changed["condition"]["threshold"] = "75"

        first = monitor.build_idempotency_key(task_json, "pnl-trigger")
        second = monitor.build_idempotency_key(task_json, "pnl-trigger")
        third = monitor.build_idempotency_key(changed, "pnl-trigger")

        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertTrue(first.startswith("monitor:mon_fixed:pnl-trigger:"))

    def test_run_once_dry_run_records_trigger_without_live_execution(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "ETHUSDT",
            "position_side": "SHORT",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "25",
            },
            "action": {
                "type": "market_close",
                "target": "SHORT",
            },
            "callback": {"type": "current_thread"},
        }
        positions = [
            {
                "symbol": "ETHUSDT",
                "side": "SHORT",
                "size": "0.2",
                "unrealizePnl": "31.5",
            }
        ]
        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                confirmed = self._prepare_and_confirm(task_json, now_ms=1000)
                results = monitor.run_once_dry_run(positions, now_ms=2000)
                tasks = monitor.load_tasks()
                events = monitor.load_events(confirmed["task_id"])

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["result"]["triggered"])
        self.assertEqual(results[0]["dry_run"], True)
        self.assertEqual(tasks[0]["status"], "triggered")
        self.assertEqual(results[0]["result"]["execution_delegate"], "weex-trader-skill")
        self.assertIn("idempotency_key", results[0])
        self.assertNotIn("exchange_response", results[0])
        self.assertEqual(
            [event["event_type"] for event in events],
            ["task_confirmation_rendered", "task_confirmed", "dry_run_evaluated", "dry_run_triggered"],
        )

    def test_run_loop_dry_run_is_one_shot_and_returns_thread_report(self) -> None:
        task_json = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "ETHUSDT",
            "position_side": "SHORT",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "25",
            },
            "action": {
                "type": "market_close",
                "target": "SHORT",
            },
            "callback": {"type": "current_thread"},
        }
        positions_sequence = [
            [
                {
                    "symbol": "ETHUSDT",
                    "side": "SHORT",
                    "size": "0.2",
                    "unrealizePnl": "31.5",
                }
            ],
            [
                {
                    "symbol": "ETHUSDT",
                    "side": "SHORT",
                    "size": "0.2",
                    "unrealizePnl": "50",
                }
            ],
        ]
        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                confirmed = self._prepare_and_confirm(task_json, now_ms=1000)
                loop_result = monitor.run_loop_dry_run(
                    positions_sequence,
                    iterations=2,
                    sleep_seconds=0,
                    now_ms=2000,
                )
                tasks = monitor.load_tasks()
                events = monitor.load_events(confirmed["task_id"])

        self.assertEqual(loop_result["dry_run"], True)
        self.assertEqual(loop_result["iterations_requested"], 2)
        self.assertEqual(loop_result["iterations_completed"], 2)
        self.assertEqual(loop_result["triggered_count"], 1)
        self.assertEqual(tasks[0]["status"], "triggered")
        self.assertIn("thread_report", loop_result["iterations"][0]["results"][0])
        self.assertTrue(
            loop_result["iterations"][0]["results"][0]["thread_report"].startswith(
                "当前交易环境： 真实盘\n"
            )
        )
        self.assertIn("Real trading authorization is required", loop_result["iterations"][0]["results"][0]["thread_report"])
        self.assertNotIn("Real-account authorization is required", loop_result["iterations"][0]["results"][0]["thread_report"])
        self.assertNotIn("授权使用真实账户", loop_result["iterations"][0]["results"][0]["thread_report"])
        self.assertEqual(
            [event["event_type"] for event in events],
            ["task_confirmation_rendered", "task_confirmed", "dry_run_evaluated", "dry_run_triggered"],
        )

    def test_trigger_result_builds_live_delegate_plan_without_submitting(self) -> None:
        task_json = {
            "task_id": "mon_delegate",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "ETHUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "25",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        result = monitor.evaluate_pnl_task(
            task_json,
            [{"symbol": "ETHUSDT", "side": "LONG", "size": "0.3", "unrealizePnl": "31.5"}],
        )

        delegate_plan = monitor.build_live_delegate_plan(task_json, result, purpose="pnl-trigger")

        self.assertEqual(delegate_plan["delegate_skill"], "weex-trader-skill")
        self.assertEqual(delegate_plan["requires_real_trading_authorization"], True)
        self.assertNotIn("requires_real_trading_environment_authorization", delegate_plan)
        self.assertNotIn("requires_live_account_authorization", delegate_plan)
        self.assertEqual(delegate_plan["mutating_request_submitted"], False)
        self.assertEqual(delegate_plan["close_order"]["side"], "SELL")
        self.assertTrue(delegate_plan["idempotency_key"].startswith("monitor:mon_delegate:pnl-trigger:"))

    def test_run_live_once_rejects_forged_active_task_without_persisted_confirmation(self) -> None:
        forged_active_task = {
            "task_id": "mon_pnl_forged",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "status": "active",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                task = monitor.normalize_task(forged_active_task, now_ms=1000)
                task["status"] = "active"
                with monitor._connect() as conn:
                    monitor._upsert_task(conn, task, updated_at_ms=1000)
                with mock.patch.object(monitor, "_run_json_command") as runner:
                    results = monitor.run_live_once(confirm_live=True, now_ms=2000)
                tasks = monitor.load_tasks()
                events = monitor.load_events(task["task_id"])

        self.assertEqual(results[0]["status"], "review_required")
        self.assertEqual(results[0]["result"]["reason"], "missing_monitor_confirmation")
        self.assertEqual(tasks[0]["status"], "review_required")
        runner.assert_not_called()
        self.assertIn("live_order_failed", [event["event_type"] for event in events])

    def test_run_live_once_executes_triggered_pnl_close_through_trader_guard(self) -> None:
        task_json = {
            "task_id": "mon_pnl_live",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "51.2",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                confirmed = self._prepare_and_confirm(task_json, now_ms=1000)
                with mock.patch.object(
                    monitor,
                    "_run_json_command",
                    side_effect=[
                        account_payload,
                        account_payload,
                        {"intent_id": "intent-close", "risk_signature": "sig-close"},
                        {
                            "ok": True,
                            "orderId": "9001",
                            "clientOrderId": "monitor_mon_pnl_live",
                            "status": "FILLED",
                            "avgPrice": "70001.2",
                            "accountBalance": "999.99",
                            "rawPosition": {"quantity": "0.01", "entryPrice": "70000"},
                        },
                    ],
                ) as runner:
                    results = monitor.run_live_once(confirm_live=True, now_ms=2000)
                tasks = monitor.load_tasks()
                events = monitor.load_events(confirmed["task_id"])

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["result"]["triggered"])
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(
            results[0]["exchange_response"],
            {
                "ok": True,
                "order_id": "9001",
                "client_order_id": "monitor_mon_pnl_live",
                "status": "FILLED",
            },
        )
        self.assertTrue(results[0]["thread_report"].startswith("当前交易环境： 真实盘\n"))
        self.assertIn("Live close order submitted", results[0]["thread_report"])
        self.assertIn("Exchange summary", results[0]["thread_report"])
        self.assertNotIn("avgPrice", results[0]["thread_report"])
        self.assertNotIn("accountBalance", results[0]["thread_report"])
        self.assertEqual(tasks[0]["status"], "completed")
        self.assertEqual(tasks[0]["exchange_response"], results[0]["exchange_response"])
        self.assertIn("live_order_submitted", [event["event_type"] for event in events])
        submitted_events = [event for event in events if event["event_type"] == "live_order_submitted"]
        submitted_payload = json.dumps(submitted_events[-1]["payload"], sort_keys=True)
        self.assertIn("order_id", submitted_payload)
        self.assertNotIn("avgPrice", submitted_payload)
        self.assertNotIn("accountBalance", submitted_payload)
        self.assertNotIn("rawPosition", submitted_payload)
        preview_command = runner.call_args_list[2].args[0]
        confirm_command = runner.call_args_list[3].args[0]
        self.assertIn("preview-order", preview_command)
        self.assertIn("confirm-order", confirm_command)
        self.assertIn("--confirm-live", confirm_command)
        preview_order_json = preview_command[preview_command.index("--order-json") + 1]
        preview_order = json.loads(preview_order_json)
        self.assertEqual(preview_order["side"], "SELL")
        self.assertEqual(preview_order["position_side"], "LONG")
        self.assertEqual(preview_order["new_client_order_id"], "monitor_mon_pnl_live")

    def test_run_live_once_allows_demo_known_degraded_payload_and_uses_confirm_demo(self) -> None:
        task_json = {
            "task_id": "mon_pnl_demo",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "demo",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "-999999",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "trading_mode": "demo",
            "environment": {"trading_mode": "demo", "uses_real_funds": False},
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.001",
                    "unrealized_pnl": "-1.2",
                }
            ],
            "degraded_reasons": [
                "demo_futures_open_orders_unavailable",
                "demo_futures_conditional_orders_unavailable",
                "demo_futures_tp_sl_state_unavailable",
            ],
            "partial": True,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                self._prepare_and_confirm(task_json, now_ms=1000)
                with mock.patch.object(
                    monitor,
                    "_run_json_command",
                    side_effect=[
                        account_payload,
                        account_payload,
                        {"intent_id": "intent-demo-close", "risk_signature": "sig-demo-close"},
                        {"ok": True, "orderId": "demo-9001", "clientOrderId": "monitor_mon_pnl_demo"},
                    ],
                ) as runner:
                    results = monitor.run_live_once(confirm_live=False, confirm_demo=True, now_ms=2000)

        self.assertEqual(results[0]["status"], "completed")
        self.assertTrue(results[0]["thread_report"].startswith("当前交易环境： 模拟盘\n"))
        self.assertIn("Demo close order submitted", results[0]["thread_report"])
        confirm_command = runner.call_args_list[3].args[0]
        self.assertIn("--confirm-demo", confirm_command)
        self.assertNotIn("--confirm-live", confirm_command)

    def test_run_live_loop_uses_confirm_live_and_reuses_active_frequency(self) -> None:
        task_json = {
            "task_id": "mon_pnl_loop",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "frequency_seconds": 3,
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "51.2",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                self._prepare_and_confirm(task_json, now_ms=1000)
                with mock.patch.object(
                    monitor,
                    "_run_json_command",
                    side_effect=[
                        account_payload,
                        account_payload,
                        {"intent_id": "intent-close", "risk_signature": "sig-close"},
                        {"ok": True, "orderId": "9001", "clientOrderId": "monitor_mon_pnl_loop"},
                    ],
                ):
                    loop_result = monitor.run_live_loop(
                        confirm_live=True,
                        iterations=2,
                        sleep_seconds=0,
                        now_ms=2000,
                    )
                tasks = monitor.load_tasks()

        self.assertEqual(loop_result["live"], True)
        self.assertEqual(loop_result["iterations_requested"], 2)
        self.assertEqual(loop_result["iterations_completed"], 1)
        self.assertEqual(loop_result["submitted_count"], 1)
        self.assertEqual(loop_result["effective_sleep_seconds"], 0)
        self.assertEqual(tasks[0]["status"], "completed")

    def test_run_live_once_skips_duplicate_active_monitor_for_same_position_bucket(self) -> None:
        base_task = {
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">=",
                "threshold": "0",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "1",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                for suffix in ("a", "b"):
                    task = dict(base_task)
                    task["task_id"] = f"mon_duplicate_{suffix}"
                    self._prepare_and_confirm(task, now_ms=1000)
                with mock.patch.object(
                    monitor,
                    "_run_json_command",
                    side_effect=[
                        account_payload,
                        account_payload,
                        {"intent_id": "intent-close", "risk_signature": "sig-close"},
                        {"ok": True, "orderId": "9001"},
                        account_payload,
                        account_payload,
                        {"intent_id": "intent-close-2", "risk_signature": "sig-close-2"},
                        {"ok": True, "orderId": "9002"},
                    ],
                ) as runner:
                    results = monitor.run_live_once(confirm_live=True, now_ms=2000)
                tasks = sorted(monitor.load_tasks(), key=lambda item: item["task_id"])
                duplicate_events = monitor.load_events("mon_duplicate_b")

        self.assertEqual(runner.call_count, 4)
        self.assertEqual([result["status"] for result in results], ["completed", "review_required"])
        self.assertEqual(results[1]["result"]["reason"], "position_execution_already_claimed")
        self.assertEqual([task["status"] for task in tasks], ["completed", "review_required"])
        self.assertIn("live_execution_skipped", [event["event_type"] for event in duplicate_events])

    def test_confirm_and_run_live_loop_uses_duration_seconds_from_one_confirmation(self) -> None:
        task_json = {
            "task_id": "mon_pnl_combined",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "frequency_seconds": 5,
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "4.2",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(
                    monitor,
                    "_run_json_command",
                    return_value={
                        "positions": [
                            {
                                "symbol": "BTCUSDT",
                                "side": "LONG",
                                "quantity": "0.01",
                                "unrealized_pnl": "4.2",
                            }
                        ],
                        "degraded_reasons": [],
                        "partial": False,
                    },
                ):
                    prepared = monitor.prepare_live_confirmation(task_json, duration_seconds=11, now_ms=1000)
                with mock.patch.object(
                    monitor,
                    "_run_json_command",
                    side_effect=[
                        account_payload,
                        account_payload,
                        account_payload,
                    ],
                ) as runner:
                    result = monitor.confirm_and_run_live_loop(
                        prepared["task"],
                        confirm_monitor=True,
                        confirmation_token=prepared["confirmation_token"],
                        confirm_live=True,
                        duration_seconds=11,
                        sleep_seconds=0,
                        now_ms=2000,
                    )
                tasks = monitor.load_tasks()
                events = monitor.load_events(task_json["task_id"])

        self.assertEqual(result["confirmed_task"]["status"], "cancelled")
        self.assertEqual(result["duration_seconds"], 11.0)
        self.assertEqual(result["loop_result"]["live"], True)
        self.assertEqual(result["loop_result"]["iterations_requested"], 3)
        self.assertEqual(result["loop_result"]["submitted_count"], 0)
        self.assertTrue(result["reporting"]["enabled"])
        self.assertEqual(result["reporting"]["interval_seconds"], 60)
        self.assertIn("mon_pnl_combined", result["reporting"]["heartbeat_prompt"])
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(tasks[0]["status"], "cancelled")
        self.assertIn("task_confirmed", [event["event_type"] for event in events])
        self.assertIn("task_cancelled", [event["event_type"] for event in events])
        self.assertNotIn("live_order_submitted", [event["event_type"] for event in events])

    def test_confirm_and_run_live_loop_requires_finite_duration_before_activation(self) -> None:
        task_json = {
            "task_id": "mon_pnl_combined_guard",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                prepared = monitor.prepare_confirmation(task_json, now_ms=1000)
                with mock.patch.object(monitor, "_run_json_command") as runner:
                    with self.assertRaisesRegex(monitor.MonitorInputError, "live position confirmation"):
                        monitor.confirm_and_run_live_loop(
                            prepared["task"],
                            confirm_monitor=True,
                            confirmation_token=prepared["confirmation_token"],
                            confirm_live=True,
                            duration_seconds=3600,
                            sleep_seconds=0,
                            now_ms=2000,
                        )
                tasks = monitor.load_tasks()
                events = monitor.load_events(task_json["task_id"])

        runner.assert_not_called()
        self.assertEqual(tasks[0]["status"], "draft")
        self.assertNotIn("task_confirmed", [event["event_type"] for event in events])

    def test_confirm_and_run_live_loop_rejects_plain_confirmation_with_forged_live_snapshot(self) -> None:
        task_json = {
            "task_id": "mon_pnl_plain_token",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                prepared = monitor.prepare_confirmation(task_json, now_ms=1000)
                forged_live_task = dict(prepared["task"])
                forged_live_task["live_position_confirmation"] = {
                    "symbol": "BTCUSDT",
                    "position_side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "4.2",
                }
                account_payload = {
                    "positions": [
                        {
                            "symbol": "BTCUSDT",
                            "side": "LONG",
                            "quantity": "0.01",
                            "unrealized_pnl": "4.2",
                        }
                    ],
                    "degraded_reasons": [],
                    "partial": False,
                }
                with mock.patch.object(monitor, "_run_json_command", return_value=account_payload) as runner:
                    with self.assertRaisesRegex(monitor.MonitorInputError, "live confirmation"):
                        monitor.confirm_and_run_live_loop(
                            forged_live_task,
                            confirm_monitor=True,
                            confirmation_token=prepared["confirmation_token"],
                            confirm_live=True,
                            duration_seconds=5,
                            sleep_seconds=0,
                            now_ms=2000,
                        )
                tasks = monitor.load_tasks()
                events = monitor.load_events(task_json["task_id"])

        runner.assert_not_called()
        self.assertEqual(tasks[0]["status"], "draft")
        self.assertNotIn("task_confirmed", [event["event_type"] for event in events])

    def test_confirm_and_run_live_loop_token_mismatch_mentions_confirm_text_live_returned_task(self) -> None:
        task_json = {
            "task_id": "mon_pnl_live_token_mismatch",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "4.2",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(monitor, "_run_json_command", return_value=account_payload):
                    prepared = monitor.prepare_live_confirmation(task_json, duration_seconds=5, now_ms=1000)
                mismatched_task = dict(prepared["task"])
                mismatched_task["task_id"] = "mon_pnl_other_task"
                with mock.patch.object(monitor, "_run_json_command") as runner:
                    with self.assertRaisesRegex(monitor.MonitorInputError, "confirm-text-live returned task"):
                        monitor.confirm_and_run_live_loop(
                            mismatched_task,
                            confirm_monitor=True,
                            confirmation_token=prepared["confirmation_token"],
                            confirm_live=True,
                            duration_seconds=5,
                            sleep_seconds=0,
                            now_ms=2000,
                        )

        runner.assert_not_called()

    def test_confirm_and_run_live_loop_rejects_duration_mismatch_from_live_confirmation(self) -> None:
        task_json = {
            "task_id": "mon_pnl_duration_mismatch",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "4.2",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(monitor, "_run_json_command", return_value=account_payload):
                    prepared = monitor.prepare_live_confirmation(task_json, duration_seconds=5, now_ms=1000)
                with mock.patch.object(monitor, "_run_json_command") as runner:
                    with self.assertRaisesRegex(monitor.MonitorInputError, "duration"):
                        monitor.confirm_and_run_live_loop(
                            prepared["task"],
                            confirm_monitor=True,
                            confirmation_token=prepared["confirmation_token"],
                            confirm_live=True,
                            duration_seconds=10,
                            sleep_seconds=0,
                            now_ms=2000,
                        )
                tasks = monitor.load_tasks()
                events = monitor.load_events(task_json["task_id"])

        runner.assert_not_called()
        self.assertEqual(tasks[0]["status"], "draft")
        self.assertNotIn("task_confirmed", [event["event_type"] for event in events])

    def test_run_live_loop_records_actual_iteration_timestamps(self) -> None:
        with mock.patch.object(monitor, "_now_ms", side_effect=[2000, 7000]):
            with mock.patch.object(monitor, "run_live_once", return_value=[]) as run_once:
                with mock.patch.object(monitor, "_has_active_pnl_tasks", side_effect=[True, False]):
                    monitor.run_live_loop(confirm_live=True, iterations=2, sleep_seconds=0)

        self.assertEqual(
            [call.kwargs["now_ms"] for call in run_once.call_args_list],
            [2000, 7000],
        )

    def test_run_live_loop_stops_after_target_task_completes(self) -> None:
        task_json = {
            "task_id": "mon_pnl_stop_after_complete",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "frequency_seconds": 5,
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "51.2",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                self._prepare_and_confirm(task_json, now_ms=1000)
                with mock.patch.object(
                    monitor,
                    "_run_json_command",
                    side_effect=[
                        account_payload,
                        account_payload,
                        {"intent_id": "intent-close", "risk_signature": "sig-close"},
                        {"ok": True, "orderId": "9001", "clientOrderId": "monitor_mon_pnl_stop_after_complete"},
                    ],
                ) as runner:
                    loop_result = monitor.run_live_loop(
                        confirm_live=True,
                        iterations=5,
                        sleep_seconds=0,
                        now_ms=2000,
                        task_id=task_json["task_id"],
                    )

        self.assertEqual(loop_result["submitted_count"], 1)
        self.assertEqual(loop_result["iterations_completed"], 1)
        self.assertEqual(runner.call_count, 4)

    def test_live_delegate_failure_is_recorded_and_moves_task_to_review_required(self) -> None:
        task_json = {
            "task_id": "mon_pnl_fail",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }
        account_payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": "0.01",
                    "unrealized_pnl": "51.2",
                }
            ],
            "degraded_reasons": [],
            "partial": False,
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                confirmed = self._prepare_and_confirm(task_json, now_ms=1000)
                with mock.patch.object(
                    monitor,
                    "_run_json_command",
                    side_effect=[
                        account_payload,
                        account_payload,
                        monitor.MonitorInputError("preview failed"),
                    ],
                ):
                    results = monitor.run_live_once(confirm_live=True, now_ms=2000)
                tasks = monitor.load_tasks()
                events = monitor.load_events(confirmed["task_id"])

        self.assertEqual(results[0]["status"], "review_required")
        self.assertEqual(results[0]["result"]["reason"], "live_order_preview_failed")
        self.assertEqual(tasks[0]["status"], "review_required")
        self.assertIn("live_order_failed", [event["event_type"] for event in events])

    def test_execution_claim_is_atomic_for_active_task(self) -> None:
        task_json = {
            "task_id": "mon_claim",
            "task_type": "position_pnl_monitor",
            "profile": "demo",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "condition": {
                "metric": "unrealized_pnl",
                "operator": ">",
                "threshold": "50",
            },
            "action": {
                "type": "market_close",
                "target": "LONG",
            },
            "callback": {"type": "current_thread"},
        }

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_MONITOR_SKILL_HOME": tempdir}, clear=False):
                confirmed = self._prepare_and_confirm(task_json, now_ms=1000)
                first_claim = monitor.claim_task_for_execution(confirmed, now_ms=2000)
                second_claim = monitor.claim_task_for_execution(confirmed, now_ms=2001)
                tasks = monitor.load_tasks()
                events = monitor.load_events(confirmed["task_id"])

        self.assertTrue(first_claim)
        self.assertFalse(second_claim)
        self.assertEqual(tasks[0]["status"], "executing")
        self.assertIn("live_execution_claimed", [event["event_type"] for event in events])


if __name__ == "__main__":
    unittest.main()
