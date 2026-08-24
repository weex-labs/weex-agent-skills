#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = ROOT / "scripts" / "weex_auto_trade_state.py"
AMOUNT_MODULE_PATH = ROOT / "scripts" / "weex_auto_trade_amount.py"
CLI_PATH = ROOT / "scripts" / "weex_auto_trade.py"
NOTIFY_MODULE_PATH = ROOT / "scripts" / "weex_auto_trade_notify.py"


def load_state_module():
    if not MODULE_PATH.exists():
        raise AssertionError("weex_auto_trade_state.py has not been implemented")
    spec = importlib.util.spec_from_file_location("weex_auto_trade_state", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load weex_auto_trade_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_amount_module():
    if not AMOUNT_MODULE_PATH.exists():
        raise AssertionError("weex_auto_trade_amount.py has not been implemented")
    spec = importlib.util.spec_from_file_location("weex_auto_trade_amount", AMOUNT_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load weex_auto_trade_amount.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_notify_module():
    if not NOTIFY_MODULE_PATH.exists():
        raise AssertionError("weex_auto_trade_notify.py has not been implemented")
    spec = importlib.util.spec_from_file_location("weex_auto_trade_notify", NOTIFY_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load weex_auto_trade_notify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cli_module():
    spec = importlib.util.spec_from_file_location("weex_auto_trade", CLI_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load weex_auto_trade.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def complete_scope(**overrides):
    scope = {
        "trade_types": ["SPOT"],
        "symbols": ["BTCUSDT"],
        "all_symbols": False,
        "max_single_amount": "100",
        "max_total_amount": "1000",
        "valid_hours": "24",
    }
    scope.update(overrides)
    return scope


def complete_spot_account_snapshot(*, quote_available_u="1000", base_available="10"):
    return {
        "available_balance": quote_available_u,
        "base_asset": "BTC",
        "base_available_quantity": base_available,
        "quote_asset": "USDT",
        "quote_available_balance": quote_available_u,
        "quote_available_balance_u": quote_available_u,
    }


def complete_spot_symbol_facts(*, maker_fee="0", taker_fee="0"):
    return {
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "stepSize": "0.000001",
        "minTradeAmount": "0.000001",
        "maxTradeAmount": "100",
        "makerFeeRate": maker_fee,
        "takerFeeRate": taker_fee,
    }


class AutoTradeStateSchemaTests(unittest.TestCase):
    def test_v4_validity_constraint_migrates_without_data_loss_and_accepts_720_hours(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            db_path.parent.mkdir(mode=0o700)
            db_path.touch(mode=0o600)
            db_path.chmod(0o600)
            with closing(sqlite3.connect(db_path)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.executescript(
                    state_module.SCHEMA_V1.replace("2592000", "86400")
                )
                state_module._migrate_v1_to_v2(connection)
                state_module._migrate_v2_to_v3(connection)
                state_module._migrate_v3_to_v4(connection)
                connection.execute("PRAGMA user_version = 4")
                connection.commit()
                state_module._validate_connection(
                    connection, full=True, expected_version=4
                )

            legacy_state = state_module.AutoTradeState(db_path)
            strategy = legacy_state.register_strategy(
                profile_id="profile-1", strategy_name="v4-validity", distribution="official"
            )
            now = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
            request = legacy_state.ensure_authorization(
                strategy_id=strategy["strategy_id"], scope=complete_scope(), now=now
            )
            authorization = legacy_state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )

            state = state_module.AutoTradeState(db_path)
            state.initialize()
            self.assertEqual(
                state.health_check(full=True)["user_version"],
                state_module.CURRENT_SCHEMA_VERSION,
            )
            self.assertEqual(
                state.list_authorizations(strategy_id=strategy["strategy_id"])[0][
                    "authorization_id"
                ],
                authorization["authorization_id"],
            )

            thirty_day_request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(max_total_amount="2000", valid_hours="720"),
                now=now,
            )
            thirty_day = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=thirty_day_request["request_id"],
                scope_signature=thirty_day_request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            self.assertEqual(
                thirty_day["expires_at"],
                (now + timedelta(hours=720)).isoformat(timespec="microseconds").replace(
                    "+00:00", "Z"
                ),
            )
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT valid_seconds FROM authorizations WHERE authorization_id = ?",
                        (thirty_day["authorization_id"],),
                    ).fetchone()[0],
                    2_592_000,
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_windows_state_storage_fails_closed_until_owner_only_dacl_is_supported(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            with patch.object(state_module.os, "name", "nt"):
                with self.assertRaisesRegex(
                    state_module.StateConflictError,
                    "Windows owner-only DACL enforcement is not supported",
                ):
                    state.initialize()

    def test_empty_database_initializes_six_table_schema_with_owner_only_permissions(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state_dir = Path(tempdir) / "auto-trade"
            db_path = state_dir / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()

            with closing(sqlite3.connect(db_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                user_version = connection.execute("PRAGMA user_version").fetchone()[0]

            self.assertEqual(
                tables,
                {
                    "strategies",
                    "authorization_requests",
                    "authorizations",
                    "authorization_usage",
                    "auto_trade_orders",
                    "authorization_events",
                },
            )
            self.assertEqual(user_version, state_module.CURRENT_SCHEMA_VERSION)
            self.assertEqual(stat.S_IMODE(state_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(db_path.stat().st_mode), 0o600)

    def test_health_checks_fail_closed_without_repairing_permissions_or_invalid_decimals(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            self.assertTrue(hasattr(state, "health_check"), "state health checks are not implemented")
            healthy = state.health_check(full=True)
            self.assertEqual(healthy["status"], "HEALTHY")
            self.assertEqual(healthy["user_version"], state_module.CURRENT_SCHEMA_VERSION)
            self.assertEqual(healthy["foreign_key_violations"], 0)

            db_path.chmod(0o644)
            with self.assertRaises(state_module.StateConflictError):
                state_module.AutoTradeState(db_path).initialize()
            self.assertEqual(stat.S_IMODE(db_path.stat().st_mode), 0o644)

            db_path.chmod(0o600)
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="health", distribution="official"
            )
            now = datetime(2026, 8, 16, 11, 0, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "10",
                    "max_total_amount": "100",
                    "valid_hours": "24",
                },
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    "UPDATE authorizations SET accepted_amount_u = 'not-a-decimal' WHERE authorization_id = ?",
                    (authorization["authorization_id"],),
                )
                connection.commit()

            with self.assertRaises(state_module.StateConflictError):
                state.health_check(full=True)

    def test_adjacent_schema_migration_rolls_back_and_unknown_versions_fail_closed(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "state"
            root.mkdir(mode=0o700)
            db_path = root / "state.sqlite3"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(state_module.SCHEMA_V1)
                connection.execute("PRAGMA user_version = 1")
                connection.commit()
            db_path.chmod(0o600)

            self.assertTrue(hasattr(state_module, "MIGRATION_REGISTRY"), "migration registry is not implemented")
            self.assertIn(1, state_module.MIGRATION_REGISTRY)
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    state_module.CURRENT_SCHEMA_VERSION,
                )

            failing_db = Path(tempdir) / "failing" / "state.sqlite3"
            failing_state = state_module.AutoTradeState(failing_db)
            failing_state.initialize()
            with closing(sqlite3.connect(failing_db)) as connection:
                connection.execute("PRAGMA user_version = 1")
                connection.commit()
            original_migration = state_module.MIGRATION_REGISTRY[1]
            state_module.MIGRATION_REGISTRY[1] = lambda connection: (_ for _ in ()).throw(
                RuntimeError("injected migration failure")
            )
            try:
                with self.assertRaises(state_module.StateConflictError):
                    failing_state.initialize()
            finally:
                state_module.MIGRATION_REGISTRY[1] = original_migration
            with closing(sqlite3.connect(failing_db)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

            unknown_db = Path(tempdir) / "unknown" / "state.sqlite3"
            unknown_state = state_module.AutoTradeState(unknown_db)
            unknown_state.initialize()
            with closing(sqlite3.connect(unknown_db)) as connection:
                connection.execute("PRAGMA user_version = 999")
                connection.commit()
            with self.assertRaises(state_module.StateConflictError):
                unknown_state.initialize()

    def test_populated_v2_database_migrates_and_legacy_group_replays_without_binding_digest(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="legacy-replay", distribution="official"
            )
            now = datetime(2026, 8, 16, 11, 0, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(max_single_amount="10", max_total_amount="100"),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            state.prepare_submission_group(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="legacy-v2",
                request_fingerprint="a" * 64,
                legs=[
                    {
                        "leg_id": "leg-0",
                        "leg_index": 0,
                        "leg_type": "PRIMARY",
                        "module": "SPOT",
                        "symbol": "BTCUSDT",
                        "estimated_amount_u": "10",
                        "valuation_source": "fixture",
                        "side": "BUY",
                        "order_type": "LIMIT",
                        "quantity": "1",
                        "price": "10",
                    }
                ],
                now=now,
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    "ALTER TABLE authorization_usage DROP COLUMN request_fingerprint"
                )
                for table in ("authorization_requests", "authorizations"):
                    for column in (
                        "allowed_sides_csv",
                        "allowed_order_types_csv",
                        "min_single_amount_u",
                        "max_order_count",
                    ):
                        connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
                connection.execute("PRAGMA user_version = 2")
                connection.commit()

            state.initialize()
            replay = state.get_submission_group_by_idempotency(
                authorization_id=authorization["authorization_id"],
                idempotency_key="legacy-v2",
                request_fingerprint="b" * 64,
                legacy_legs=[
                    {
                        "leg_id": "leg-0",
                        "leg_index": 0,
                        "leg_type": "PRIMARY",
                        "module": "SPOT",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "order_type": "LIMIT",
                        "quantity": "1",
                        "price": "10",
                    }
                ],
            )
            self.assertTrue(replay["replayed"])
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertIsNone(
                    connection.execute(
                        "SELECT request_fingerprint FROM authorization_usage"
                    ).fetchone()[0]
                )

            with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
                state.get_submission_group_by_idempotency(
                    authorization_id=authorization["authorization_id"],
                    idempotency_key="legacy-v2",
                    request_fingerprint="c" * 64,
                    legacy_legs=[
                        {
                            "leg_id": "leg-0",
                            "leg_index": 0,
                            "leg_type": "PRIMARY",
                            "module": "SPOT",
                            "symbol": "BTCUSDT",
                            "side": "BUY",
                            "order_type": "LIMIT",
                            "quantity": "2",
                            "price": "10",
                        }
                    ],
                )

    def test_foreign_key_break_and_invalid_usage_transition_fail_closed(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    """
                    INSERT INTO authorization_events (
                        event_id, strategy_id, event_type, event_schema_version, severity,
                        occurred_at, payload_json, notification_status, created_at
                    ) VALUES ('orphan-event', 'missing-strategy', 'BROKEN', 1, 'EXCEPTION',
                              '2026-08-16T00:00:00Z', '{\"event_schema_version\":1}', 'NOT_APPLICABLE',
                              '2026-08-16T00:00:00Z')
                    """
                )
                connection.commit()
            with self.assertRaises(state_module.StateConflictError):
                state.health_check(full=True)

            clean_db = Path(tempdir) / "transition" / "state.sqlite3"
            clean_state = state_module.AutoTradeState(clean_db)
            clean_state.initialize()
            strategy = clean_state.register_strategy(
                profile_id="profile-1", strategy_name="transition", distribution="official"
            )
            now = datetime(2026, 8, 16, 11, 30, tzinfo=UTC)
            request = clean_state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "10",
                    "max_total_amount": "100",
                    "valid_hours": "24",
                },
                now=now,
            )
            authorization = clean_state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            usage = clean_state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="transition-order",
                estimated_amount_u="5",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="official-depth",
                now=now,
            )
            clean_state.settle_usage(usage_id=usage["usage_id"], outcome="ACCEPTED", now=now)
            with self.assertRaisesRegex(ValueError, "USAGE_STATE_CONFLICT"):
                clean_state.settle_usage(usage_id=usage["usage_id"], outcome="RELEASED", now=now)


class AutoTradeSnapshotTests(unittest.TestCase):
    def test_snapshot_is_owner_only_consistent_and_rotates_only_trusted_oldest_records(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            db_path = config_dir / "auto-trade" / "authorization-state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            state.register_strategy(
                profile_id="profile-1", strategy_name="snapshot", distribution="official"
            )
            snapshots = []
            for minute in range(3):
                snapshots.append(
                    state.snapshot_state(
                        retention_count=2,
                        now=datetime(2026, 8, 16, 16, minute, tzinfo=UTC),
                    )
                )
                if minute == 0:
                    unknown = config_dir / "snapshots" / "unknown-user-file.sqlite3"
                    unknown.write_bytes(b"not a managed snapshot")

            snapshots_dir = config_dir / "snapshots"
            index_path = snapshots_dir / "index.json"
            self.assertEqual(stat.S_IMODE(snapshots_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(index_path.stat().st_mode), 0o600)
            self.assertTrue((snapshots_dir / "unknown-user-file.sqlite3").exists())
            self.assertFalse((snapshots_dir / snapshots[0]["relative_path"]).exists())
            self.assertEqual(snapshots[2]["retention_status"], "COMPLETE")
            self.assertEqual(snapshots[2]["retained_count"], 2)
            retained = snapshots[2]["snapshots"]
            self.assertEqual(
                [item["snapshot_id"] for item in retained],
                [snapshots[1]["snapshot_id"], snapshots[2]["snapshot_id"]],
            )
            for item in retained:
                snapshot_path = snapshots_dir / item["relative_path"]
                self.assertEqual(stat.S_IMODE(snapshot_path.stat().st_mode), 0o600)
                with closing(sqlite3.connect(snapshot_path)) as connection:
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(
                        connection.execute("PRAGMA user_version").fetchone()[0],
                        state_module.CURRENT_SCHEMA_VERSION,
                    )

    def test_snapshot_publish_fsyncs_directory_namespace_changes(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(
                Path(tempdir) / "config" / "auto-trade" / "authorization-state.sqlite3"
            )
            state.initialize()
            with patch.object(
                state_module,
                "_fsync_directory",
                wraps=state_module._fsync_directory,
            ) as fsync_directory:
                state.snapshot_state(
                    retention_count=10,
                    now=datetime(2026, 8, 16, 16, 30, tzinfo=UTC),
                )
            synced = {Path(call.args[0]) for call in fsync_directory.call_args_list}
            self.assertIn(Path(tempdir) / "config" / "snapshots", synced)

    def test_restore_preserves_current_evidence_and_requires_fresh_authorization(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            db_path = config_dir / "auto-trade" / "authorization-state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            now = datetime(2026, 8, 16, 17, 0, tzinfo=UTC)
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="restore", distribution="official"
            )
            scope = complete_scope(
                max_single_amount="20",
                max_total_amount="100",
            )
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=scope,
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            reserved = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="restore-reserved",
                estimated_amount_u="5",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="fixture",
                now=now,
            )
            state.record_order(
                usage_id=reserved["usage_id"],
                weex_order_id=None,
                side="BUY",
                order_type="LIMIT",
                quantity="1",
                price="10",
                now=now,
            )
            review = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="restore-review",
                estimated_amount_u="6",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="fixture",
                now=now,
            )
            state.record_order(
                usage_id=review["usage_id"],
                weex_order_id=None,
                side="BUY",
                order_type="LIMIT",
                quantity="1",
                price="10",
                now=now,
            )
            state.settle_usage(
                usage_id=review["usage_id"], outcome="REVIEW_REQUIRED", now=now
            )
            snapshot = state.snapshot_state(retention_count=10, now=now)

            state.settle_usage(
                usage_id=reserved["usage_id"], outcome="ACCEPTED", now=now
            )
            state.revoke_authorization(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                now=now,
            )

            restored = state.restore_state(
                snapshot_id=snapshot["snapshot_id"],
                now=datetime(2026, 8, 16, 17, 5, tzinfo=UTC),
            )

            self.assertEqual(restored["status"], "STATE_RESTORED_DISABLED")
            self.assertTrue(restored["kill_switch_enabled"])
            preserved_path = config_dir / restored["preserved_database_relative_path"]
            self.assertTrue(preserved_path.exists())
            self.assertEqual(stat.S_IMODE(preserved_path.stat().st_mode), 0o600)
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT status FROM authorizations").fetchone()[0],
                    "REVOKED",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM authorization_usage ORDER BY idempotency_key"
                    ).fetchall(),
                    [("RESERVED",), ("REVIEW_REQUIRED",)],
                )
            self.assertTrue((config_dir / "auto-trade" / "automatic-trading.disabled").exists())
            with self.assertRaisesRegex(ValueError, "AUTO_TRADE_DISABLED"):
                state.prepare_submission_group(
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key="blocked-after-restore",
                    request_fingerprint="c" * 64,
                    legs=[
                        {
                            "leg_id": "leg-0",
                            "leg_index": 0,
                            "leg_type": "PRIMARY",
                            "module": "SPOT",
                            "symbol": "BTCUSDT",
                            "estimated_amount_u": "1",
                            "valuation_source": "fixture",
                            "side": "BUY",
                            "order_type": "LIMIT",
                            "quantity": "1",
                            "price": "1",
                        }
                    ],
                    now=now,
                )

            fresh_request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=scope,
                now=datetime(2026, 8, 16, 17, 6, tzinfo=UTC),
            )
            fresh_authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=fresh_request["request_id"],
                scope_signature=fresh_request["scope_signature"],
                confirm_live=True,
                now=datetime(2026, 8, 16, 17, 6, tzinfo=UTC),
            )
            self.assertEqual(
                fresh_authorization["next_action"],
                "RESOLVE_AUTO_USAGE_AND_ENABLE_AUTO_TRADING_AFTER_RESTORE",
            )
            repeated_grant = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=fresh_request["request_id"],
                scope_signature=fresh_request["scope_signature"],
                confirm_live=True,
                now=datetime(2026, 8, 16, 17, 6, tzinfo=UTC),
            )
            self.assertEqual(
                repeated_grant["next_action"],
                "RESOLVE_AUTO_USAGE_AND_ENABLE_AUTO_TRADING_AFTER_RESTORE",
            )
            self.assertTrue((config_dir / "auto-trade" / "automatic-trading.disabled").exists())
            with self.assertRaisesRegex(ValueError, "UNRESOLVED_USAGE_REQUIRES_RECONCILIATION"):
                state.enable_auto_trading_after_restore(
                    confirm_live=True,
                    now=datetime(2026, 8, 16, 17, 6, tzinfo=UTC),
                )
            def release_evidence(usage_id):
                order = state.get_order_for_usage(usage_id=usage_id)
                return state_module.build_verified_usage_evidence(
                    {
                        "outcome": "RELEASED",
                        "evidence_source": "WEEX_SPOT_ORDER_NOT_FOUND",
                        "weex_order_id": None,
                        **{key: order[key] for key in (
                            "usage_id", "strategy_id", "authorization_id", "submission_group_id",
                            "leg_id", "client_order_id", "module", "symbol", "side", "order_type",
                            "quantity", "price",
                        )},
                    }
                )

            resolved_reserved = state.resolve_uncertain_usage(
                verified_evidence=release_evidence(reserved["usage_id"]),
                confirm_live=True,
                now=datetime(2026, 8, 16, 17, 7, tzinfo=UTC),
            )
            resolved_review = state.resolve_uncertain_usage(
                verified_evidence=release_evidence(review["usage_id"]),
                confirm_live=True,
                now=datetime(2026, 8, 16, 17, 7, tzinfo=UTC),
            )
            self.assertEqual(resolved_reserved["status"], "RELEASED")
            self.assertEqual(resolved_review["status"], "RELEASED")
            repeated_ensure = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=scope,
                now=datetime(2026, 8, 16, 17, 7, tzinfo=UTC),
            )
            self.assertEqual(
                repeated_ensure["next_action"], "ENABLE_AUTO_TRADING_AFTER_RESTORE"
            )
            listed_before_enable = state.list_authorizations(
                strategy_id=strategy["strategy_id"],
                now=datetime(2026, 8, 16, 17, 7, tzinfo=UTC),
            )
            self.assertEqual(
                listed_before_enable[-1]["next_action"],
                "ENABLE_AUTO_TRADING_AFTER_RESTORE",
            )
            enabled = state.enable_auto_trading_after_restore(
                confirm_live=True,
                now=datetime(2026, 8, 16, 17, 7, tzinfo=UTC),
            )
            self.assertEqual(enabled["status"], "AUTOMATIC_TRADING_ENABLED")
            self.assertFalse((config_dir / "auto-trade" / "automatic-trading.disabled").exists())
            enabled_authorization = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=scope,
                now=datetime(2026, 8, 16, 17, 8, tzinfo=UTC),
            )
            self.assertEqual(enabled_authorization["next_action"], "SUBMIT_ALLOWED")
            prepared = state.prepare_submission_group(
                strategy_id=strategy["strategy_id"],
                authorization_id=fresh_authorization["authorization_id"],
                idempotency_key="allowed-after-fresh-grant",
                request_fingerprint="d" * 64,
                legs=[
                    {
                        "leg_id": "leg-0",
                        "leg_index": 0,
                        "leg_type": "PRIMARY",
                        "module": "SPOT",
                        "symbol": "BTCUSDT",
                        "estimated_amount_u": "1",
                        "valuation_source": "fixture",
                        "side": "BUY",
                        "order_type": "LIMIT",
                        "quantity": "1",
                        "price": "1",
                    }
                ],
                now=datetime(2026, 8, 16, 17, 6, tzinfo=UTC),
            )
            self.assertEqual(prepared["status"], "RESERVED")

    def test_restore_rejects_snapshot_file_substituted_under_registered_id(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            state = state_module.AutoTradeState(
                config_dir / "auto-trade" / "authorization-state.sqlite3"
            )
            state.initialize()
            state.register_strategy(
                profile_id="profile-1", strategy_name="snapshot-a", distribution="official"
            )
            snapshot_a = state.snapshot_state(
                retention_count=10, now=datetime(2026, 8, 16, 18, 0, tzinfo=UTC)
            )
            state.register_strategy(
                profile_id="profile-1", strategy_name="snapshot-b", distribution="official"
            )
            snapshot_b = state.snapshot_state(
                retention_count=10, now=datetime(2026, 8, 16, 18, 1, tzinfo=UTC)
            )
            snapshots_dir = config_dir / "snapshots"
            path_a = snapshots_dir / snapshot_a["relative_path"]
            path_b = snapshots_dir / snapshot_b["relative_path"]
            path_a.write_bytes(path_b.read_bytes())
            path_a.chmod(0o600)

            with self.assertRaises(state_module.StateConflictError):
                state.restore_state(
                    snapshot_id=snapshot_a["snapshot_id"],
                    now=datetime(2026, 8, 16, 18, 2, tzinfo=UTC),
                )

    def test_snapshot_retention_validation_and_creation_failure_delete_nothing(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            state = state_module.AutoTradeState(
                config_dir / "auto-trade" / "authorization-state.sqlite3"
            )
            state.initialize()
            for invalid in (0, 101, True, "10"):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):
                        state.snapshot_state(retention_count=invalid)
            self.assertFalse((config_dir / "snapshots").exists())

            first = state.snapshot_state(
                retention_count=2,
                now=datetime(2026, 8, 16, 18, 0, tzinfo=UTC),
            )
            second = state.snapshot_state(
                retention_count=2,
                now=datetime(2026, 8, 16, 18, 1, tzinfo=UTC),
            )
            with patch.object(
                state_module,
                "_fsync_regular_file",
                side_effect=OSError("injected snapshot write failure"),
            ):
                with self.assertRaises(state_module.StateConflictError):
                    state.snapshot_state(
                        retention_count=1,
                        now=datetime(2026, 8, 16, 18, 2, tzinfo=UTC),
                    )
            snapshots_dir = config_dir / "snapshots"
            self.assertTrue((snapshots_dir / first["relative_path"]).exists())
            self.assertTrue((snapshots_dir / second["relative_path"]).exists())
            index = json.loads((snapshots_dir / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["snapshots"]), 2)

            boundary_one = state.snapshot_state(
                retention_count=1,
                now=datetime(2026, 8, 16, 18, 3, tzinfo=UTC),
            )
            boundary_hundred = state.snapshot_state(
                retention_count=100,
                now=datetime(2026, 8, 16, 18, 4, tzinfo=UTC),
            )
            self.assertEqual(boundary_one["retention_count"], 1)
            self.assertEqual(boundary_one["retention_status"], "COMPLETE")
            self.assertEqual(boundary_hundred["retention_count"], 100)
            self.assertEqual(boundary_hundred["retention_status"], "COMPLETE")

    def test_snapshot_delete_failure_keeps_new_snapshot_and_does_not_skip_oldest(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            state = state_module.AutoTradeState(
                config_dir / "auto-trade" / "authorization-state.sqlite3"
            )
            state.initialize()
            existing = [
                state.snapshot_state(
                    retention_count=3,
                    now=datetime(2026, 8, 16, 18, minute, tzinfo=UTC),
                )
                for minute in range(3)
            ]
            real_unlink = os.unlink
            deleting_calls = []

            def fail_oldest_delete(path, *args, **kwargs):
                if str(path).endswith(".deleting"):
                    deleting_calls.append(str(path))
                    raise OSError("injected deletion failure")
                return real_unlink(path, *args, **kwargs)

            with patch.object(state_module.os, "unlink", side_effect=fail_oldest_delete):
                newest = state.snapshot_state(
                    retention_count=1,
                    now=datetime(2026, 8, 16, 18, 3, tzinfo=UTC),
                )

            self.assertEqual(newest["retention_status"], "INCOMPLETE")
            self.assertEqual(newest["retained_count"], 4)
            self.assertEqual(len(deleting_calls), 1)
            snapshots_dir = config_dir / "snapshots"
            for item in [*existing, newest]:
                self.assertTrue((snapshots_dir / item["relative_path"]).exists())

    def test_snapshot_index_publish_failure_does_not_delete_registered_snapshots(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            state = state_module.AutoTradeState(
                config_dir / "auto-trade" / "authorization-state.sqlite3"
            )
            state.initialize()
            existing = [
                state.snapshot_state(
                    retention_count=2,
                    now=datetime(2026, 8, 16, 18, minute, tzinfo=UTC),
                )
                for minute in range(2)
            ]
            snapshots_dir = config_dir / "snapshots"
            original_index = (snapshots_dir / "index.json").read_bytes()

            with patch.object(
                state_module,
                "_write_snapshot_index",
                side_effect=OSError("injected index publish failure"),
            ):
                with self.assertRaises(state_module.StateConflictError):
                    state.snapshot_state(
                        retention_count=1,
                        now=datetime(2026, 8, 16, 18, 2, tzinfo=UTC),
                    )

            self.assertEqual((snapshots_dir / "index.json").read_bytes(), original_index)
            for item in existing:
                self.assertTrue((snapshots_dir / item["relative_path"]).exists())
            self.assertEqual(
                len(list(snapshots_dir.glob("auto-trade-snap_*.sqlite3"))),
                3,
            )

    def test_corrupt_snapshot_restore_leaves_active_database_unchanged_and_disabled(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            db_path = config_dir / "auto-trade" / "authorization-state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            state.register_strategy(
                profile_id="profile-1", strategy_name="before", distribution="official"
            )
            snapshot = state.snapshot_state(
                now=datetime(2026, 8, 16, 18, 30, tzinfo=UTC)
            )
            state.register_strategy(
                profile_id="profile-1", strategy_name="after", distribution="official"
            )
            snapshot_path = config_dir / "snapshots" / snapshot["relative_path"]
            snapshot_path.write_bytes(b"corrupt sqlite snapshot")

            with self.assertRaises(state_module.StateConflictError):
                state.restore_state(
                    snapshot_id=snapshot["snapshot_id"],
                    now=datetime(2026, 8, 16, 18, 31, tzinfo=UTC),
                )

            self.assertEqual(
                [item["strategy_name"] for item in state.list_strategies(profile_id="profile-1")],
                ["before", "after"],
            )
            self.assertTrue((config_dir / "auto-trade" / "automatic-trading.disabled").exists())

    def test_restore_rejects_unknown_and_overnew_snapshots_fail_closed(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            db_path = config_dir / "auto-trade" / "authorization-state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            state.register_strategy(
                profile_id="profile-1", strategy_name="schema-restore", distribution="official"
            )
            snapshot = state.snapshot_state(now=datetime(2026, 8, 16, 18, 40, tzinfo=UTC))

            with self.assertRaisesRegex(ValueError, "UNKNOWN_SNAPSHOT"):
                state.restore_state(
                    snapshot_id="snap_" + "0" * 32,
                    now=datetime(2026, 8, 16, 18, 41, tzinfo=UTC),
                )
            self.assertTrue((config_dir / "auto-trade" / "automatic-trading.disabled").exists())
            with self.assertRaisesRegex(ValueError, "FRESH_ACTIVE_AUTHORIZATION_REQUIRED"):
                state.enable_auto_trading_after_restore(
                    confirm_live=True,
                    now=datetime(2026, 8, 16, 18, 41, tzinfo=UTC),
                )

            snapshot_path = config_dir / "snapshots" / snapshot["relative_path"]
            with closing(sqlite3.connect(snapshot_path)) as connection:
                connection.execute(
                    f"PRAGMA user_version = {state_module.CURRENT_SCHEMA_VERSION + 1}"
                )
                connection.commit()
            with self.assertRaises(state_module.StateConflictError):
                state.restore_state(
                    snapshot_id=snapshot["snapshot_id"],
                    now=datetime(2026, 8, 16, 18, 42, tzinfo=UTC),
                )
            self.assertTrue((config_dir / "auto-trade" / "automatic-trading.disabled").exists())
            self.assertEqual(len(state.list_strategies(profile_id="profile-1")), 1)

    def test_malformed_snapshot_index_attempt_latches_automatic_trading(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            state = state_module.AutoTradeState(
                config_dir / "auto-trade" / "authorization-state.sqlite3"
            )
            state.initialize()
            snapshot = state.snapshot_state(now=datetime(2026, 8, 16, 18, 45, tzinfo=UTC))
            (config_dir / "snapshots" / "index.json").write_text("{", encoding="utf-8")
            with self.assertRaises(state_module.StateConflictError):
                state.restore_state(
                    snapshot_id=snapshot["snapshot_id"],
                    now=datetime(2026, 8, 16, 18, 46, tzinfo=UTC),
                )
            self.assertTrue((config_dir / "auto-trade" / "automatic-trading.disabled").exists())

    def test_restore_accepts_digest_bound_v2_snapshot_and_runs_registered_migration(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            db_path = config_dir / "auto-trade" / "authorization-state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            state.register_strategy(
                profile_id="profile-1", strategy_name="v2-snapshot", distribution="official"
            )
            snapshot = state.snapshot_state(now=datetime(2026, 8, 16, 18, 47, tzinfo=UTC))
            snapshots_dir = config_dir / "snapshots"
            snapshot_path = snapshots_dir / snapshot["relative_path"]
            with closing(sqlite3.connect(snapshot_path)) as connection:
                connection.execute(
                    "ALTER TABLE authorization_usage DROP COLUMN request_fingerprint"
                )
                for table in ("authorization_requests", "authorizations"):
                    for column in (
                        "allowed_sides_csv",
                        "allowed_order_types_csv",
                        "min_single_amount_u",
                        "max_order_count",
                    ):
                        connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
                connection.execute("PRAGMA user_version = 2")
                connection.commit()
            index_path = snapshots_dir / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            record = index["snapshots"][0]
            record["database_schema_version"] = 2
            record["size_bytes"] = snapshot_path.stat().st_size
            record["sha256"] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
            index_path.write_text(json.dumps(index), encoding="utf-8")

            restored = state.restore_state(
                snapshot_id=snapshot["snapshot_id"],
                now=datetime(2026, 8, 16, 18, 48, tzinfo=UTC),
            )
            self.assertEqual(restored["status"], "STATE_RESTORED_DISABLED")
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    state_module.CURRENT_SCHEMA_VERSION,
                )

    def test_restore_enable_expires_and_rejects_a_fresh_but_elapsed_authorization(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            db_path = config_dir / "auto-trade" / "authorization-state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="expired-unlock", distribution="official"
            )
            snapshot = state.snapshot_state(now=datetime(2026, 8, 16, 19, 0, tzinfo=UTC))
            state.restore_state(
                snapshot_id=snapshot["snapshot_id"],
                now=datetime(2026, 8, 16, 19, 1, tzinfo=UTC),
            )
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "10",
                    "max_total_amount": "100",
                    "valid_hours": "0.01",
                },
                now=datetime(2026, 8, 16, 19, 2, tzinfo=UTC),
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=datetime(2026, 8, 16, 19, 2, tzinfo=UTC),
            )

            with self.assertRaisesRegex(ValueError, "FRESH_ACTIVE_AUTHORIZATION_REQUIRED"):
                state.enable_auto_trading_after_restore(
                    confirm_live=True,
                    now=datetime(2026, 8, 16, 19, 3, tzinfo=UTC),
                )
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM authorizations WHERE authorization_id = ?",
                        (authorization["authorization_id"],),
                    ).fetchone()[0],
                    "EXPIRED",
                )
            self.assertTrue(state._kill_switch_path().exists())

    def test_restore_atomic_replace_failure_keeps_active_database_unchanged(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            db_path = config_dir / "auto-trade" / "authorization-state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            state.register_strategy(
                profile_id="profile-1", strategy_name="replace-restore", distribution="official"
            )
            snapshot = state.snapshot_state(now=datetime(2026, 8, 16, 18, 50, tzinfo=UTC))
            state.register_strategy(
                profile_id="profile-1", strategy_name="must-survive", distribution="official"
            )
            real_replace = os.replace
            failed = False

            def fail_active_replace(source, destination, *args, **kwargs):
                nonlocal failed
                if Path(destination) == db_path and not failed:
                    failed = True
                    raise OSError("injected active database replace failure")
                return real_replace(source, destination, *args, **kwargs)

            with patch.object(state_module.os, "replace", side_effect=fail_active_replace):
                with self.assertRaises(state_module.StateConflictError):
                    state.restore_state(
                        snapshot_id=snapshot["snapshot_id"],
                        now=datetime(2026, 8, 16, 18, 51, tzinfo=UTC),
                    )

            self.assertTrue(failed)
            self.assertEqual(
                [item["strategy_name"] for item in state.list_strategies(profile_id="profile-1")],
                ["replace-restore", "must-survive"],
            )
            self.assertTrue((config_dir / "auto-trade" / "automatic-trading.disabled").exists())

    def test_concurrent_snapshot_creation_is_serialized_and_leaves_no_temporary_files(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            state = state_module.AutoTradeState(
                config_dir / "auto-trade" / "authorization-state.sqlite3"
            )
            state.initialize()
            barrier = threading.Barrier(6)

            def create_snapshot(index):
                barrier.wait()
                return state.snapshot_state(
                    retention_count=3,
                    now=datetime(2026, 8, 16, 19, index, tzinfo=UTC),
                )

            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(create_snapshot, range(6)))

            self.assertEqual(len({item["snapshot_id"] for item in results}), 6)
            snapshots_dir = config_dir / "snapshots"
            index = json.loads((snapshots_dir / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["snapshots"]), 3)
            self.assertEqual(
                [item["created_at"] for item in index["snapshots"]],
                sorted(item["created_at"] for item in index["snapshots"]),
            )
            self.assertEqual(
                [path.name for path in snapshots_dir.iterdir() if path.name.endswith((".tmp", ".deleting"))],
                [],
            )


class AutoTradeStrategyTests(unittest.TestCase):
    def test_strategy_id_is_reused_on_restart_and_copy_gets_a_new_id(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            self.assertTrue(hasattr(state, "register_strategy"), "strategy registry is not implemented")

            created = state.register_strategy(
                profile_id="profile-1",
                strategy_name="grid-btc",
                distribution="official",
                trading_mode="live",
            )
            restarted = state.register_strategy(
                profile_id="profile-1",
                strategy_name="grid-btc-renamed",
                distribution="official",
                trading_mode="live",
                strategy_id=created["strategy_id"],
            )
            copied = state.register_strategy(
                profile_id="profile-1",
                strategy_name="grid-btc-renamed",
                distribution="official",
                trading_mode="live",
            )

            self.assertEqual(restarted["strategy_id"], created["strategy_id"])
            self.assertEqual(restarted["strategy_name"], "grid-btc-renamed")
            self.assertNotEqual(copied["strategy_id"], created["strategy_id"])
            self.assertEqual(created["status"], "ACTIVE")
            self.assertEqual(copied["status"], "ACTIVE")


class AutoTradeAuthorizationRequestTests(unittest.TestCase):
    def test_v11_scope_rejects_expanded_fields_and_requires_valid_hours(self) -> None:
        state_module = load_state_module()
        base_scope = {
            "trade_types": ["SPOT"],
            "symbols": ["BTCUSDT"],
            "all_symbols": False,
            "max_single_amount": "20",
            "max_total_amount": "50",
        }

        with self.assertRaisesRegex(ValueError, "missing scope fields: valid_hours"):
            state_module.normalize_scope(base_scope)

        for field, value in (
            ("allowed_sides", ["BUY"]),
            ("allowed_order_types", ["MARKET"]),
            ("min_single_amount", "1"),
            ("max_order_count", 1),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, f"unknown scope fields: {field}"
            ):
                state_module.normalize_scope({**base_scope, "valid_hours": "24", field: value})

    def test_v11_scope_accepts_thirty_days_and_rejects_longer_validity(self) -> None:
        state_module = load_state_module()
        base_scope = {
            "trade_types": ["SPOT"],
            "symbols": ["BTCUSDT"],
            "all_symbols": False,
            "max_single_amount": "20",
            "max_total_amount": "50",
        }

        self.assertEqual(
            state_module.normalize_scope({**base_scope, "valid_hours": "720"})[
                "valid_hours"
            ],
            "720",
        )
        with self.assertRaisesRegex(ValueError, "valid_hours"):
            state_module.normalize_scope({**base_scope, "valid_hours": "720.0001"})
        with self.assertRaisesRegex(ValueError, "valid_hours"):
            state_module.normalize_scope({**base_scope, "valid_hours": "721"})

    def test_legacy_expanded_authorization_requires_new_scope_reauthorization(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="legacy-expanded", distribution="official"
            )
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "100",
                    "max_total_amount": "200",
                    "valid_hours": "24",
                },
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
            )
            with closing(sqlite3.connect(state.db_path)) as connection:
                for table, key, value in (
                    ("authorization_requests", "request_id", request["request_id"]),
                    ("authorizations", "authorization_id", authorization["authorization_id"]),
                ):
                    connection.execute(
                        f"UPDATE {table} SET allowed_sides_csv = 'BUY', "
                        "allowed_order_types_csv = 'MARKET', min_single_amount_u = '1', "
                        "max_order_count = 1 WHERE " + key + " = ?",
                        (value,),
                    )
                connection.commit()

            with self.assertRaisesRegex(
                ValueError, "AUTHORIZATION_SCOPE_REAUTHORIZATION_REQUIRED"
            ):
                state.prepare_submission_group(
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key="legacy-expanded-blocked",
                    request_fingerprint=hashlib.sha256(b"legacy-expanded-blocked").hexdigest(),
                    legs=[
                        {
                            "leg_id": "leg-0",
                            "leg_index": 0,
                            "leg_type": "PRIMARY",
                            "module": "SPOT",
                            "symbol": "BTCUSDT",
                            "estimated_amount_u": "20",
                            "valuation_source": "fixture",
                            "side": "BUY",
                            "order_type": "MARKET",
                            "quantity": "0.001",
                            "price": None,
                        }
                    ],
                )

    def test_ensure_reuses_pending_request_for_same_owner_and_normalized_scope(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1",
                strategy_name="grid-btc",
                distribution="official",
                trading_mode="live",
            )
            self.assertTrue(hasattr(state, "ensure_authorization"), "authorization ensure is not implemented")

            now = datetime(2026, 8, 16, 6, 30, tzinfo=UTC)
            first = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["FUTURES", "SPOT", "FUTURES"],
                    "symbols": ["btcusdt", "BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "10.00",
                    "max_total_amount": "100.000",
                    "valid_hours": "24.0",
                },
                now=now,
            )
            repeated = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT", "FUTURES"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "10",
                    "max_total_amount": "100",
                    "valid_hours": "24",
                },
                now=now,
            )

            self.assertEqual(first["status"], "AUTHORIZATION_REQUIRED")
            self.assertEqual(repeated["request_id"], first["request_id"])
            self.assertEqual(
                first["scope"],
                {
                    "trade_types": ["SPOT", "FUTURES"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "10",
                    "max_total_amount": "100",
                    "valid_hours": "24",
                },
            )
            self.assertEqual(first["consumed_amount_u"], "0")
            self.assertEqual(first["reserved_amount_u"], "0")
            self.assertEqual(first["remaining_amount_u"], "100")
            self.assertEqual(first["next_action"], "WAIT_FOR_AUTHORIZATION")

    def test_scope_change_rejects_stale_pending_request_without_affecting_other_strategy(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy_a = state.register_strategy(
                profile_id="profile-1", strategy_name="strategy-a", distribution="official"
            )
            strategy_b = state.register_strategy(
                profile_id="profile-1", strategy_name="strategy-b", distribution="official"
            )
            now = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
            base_scope = {
                "trade_types": ["SPOT"],
                "symbols": ["BTCUSDT"],
                "all_symbols": False,
                "max_single_amount": "20",
                "max_total_amount": "100",
                "valid_hours": "24",
            }
            stale = state.ensure_authorization(
                strategy_id=strategy_a["strategy_id"], scope=base_scope, now=now
            )
            other_owner = state.ensure_authorization(
                strategy_id=strategy_b["strategy_id"], scope=base_scope, now=now
            )

            replacement = state.ensure_authorization(
                strategy_id=strategy_a["strategy_id"],
                scope={**base_scope, "max_total_amount": "50"},
                now=now + timedelta(seconds=1),
            )
            repeated = state.ensure_authorization(
                strategy_id=strategy_a["strategy_id"],
                scope={**base_scope, "max_total_amount": "50.0"},
                now=now + timedelta(seconds=2),
            )

            self.assertNotEqual(replacement["request_id"], stale["request_id"])
            self.assertEqual(repeated["request_id"], replacement["request_id"])
            self.assertEqual(
                state.get_authorization_request(
                    strategy_id=strategy_a["strategy_id"],
                    request_id=stale["request_id"],
                    now=now + timedelta(seconds=2),
                )["request_status"],
                "REJECTED",
            )
            self.assertEqual(
                state.get_authorization_request(
                    strategy_id=strategy_b["strategy_id"],
                    request_id=other_owner["request_id"],
                    now=now + timedelta(seconds=2),
                )["request_status"],
                "PENDING",
            )
            with self.assertRaisesRegex(ValueError, "AUTHORIZATION_REQUEST_NOT_PENDING"):
                state.grant_authorization(
                    strategy_id=strategy_a["strategy_id"],
                    request_id=stale["request_id"],
                    scope_signature=stale["scope_signature"],
                    confirm_live=True,
                    now=now + timedelta(seconds=2),
                )
            replaced_event = next(
                event
                for event in state.list_events(strategy_id=strategy_a["strategy_id"])
                if event["request_id"] == stale["request_id"]
                and event["event_type"] == "AUTHORIZATION_REQUEST_REJECTED"
            )
            self.assertEqual(replaced_event["payload"]["reason"], "SCOPE_CHANGED")


class AutoTradeAuthorizationGrantTests(unittest.TestCase):
    def test_grant_strongly_binds_request_and_replaces_only_the_same_strategy_owner(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy_a = state.register_strategy(
                profile_id="profile-1", strategy_name="strategy-a", distribution="official"
            )
            strategy_b = state.register_strategy(
                profile_id="profile-1", strategy_name="strategy-b", distribution="official"
            )
            now = datetime(2026, 8, 16, 7, 0, tzinfo=UTC)
            base_scope = {
                "trade_types": ["SPOT"],
                "symbols": ["BTCUSDT"],
                "all_symbols": False,
                "max_single_amount": "10",
                "max_total_amount": "100",
                "valid_hours": "24",
            }
            request_a1 = state.ensure_authorization(
                strategy_id=strategy_a["strategy_id"], scope=base_scope, now=now
            )
            request_b = state.ensure_authorization(
                strategy_id=strategy_b["strategy_id"], scope=base_scope, now=now
            )
            self.assertTrue(hasattr(state, "grant_authorization"), "authorization grant is not implemented")

            with self.assertRaisesRegex(ValueError, "STRATEGY_AUTHORIZATION_MISMATCH"):
                state.grant_authorization(
                    strategy_id=strategy_b["strategy_id"],
                    request_id=request_a1["request_id"],
                    scope_signature=request_a1["scope_signature"],
                    confirm_live=True,
                    now=now,
                )

            authorization_a1 = state.grant_authorization(
                strategy_id=strategy_a["strategy_id"],
                request_id=request_a1["request_id"],
                scope_signature=request_a1["scope_signature"],
                confirm_live=True,
                now=now,
            )
            authorization_b = state.grant_authorization(
                strategy_id=strategy_b["strategy_id"],
                request_id=request_b["request_id"],
                scope_signature=request_b["scope_signature"],
                confirm_live=True,
                now=now,
            )
            request_a2 = state.ensure_authorization(
                strategy_id=strategy_a["strategy_id"],
                scope={**base_scope, "max_total_amount": "200"},
                now=now,
            )
            authorization_a2 = state.grant_authorization(
                strategy_id=strategy_a["strategy_id"],
                request_id=request_a2["request_id"],
                scope_signature=request_a2["scope_signature"],
                confirm_live=True,
                now=now,
            )

            self.assertEqual(authorization_a1["status"], "ACTIVE")
            self.assertEqual(authorization_a2["status"], "ACTIVE")
            self.assertEqual(authorization_b["status"], "ACTIVE")
            with closing(sqlite3.connect(db_path)) as connection:
                statuses = dict(
                    connection.execute(
                        "SELECT authorization_id, status FROM authorizations"
                    ).fetchall()
                )
            self.assertEqual(statuses[authorization_a1["authorization_id"]], "REPLACED")
            self.assertEqual(statuses[authorization_a2["authorization_id"]], "ACTIVE")
            self.assertEqual(statuses[authorization_b["authorization_id"]], "ACTIVE")


class AutoTradeAuthorizationLifecycleTests(unittest.TestCase):
    def test_read_paths_expire_due_records_once(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="lazy-expiry", distribution="official"
            )
            started_at = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
            pending = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "10",
                    "max_total_amount": "100",
                    "valid_hours": "0.01",
                },
                now=started_at,
            )
            pending_after = started_at.replace(minute=16)
            self.assertEqual(
                state.get_authorization_request(
                    strategy_id=strategy["strategy_id"],
                    request_id=pending["request_id"],
                    now=pending_after,
                )["request_status"],
                "EXPIRED",
            )
            state.get_authorization_request(
                strategy_id=strategy["strategy_id"],
                request_id=pending["request_id"],
                now=pending_after,
            )

            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["ETHUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "10",
                    "max_total_amount": "100",
                    "valid_hours": "0.01",
                },
                now=pending_after,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=pending_after,
            )
            expired_at = pending_after.replace(second=37)
            listed = state.list_authorizations(
                strategy_id=strategy["strategy_id"], now=expired_at
            )
            self.assertEqual(
                next(
                    item for item in listed
                    if item["authorization_id"] == authorization["authorization_id"]
                )["status"],
                "EXPIRED",
            )
            state.list_authorizations(strategy_id=strategy["strategy_id"], now=expired_at)
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM authorization_events "
                        "WHERE request_id = ? AND event_type = 'AUTHORIZATION_REQUEST_EXPIRED'",
                        (pending["request_id"],),
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM authorization_events "
                        "WHERE authorization_id = ? AND event_type = 'AUTHORIZATION_EXPIRED'",
                        (authorization["authorization_id"],),
                    ).fetchone()[0],
                    1,
                )

    def test_expire_revoke_and_retire_preserve_history_and_block_future_authorization(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="lifecycle", distribution="official"
            )
            scope = {
                "trade_types": ["FUTURES"],
                "symbols": ["BTCUSDT"],
                "all_symbols": False,
                "max_single_amount": "10",
                "max_total_amount": "100",
                "valid_hours": "0.01",
            }
            started_at = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
            request_1 = state.ensure_authorization(
                strategy_id=strategy["strategy_id"], scope=scope, now=started_at
            )
            authorization_1 = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request_1["request_id"],
                scope_signature=request_1["scope_signature"],
                confirm_live=True,
                now=started_at,
            )

            after_expiry = started_at.replace(second=37)
            request_2 = state.ensure_authorization(
                strategy_id=strategy["strategy_id"], scope=scope, now=after_expiry
            )
            authorization_2 = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request_2["request_id"],
                scope_signature=request_2["scope_signature"],
                confirm_live=True,
                now=after_expiry,
            )
            self.assertTrue(hasattr(state, "revoke_authorization"), "authorization revoke is not implemented")
            revoked = state.revoke_authorization(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization_2["authorization_id"],
                now=after_expiry,
            )
            self.assertEqual(revoked["status"], "REVOKED")

            request_3 = state.ensure_authorization(
                strategy_id=strategy["strategy_id"], scope=scope, now=after_expiry
            )
            authorization_3 = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request_3["request_id"],
                scope_signature=request_3["scope_signature"],
                confirm_live=True,
                now=after_expiry,
            )
            self.assertTrue(hasattr(state, "retire_strategy"), "strategy retirement is not implemented")
            retired = state.retire_strategy(strategy_id=strategy["strategy_id"], now=after_expiry)
            self.assertEqual(retired["status"], "RETIRED")
            with self.assertRaisesRegex(ValueError, "STRATEGY_RETIRED"):
                state.ensure_authorization(
                    strategy_id=strategy["strategy_id"], scope=scope, now=after_expiry
                )

            with closing(sqlite3.connect(db_path)) as connection:
                history = dict(
                    connection.execute(
                        "SELECT authorization_id, status FROM authorizations"
                    ).fetchall()
                )
            self.assertEqual(history[authorization_1["authorization_id"]], "EXPIRED")
            self.assertEqual(history[authorization_2["authorization_id"]], "REVOKED")
            self.assertEqual(history[authorization_3["authorization_id"]], "REVOKED")
            self.assertEqual(len(history), 3)

    def test_expired_submission_commits_state_and_one_immediate_lifecycle_event(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="expire-submit", distribution="official"
            )
            started_at = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "10",
                    "max_total_amount": "100",
                    "valid_hours": "0.01",
                },
                now=started_at,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=started_at,
            )

            with self.assertRaisesRegex(ValueError, "AUTHORIZATION_NOT_ACTIVE"):
                state.prepare_submission_group(
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key="expired-submit",
                    request_fingerprint="e" * 64,
                    legs=[
                        {
                            "leg_id": "leg-0",
                            "leg_index": 0,
                            "leg_type": "PRIMARY",
                            "module": "SPOT",
                            "symbol": "BTCUSDT",
                            "estimated_amount_u": "1",
                            "valuation_source": "fixture",
                            "side": "BUY",
                            "order_type": "LIMIT",
                            "quantity": "1",
                            "price": "1",
                        }
                    ],
                    now=started_at.replace(second=37),
                )

            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM authorizations WHERE authorization_id = ?",
                        (authorization["authorization_id"],),
                    ).fetchone()[0],
                    "EXPIRED",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM authorization_events "
                        "WHERE authorization_id = ? AND event_type = 'AUTHORIZATION_EXPIRED'",
                        (authorization["authorization_id"],),
                    ).fetchone()[0],
                    1,
                )
            claims = state.claim_notifications(now=started_at.replace(second=38))
            lifecycle = [claim for claim in claims if claim.get("event_type") == "AUTHORIZATION_EXPIRED"]
            self.assertEqual(len(lifecycle), 1)


class AutoTradeUsageTests(unittest.TestCase):
    def test_v11_scope_does_not_add_side_type_minimum_or_order_count_limits(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="bound-strategy", distribution="official"
            )
            now = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(
                    max_single_amount="21",
                    max_total_amount="50",
                ),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )

            def leg(**overrides):
                value = {
                    "leg_id": "leg-0",
                    "leg_index": 0,
                    "leg_type": "PRIMARY",
                    "module": "SPOT",
                    "symbol": "BTCUSDT",
                    "estimated_amount_u": "20",
                    "valuation_source": "fixture",
                    "side": "BUY",
                    "order_type": "MARKET",
                    "quantity": "0.000309",
                    "price": None,
                }
                value.update(overrides)
                return value

            for key, changed_leg in (
                ("sell-side", leg(side="SELL")),
                ("limit-type", leg(order_type="LIMIT", price="65000")),
                ("small-positive-amount", leg(estimated_amount_u="0.01")),
            ):
                with self.subTest(key=key):
                    group = state.prepare_submission_group(
                        strategy_id=strategy["strategy_id"],
                        authorization_id=authorization["authorization_id"],
                        idempotency_key=key,
                        request_fingerprint=hashlib.sha256(key.encode()).hexdigest(),
                        legs=[changed_leg],
                        now=now,
                    )
                    self.assertEqual(len(group["legs"]), 1)
                state.settle_usage(
                    usage_id=group["legs"][0]["usage_id"], outcome="RELEASED", now=now
                )

    def test_usage_transitions_keep_decimal_accepted_and_reserved_balances(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="quota", distribution="official"
            )
            scope = {
                "trade_types": ["SPOT", "FUTURES"],
                "symbols": ["BTCUSDT"],
                "all_symbols": False,
                "max_single_amount": "40",
                "max_total_amount": "100",
                "valid_hours": "24",
            }
            now = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"], scope=scope, now=now
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            self.assertTrue(hasattr(state, "reserve_usage"), "usage reservation is not implemented")

            reserved_1 = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="order-1",
                estimated_amount_u="30.00",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="official-depth",
                now=now,
            )
            repeated_1 = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="order-1",
                estimated_amount_u="30",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="official-depth",
                now=now,
            )
            self.assertEqual(repeated_1["usage_id"], reserved_1["usage_id"])
            self.assertEqual(reserved_1["reserved_amount_u"], "30")
            self.assertEqual(reserved_1["remaining_amount_u"], "70")

            self.assertTrue(hasattr(state, "settle_usage"), "usage settlement is not implemented")
            accepted_1 = state.settle_usage(
                usage_id=reserved_1["usage_id"], outcome="ACCEPTED", now=now
            )
            self.assertEqual(accepted_1["accepted_amount_u"], "30")
            self.assertEqual(accepted_1["reserved_amount_u"], "0")
            self.assertEqual(accepted_1["remaining_amount_u"], "70")

            reserved_2 = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="order-2",
                estimated_amount_u="40",
                module="FUTURES",
                symbol="BTCUSDT",
                valuation_source="official-depth",
                now=now,
            )
            released_2 = state.settle_usage(
                usage_id=reserved_2["usage_id"], outcome="RELEASED", now=now
            )
            self.assertEqual(released_2["accepted_amount_u"], "30")
            self.assertEqual(released_2["reserved_amount_u"], "0")
            self.assertEqual(released_2["remaining_amount_u"], "70")

            reserved_3 = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="order-3",
                estimated_amount_u="40",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="official-depth",
                now=now,
            )
            review_3 = state.settle_usage(
                usage_id=reserved_3["usage_id"], outcome="REVIEW_REQUIRED", now=now
            )
            self.assertEqual(review_3["accepted_amount_u"], "30")
            self.assertEqual(review_3["reserved_amount_u"], "40")
            self.assertEqual(review_3["remaining_amount_u"], "30")

            with self.assertRaisesRegex(ValueError, "TOTAL_LIMIT_EXCEEDED"):
                state.reserve_usage(
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key="order-4",
                    estimated_amount_u="31",
                    module="SPOT",
                    symbol="BTCUSDT",
                    valuation_source="official-depth",
                    now=now,
                )

    def test_concurrent_reservations_cannot_overspend_the_last_total_quota(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="concurrent", distribution="official"
            )
            now = datetime(2026, 8, 16, 9, 30, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "60",
                    "max_total_amount": "100",
                    "valid_hours": "24",
                },
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            barrier = threading.Barrier(2)

            def reserve(key: str):
                worker_state = state_module.AutoTradeState(db_path)
                barrier.wait(timeout=2)
                try:
                    return worker_state.reserve_usage(
                        strategy_id=strategy["strategy_id"],
                        authorization_id=authorization["authorization_id"],
                        idempotency_key=key,
                        estimated_amount_u="60",
                        module="SPOT",
                        symbol="BTCUSDT",
                        valuation_source="official-depth",
                        now=now,
                    )
                except Exception as exc:  # The exact fail-closed error is asserted below.
                    return exc

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(reserve, ("worker-a", "worker-b")))

            successes = [result for result in results if isinstance(result, dict)]
            failures = [result for result in results if isinstance(result, Exception)]
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], ValueError)
            self.assertIn("TOTAL_LIMIT_EXCEEDED", str(failures[0]))
            self.assertEqual(successes[0]["reserved_amount_u"], "60")
            self.assertEqual(successes[0]["remaining_amount_u"], "40")

    def test_decimal_accumulation_never_rounds_past_total_limit(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="decimal-precision", distribution="official"
            )
            now = datetime(2026, 8, 16, 9, 45, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "100000000000000000000",
                    "max_total_amount": "1000000000000000000000",
                    "valid_hours": "24",
                },
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            for index in range(10):
                state.reserve_usage(
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key=f"precision-{index}",
                    estimated_amount_u="99999999999999999999.99999994",
                    module="SPOT",
                    symbol="BTCUSDT",
                    valuation_source="fixture",
                    now=now,
                )
            with self.assertRaisesRegex(ValueError, "TOTAL_LIMIT_EXCEEDED"):
                state.reserve_usage(
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key="precision-overflow",
                    estimated_amount_u="0.00000070",
                    module="SPOT",
                    symbol="BTCUSDT",
                    valuation_source="fixture",
                    now=now,
                )
            self.assertEqual(state.health_check(full=True)["status"], "HEALTHY")


class AutoTradeInvariantRegressionTests(unittest.TestCase):
    def test_health_check_rejects_cross_strategy_authorization_request_mismatch(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            now = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)
            strategies = [
                state.register_strategy(
                    profile_id="profile-1", strategy_name=f"invariant-{index}", distribution="official"
                )
                for index in range(2)
            ]
            requests = [
                state.ensure_authorization(
                    strategy_id=strategy["strategy_id"],
                    scope={
                        "trade_types": ["SPOT"],
                        "symbols": ["BTCUSDT"],
                        "all_symbols": False,
                        "max_single_amount": "10",
                        "max_total_amount": "100",
                        "valid_hours": "24",
                    },
                    now=now,
                )
                for strategy in strategies
            ]
            authorization = state.grant_authorization(
                strategy_id=strategies[0]["strategy_id"],
                request_id=requests[0]["request_id"],
                scope_signature=requests[0]["scope_signature"],
                confirm_live=True,
                now=now,
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "UPDATE authorizations SET request_id = ? WHERE authorization_id = ?",
                    (requests[1]["request_id"], authorization["authorization_id"]),
                )
                connection.commit()

            with self.assertRaises(state_module.StateConflictError):
                state.health_check(full=True)

    def test_health_check_rejects_contradictory_event_reference_chain(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="event-chain", distribution="official"
            )
            now = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)

            def grant(symbol, total):
                request = state.ensure_authorization(
                    strategy_id=strategy["strategy_id"],
                    scope=complete_scope(
                        symbols=[symbol],
                        max_single_amount="10",
                        max_total_amount=total,
                    ),
                    now=now,
                )
                authorization = state.grant_authorization(
                    strategy_id=strategy["strategy_id"],
                    request_id=request["request_id"],
                    scope_signature=request["scope_signature"],
                    confirm_live=True,
                    now=now,
                )
                group = state.prepare_submission_group(
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key=f"chain-{symbol}",
                    request_fingerprint=("a" if symbol == "BTCUSDT" else "b") * 64,
                    legs=[
                        {
                            "leg_id": "leg-0",
                            "leg_index": 0,
                            "leg_type": "PRIMARY",
                            "module": "SPOT",
                            "symbol": symbol,
                            "estimated_amount_u": "1",
                            "valuation_source": "fixture",
                            "side": "BUY",
                            "order_type": "LIMIT",
                            "quantity": "1",
                            "price": "1",
                        }
                    ],
                    now=now,
                )
                return request, authorization, group["legs"][0]

            first = grant("BTCUSDT", "100")
            second = grant("ETHUSDT", "101")
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    """
                    UPDATE authorization_events
                    SET request_id = ?, authorization_id = ?, usage_id = ?, auto_trade_order_id = ?
                    WHERE event_type = 'ORDER_RECORDED' AND usage_id = ?
                    """,
                    (
                        second[0]["request_id"],
                        second[1]["authorization_id"],
                        first[2]["usage_id"],
                        second[2]["auto_trade_order_id"],
                        first[2]["usage_id"],
                    ),
                )
                connection.commit()

            with self.assertRaises(state_module.StateConflictError):
                state.health_check(full=True)


class AutoTradeOrderFactTests(unittest.TestCase):
    def test_reconciliation_updates_order_facts_without_changing_accepted_quota(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="reconcile", distribution="official"
            )
            now = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(max_single_amount="50", max_total_amount="100"),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            usage = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="order-accepted",
                estimated_amount_u="25",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="official-depth",
                now=now,
            )
            self.assertTrue(hasattr(state, "record_order"), "order ownership recording is not implemented")
            order = state.record_order(
                usage_id=usage["usage_id"],
                weex_order_id="weex-order-1",
                side="BUY",
                order_type="MARKET",
                quantity="0.001",
                price=None,
                now=now,
            )
            accepted = state.settle_usage(
                usage_id=usage["usage_id"], outcome="ACCEPTED", now=now
            )
            self.assertNotIn(authorization["authorization_id"], order["client_order_id"])
            self.assertTrue(hasattr(state, "reconcile_order"), "order reconciliation is not implemented")

            reconciled = state.reconcile_order(
                auto_trade_order_id=order["auto_trade_order_id"],
                reconciliation_status="COMPLETE",
                exchange_status="FILLED",
                executed_quantity="0.001",
                executed_quote_amount="24.50",
                fee_amount="0.0245",
                fee_asset="USDT",
                reconciliation_source="official-order-query",
                now=now,
            )

            self.assertEqual(reconciled["reconciliation_status"], "COMPLETE")
            self.assertEqual(reconciled["executed_quote_amount"], "24.5")
            self.assertEqual(reconciled["fee_amount"], "0.0245")
            self.assertEqual(reconciled["usage_status"], "ACCEPTED")
            self.assertEqual(reconciled["estimated_amount_u"], "25")
            self.assertEqual(reconciled["accepted_amount_u"], accepted["accepted_amount_u"])
            self.assertEqual(reconciled["reserved_amount_u"], accepted["reserved_amount_u"])
            self.assertEqual(reconciled["remaining_amount_u"], accepted["remaining_amount_u"])


class AutoTradeEventTests(unittest.TestCase):
    def test_business_events_are_append_only_versioned_and_traceable(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            now = datetime(2026, 8, 16, 10, 30, tzinfo=UTC)
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="events", distribution="official"
            )
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(max_single_amount="50", max_total_amount="100"),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            usage = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="event-order",
                estimated_amount_u="25",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="official-depth",
                now=now,
            )
            order = state.record_order(
                usage_id=usage["usage_id"],
                weex_order_id="weex-event-order",
                side="BUY",
                order_type="MARKET",
                quantity="0.001",
                price=None,
                now=now,
            )
            state.settle_usage(usage_id=usage["usage_id"], outcome="ACCEPTED", now=now)
            state.reconcile_order(
                auto_trade_order_id=order["auto_trade_order_id"],
                reconciliation_status="UNAVAILABLE",
                exchange_status=None,
                executed_quantity=None,
                executed_quote_amount=None,
                fee_amount=None,
                fee_asset=None,
                reconciliation_source="official-query-unavailable",
                now=now,
            )
            state.revoke_authorization(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                now=now,
            )
            self.assertTrue(hasattr(state, "list_events"), "event facade is not implemented")

            events = state.list_events(strategy_id=strategy["strategy_id"])

            self.assertEqual(
                [event["event_type"] for event in events],
                [
                    "STRATEGY_REGISTERED",
                    "AUTHORIZATION_REQUESTED",
                    "AUTHORIZATION_GRANTED",
                    "USAGE_RESERVED",
                    "ORDER_RECORDED",
                    "USAGE_ACCEPTED",
                    "ORDER_RECONCILIATION_UNAVAILABLE",
                    "AUTHORIZATION_REVOKED",
                ],
            )
            self.assertEqual(len({event["event_id"] for event in events}), len(events))
            for event in events:
                self.assertEqual(event["event_schema_version"], state_module.EVENT_SCHEMA_VERSION)
                self.assertEqual(
                    event["payload"]["event_schema_version"], state_module.EVENT_SCHEMA_VERSION
                )
                self.assertEqual(event["strategy_id"], strategy["strategy_id"])

    def test_notification_claims_aggregate_success_and_do_not_retry_failures(self) -> None:
        state_module = load_state_module()

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="notifications", distribution="official"
            )
            window_start = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "20",
                    "max_total_amount": "100",
                    "valid_hours": "24",
                },
                now=window_start,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=window_start,
            )
            for index in range(2):
                usage = state.reserve_usage(
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key=f"notification-{index}",
                    estimated_amount_u="10",
                    module="SPOT",
                    symbol="BTCUSDT",
                    valuation_source="official-depth",
                    now=window_start.replace(second=index + 1),
                )
                state.settle_usage(
                    usage_id=usage["usage_id"],
                    outcome="ACCEPTED",
                    now=window_start.replace(second=index + 1),
                )
            self.assertTrue(hasattr(state, "claim_notifications"), "notification claims are not implemented")

            claims = state.claim_notifications(now=window_start.replace(minute=1, second=1))
            summaries = [claim for claim in claims if claim["kind"] == "ACCEPTED_SUMMARY"]
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["order_count"], 2)
            self.assertEqual(summaries[0]["estimated_amount_u"], "20")
            self.assertEqual(summaries[0]["remaining_amount_u"], "80")
            self.assertNotIn(authorization["authorization_id"], json.dumps(summaries[0]))
            self.assertEqual(
                state.claim_notifications(now=window_start.replace(minute=1, second=1)),
                [],
            )

            review_usage = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="notification-review",
                estimated_amount_u="10",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="official-depth",
                now=window_start.replace(minute=1, second=2),
            )
            state.settle_usage(
                usage_id=review_usage["usage_id"],
                outcome="REVIEW_REQUIRED",
                now=window_start.replace(minute=1, second=2),
            )
            exception_claims = state.claim_notifications(
                now=window_start.replace(minute=1, second=3)
            )
            immediate = [claim for claim in exception_claims if claim["kind"] == "EXCEPTION"]
            self.assertEqual(len(immediate), 1)
            self.assertTrue(hasattr(state, "complete_notification"), "notification result is not implemented")
            state.complete_notification(
                notification_key=immediate[0]["notification_key"],
                outcome="FAILED",
                now=window_start.replace(minute=1, second=3),
            )
            self.assertEqual(
                state.claim_notifications(now=window_start.replace(minute=1, second=4)),
                [],
            )
            authorization_after = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "20",
                    "max_total_amount": "100",
                    "valid_hours": "24",
                },
                now=window_start.replace(minute=1, second=4),
            )
            self.assertEqual(authorization_after["consumed_amount_u"], "20")
            self.assertEqual(authorization_after["reserved_amount_u"], "10")


class AutoTradeAmountTests(unittest.TestCase):
    def test_spot_limit_uses_decimal_notional_and_fee_upper_bound(self) -> None:
        amount_module = load_amount_module()
        self.assertTrue(hasattr(amount_module, "estimate_order_amount"), "amount estimator is not implemented")
        now_ms = 1_700_000_000_000
        result = amount_module.estimate_order_amount(
            market="SPOT",
            order={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "LIMIT",
                "quantity": "2",
                "price": "100",
            },
            facts={
                "timestamp_ms": now_ms - 100,
                "symbol": {
                    "quoteAsset": "USDT",
                    "makerFeeRate": "0.001",
                    "takerFeeRate": "0.002",
                },
            },
            now_ms=now_ms,
        )
        self.assertEqual(result["estimated_amount_u"], "200.4")
        self.assertTrue(result["estimated"])
        self.assertEqual(result["quote_asset"], "USDT")
        self.assertEqual(result["valuation_source"], "SPOT_LIMIT_NOTIONAL_PLUS_FEE_UPPER_BOUND")

    def test_spot_limit_preserves_precision_before_conservative_u_rounding(self) -> None:
        amount_module = load_amount_module()
        now_ms = 1_700_000_000_000

        result = amount_module.estimate_order_amount(
            market="SPOT",
            order={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "LIMIT",
                "quantity": "1.00000000000000000000000000001",
                "price": "1",
            },
            facts={
                "timestamp_ms": now_ms,
                "symbol": {
                    "quoteAsset": "USDT",
                    "makerFeeRate": "0",
                    "takerFeeRate": "0",
                },
            },
            now_ms=now_ms,
        )

        self.assertEqual(result["notional_quote"], "1.00000000000000000000000000001")
        self.assertEqual(result["estimated_amount_u"], "1.00000001")

    def test_spot_market_consumes_adverse_depth_and_rejects_insufficient_depth(self) -> None:
        amount_module = load_amount_module()
        now_ms = 1_700_000_000_000
        facts = {
            "timestamp_ms": now_ms - 100,
            "depth": {
                "timestamp_ms": now_ms - 100,
                "limit": 15,
                "asks": [["100", "1"], ["101", "2"]],
                "bids": [["99", "1"], ["98", "2"]],
            },
            "symbol": {
                "quoteAsset": "USDT",
                "makerFeeRate": "0.001",
                "takerFeeRate": "0.002",
            },
        }
        result = amount_module.estimate_order_amount(
            market="SPOT",
            order={"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": "2"},
            facts=facts,
            now_ms=now_ms,
        )
        self.assertEqual(result["notional_quote"], "201")
        self.assertEqual(result["estimated_amount_u"], "201.402")
        with self.assertRaises(amount_module.ValuationUnavailable):
            amount_module.estimate_order_amount(
                market="SPOT",
                order={"symbol": "BTCUSDT", "side": "SELL", "type": "MARKET", "quantity": "4"},
                facts=facts,
                now_ms=now_ms,
            )

    def test_btcusdt_19_9_target_uses_conservative_amounts_and_stops_before_third_order(
        self,
    ) -> None:
        amount_module = load_amount_module()
        state_module = load_state_module()
        now_ms = 1_700_000_000_000
        order = {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": "0.000309",
        }
        estimated_amounts = []
        for ask_price in ("64372.06", "64389.99"):
            valuation = amount_module.estimate_order_amount(
                market="SPOT",
                order=order,
                facts={
                    "timestamp_ms": now_ms,
                    "depth": {
                        "timestamp_ms": now_ms,
                        "limit": 200,
                        "asks": [[ask_price, "1"]],
                        "bids": [["64000", "1"]],
                    },
                    "symbol": {
                        "quoteAsset": "USDT",
                        "makerFeeRate": "0.01",
                        "takerFeeRate": "0.01",
                    },
                },
                now_ms=now_ms,
            )
            estimated_amounts.append(valuation["estimated_amount_u"])

        self.assertEqual(estimated_amounts, ["20.08987621", "20.09547198"])

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            now = datetime(2026, 8, 18, 6, 28, tzinfo=UTC)
            strategy = state.register_strategy(
                profile_id="profile-1",
                strategy_name="btcusdt-19.9u",
                distribution="official",
            )
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(
                    max_single_amount="21",
                    max_total_amount="50",
                    valid_hours="1",
                ),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )

            for index, estimated_amount in enumerate(estimated_amounts):
                group = state.prepare_submission_group(
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key=f"btcusdt-19.9u-{index}",
                    request_fingerprint=hashlib.sha256(
                        f"btcusdt-19.9u-{index}".encode("ascii")
                    ).hexdigest(),
                    legs=[
                        {
                            "leg_id": "leg-0",
                            "leg_index": 0,
                            "leg_type": "PRIMARY",
                            "module": "SPOT",
                            "symbol": "BTCUSDT",
                            "estimated_amount_u": estimated_amount,
                            "valuation_source": "SPOT_ADVERSE_DEPTH_PLUS_FEE_UPPER_BOUND",
                            "side": "BUY",
                            "order_type": "MARKET",
                            "quantity": "0.000309",
                            "price": None,
                        }
                    ],
                    now=now,
                )
                state.settle_usage(
                    usage_id=group["legs"][0]["usage_id"],
                    outcome="ACCEPTED",
                    now=now,
                )

            current = state.list_authorizations(strategy_id=strategy["strategy_id"], now=now)[0]
            self.assertEqual(current["consumed_amount_u"], "40.18534819")
            self.assertEqual(current["reserved_amount_u"], "0")
            self.assertEqual(current["remaining_amount_u"], "9.81465181")

            with self.assertRaisesRegex(ValueError, "TOTAL_LIMIT_EXCEEDED"):
                state.prepare_submission_group(
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key="btcusdt-19.9u-third",
                    request_fingerprint=hashlib.sha256(b"btcusdt-19.9u-third").hexdigest(),
                    legs=[
                        {
                            "leg_id": "leg-0",
                            "leg_index": 0,
                            "leg_type": "PRIMARY",
                            "module": "SPOT",
                            "symbol": "BTCUSDT",
                            "estimated_amount_u": estimated_amounts[0],
                            "valuation_source": "SPOT_ADVERSE_DEPTH_PLUS_FEE_UPPER_BOUND",
                            "side": "BUY",
                            "order_type": "MARKET",
                            "quantity": "0.000309",
                            "price": None,
                        }
                    ],
                    now=now,
                )


class AutoTradeCliTests(unittest.TestCase):
    def _profile_home(self, root: Path) -> Path:
        home = root / "weex-home"
        home.mkdir(mode=0o700)
        metadata = home / "profiles.meta.json"
        metadata.write_text(
            json.dumps(
                {
                    "version": 2,
                    "default_profile_id": "profile-1",
                    "profiles": {
                        "profile-1": {
                            "id": "profile-1",
                            "name": "strategy-live",
                            "description": "fixture without credentials",
                            "contract_base_url": "",
                            "spot_base_url": "",
                            "api_key_hint": "",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        metadata.chmod(0o600)
        return home

    def _run_cli(
        self,
        home: Path,
        command: str,
        payload: dict,
        *,
        confirm_live: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        argv = [sys.executable, str(CLI_PATH), command, "--input", "-"]
        if confirm_live:
            argv.append("--confirm-live")
        process = subprocess.run(
            argv,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=ROOT,
            env={
                **os.environ,
                "WEEX_TRADER_SKILL_HOME": str(home),
                "WEEX_AUTO_TRADE_NOTIFICATION_MODE": "disabled",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=False,
        )
        try:
            response = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"CLI did not return JSON: stdout={process.stdout!r}, stderr={process.stderr!r}, error={exc}")
        return process, response

    def test_cli_authorization_flow_returns_detailed_confirmation_and_events(self) -> None:
        if not CLI_PATH.exists():
            self.fail("weex_auto_trade.py has not been implemented")
        with tempfile.TemporaryDirectory() as tempdir:
            home = self._profile_home(Path(tempdir))
            process, registered = self._run_cli(
                home,
                "register-strategy",
                {"profile": "strategy-live", "strategy_name": "grid-btc"},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(registered["status"], "ACTIVE")

            scope_payload = {
                "profile": "strategy-live",
                "strategy_id": registered["strategy_id"],
                "trade_types": ["SPOT", "FUTURES"],
                "symbols": ["BTCUSDT"],
                "all_symbols": False,
                "max_single_amount": "10",
                "max_total_amount": "100",
                "valid_hours": "24",
            }
            process, pending = self._run_cli(home, "ensure-authorization", scope_payload)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(pending["status"], "AUTHORIZATION_REQUIRED")

            process, shown = self._run_cli(
                home,
                "show-authorization-request",
                {
                    "profile": "strategy-live",
                    "strategy_id": registered["strategy_id"],
                    "request_id": pending["request_id"],
                },
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            confirmation = shown["confirmation"]
            self.assertEqual(confirmation["strategy_name"], "grid-btc")
            self.assertEqual(confirmation["profile"], "strategy-live")
            self.assertEqual(confirmation["trading_mode"], "live")
            self.assertEqual(confirmation["trade_types"], ["SPOT", "FUTURES"])
            self.assertEqual(confirmation["max_single_amount_u"], "10")
            self.assertEqual(confirmation["max_total_amount_u"], "100")
            self.assertEqual(confirmation["valid_hours"], "24")
            for removed_field in (
                "allowed_sides",
                "allowed_order_types",
                "min_single_amount_u",
                "max_order_count",
            ):
                self.assertNotIn(removed_field, confirmation)
            self.assertTrue(confirmation["orders_skip_per_order_confirmation"])
            self.assertIn("not identity authentication", confirmation["trust_boundary"])
            self.assertIn("revoke-authorization", confirmation["revoke_command"])

            process, granted = self._run_cli(
                home,
                "grant-authorization",
                {
                    "profile": "strategy-live",
                    "strategy_id": registered["strategy_id"],
                    "request_id": pending["request_id"],
                    "scope_signature": pending["scope_signature"],
                },
                confirm_live=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(granted["status"], "ACTIVE")

            process, event_list = self._run_cli(
                home,
                "event-list",
                {"profile": "strategy-live", "strategy_id": registered["strategy_id"]},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertGreaterEqual(len(event_list["events"]), 3)
            self.assertTrue(
                all(
                    event["authorization_id"] is None
                    or event["authorization_id"].startswith("auth_***")
                    for event in event_list["events"]
                )
            )

    def test_cli_rejects_raw_credentials_before_creating_state(self) -> None:
        if not CLI_PATH.exists():
            self.fail("weex_auto_trade.py has not been implemented")
        with tempfile.TemporaryDirectory() as tempdir:
            home = self._profile_home(Path(tempdir))
            process, response = self._run_cli(
                home,
                "register-strategy",
                {
                    "profile": "strategy-live",
                    "strategy_name": "must-not-exist",
                    "api_secret": "never-echo-this-value",
                },
            )
            self.assertEqual(process.returncode, 2)
            self.assertEqual(response["error"]["code"], "RAW_CREDENTIALS_NOT_ALLOWED")
            self.assertNotIn("never-echo-this-value", process.stdout)
            self.assertFalse((home / "auto-trade" / "authorization-state.sqlite3").exists())

    def test_cli_snapshot_and_restore_use_managed_snapshot_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = self._profile_home(Path(tempdir))
            process, registered = self._run_cli(
                home,
                "register-strategy",
                {"profile": "strategy-live", "strategy_name": "before-snapshot"},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            process, snapshot = self._run_cli(
                home,
                "snapshot-state",
                {"profile": "strategy-live", "retention_count": 3},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(snapshot["status"], "SNAPSHOT_CREATED")
            self.assertEqual(snapshot["profile"], "strategy-live")

            process, later = self._run_cli(
                home,
                "register-strategy",
                {"profile": "strategy-live", "strategy_name": "after-snapshot"},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertNotEqual(later["strategy_id"], registered["strategy_id"])

            process, restored = self._run_cli(
                home,
                "restore-state",
                {"profile": "strategy-live", "snapshot_id": snapshot["snapshot_id"]},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(restored["status"], "STATE_RESTORED_DISABLED")
            self.assertEqual(restored["profile"], "strategy-live")
            process, listed = self._run_cli(
                home,
                "list-strategies",
                {"profile": "strategy-live"},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(
                [item["strategy_name"] for item in listed["strategies"]],
                ["before-snapshot"],
            )

    def test_cli_snapshot_rejects_password_key_and_path_fields_before_state_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            for field in ("snapshot_password", "encryption_key", "destination_path"):
                with self.subTest(field=field):
                    case_root = Path(tempdir) / field
                    case_root.mkdir()
                    home = self._profile_home(case_root)
                    process, response = self._run_cli(
                        home,
                        "snapshot-state",
                        {
                            "profile": "strategy-live",
                            field: "must-not-be-accepted-or-echoed",
                        },
                    )
                    self.assertEqual(process.returncode, 2)
                    self.assertIn(
                        response["error"]["code"],
                        {"RAW_CREDENTIALS_NOT_ALLOWED", "INVALID_REQUEST"},
                    )
                    self.assertNotIn("must-not-be-accepted-or-echoed", process.stdout)
                    self.assertFalse(
                        (home / "auto-trade" / "authorization-state.sqlite3").exists()
                    )

    def test_cli_retention_errors_use_stable_public_code_without_snapshot_action(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = self._profile_home(Path(tempdir))
            for invalid in (0, 101, -1, "10", True):
                with self.subTest(invalid=invalid):
                    process, response = self._run_cli(
                        home,
                        "snapshot-state",
                        {"profile": "strategy-live", "retention_count": invalid},
                    )
                    self.assertEqual(process.returncode, 2)
                    self.assertEqual(response["error"]["code"], "INVALID_RETENTION_COUNT")
                    self.assertFalse((home / "snapshots").exists())

    def test_file_input_limit_is_checked_before_reading_the_file(self) -> None:
        cli_module = load_cli_module()
        with tempfile.TemporaryDirectory() as tempdir:
            input_path = Path(tempdir) / "large.json"
            input_path.write_bytes(b"x" * (cli_module.MAX_INPUT_BYTES + 1))
            with patch.object(Path, "read_text", side_effect=AssertionError("unbounded read")):
                with self.assertRaisesRegex(cli_module.FacadeError, "too large"):
                    cli_module._load_input(str(input_path))


class AutoTradeFacadeProductionBoundaryTests(unittest.TestCase):
    def _authorized_fixture(self, root: Path):
        state_module = load_state_module()
        cli_module = load_cli_module()
        state = state_module.AutoTradeState(root / "state" / "state.sqlite3")
        state.initialize()
        profile = SimpleNamespace(profile_id="profile-1", name="strategy-live")
        strategy = state.register_strategy(
            profile_id=profile.profile_id,
            strategy_name="facade-boundary",
            distribution="official",
        )
        request = state.ensure_authorization(
            strategy_id=strategy["strategy_id"],
            scope=complete_scope(),
        )
        authorization = state.grant_authorization(
            strategy_id=strategy["strategy_id"],
            request_id=request["request_id"],
            scope_signature=request["scope_signature"],
            confirm_live=True,
        )
        return state, cli_module, profile, strategy, authorization

    def test_public_auto_and_event_projections_separate_leg_amount_from_quota(self) -> None:
        cli_module = load_cli_module()
        public = cli_module._public_auto_result(
            {
                "ok": True,
                "status": "ACCEPTED",
                "legs": [
                    {
                        "status": "ACCEPTED",
                        "strategy_id": "strat_1234567890",
                        "authorization_id": "auth_1234567890",
                        "estimated_amount_u": "20",
                        "accepted_amount_u": "40",
                        "reserved_amount_u": "0",
                        "remaining_amount_u": "10",
                    }
                ],
            },
            profile_name="profile",
            strategy_id="strat_1234567890",
            authorization_id="auth_1234567890",
        )
        leg = public["legs"][0]
        self.assertEqual(leg["estimated_amount_u"], "20")
        self.assertEqual(
            leg["authorization_quota"],
            {
                "consumed_amount_u": "40",
                "reserved_amount_u": "0",
                "remaining_amount_u": "10",
            },
        )
        self.assertNotIn("accepted_amount_u", leg)
        self.assertNotIn("reserved_amount_u", leg)
        self.assertNotIn("remaining_amount_u", leg)

        event = cli_module._public_event(
            {
                "event_id": "evt_1",
                "strategy_id": "strat_1234567890",
                "authorization_id": "auth_1234567890",
                "payload": {
                    "event_schema_version": 1,
                    "status": "ACCEPTED",
                    "estimated_amount_u": "20",
                    "accepted_amount_u": "40",
                    "reserved_amount_u": "0",
                },
            },
            max_total_amount_u="50",
        )
        self.assertEqual(event["payload"]["estimated_amount_u"], "20")
        self.assertEqual(
            event["payload"]["authorization_quota"],
            {
                "consumed_amount_u": "40",
                "reserved_amount_u": "0",
                "remaining_amount_u": "10",
            },
        )
        self.assertNotIn("accepted_amount_u", event["payload"])

    def test_submit_auto_facade_uses_guard_runtime_and_persists_accepted_leg(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state, cli_module, profile, strategy, authorization = self._authorized_fixture(
                Path(tempdir)
            )
            now_ms = int(datetime.now(UTC).timestamp() * 1000)
            runtime = SimpleNamespace(
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": complete_spot_account_snapshot(),
                    "generated_at": datetime.now(UTC).isoformat(),
                },
                risk_evaluator=lambda payload: {"alerts": [], "rule_version": "fixture-v1"},
                facts_provider=lambda leg: {
                    "timestamp_ms": now_ms,
                    "symbol": complete_spot_symbol_facts(),
                },
                submitter=lambda operation_key, legs: [
                    {
                        "clientOrderId": legs[0]["client_order_id"],
                        "success": True,
                        "orderId": "weex-facade-1",
                    }
                ],
            )
            notification_worker_launcher = Mock()
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
                auto_trade_runtime_factory=lambda resolved: runtime,
                notification_worker_launcher=notification_worker_launcher,
            )

            result = facade.execute(
                "submit-auto",
                {
                    "profile": profile.name,
                    "strategy_id": strategy["strategy_id"],
                    "authorization_id": authorization["authorization_id"],
                    "idempotency_key": "facade-submit-1",
                    "operation_key": "spot.order.place_order",
                    "orders": [
                        {
                            "symbol": "BTCUSDT",
                            "side": "BUY",
                            "type": "LIMIT",
                            "timeInForce": "GTC",
                            "quantity": "1",
                            "price": "10",
                        }
                    ],
                },
                confirm_live=True,
            )

            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["profile"], profile.name)
            self.assertEqual(result["authorization_id"], "auth_***" + authorization["authorization_id"][-6:])
            self.assertEqual(result["legs"][0]["weex_order_id"], "weex-facade-1")
            notification_worker_launcher.assert_called_once()
            worker_call = notification_worker_launcher.call_args.kwargs
            self.assertEqual(worker_call["state_path"], state.db_path)
            self.assertTrue(worker_call["notification_key"].startswith("summary:"))
            self.assertEqual(worker_call["not_before"].second, 0)
            self.assertEqual(worker_call["not_before"].microsecond, 0)
            self.assertEqual(worker_call["not_before"].tzinfo, UTC)

    def test_submit_auto_manual_fallback_creates_bound_pending_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state, cli_module, profile, strategy, authorization = self._authorized_fixture(
                Path(tempdir)
            )
            captured: list[dict] = []
            runtime = SimpleNamespace(
                risk_payload_provider=Mock(), risk_evaluator=Mock(), facts_provider=Mock(), submitter=Mock()
            )
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
                auto_trade_runtime_factory=lambda resolved: runtime,
                manual_intent_writer=captured.append,
            )
            order = {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": "1",
                "price": "120",
            }
            guard_result = {
                "ok": False,
                "status": "MANUAL_CONFIRMATION_REQUIRED",
                "error": {"code": "SINGLE_LIMIT_EXCEEDED"},
                "advisory_alerts": [],
                "blocking_reasons": [
                    {
                        "code": "SINGLE_LIMIT_EXCEEDED",
                        "message": "authorization, scope, or quota check failed",
                    }
                ],
                "next_action": "PREVIEW_AND_CONFIRM_ORDER_MANUALLY",
            }
            with patch.object(cli_module, "submit_authorized_order", return_value=guard_result):
                result = facade.execute(
                    "submit-auto",
                    {
                        "profile": profile.name,
                        "strategy_id": strategy["strategy_id"],
                        "authorization_id": authorization["authorization_id"],
                        "idempotency_key": "facade-submit-fallback",
                        "operation_key": "spot.order.place_order",
                        "orders": [order],
                    },
                    confirm_live=True,
                )

            self.assertEqual(result["status"], "MANUAL_CONFIRMATION_REQUIRED")
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0]["intent_type"], "order")
            self.assertEqual(captured[0]["strategy_id"], strategy["strategy_id"])
            self.assertEqual(captured[0]["authorization_id"], authorization["authorization_id"])
            self.assertEqual(captured[0]["idempotency_key"], "facade-submit-fallback")
            self.assertEqual(captured[0]["auto_fallback_operation_key"], "spot.order.place_order")
            self.assertEqual(captured[0]["auto_fallback_orders"], [order])
            self.assertEqual(result["intent_id"], captured[0]["intent_id"])
            self.assertEqual(result["order_preview"]["orders"], [order])
            self.assertEqual(result["user_confirmation"]["reply_text"], "确认")
            self.assertIn("本次订单超过自动交易授权范围，尚未下单", result["user_confirmation"]["reply_instruction"])
            self.assertIn("确认后回复：确认", result["user_confirmation"]["reply_instruction"])
            self.assertIn("申请自动交易授权", result["authorization_hint"])
            fallback_event = next(
                event
                for event in state.list_events(strategy_id=strategy["strategy_id"])
                if event["event_type"] == "AUTO_TRADE_MANUAL_FALLBACK"
            )
            self.assertEqual(fallback_event["severity"], "EXCEPTION")
            self.assertEqual(fallback_event["payload"]["error_code"], "SINGLE_LIMIT_EXCEEDED")

    def test_submit_auto_unknown_operation_does_not_create_unusable_confirmation_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state, cli_module, profile, strategy, authorization = self._authorized_fixture(Path(tempdir))
            writer = Mock()
            runtime = SimpleNamespace(
                risk_payload_provider=Mock(), risk_evaluator=Mock(), facts_provider=Mock(), submitter=Mock()
            )
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
                auto_trade_runtime_factory=lambda resolved: runtime,
                manual_intent_writer=writer,
            )

            result = facade.execute(
                "submit-auto",
                {
                    "profile": profile.name,
                    "strategy_id": strategy["strategy_id"],
                    "authorization_id": authorization["authorization_id"],
                    "idempotency_key": "facade-unknown-fallback",
                    "operation_key": "transaction.unknown_order",
                    "orders": [{"symbol": "BTCUSDT"}],
                },
                confirm_live=True,
            )

            self.assertEqual(result["status"], "MANUAL_CONFIRMATION_REQUIRED")
            self.assertNotIn("intent_id", result)
            writer.assert_not_called()

    def test_show_authorization_request_is_state_aware(self) -> None:
        state_module = load_state_module()
        cli_module = load_cli_module()
        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            profile = SimpleNamespace(profile_id="profile-1", name="strategy-live")
            facade = cli_module.AutoTradeFacade(state, profile_resolver=lambda name: profile)
            strategy = state.register_strategy(
                profile_id=profile.profile_id, strategy_name="request-display", distribution="official"
            )
            scope = {
                "trade_types": ["SPOT"],
                "symbols": ["BTCUSDT"],
                "all_symbols": False,
                "max_single_amount": "20",
                "max_total_amount": "100",
                "valid_hours": "24",
            }
            request = state.ensure_authorization(strategy_id=strategy["strategy_id"], scope=scope)
            payload = {
                "profile": profile.name,
                "strategy_id": strategy["strategy_id"],
                "request_id": request["request_id"],
            }

            pending = facade.execute("show-authorization-request", payload)
            self.assertEqual(pending["status"], "PENDING")
            self.assertEqual(pending["next_action"], "CONFIRM_OR_REJECT_AUTHORIZATION")
            self.assertIn("projected_expires_at_if_granted_now", pending["confirmation"])

            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
            )
            granted = facade.execute("show-authorization-request", payload)
            self.assertEqual(granted["status"], "GRANTED")
            self.assertEqual(granted["next_action"], "SUBMIT_ALLOWED")
            self.assertNotIn("projected_expires_at_if_granted_now", granted["confirmation"])
            self.assertEqual(
                granted["confirmation"]["authorization_starts_at"], authorization["starts_at"]
            )
            self.assertEqual(
                granted["confirmation"]["authorization_expires_at"], authorization["expires_at"]
            )

            stale = state.ensure_authorization(
                strategy_id=strategy["strategy_id"], scope={**scope, "max_total_amount": "80"}
            )
            state.ensure_authorization(
                strategy_id=strategy["strategy_id"], scope={**scope, "max_total_amount": "60"}
            )
            rejected = facade.execute(
                "show-authorization-request", {**payload, "request_id": stale["request_id"]}
            )
            self.assertEqual(rejected["status"], "REJECTED")
            self.assertEqual(rejected["next_action"], "REQUEST_NEW_AUTHORIZATION")
            self.assertNotIn("projected_expires_at_if_granted_now", rejected["confirmation"])

            expired_strategy = state.register_strategy(
                profile_id=profile.profile_id, strategy_name="expired-request", distribution="official"
            )
            expired_request = state.ensure_authorization(
                strategy_id=expired_strategy["strategy_id"],
                scope=scope,
                now=datetime.now(UTC) - timedelta(hours=1),
                request_ttl_seconds=1,
            )
            expired = facade.execute(
                "show-authorization-request",
                {
                    "profile": profile.name,
                    "strategy_id": expired_strategy["strategy_id"],
                    "request_id": expired_request["request_id"],
                },
            )
            self.assertEqual(expired["status"], "EXPIRED")
            self.assertEqual(expired["next_action"], "REQUEST_NEW_AUTHORIZATION")
            self.assertNotIn("projected_expires_at_if_granted_now", expired["confirmation"])

    def test_submit_auto_post_submit_state_failure_never_creates_manual_retry_intent(self) -> None:
        import weex_auto_trade_state as runtime_state_module

        with tempfile.TemporaryDirectory() as tempdir:
            state, cli_module, profile, strategy, authorization = self._authorized_fixture(
                Path(tempdir)
            )
            now_ms = int(datetime.now(UTC).timestamp() * 1000)
            captured: list[dict] = []
            runtime = SimpleNamespace(
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": complete_spot_account_snapshot(),
                },
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": now_ms,
                    "symbol": complete_spot_symbol_facts(),
                },
                submitter=lambda operation_key, legs: [
                    {
                        "clientOrderId": legs[0]["client_order_id"],
                        "success": True,
                        "orderId": "weex-state-failure",
                    }
                ],
            )
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
                auto_trade_runtime_factory=lambda resolved: runtime,
                manual_intent_writer=captured.append,
            )
            with patch.object(
                state,
                "settle_usage",
                side_effect=runtime_state_module.StateConflictError("injected"),
            ):
                result = facade.execute(
                    "submit-auto",
                    {
                        "profile": profile.name,
                        "strategy_id": strategy["strategy_id"],
                        "authorization_id": authorization["authorization_id"],
                        "idempotency_key": "post-submit-state-failure",
                        "operation_key": "spot.order.place_order",
                        "orders": [
                            {
                                "symbol": "BTCUSDT",
                                "side": "BUY",
                                "type": "LIMIT",
                                "timeInForce": "GTC",
                                "quantity": "1",
                                "price": "10",
                            }
                        ],
                    },
                    confirm_live=True,
                )

            self.assertEqual(result["status"], "REVIEW_REQUIRED")
            self.assertEqual(result["error"]["code"], "SUBMISSION_STATE_UNCERTAIN")
            self.assertEqual(captured, [])
            uncertain_events = [
                event
                for event in state.list_events(strategy_id=strategy["strategy_id"])
                if event["event_type"] == "SUBMISSION_STATE_UNCERTAIN"
            ]
            self.assertEqual(len(uncertain_events), 1)
            uncertain_event = uncertain_events[0]
            self.assertEqual(uncertain_event["severity"], "EXCEPTION")
            self.assertEqual(
                uncertain_event["payload"]["next_action"],
                "INSPECT_AND_RECONCILE_MANUALLY",
            )
            self.assertNotIn(
                "post-submit-state-failure",
                json.dumps(uncertain_event["payload"], ensure_ascii=True),
            )

    def test_submit_auto_engages_kill_switch_when_uncertainty_event_cannot_be_written(self) -> None:
        import weex_auto_trade_state as runtime_state_module

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            state, cli_module, profile, strategy, authorization = self._authorized_fixture(root)
            now_ms = int(datetime.now(UTC).timestamp() * 1000)
            runtime = SimpleNamespace(
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": complete_spot_account_snapshot(),
                },
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": now_ms,
                    "symbol": complete_spot_symbol_facts(),
                },
                submitter=lambda operation_key, legs: [
                    {
                        "clientOrderId": legs[0]["client_order_id"],
                        "success": True,
                        "orderId": "weex-state-failure",
                    }
                ],
            )
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
                auto_trade_runtime_factory=lambda resolved: runtime,
                manual_intent_writer=Mock(),
            )
            with patch.object(
                state,
                "settle_usage",
                side_effect=runtime_state_module.StateConflictError("injected"),
            ), patch.object(
                state,
                "record_submission_state_uncertain",
                side_effect=runtime_state_module.StateConflictError("injected audit failure"),
            ):
                result = facade.execute(
                    "submit-auto",
                    {
                        "profile": profile.name,
                        "strategy_id": strategy["strategy_id"],
                        "authorization_id": authorization["authorization_id"],
                        "idempotency_key": "unrecorded-uncertainty",
                        "operation_key": "spot.order.place_order",
                        "orders": [
                            {
                                "symbol": "BTCUSDT",
                                "side": "BUY",
                                "type": "LIMIT",
                                "timeInForce": "GTC",
                                "quantity": "1",
                                "price": "10",
                            }
                        ],
                    },
                    confirm_live=True,
                )

            self.assertEqual(result["status"], "REVIEW_REQUIRED")
            kill_switch_path = state._kill_switch_path()
            self.assertTrue(kill_switch_path.exists())
            self.assertEqual(
                json.loads(kill_switch_path.read_text(encoding="utf-8"))["reason"],
                "SUBMISSION_STATE_UNCERTAIN",
            )

    def test_reconcile_auto_order_accepts_only_official_provider_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state, cli_module, profile, strategy, authorization = self._authorized_fixture(
                Path(tempdir)
            )
            usage = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="reconcile-boundary",
                estimated_amount_u="10",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="TEST",
            )
            order = state.record_order(
                usage_id=usage["usage_id"],
                weex_order_id="weex-reconcile-1",
                side="BUY",
                order_type="LIMIT",
                quantity="1",
                price="10",
            )
            state.settle_usage(usage_id=usage["usage_id"], outcome="ACCEPTED")
            later_usage = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="reconcile-later-order",
                estimated_amount_u="7",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="TEST",
            )
            state.record_order(
                usage_id=later_usage["usage_id"],
                weex_order_id="weex-reconcile-2",
                side="BUY",
                order_type="LIMIT",
                quantity="0.7",
                price="10",
            )
            state.settle_usage(usage_id=later_usage["usage_id"], outcome="ACCEPTED")
            provider = Mock(
                return_value={
                    "reconciliation_status": "COMPLETE",
                    "exchange_status": "FILLED",
                    "executed_quantity": "1",
                    "executed_quote_amount": "10",
                    "fee_amount": "0.01",
                    "fee_asset": "USDT",
                    "reconciliation_source": "WEEX_SPOT_ORDER_AND_TRADES",
                }
            )
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
                reconciliation_provider=provider,
            )

            result = facade.execute(
                "reconcile-auto-order",
                {
                    "profile": profile.name,
                    "strategy_id": strategy["strategy_id"],
                    "auto_trade_order_id": order["auto_trade_order_id"],
                },
            )

            self.assertEqual(result["reconciliation_status"], "COMPLETE")
            self.assertEqual(result["estimated_amount_u"], "10")
            self.assertEqual(
                result["authorization_quota"],
                {
                    "consumed_amount_u": "17",
                    "reserved_amount_u": "0",
                    "remaining_amount_u": "983",
                },
            )
            self.assertNotIn("accepted_amount_u", result)
            self.assertNotIn("reserved_amount_u", result)
            self.assertNotIn("remaining_amount_u", result)
            provider.assert_called_once()
            self.assertEqual(provider.call_args.args[0]["weex_order_id"], "weex-reconcile-1")

    def test_post_commit_notification_failure_does_not_change_authorization_request(self) -> None:
        state_module = load_state_module()
        cli_module = load_cli_module()
        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            profile = SimpleNamespace(profile_id="profile-1", name="strategy-live")
            adapter = Mock(side_effect=RuntimeError("notification unavailable"))
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
                notification_adapter=adapter,
            )
            strategy = facade.execute(
                "register-strategy",
                {"profile": profile.name, "strategy_name": "notify-failure"},
            )
            result = facade.execute(
                "ensure-authorization",
                {
                    "profile": profile.name,
                    "strategy_id": strategy["strategy_id"],
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "10",
                    "max_total_amount": "100",
                    "valid_hours": "24",
                },
            )

            self.assertEqual(result["status"], "AUTHORIZATION_REQUIRED")
            self.assertEqual(
                state.get_authorization_request(
                    strategy_id=strategy["strategy_id"],
                    request_id=result["request_id"],
                )["request_status"],
                "PENDING",
            )
            self.assertGreaterEqual(adapter.call_count, 1)
            self.assertTrue(
                any(
                    event["event_type"] == "NOTIFICATION_FAILED"
                    for event in state.list_events(strategy_id=strategy["strategy_id"])
                )
            )

    def test_recovery_commands_resolve_uncertain_usage_and_expose_enable_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state, cli_module, profile, strategy, authorization = self._authorized_fixture(
                Path(tempdir)
            )
            usage = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="manual-recovery",
                estimated_amount_u="10",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="TEST",
            )
            state.record_order(
                usage_id=usage["usage_id"],
                weex_order_id=None,
                side="BUY",
                order_type="LIMIT",
                quantity="1",
                price="10",
            )
            state.settle_usage(usage_id=usage["usage_id"], outcome="REVIEW_REQUIRED")
            def provider(order, _profile):
                return {
                    "outcome": "RELEASED",
                    "evidence_source": "WEEX_SPOT_ORDER_NOT_FOUND",
                    "weex_order_id": None,
                    "usage_id": order["usage_id"],
                    "strategy_id": order["strategy_id"],
                    "authorization_id": order["authorization_id"],
                    "submission_group_id": order["submission_group_id"],
                    "leg_id": order["leg_id"],
                    "client_order_id": order["client_order_id"],
                    "module": order["module"],
                    "symbol": order["symbol"],
                    "side": order["side"],
                    "order_type": order["order_type"],
                    "quantity": order["quantity"],
                    "price": order["price"],
                }
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
                usage_resolution_provider=provider,
            )

            resolved = facade.execute(
                "resolve-auto-usage",
                {
                    "profile": profile.name,
                    "strategy_id": strategy["strategy_id"],
                    "usage_id": usage["usage_id"],
                },
                confirm_live=True,
            )
            self.assertEqual(resolved["status"], "RELEASED")
            enabled = facade.execute(
                "enable-auto-trading-after-restore",
                {"profile": profile.name},
                confirm_live=True,
            )
            self.assertEqual(enabled["status"], "AUTOMATIC_TRADING_ALREADY_ENABLED")

    def test_resolve_auto_usage_rejects_caller_supplied_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state, cli_module, profile, strategy, _authorization = self._authorized_fixture(
                Path(tempdir)
            )
            usage = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=_authorization["authorization_id"],
                idempotency_key="forged-evidence",
                estimated_amount_u="10",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="TEST",
            )
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
            )
            with self.assertRaisesRegex(cli_module.FacadeError, "unknown field"):
                facade.execute(
                    "resolve-auto-usage",
                    {
                        "profile": profile.name,
                        "strategy_id": strategy["strategy_id"],
                        "usage_id": usage["usage_id"],
                        "outcome": "RELEASED",
                        "evidence_source": "attacker-controlled",
                    },
                    confirm_live=True,
                )
            self.assertEqual(state.get_usage(usage_id=usage["usage_id"])["status"], "RESERVED")

    def test_verified_usage_evidence_rejects_cross_module_source(self) -> None:
        state_module = load_state_module()
        with self.assertRaisesRegex(ValueError, "UNTRUSTED_USAGE_EVIDENCE"):
            state_module.build_verified_usage_evidence(
                {
                    "outcome": "RELEASED",
                    "evidence_source": "WEEX_FUTURES_ORDER_NOT_FOUND",
                    "weex_order_id": None,
                    "usage_id": "use-cross-module",
                    "strategy_id": "strategy-1",
                    "authorization_id": "auth-1",
                    "submission_group_id": "group-1",
                    "leg_id": "leg-1",
                    "client_order_id": "client-cross-module",
                    "module": "SPOT",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "order_type": "LIMIT",
                    "quantity": "1",
                    "price": "10",
                }
            )

    def test_resolve_auto_usage_keeps_review_when_official_query_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state, cli_module, profile, strategy, authorization = self._authorized_fixture(
                Path(tempdir)
            )
            usage = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="query-timeout",
                estimated_amount_u="10",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="TEST",
            )
            state.record_order(
                usage_id=usage["usage_id"],
                weex_order_id=None,
                side="BUY",
                order_type="LIMIT",
                quantity="1",
                price="10",
            )
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
                usage_resolution_provider=Mock(side_effect=TimeoutError("WEEX timeout")),
            )
            with self.assertRaisesRegex(cli_module.FacadeError, "official order query"):
                facade.execute(
                    "resolve-auto-usage",
                    {
                        "profile": profile.name,
                        "strategy_id": strategy["strategy_id"],
                        "usage_id": usage["usage_id"],
                    },
                    confirm_live=True,
                )
            self.assertEqual(state.get_usage(usage_id=usage["usage_id"])["status"], "REVIEW_REQUIRED")

    def test_resolve_auto_usage_rejects_release_when_order_mapping_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state, cli_module, profile, strategy, authorization = self._authorized_fixture(
                Path(tempdir)
            )
            usage = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="mapped-order-release",
                estimated_amount_u="10",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="TEST",
            )
            order = state.record_order(
                usage_id=usage["usage_id"],
                weex_order_id="weex-known-order",
                side="BUY",
                order_type="LIMIT",
                quantity="1",
                price="10",
            )
            state.settle_usage(usage_id=usage["usage_id"], outcome="REVIEW_REQUIRED")
            facts = {
                "outcome": "RELEASED",
                "evidence_source": "WEEX_SPOT_ORDER_NOT_FOUND",
                "weex_order_id": None,
                **{key: order[key] for key in (
                    "usage_id", "strategy_id", "authorization_id", "submission_group_id",
                    "leg_id", "client_order_id", "module", "symbol", "side", "order_type",
                    "quantity", "price",
                )},
            }
            facade = cli_module.AutoTradeFacade(
                state,
                profile_resolver=lambda name: profile,
                usage_resolution_provider=Mock(return_value=facts),
            )
            with self.assertRaisesRegex(cli_module.FacadeError, "ORDER_MAPPING_CONFLICT"):
                facade.execute(
                    "resolve-auto-usage",
                    {
                        "profile": profile.name,
                        "strategy_id": strategy["strategy_id"],
                        "usage_id": usage["usage_id"],
                    },
                    confirm_live=True,
                )
            self.assertEqual(state.get_usage(usage_id=usage["usage_id"])["status"], "REVIEW_REQUIRED")


class AutoTradeOfficialRuntimeTests(unittest.TestCase):
    class FakeApi:
        def __init__(self, responses: dict[tuple[str, str], object]) -> None:
            self.responses = responses
            self.calls: list[dict] = []

        def call(
            self,
            *,
            module: str,
            endpoint_key: str,
            query: dict | None = None,
            body: dict | None = None,
            public: bool = False,
            mutating: bool = False,
        ):
            self.calls.append(
                {
                    "module": module,
                    "endpoint_key": endpoint_key,
                    "query": query or {},
                    "body": body or {},
                    "public": public,
                    "mutating": mutating,
                }
            )
            result = self.responses[(module, endpoint_key)]
            if isinstance(result, Exception):
                raise result
            return result

    def test_official_boundary_distinguishes_explicit_http_rejection_from_uncertainty(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        endpoint = SimpleNamespace(mutating=True)
        api_module = SimpleNamespace(
            ENDPOINTS={
                "transaction.place_order": endpoint,
                "transaction.place_orders_batch": endpoint,
            },
            validate_endpoint_trading_mode=lambda selected, mode: None,
        )

        class FakeClient:
            def __init__(self, response):
                self.response = response

            def prepare_request(self, selected, *, query, body):
                return {"query": query, "body": body}

            def send(self, prepared):
                return self.response

        boundary = runtime_module.OfficialApiBoundary(profile_name="strategy-live")
        boundary._client = lambda module, private: (
            api_module,
            FakeClient(
                {
                    "ok": False,
                    "status": 400,
                    "error": {"code": "40012", "msg": "batch order rejected"},
                }
            ),
        )
        with self.assertRaises(RuntimeError) as rejected:
            boundary.call(
                module="FUTURES",
                endpoint_key="transaction.place_order",
                body={"symbol": "BTCUSDT"},
                mutating=True,
            )
        self.assertEqual(type(rejected.exception).__name__, "OfficialRequestRejected")
        self.assertEqual(rejected.exception.error_code, "40012")
        self.assertEqual(rejected.exception.error_message, "batch order rejected")

        runtime = runtime_module.OfficialAutoTradeRuntime(
            profile_name="strategy-live",
            api=boundary,
            risk_aggregator=Mock(),
        )
        released = runtime.submitter(
            "transaction.place_orders_batch",
            [
                {
                    "leg_id": "leg-0",
                    "client_order_id": "client-0",
                    "order": {
                        "symbol": "BTCUSDT",
                        "newClientOrderId": "client-0",
                    },
                },
                {
                    "leg_id": "leg-1",
                    "client_order_id": "client-1",
                    "order": {
                        "symbol": "BTCUSDT",
                        "newClientOrderId": "client-1",
                    },
                },
            ],
        )
        self.assertEqual([item["status"] for item in released], ["RELEASED", "RELEASED"])
        self.assertEqual(
            [item["client_order_id"] for item in released],
            ["client-0", "client-1"],
        )
        self.assertTrue(all(item["errorCode"] == "40012" for item in released))
        self.assertTrue(
            all(item["errorMessage"] == "batch order rejected" for item in released)
        )

        boundary._client = lambda module, private: (
            api_module,
            FakeClient(
                {
                    "ok": False,
                    "status": None,
                    "error": {"message": "connection reset"},
                }
            ),
        )
        with self.assertRaises(RuntimeError) as uncertain:
            boundary.call(
                module="FUTURES",
                endpoint_key="transaction.place_order",
                body={"symbol": "BTCUSDT"},
                mutating=True,
            )
        self.assertEqual(type(uncertain.exception).__name__, "OfficialRequestUncertain")

    def test_official_boundary_preserves_read_error_code_for_spot_history_fallback(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        endpoint = SimpleNamespace(mutating=False)
        api_module = SimpleNamespace(
            ENDPOINTS={"spot.order.order_details": endpoint},
            is_mutating=lambda selected: False,
        )

        class FakeClient:
            def prepare_request(self, selected, *, query, body):
                return {"query": query, "body": body}

            def send(self, prepared):
                return {
                    "ok": False,
                    "status": 400,
                    "error": {"code": -2200, "message": "Order does not exist"},
                }

        boundary = runtime_module.OfficialApiBoundary(profile_name="strategy-live")
        boundary._client = lambda module, private: (api_module, FakeClient())

        with self.assertRaises(runtime_module.OfficialReadRequestFailed) as failure:
            boundary.call(
                module="SPOT",
                endpoint_key="spot.order.order_details",
                query={"orderId": "s-archived"},
            )

        self.assertEqual(failure.exception.error_code, "-2200")

    def test_runtime_builds_spot_facts_and_canonical_batch_envelope(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        api = self.FakeApi(
            {
                ("SPOT", "spot.config.get_product_info"): {
                    "serverTime": 1_700_000_000_000,
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "status": "TRADING",
                            "quoteAsset": "USDT",
                            "makerFeeRate": 0.001,
                            "takerFeeRate": 0.002,
                        }
                    ],
                },
                ("SPOT", "spot.order.bulk_order"): {
                    "orderList": [
                        {"clientOrderId": "client-a", "orderId": "order-a"},
                        {"clientOrderId": "client-b", "orderId": "order-b"},
                    ]
                },
            }
        )
        runtime = runtime_module.OfficialAutoTradeRuntime(
            profile_name="strategy-live",
            api=api,
            risk_aggregator=Mock(),
        )
        facts = runtime.facts_provider(
            {
                "module": "SPOT",
                "order": {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "type": "LIMIT",
                    "quantity": "1",
                    "price": "10",
                },
            }
        )
        self.assertEqual(facts["symbol"]["quoteAsset"], "USDT")
        self.assertEqual(facts["symbol"]["makerFeeRate"], "0.001")
        self.assertEqual(facts["symbol"]["takerFeeRate"], "0.002")
        self.assertIsInstance(facts["timestamp_ms"], int)

        results = runtime.submitter(
            "spot.order.bulk_order",
            [
                {
                    "leg_id": "leg-0",
                    "client_order_id": "client-a",
                    "order": {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "type": "LIMIT",
                        "timeInForce": "GTC",
                        "quantity": "1",
                        "price": "10",
                        "newClientOrderId": "client-a",
                    },
                },
                {
                    "leg_id": "leg-1",
                    "client_order_id": "client-b",
                    "order": {
                        "symbol": "BTCUSDT",
                        "side": "SELL",
                        "type": "LIMIT",
                        "timeInForce": "GTC",
                        "quantity": "1",
                        "price": "11",
                        "newClientOrderId": "client-b",
                    },
                },
            ],
        )
        submit_call = api.calls[-1]
        self.assertTrue(submit_call["mutating"])
        self.assertEqual(submit_call["body"]["symbol"], "BTCUSDT")
        self.assertNotIn("symbol", submit_call["body"]["orderList"][0])
        self.assertEqual([item["client_order_id"] for item in results], ["client-a", "client-b"])

    def test_runtime_classifies_spot_tp_sl_capability_gap_as_advisory_context(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        aggregator = Mock()
        aggregator.collect_order_risk_payload.return_value = {
            "partial": False,
            "degraded_reasons": ["spot_tp_sl_state_unavailable"],
            "constraints": [],
            "tp_sl": {"has_take_profit": False, "has_stop_loss": False},
        }
        runtime = runtime_module.OfficialAutoTradeRuntime(
            profile_name="strategy-live",
            api=Mock(),
            risk_aggregator=aggregator,
        )
        payload = runtime.risk_payload_provider(
            {
                "module": "SPOT",
                "leg_type": "PRIMARY",
                "order": {"symbol": "BTCUSDT"},
            }
        )
        self.assertEqual(payload["degraded_reasons"], [])
        self.assertEqual(payload["capability_notes"], ["spot_tp_sl_state_unavailable"])
        self.assertFalse(payload["partial"])

    def test_runtime_proves_partial_tp_sl_against_the_matching_position(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        api = self.FakeApi(
            {
                ("FUTURES", "market.get_contract_info"): {
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "status": "TRADING",
                            "quoteAsset": "USDT",
                            "marginAsset": "USDT",
                            "contractVal": 0.001,
                            "makerFeeRate": "0.001",
                            "takerFeeRate": "0.002",
                        }
                    ]
                },
                ("FUTURES", "account.get_symbol_config"): {
                    "symbol": "BTCUSDT",
                    "marginType": "CROSSED",
                    "crossLeverage": "10",
                },
                ("FUTURES", "account.get_commission_rate"): {
                    "makerCommissionRate": "0.001",
                    "takerCommissionRate": "0.002",
                },
                ("FUTURES", "account.get_all_positions"): [
                    {"symbol": "BTCUSDT", "side": "LONG", "size": "2"}
                ],
            }
        )
        runtime = runtime_module.OfficialAutoTradeRuntime(
            profile_name="strategy-live",
            api=api,
            risk_aggregator=Mock(),
        )

        facts = runtime.facts_provider(
            {
                "module": "FUTURES",
                "leg_type": "TAKE_PROFIT",
                "order": {
                    "symbol": "BTCUSDT",
                    "planType": "TAKE_PROFIT",
                    "triggerPrice": "65000",
                    "executePrice": "60000",
                    "quantity": "1",
                    "positionSide": "LONG",
                },
            }
        )

        self.assertTrue(facts["reduce_only_proven"])
        self.assertEqual(facts["symbol"]["contractVal"], "0.001")
        self.assertEqual(api.calls[-1]["endpoint_key"], "account.get_all_positions")

    def test_official_reconciliation_uses_read_only_order_and_trade_queries(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        futures_api = self.FakeApi(
            {
                ("FUTURES", "transaction.get_single_order_info"): {
                    "orderId": "f-1",
                    "status": "FILLED",
                    "executedQty": "2",
                    "cumQuote": "20",
                },
                ("FUTURES", "transaction.get_trade_details"): [
                    {
                        "orderId": "f-1",
                        "commission": "0.01",
                        "commissionAsset": "USDT",
                        "qty": "1",
                        "quoteQty": "10",
                    },
                    {
                        "orderId": "f-1",
                        "commission": "0.02",
                        "commissionAsset": "USDT",
                        "qty": "1",
                        "quoteQty": "10",
                    },
                ],
            }
        )
        facts = runtime_module.query_official_order_facts(
            order={
                "module": "FUTURES",
                "symbol": "BTCUSDT",
                "weex_order_id": "f-1",
                "client_order_id": "client-f-1",
            },
            profile_name="strategy-live",
            api=futures_api,
        )
        self.assertEqual(facts["reconciliation_status"], "COMPLETE")
        self.assertEqual(facts["fee_amount"], "0.03")
        self.assertEqual(facts["fee_asset"], "USDT")
        self.assertTrue(all(not call["mutating"] for call in futures_api.calls))

        spot_api = self.FakeApi(
            {
                ("SPOT", "spot.order.order_details"): {
                    "orderId": "s-1",
                    "status": "FILLED",
                    "executedQty": "1",
                    "cummulativeQuoteQty": "10",
                },
                ("SPOT", "spot.order.transaction_details"): [
                    {"orderId": "s-1", "commission": "0.01"}
                ],
            }
        )
        partial = runtime_module.query_official_order_facts(
            order={
                "module": "SPOT",
                "symbol": "BTCUSDT",
                "weex_order_id": "s-1",
                "client_order_id": "client-s-1",
            },
            profile_name="strategy-live",
            api=spot_api,
        )
        self.assertEqual(partial["reconciliation_status"], "PARTIAL")
        self.assertIsNone(partial["fee_amount"])
        self.assertIsNone(partial["fee_asset"])

    def test_official_usage_resolution_binds_spot_order_and_client_identity(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        api = self.FakeApi(
            {
                ("SPOT", "spot.order.order_details"): {
                    "orderId": "s-resolution-1",
                    "clientOrderId": "client-resolution-1",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "type": "LIMIT",
                    "origQty": "1",
                    "price": "10",
                    "status": "NEW",
                }
            }
        )
        order = {
            "usage_id": "use-resolution-1",
            "strategy_id": "strategy-1",
            "authorization_id": "auth-1",
            "submission_group_id": "group-1",
            "leg_id": "leg-1",
            "client_order_id": "client-resolution-1",
            "weex_order_id": "s-resolution-1",
            "module": "SPOT",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": "1",
            "price": "10",
        }
        facts = runtime_module.query_official_usage_resolution(
            order=order,
            profile_name="strategy-live",
            api=api,
        )
        self.assertEqual(facts["outcome"], "ACCEPTED")
        self.assertEqual(facts["evidence_source"], "WEEX_SPOT_ORDER_ACCEPTED")
        self.assertEqual(facts["weex_order_id"], "s-resolution-1")
        self.assertEqual(api.calls[0]["query"], {"orderId": "s-resolution-1"})
        self.assertTrue(all(not call["mutating"] for call in api.calls))

    def test_official_usage_resolution_proves_spot_missing_client_order_only_after_history_scan(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        api = self.FakeApi(
            {
                ("SPOT", "spot.order.order_details"): runtime_module.OfficialReadRequestFailed(
                    error_code="-2200", error_message="Order does not exist"
                ),
                ("SPOT", "spot.order.history_orders"): [],
            }
        )
        order = {
            "usage_id": "use-resolution-2",
            "strategy_id": "strategy-1",
            "authorization_id": "auth-1",
            "submission_group_id": "group-1",
            "leg_id": "leg-1",
            "client_order_id": "client-resolution-2",
            "weex_order_id": None,
            "module": "SPOT",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": "1",
            "price": "10",
        }
        facts = runtime_module.query_official_usage_resolution(
            order=order,
            profile_name="strategy-live",
            api=api,
        )
        self.assertEqual(facts["outcome"], "RELEASED")
        self.assertEqual(facts["evidence_source"], "WEEX_SPOT_ORDER_NOT_FOUND")
        self.assertIsNone(facts["weex_order_id"])
        self.assertEqual(api.calls[1]["query"], {"symbol": "BTCUSDT", "limit": 1000, "page": 1})

    def test_official_usage_resolution_rejects_non_advancing_spot_history_pagination(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        repeated_page = [
            {
                "orderId": f"archived-{index}",
                "clientOrderId": f"other-client-{index}",
                "symbol": "BTCUSDT",
            }
            for index in range(1000)
        ]
        api = self.FakeApi(
            {
                ("SPOT", "spot.order.order_details"): runtime_module.OfficialReadRequestFailed(
                    error_code="-2200", error_message="Order does not exist"
                ),
                ("SPOT", "spot.order.history_orders"): repeated_page,
            }
        )
        with self.assertRaisesRegex(ValueError, "pagination did not advance"):
            runtime_module.query_official_usage_resolution(
                order={
                    "usage_id": "use-resolution-pagination",
                    "strategy_id": "strategy-1",
                    "authorization_id": "auth-1",
                    "submission_group_id": "group-1",
                    "leg_id": "leg-1",
                    "client_order_id": "client-resolution-pagination",
                    "weex_order_id": None,
                    "module": "SPOT",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "order_type": "LIMIT",
                    "quantity": "1",
                    "price": "10",
                },
                profile_name="strategy-live",
                api=api,
            )
        self.assertEqual(api.calls[-1]["query"], {"symbol": "BTCUSDT", "limit": 1000, "page": 2})

    def test_official_usage_resolution_keeps_futures_without_order_id_unresolved(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        with self.assertRaisesRegex(ValueError, "CLIENT_ID_QUERY_UNSUPPORTED"):
            runtime_module.query_official_usage_resolution(
                order={
                    "usage_id": "use-resolution-3",
                    "strategy_id": "strategy-1",
                    "authorization_id": "auth-1",
                    "submission_group_id": "group-1",
                    "leg_id": "leg-1",
                    "client_order_id": "client-resolution-3",
                    "weex_order_id": None,
                    "module": "FUTURES",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "order_type": "LIMIT",
                    "quantity": "1",
                    "price": "10",
                },
                profile_name="strategy-live",
                api=self.FakeApi({}),
            )

    def test_spot_reconciliation_falls_back_to_history_after_archived_order_error(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        api = self.FakeApi(
            {
                ("SPOT", "spot.order.order_details"): runtime_module.OfficialReadRequestFailed(
                    error_code="-2200",
                    error_message="Order does not exist",
                ),
                ("SPOT", "spot.order.history_orders"): [
                    {
                        "orderId": "s-archived",
                        "clientOrderId": "client-s-archived",
                        "status": "FILLED",
                        "executedQty": "1",
                        "cummulativeQuoteQty": "10",
                    }
                ],
                ("SPOT", "spot.order.transaction_details"): [
                    {
                        "orderId": "s-archived",
                        "qty": "1",
                        "quoteQty": "10",
                        "commission": "0.01",
                        "commissionAsset": "USDT",
                    }
                ],
            }
        )

        facts = runtime_module.query_official_order_facts(
            order={
                "module": "SPOT",
                "symbol": "BTCUSDT",
                "weex_order_id": "s-archived",
                "client_order_id": "client-s-archived",
            },
            profile_name="strategy-live",
            api=api,
        )

        self.assertEqual(facts["reconciliation_status"], "COMPLETE")
        self.assertEqual(facts["exchange_status"], "FILLED")
        self.assertEqual(facts["executed_quantity"], "1")
        self.assertEqual(facts["executed_quote_amount"], "10")
        self.assertEqual(facts["fee_amount"], "0.01")
        self.assertEqual(facts["fee_asset"], "USDT")
        self.assertEqual(
            facts["reconciliation_source"],
            "WEEX_SPOT_HISTORY_ORDER_AND_TRADES",
        )
        self.assertEqual(
            [call["endpoint_key"] for call in api.calls],
            [
                "spot.order.order_details",
                "spot.order.history_orders",
                "spot.order.transaction_details",
            ],
        )
        self.assertTrue(all(not call["mutating"] for call in api.calls))

    def test_spot_history_fallback_keeps_incomplete_fill_or_fee_evidence_partial(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        cases = {
            "incomplete_fill_coverage": [
                {
                    "orderId": "s-archived",
                    "qty": "0.5",
                    "quoteQty": "5",
                    "commission": "0.005",
                    "commissionAsset": "USDT",
                }
            ],
            "missing_fee_asset": [
                {
                    "orderId": "s-archived",
                    "qty": "1",
                    "quoteQty": "10",
                    "commission": "0.01",
                }
            ],
        }
        for case_name, trades in cases.items():
            with self.subTest(case=case_name):
                api = self.FakeApi(
                    {
                        (
                            "SPOT",
                            "spot.order.order_details",
                        ): runtime_module.OfficialReadRequestFailed(error_code="-2200"),
                        ("SPOT", "spot.order.history_orders"): [
                            {
                                "orderId": "s-archived",
                                "clientOrderId": "client-s-archived",
                                "status": "FILLED",
                                "executedQty": "1",
                                "cummulativeQuoteQty": "10",
                            }
                        ],
                        ("SPOT", "spot.order.transaction_details"): trades,
                    }
                )

                facts = runtime_module.query_official_order_facts(
                    order={
                        "module": "SPOT",
                        "symbol": "BTCUSDT",
                        "weex_order_id": "s-archived",
                        "client_order_id": "client-s-archived",
                    },
                    profile_name="strategy-live",
                    api=api,
                )

                self.assertEqual(facts["reconciliation_status"], "PARTIAL")
                self.assertIsNone(facts["fee_amount"])
                self.assertIsNone(facts["fee_asset"])

    def test_spot_history_fallback_requires_one_order_and_client_id_match(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        for history in (
            [],
            [
                {
                    "orderId": "s-archived",
                    "clientOrderId": "different-client-id",
                    "status": "FILLED",
                }
            ],
        ):
            with self.subTest(history=history):
                api = self.FakeApi(
                    {
                        (
                            "SPOT",
                            "spot.order.order_details",
                        ): runtime_module.OfficialReadRequestFailed(error_code="-2200"),
                        ("SPOT", "spot.order.history_orders"): history,
                    }
                )

                with self.assertRaisesRegex(ValueError, "no unique matching order"):
                    runtime_module.query_official_order_facts(
                        order={
                            "module": "SPOT",
                            "symbol": "BTCUSDT",
                            "weex_order_id": "s-archived",
                            "client_order_id": "client-s-archived",
                        },
                        profile_name="strategy-live",
                        api=api,
                    )
                self.assertTrue(all(not call["mutating"] for call in api.calls))

    def test_futures_plan_reconciliation_uses_plan_then_actual_order_endpoints(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        api = self.FakeApi(
            {
                ("FUTURES", "transaction.get_current_pending_orders"): [
                    {
                        "algoId": "plan-1",
                        "algoStatus": "FILLED",
                        "actualOrderId": "actual-1",
                    }
                ],
                ("FUTURES", "transaction.get_single_order_info"): {
                    "orderId": "actual-1",
                    "status": "FILLED",
                    "executedQty": "2",
                    "cumQuote": "20",
                },
                ("FUTURES", "transaction.get_trade_details"): [
                    {
                        "orderId": "actual-1",
                        "qty": "2",
                        "quoteQty": "20",
                        "commission": "0.03",
                        "commissionAsset": "USDT",
                    }
                ],
            }
        )

        facts = runtime_module.query_official_order_facts(
            order={
                "module": "FUTURES",
                "leg_type": "TAKE_PROFIT",
                "symbol": "BTCUSDT",
                "weex_order_id": "plan-1",
                "client_order_id": "client-plan-1",
            },
            profile_name="strategy-live",
            api=api,
        )

        self.assertEqual(facts["reconciliation_status"], "COMPLETE")
        self.assertEqual(facts["exchange_status"], "FILLED")
        self.assertEqual(facts["fee_amount"], "0.03")
        self.assertEqual(
            [call["endpoint_key"] for call in api.calls],
            [
                "transaction.get_current_pending_orders",
                "transaction.get_single_order_info",
                "transaction.get_trade_details",
            ],
        )
        self.assertTrue(all(not call["mutating"] for call in api.calls))

        pending_api = self.FakeApi(
            {
                ("FUTURES", "transaction.get_current_pending_orders"): [
                    {"algoId": "plan-2", "algoStatus": "UNTRIGGERED", "actualOrderId": 0}
                ]
            }
        )
        pending = runtime_module.query_official_order_facts(
            order={
                "module": "FUTURES",
                "leg_type": "CONDITIONAL",
                "symbol": "BTCUSDT",
                "weex_order_id": "plan-2",
                "client_order_id": "client-plan-2",
            },
            profile_name="strategy-live",
            api=pending_api,
        )
        self.assertEqual(pending["reconciliation_status"], "PARTIAL")
        self.assertEqual(pending["exchange_status"], "UNTRIGGERED")
        self.assertEqual(len(pending_api.calls), 1)

    def test_truncated_trade_rows_cannot_produce_complete_fee_facts(self) -> None:
        import weex_auto_trade_runtime as runtime_module

        trades = [
            {
                "orderId": "f-truncated",
                "qty": "0.01",
                "quoteQty": "0.1",
                "commission": "0.01",
                "commissionAsset": "USDT",
            }
            for _ in range(100)
        ]
        api = self.FakeApi(
            {
                ("FUTURES", "transaction.get_single_order_info"): {
                    "orderId": "f-truncated",
                    "status": "FILLED",
                    "executedQty": "2",
                    "cumQuote": "20",
                },
                ("FUTURES", "transaction.get_trade_details"): trades,
            }
        )

        facts = runtime_module.query_official_order_facts(
            order={
                "module": "FUTURES",
                "leg_type": "PRIMARY",
                "symbol": "BTCUSDT",
                "weex_order_id": "f-truncated",
            },
            profile_name="strategy-live",
            api=api,
        )

        self.assertEqual(facts["reconciliation_status"], "PARTIAL")
        self.assertIsNone(facts["fee_amount"])
        self.assertIsNone(facts["fee_asset"])


class AutoTradeNotificationAdapterTests(unittest.TestCase):
    def test_final_accepted_window_worker_delivers_without_later_facade_call(self) -> None:
        state_module = load_state_module()
        notify_module = load_notify_module()
        window_start = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)

        with tempfile.TemporaryDirectory() as tempdir:
            state_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(state_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1",
                strategy_name="final-window",
                distribution="official",
            )
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope={
                    "trade_types": ["SPOT"],
                    "symbols": ["BTCUSDT"],
                    "all_symbols": False,
                    "max_single_amount": "20",
                    "max_total_amount": "50",
                    "valid_hours": "24",
                },
                now=window_start,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=window_start,
            )
            usage = state.reserve_usage(
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="final-window-accepted",
                estimated_amount_u="19.9",
                module="SPOT",
                symbol="BTCUSDT",
                valuation_source="official-depth",
                now=window_start.replace(second=30),
            )
            state.settle_usage(
                usage_id=usage["usage_id"],
                outcome="ACCEPTED",
                now=window_start.replace(second=30),
            )
            owner_key = f"official:profile-1:live:{strategy['strategy_id']}"
            bucket = window_start.isoformat(timespec="microseconds").replace("+00:00", "Z")
            notification_key = "summary:" + hashlib.sha256(
                f"{owner_key}:{bucket}".encode("utf-8")
            ).hexdigest()
            target = state.accepted_summary_notification_target(
                strategy_id=strategy["strategy_id"]
            )
            self.assertEqual(target["notification_key"], notification_key)
            self.assertEqual(target["not_before"], window_start.replace(minute=1))

            class Clock:
                current = window_start.replace(second=31)

                def sleep(self, seconds):
                    self.current = datetime.fromtimestamp(
                        self.current.timestamp() + seconds,
                        tz=UTC,
                    )

            clock = Clock()
            delivered = []
            results = notify_module.run_notification_worker(
                state_path=state_path,
                notification_key=notification_key,
                not_before=window_start.replace(minute=1),
                adapter=delivered.append,
                now_provider=lambda: clock.current,
                sleep=clock.sleep,
            )

            self.assertEqual(len(delivered), 1)
            self.assertEqual(delivered[0]["kind"], "ACCEPTED_SUMMARY")
            self.assertEqual(delivered[0]["order_count"], 1)
            self.assertEqual(delivered[0]["estimated_amount_u"], "19.9")
            self.assertEqual(results[0]["status"], "DELIVERED")

    def test_dispatch_attempts_each_claim_once_and_records_adapter_failure(self) -> None:
        notify_module = load_notify_module()
        claim = {
            "kind": "EXCEPTION",
            "notification_key": "event:evt_fixture",
            "strategy_name": "grid-btc",
            "strategy_id": "str_***fixture",
            "event_type": "USAGE_REVIEW_REQUIRED",
            "event_id": "evt_fixture",
            "occurred_at": "2026-08-16T12:00:00Z",
            "next_action": "INSPECT_EVENT_AND_RECONCILE_MANUALLY",
        }

        class FakeState:
            def __init__(self):
                self.completed = []

            def claim_notifications(self, *, now=None):
                return [claim]

            def complete_notification(self, *, notification_key, outcome, now=None):
                self.completed.append((notification_key, outcome))
                return {"ok": True}

        attempts = []

        def failing_adapter(payload):
            attempts.append(payload["notification_key"])
            raise RuntimeError("injected notification failure")

        state = FakeState()
        results = notify_module.dispatch_notification_claims(state, failing_adapter)

        self.assertEqual(attempts, ["event:evt_fixture"])
        self.assertEqual(state.completed, [("event:evt_fixture", "FAILED")])
        self.assertEqual(results[0]["status"], "FAILED")
        self.assertNotIn("injected notification failure", json.dumps(results))

    def test_windows_notification_timeout_exceeds_the_command_display_duration(self) -> None:
        notify_module = load_notify_module()
        adapter = notify_module.SystemNotificationAdapter()
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch.object(notify_module.platform, "system", return_value="Windows"), patch.object(
            notify_module.shutil, "which", return_value="powershell.exe"
        ), patch.object(notify_module.subprocess, "run", return_value=completed) as run:
            adapter({"kind": "EXCEPTION", "strategy_name": "fixture", "event_type": "TEST"})
        self.assertGreater(run.call_args.kwargs["timeout"], 5.5)

    def test_notification_timeout_is_recorded_as_unknown(self) -> None:
        notify_module = load_notify_module()
        adapter = notify_module.SystemNotificationAdapter()
        claim = {
            "kind": "EXCEPTION",
            "notification_key": "event:evt_timeout",
            "strategy_name": "fixture",
            "event_type": "TEST",
        }

        class FakeState:
            def __init__(self):
                self.completed = []

            def claim_notifications(self, *, now=None):
                return [claim]

            def complete_notification(self, *, notification_key, outcome, now=None):
                self.completed.append((notification_key, outcome))

        with patch.object(notify_module.platform, "system", return_value="Darwin"), patch.object(
            notify_module.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd=["osascript"], timeout=7),
        ):
            state = FakeState()
            results = notify_module.dispatch_notification_claims(state, adapter)
        self.assertEqual(results[0]["status"], "UNKNOWN")
        self.assertEqual(state.completed, [("event:evt_timeout", "UNKNOWN")])


class AutoTradeGuardIntegrationTests(unittest.TestCase):
    def _authorized_state(
        self,
        state_module,
        root: Path,
        *,
        trade_types: list[str] | None = None,
        all_symbols: bool = False,
    ):
        state = state_module.AutoTradeState(root / "state" / "state.sqlite3")
        state.initialize()
        strategy = state.register_strategy(
            profile_id="profile-1", strategy_name="guard-regression", distribution="official"
        )
        now = datetime(2026, 8, 16, 21, 0, tzinfo=UTC)
        request = state.ensure_authorization(
            strategy_id=strategy["strategy_id"],
            scope=complete_scope(
                trade_types=trade_types or ["SPOT"],
                symbols=[] if all_symbols else ["BTCUSDT"],
                all_symbols=all_symbols,
            ),
            now=now,
        )
        authorization = state.grant_authorization(
            strategy_id=strategy["strategy_id"],
            request_id=request["request_id"],
            scope_signature=request["scope_signature"],
            confirm_live=True,
            now=now,
        )
        return state, strategy, authorization, now

    def test_spot_quantity_rules_block_before_reservation_or_submit(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state, strategy, authorization, now = self._authorized_state(
                state_module, Path(tempdir)
            )
            submitter = Mock()
            facts = {
                "timestamp_ms": 1_700_000_000_000,
                "depth": {
                    "timestamp_ms": 1_700_000_000_000,
                    "limit": 15,
                    "asks": [["65000", "2"]],
                    "bids": [["64900", "2"]],
                },
                "symbol": {
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "stepSize": "0.000001",
                    "minTradeAmount": "0.000001",
                    "maxTradeAmount": "1",
                    "makerFeeRate": "0",
                    "takerFeeRate": "0",
                },
            }

            for quantity, code in (
                ("0.0000015", "SPOT_QUANTITY_STEP_MISMATCH"),
                ("0.0000005", "SPOT_QUANTITY_BELOW_MINIMUM"),
                ("1.000001", "SPOT_QUANTITY_ABOVE_MAXIMUM"),
            ):
                with self.subTest(quantity=quantity):
                    result = trade_guard.submit_authorized_order(
                        state=state,
                        operation_key="spot.order.place_order",
                        strategy_id=strategy["strategy_id"],
                        authorization_id=authorization["authorization_id"],
                        idempotency_key=f"quantity-{quantity}",
                        orders=[
                            {
                                "symbol": "BTCUSDT",
                                "side": "BUY",
                                "type": "MARKET",
                                "quantity": quantity,
                            }
                        ],
                        risk_payload_provider=lambda leg: {
                            "partial": False,
                            "degraded_reasons": [],
                            "constraints": [],
                            "account_snapshot": {
                                "quote_asset": "USDT",
                                "quote_available_balance_u": "1000",
                            },
                        },
                        risk_evaluator=lambda payload: {"alerts": []},
                        facts_provider=lambda leg: facts,
                        submitter=submitter,
                        confirm_live=True,
                        now=now,
                        now_ms=1_700_000_000_000,
                    )
                    self.assertEqual(result["error"]["code"], code)

            missing_rules = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.place_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="quantity-rules-missing",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "type": "MARKET",
                        "quantity": "0.001",
                    }
                ],
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": {
                        "quote_asset": "USDT",
                        "quote_available_balance_u": "1000",
                    },
                },
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    key: value for key, value in facts.items() if key != "symbol"
                },
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )
            self.assertEqual(
                missing_rules["error"]["code"], "SPOT_PRODUCT_RULES_UNAVAILABLE"
            )

            submitter.assert_not_called()
            with closing(sqlite3.connect(state.db_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM authorization_usage").fetchone()[0],
                    0,
                )

    def test_spot_sell_checks_base_balance_while_buy_checks_quote_balance(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state, strategy, authorization, now = self._authorized_state(
                state_module, Path(tempdir)
            )
            submitter = Mock(
                return_value=[
                    {"leg_id": "leg-0", "status": "ACCEPTED", "weex_order_id": "spot-buy-1"}
                ]
            )
            facts = {
                "timestamp_ms": 1_700_000_000_000,
                "depth": {
                    "timestamp_ms": 1_700_000_000_000,
                    "limit": 15,
                    "asks": [["65000", "2"]],
                    "bids": [["64900", "2"]],
                },
                "symbol": {
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "stepSize": "0.000001",
                    "minTradeAmount": "0.000001",
                    "maxTradeAmount": "1",
                    "makerFeeRate": "0",
                    "takerFeeRate": "0",
                },
            }

            def submit(
                side: str,
                key: str,
                base_quantity: str | None,
                quote_u: str,
            ):
                account_snapshot = {
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                    "quote_available_balance_u": quote_u,
                }
                if base_quantity is not None:
                    account_snapshot["base_available_quantity"] = base_quantity
                return trade_guard.submit_authorized_order(
                    state=state,
                    operation_key="spot.order.place_order",
                    strategy_id=strategy["strategy_id"],
                    authorization_id=authorization["authorization_id"],
                    idempotency_key=key,
                    orders=[
                        {
                            "symbol": "BTCUSDT",
                            "side": side,
                            "type": "MARKET",
                            "quantity": "0.001",
                        }
                    ],
                    risk_payload_provider=lambda leg: {
                        "partial": False,
                        "degraded_reasons": [],
                        "constraints": [],
                        "account_snapshot": account_snapshot,
                    },
                    risk_evaluator=lambda payload: {"alerts": []},
                    facts_provider=lambda leg: facts,
                    submitter=submitter,
                    confirm_live=True,
                    now=now,
                    now_ms=1_700_000_000_000,
                )

            missing_base = submit("SELL", "sell-base-missing", None, "1000")
            self.assertEqual(
                missing_base["error"]["code"], "BASE_ASSET_BALANCE_UNAVAILABLE"
            )
            self.assertEqual(len(submitter.call_args_list), 0)

            sell = submit("SELL", "sell-no-base", "0.0005", "1000")
            self.assertEqual(sell["error"]["code"], "INSUFFICIENT_BASE_ASSET_BALANCE")
            self.assertEqual(len(submitter.call_args_list), 0)

            buy = submit("BUY", "buy-with-quote", "0", "1000")
            self.assertEqual(buy["status"], "ACCEPTED")
            self.assertEqual(len(submitter.call_args_list), 1)

    def test_advisory_allows_one_submit_while_blocking_and_unknown_operations_fall_back(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="guard", distribution="official"
            )
            now = datetime(2026, 8, 16, 13, 0, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(max_single_amount="50", max_total_amount="100"),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            self.assertTrue(
                hasattr(trade_guard, "submit_authorized_order"),
                "automatic authorization guard is not implemented",
            )
            submit_calls = []

            def facts_provider(leg):
                return {
                    "timestamp_ms": 1_700_000_000_000,
                    "symbol": complete_spot_symbol_facts(
                        maker_fee="0.001", taker_fee="0.002"
                    ),
                }

            def submitter(operation_key, prepared_legs):
                submit_calls.append((operation_key, prepared_legs))
                return [
                    {
                        "leg_id": prepared_legs[0]["leg_id"],
                        "status": "ACCEPTED",
                        "weex_order_id": "weex-auto-1",
                        "exchange_status": "NEW",
                    }
                ]

            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.place_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="guard-order-1",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "type": "LIMIT",
                        "timeInForce": "GTC",
                        "quantity": "1",
                        "price": "10",
                    }
                ],
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": complete_spot_account_snapshot(),
                    "generated_at": "2026-08-16T13:00:00Z",
                },
                risk_evaluator=lambda payload: {
                    "alerts": [{"type": "high_leverage_or_concentration", "level": "high"}],
                    "rule_version": "fixture-v1",
                },
                facts_provider=facts_provider,
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )
            self.assertEqual(len(submit_calls), 1)
            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["blocking_reasons"], [])
            self.assertEqual(len(result["advisory_alerts"]), 1)
            self.assertEqual(result["legs"][0]["estimated_amount_u"], "10.02")
            reserved_event = next(
                event
                for event in state.list_events(strategy_id=strategy["strategy_id"])
                if event["event_type"] == "USAGE_RESERVED"
            )
            self.assertEqual(reserved_event["payload"]["risk_rule_version"], "fixture-v1")
            self.assertEqual(
                reserved_event["payload"]["risk_input_timestamp"],
                "2026-08-16T13:00:00Z",
            )
            self.assertEqual(
                reserved_event["payload"]["advisory_alerts"],
                [{"type": "high_leverage_or_concentration", "level": "high"}],
            )

            blocked = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.place_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="guard-order-blocked",
                orders=[{"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "quantity": "1", "price": "10"}],
                risk_payload_provider=lambda leg: {"degraded_reasons": ["depth_unavailable"]},
                risk_evaluator=lambda payload: {"alerts": [{"type": "missing_tp_sl", "level": "high"}]},
                facts_provider=facts_provider,
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )
            self.assertEqual(blocked["status"], "MANUAL_CONFIRMATION_REQUIRED")
            self.assertTrue(blocked["blocking_reasons"])

            unknown = trade_guard.submit_authorized_order(
                state=state,
                operation_key="transaction.future_unknown_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="guard-order-unknown",
                orders=[{"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "quantity": "1", "price": "10"}],
                risk_payload_provider=lambda leg: {},
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=facts_provider,
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )
            self.assertEqual(unknown["status"], "MANUAL_CONFIRMATION_REQUIRED")
            self.assertEqual(len(submit_calls), 1)

    def test_spot_equity_estimate_partial_is_advisory_when_quote_balance_is_available(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state, strategy, authorization, now = self._authorized_state(
                state_module, Path(tempdir)
            )
            submitter = Mock(
                return_value=[
                    {
                        "leg_id": "leg-0",
                        "status": "ACCEPTED",
                        "weex_order_id": "spot-partial-equity-1",
                    }
                ]
            )
            risk_payload = {
                "partial": False,
                "degraded_reasons": ["spot_equity_estimate_partial"],
                "constraints": [],
                "account_snapshot": {
                    **complete_spot_account_snapshot(quote_available_u="50"),
                    "equity": "50",
                },
                "generated_at": "2026-08-18T02:30:00Z",
            }

            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.place_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="spot-partial-equity",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "type": "LIMIT",
                        "timeInForce": "GTC",
                        "quantity": "1",
                        "price": "10",
                    }
                ],
                risk_payload_provider=lambda leg: risk_payload,
                risk_evaluator=lambda payload: {
                    "partial": False,
                    "degraded_reasons": ["spot_equity_estimate_partial"],
                    "alerts": [],
                    "rule_version": "fixture-v1",
                },
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "symbol": complete_spot_symbol_facts(
                        maker_fee="0.001", taker_fee="0.002"
                    ),
                },
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )

            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["blocking_reasons"], [])
            submitter.assert_called_once()

    def test_spot_order_blocks_before_submit_when_quote_balance_is_insufficient(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state, strategy, authorization, now = self._authorized_state(
                state_module, Path(tempdir)
            )
            submitter = Mock()

            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.place_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="spot-insufficient-balance",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "type": "LIMIT",
                        "timeInForce": "GTC",
                        "quantity": "1",
                        "price": "10",
                    }
                ],
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": {
                        **complete_spot_account_snapshot(quote_available_u="10"),
                        "equity": "10",
                    },
                },
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "symbol": complete_spot_symbol_facts(
                        maker_fee="0.001", taker_fee="0.002"
                    ),
                },
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )

            self.assertEqual(result["status"], "MANUAL_CONFIRMATION_REQUIRED")
            self.assertEqual(result["error"]["code"], "INSUFFICIENT_AVAILABLE_BALANCE")
            self.assertEqual(
                result["blocking_reasons"],
                [
                    {
                        "code": "INSUFFICIENT_AVAILABLE_BALANCE",
                        "message": "spot quote available balance is below the conservative order amount",
                    }
                ],
            )
            submitter.assert_not_called()

    def test_blocking_risk_reasons_are_deduplicated_by_code_and_message(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state, strategy, authorization, now = self._authorized_state(
                state_module, Path(tempdir)
            )
            facts_provider = Mock()
            submitter = Mock()

            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.place_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="spot-deduped-risk",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "type": "LIMIT",
                        "timeInForce": "GTC",
                        "quantity": "1",
                        "price": "10",
                    }
                ],
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": ["depth_unavailable"],
                    "constraints": [],
                },
                risk_evaluator=lambda payload: {
                    "partial": False,
                    "degraded_reasons": ["depth_unavailable"],
                    "alerts": [],
                },
                facts_provider=facts_provider,
                submitter=submitter,
                confirm_live=True,
                now=now,
            )

            self.assertEqual(
                result["blocking_reasons"],
                [
                    {
                        "code": "RISK_DATA_DEGRADED",
                        "message": "depth_unavailable",
                    }
                ],
            )
            facts_provider.assert_not_called()
            submitter.assert_not_called()

    def test_batch_quota_precheck_is_atomic_before_any_submit(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="batch", distribution="official"
            )
            now = datetime(2026, 8, 16, 13, 30, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(max_single_amount="60", max_total_amount="100"),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            submitter = Mock()
            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.bulk_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="batch-over-limit",
                orders=[
                    {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": "1", "price": "60"},
                    {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": "1", "price": "60"},
                ],
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": complete_spot_account_snapshot(),
                },
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "symbol": complete_spot_symbol_facts(),
                },
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )

            self.assertEqual(result["status"], "MANUAL_CONFIRMATION_REQUIRED")
            self.assertEqual(result["error"]["code"], "TOTAL_LIMIT_EXCEEDED")
            submitter.assert_not_called()
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM authorization_usage").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM auto_trade_orders").fetchone()[0],
                    0,
                )

    def test_batch_results_map_by_client_order_id_without_retry_or_positional_guessing(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="batch-results", distribution="official"
            )
            now = datetime(2026, 8, 16, 13, 45, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(max_single_amount="20", max_total_amount="100"),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            submit_calls = []

            def submitter(operation_key, prepared_legs):
                submit_calls.append((operation_key, prepared_legs))
                return [
                    {
                        "clientOrderId": prepared_legs[0]["order"]["newClientOrderId"],
                        "status": "ACCEPTED",
                        "weex_order_id": "weex-batch-accepted",
                    },
                    {
                        "clientOrderId": prepared_legs[1]["order"]["newClientOrderId"],
                        "status": "RELEASED",
                        "errorCode": "ORDER_REJECTED",
                        "errorMessage": "batch leg rejected by staging",
                    },
                    {
                        "status": "ACCEPTED",
                        "weex_order_id": "unmapped-order-id",
                    },
                ]

            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.bulk_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="batch-partial-results",
                orders=[
                    {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": "1", "price": "10"},
                    {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": "1", "price": "11"},
                    {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": "1", "price": "12"},
                ],
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": complete_spot_account_snapshot(),
                },
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "symbol": complete_spot_symbol_facts(),
                },
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )

            self.assertEqual(len(submit_calls), 1)
            self.assertEqual(result["status"], "SUBMISSION_GROUP_PARTIAL")
            self.assertEqual(
                [leg["status"] for leg in result["legs"]],
                ["ACCEPTED", "RELEASED", "REVIEW_REQUIRED"],
            )
            self.assertEqual(result["legs"][1]["error_code"], "ORDER_REJECTED")
            self.assertEqual(
                result["legs"][1]["error_message"],
                "batch leg rejected by staging",
            )
            released_event = next(
                event
                for event in state.list_events(strategy_id=strategy["strategy_id"])
                if event["event_type"] == "USAGE_RELEASED"
            )
            self.assertEqual(released_event["payload"]["error_code"], "ORDER_REJECTED")
            self.assertEqual(
                released_event["payload"]["error_message"],
                "batch leg rejected by staging",
            )
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT accepted_amount_u, reserved_amount_u FROM authorizations"
                    ).fetchone(),
                    ("10", "12"),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM auto_trade_orders WHERE weex_order_id IS NOT NULL"
                    ).fetchone()[0],
                    1,
                )

    def test_full_position_tp_sl_falls_back_before_valuation_or_reservation(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="full-position", distribution="official"
            )
            now = datetime(2026, 8, 16, 14, 0, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(
                    trade_types=["FUTURES"],
                    max_single_amount="20",
                    max_total_amount="100",
                ),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            facts_provider = Mock()
            submitter = Mock()

            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="transaction.place_tp_sl_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="full-position-tp-sl",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "clientAlgoId": "caller-value-must-be-replaced",
                        "planType": "TAKE_PROFIT",
                        "triggerPrice": "70000",
                        "executePrice": "0",
                        "quantity": "0",
                        "positionSide": "LONG",
                    }
                ],
                risk_payload_provider=lambda leg: {"partial": False, "degraded_reasons": [], "constraints": []},
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=facts_provider,
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )

            self.assertEqual(result["status"], "MANUAL_CONFIRMATION_REQUIRED")
            self.assertEqual(result["error"]["code"], "FULL_POSITION_REQUIRES_MANUAL_CONFIRMATION")
            facts_provider.assert_not_called()
            submitter.assert_not_called()
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM authorization_usage").fetchone()[0],
                    0,
                )

    def test_unproven_futures_reduce_only_semantics_fall_back_before_reservation(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="reduce-only", distribution="official"
            )
            now = datetime(2026, 8, 16, 14, 15, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(
                    trade_types=["FUTURES"],
                    max_single_amount="20",
                    max_total_amount="100",
                ),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            submitter = Mock()

            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="transaction.place_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="unproven-reduce-only",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "SELL",
                        "positionSide": "LONG",
                        "type": "LIMIT",
                        "timeInForce": "GTC",
                        "quantity": "1",
                        "price": "60000",
                    }
                ],
                risk_payload_provider=lambda leg: {"partial": False, "degraded_reasons": [], "constraints": []},
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "reduce_only_proven": False,
                    "symbol": {
                        "quoteAsset": "USDT",
                        "marginAsset": "USDT",
                        "contractVal": "0.001",
                        "makerFeeRate": "0.001",
                        "takerFeeRate": "0.002",
                        "marginType": "CROSSED",
                        "crossLeverage": "10",
                    },
                },
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )

            self.assertEqual(result["status"], "MANUAL_CONFIRMATION_REQUIRED")
            self.assertEqual(result["error"]["code"], "REDUCE_ONLY_UNPROVEN")
            submitter.assert_not_called()
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM authorization_usage").fetchone()[0],
                    0,
                )

    def test_conditional_order_uses_official_client_algo_id_and_conservative_valuation(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="conditional", distribution="official"
            )
            now = datetime(2026, 8, 16, 14, 30, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(
                    trade_types=["FUTURES"],
                    max_single_amount="20",
                    max_total_amount="100",
                ),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            submitted = []

            def submitter(operation_key, prepared_legs):
                submitted.append(prepared_legs)
                outgoing = prepared_legs[0]["order"]
                self.assertNotEqual(outgoing["clientAlgoId"], "caller-controlled-id")
                self.assertTrue(outgoing["clientAlgoId"].startswith("wxa_"))
                self.assertNotIn("newClientOrderId", outgoing)
                return [
                    {
                        "clientOrderId": outgoing["clientAlgoId"],
                        "success": True,
                        "orderId": "conditional-order-1",
                    }
                ]

            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="transaction.place_pending_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="conditional-order",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "positionSide": "LONG",
                        "type": "STOP",
                        "quantity": "0.001",
                        "price": "60000",
                        "triggerPrice": "59000",
                        "clientAlgoId": "caller-controlled-id",
                    }
                ],
                risk_payload_provider=lambda leg: {"partial": False, "degraded_reasons": [], "constraints": []},
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "symbol": {
                        "quoteAsset": "USDT",
                        "marginAsset": "USDT",
                        "contractVal": "0.001",
                        "makerFeeRate": "0.001",
                        "takerFeeRate": "0.002",
                        "marginType": "CROSSED",
                        "crossLeverage": "10",
                    },
                },
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )

            self.assertEqual(len(submitted), 1)
            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["legs"][0]["estimated_amount_u"], "6.12")
            self.assertEqual(result["legs"][0]["weex_order_id"], "conditional-order-1")

    def test_guard_rejects_nested_raw_credentials_before_any_provider_or_state_write(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "state" / "state.sqlite3"
            state = state_module.AutoTradeState(db_path)
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="credential-boundary", distribution="official"
            )
            now = datetime(2026, 8, 16, 14, 45, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(max_single_amount="20", max_total_amount="100"),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            risk_provider = Mock()
            facts_provider = Mock()
            submitter = Mock()

            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.place_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="credential-in-order",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "type": "LIMIT",
                        "quantity": "1",
                        "price": "10",
                        "metadata": {"api_secret": "must-not-enter-guard"},
                    }
                ],
                risk_payload_provider=risk_provider,
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=facts_provider,
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )

            self.assertEqual(result["error"]["code"], "RAW_CREDENTIALS_NOT_ALLOWED")
            risk_provider.assert_not_called()
            facts_provider.assert_not_called()
            submitter.assert_not_called()
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM authorization_usage").fetchone()[0],
                    0,
                )

    def test_guard_rejects_fields_outside_the_official_operation_schema(self) -> None:
        import weex_trade_guard as trade_guard

        risk_provider = Mock()
        facts_provider = Mock()
        submitter = Mock()
        result = trade_guard.submit_authorized_order(
            state=Mock(),
            operation_key="spot.order.place_order",
            strategy_id="str_fixture",
            authorization_id="auth_fixture",
            idempotency_key="unknown-order-field",
            orders=[
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "type": "LIMIT",
                    "timeInForce": "GTC",
                    "quantity": "1",
                    "price": "10",
                    "metadata": {"strategy_note": "not an official request field"},
                }
            ],
            risk_payload_provider=risk_provider,
            risk_evaluator=Mock(),
            facts_provider=facts_provider,
            submitter=submitter,
            confirm_live=True,
            now=datetime(2026, 8, 16, 14, 50, tzinfo=UTC),
            now_ms=1_700_000_000_000,
        )

        self.assertEqual(result["error"]["code"], "UNSUPPORTED_ORDER_FIELDS")
        risk_provider.assert_not_called()
        facts_provider.assert_not_called()
        submitter.assert_not_called()

    def test_official_batch_leg_limits_block_before_providers(self) -> None:
        import weex_trade_guard as trade_guard

        for operation_key, count in (
            ("spot.order.bulk_order", 11),
            ("transaction.place_orders_batch", 6),
        ):
            with self.subTest(operation_key=operation_key):
                risk_provider = Mock()
                facts_provider = Mock()
                submitter = Mock()
                order = {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "type": "LIMIT",
                    "timeInForce": "GTC",
                    "quantity": "1",
                    "price": "10",
                }
                if operation_key.startswith("transaction."):
                    order["positionSide"] = "LONG"
                result = trade_guard.submit_authorized_order(
                    state=Mock(),
                    operation_key=operation_key,
                    strategy_id="str_fixture",
                    authorization_id="auth_fixture",
                    idempotency_key="too-many-legs",
                    orders=[dict(order) for _ in range(count)],
                    risk_payload_provider=risk_provider,
                    risk_evaluator=Mock(),
                    facts_provider=facts_provider,
                    submitter=submitter,
                    confirm_live=True,
                    now=datetime(2026, 8, 16, 14, 55, tzinfo=UTC),
                    now_ms=1_700_000_000_000,
                )
                self.assertEqual(result["error"]["code"], "BATCH_LEG_LIMIT_EXCEEDED")
                risk_provider.assert_not_called()
                facts_provider.assert_not_called()
                submitter.assert_not_called()

    def test_partial_tp_sl_uses_proven_reduce_only_fee_and_single_response_mapping(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state = state_module.AutoTradeState(Path(tempdir) / "state" / "state.sqlite3")
            state.initialize()
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="partial-tp", distribution="official"
            )
            now = datetime(2026, 8, 16, 15, 0, tzinfo=UTC)
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(
                    trade_types=["FUTURES"],
                    max_single_amount="1",
                    max_total_amount="10",
                ),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            submitted = []

            def submitter(operation_key, prepared_legs):
                submitted.append(prepared_legs)
                outgoing = prepared_legs[0]["order"]
                self.assertTrue(outgoing["clientAlgoId"].startswith("wxa_"))
                self.assertNotIn("newClientOrderId", outgoing)
                return [{"success": True, "orderId": "tp-order-1"}]

            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="transaction.place_tp_sl_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="partial-tp-order",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "clientAlgoId": "caller-controlled-id",
                        "planType": "TAKE_PROFIT",
                        "triggerPrice": "65000",
                        "executePrice": "60000",
                        "quantity": "0.001",
                        "positionSide": "LONG",
                    }
                ],
                risk_payload_provider=lambda leg: {"partial": False, "degraded_reasons": [], "constraints": []},
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "reduce_only_proven": True,
                    "symbol": {
                        "quoteAsset": "USDT",
                        "marginAsset": "USDT",
                        "contractVal": "0.001",
                        "makerFeeRate": "0.001",
                        "takerFeeRate": "0.002",
                        "marginType": "CROSSED",
                        "crossLeverage": "10",
                    },
                },
                submitter=submitter,
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )

            self.assertEqual(len(submitted), 1)
            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual(result["legs"][0]["estimated_amount_u"], "0.12")
            self.assertEqual(result["legs"][0]["weex_order_id"], "tp-order-1")

    def test_state_conflict_fails_closed_without_calling_submitter(self) -> None:
        import weex_trade_guard as trade_guard
        import weex_auto_trade_state as state_module

        class FailingState:
            def prepare_submission_group(self, **kwargs):
                raise state_module.StateConflictError("injected state failure")

        submitter = Mock()
        result = trade_guard.submit_authorized_order(
            state=FailingState(),
            operation_key="spot.order.place_order",
            strategy_id="str_fixture",
            authorization_id="auth_fixture",
            idempotency_key="state-conflict",
            orders=[
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "type": "LIMIT",
                    "timeInForce": "GTC",
                    "quantity": "1",
                    "price": "10",
                }
            ],
            risk_payload_provider=lambda leg: {
                "partial": False,
                "degraded_reasons": [],
                "constraints": [],
                "account_snapshot": complete_spot_account_snapshot(),
            },
            risk_evaluator=lambda payload: {"alerts": []},
            facts_provider=lambda leg: {
                "timestamp_ms": 1_700_000_000_000,
                "symbol": complete_spot_symbol_facts(),
            },
            submitter=submitter,
            confirm_live=True,
            now=datetime(2026, 8, 16, 15, 15, tzinfo=UTC),
            now_ms=1_700_000_000_000,
        )

        self.assertEqual(result["status"], "MANUAL_CONFIRMATION_REQUIRED")
        self.assertEqual(result["error"]["code"], "STATE_CONFLICT")
        submitter.assert_not_called()

    def test_restore_waits_until_authorized_submission_is_settled(self) -> None:
        import weex_trade_guard as trade_guard

        state_module = load_state_module()
        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            state = state_module.AutoTradeState(
                config_dir / "auto-trade" / "authorization-state.sqlite3"
            )
            state.initialize()
            now = datetime(2026, 8, 16, 20, 0, tzinfo=UTC)
            strategy = state.register_strategy(
                profile_id="profile-1", strategy_name="restore-race", distribution="official"
            )
            request = state.ensure_authorization(
                strategy_id=strategy["strategy_id"],
                scope=complete_scope(max_single_amount="50", max_total_amount="100"),
                now=now,
            )
            authorization = state.grant_authorization(
                strategy_id=strategy["strategy_id"],
                request_id=request["request_id"],
                scope_signature=request["scope_signature"],
                confirm_live=True,
                now=now,
            )
            snapshot = state.snapshot_state(now=now)
            submit_entered = threading.Event()
            allow_submit_to_finish = threading.Event()
            restore_started = threading.Event()
            restore_enabled_kill_switch = threading.Event()
            original_enable_kill_switch = state._enable_restore_kill_switch
            results = {}
            errors = []

            def observed_enable_kill_switch(now_text):
                restore_enabled_kill_switch.set()
                return original_enable_kill_switch(now_text)

            def submitter(operation_key, prepared_legs):
                submit_entered.set()
                if not allow_submit_to_finish.wait(timeout=3):
                    raise RuntimeError("test did not release the submitter")
                return [
                    {
                        "leg_id": prepared_legs[0]["leg_id"],
                        "status": "ACCEPTED",
                        "weex_order_id": "weex-before-restore",
                    }
                ]

            def submit_order():
                try:
                    results["submission"] = trade_guard.submit_authorized_order(
                        state=state,
                        operation_key="spot.order.place_order",
                        strategy_id=strategy["strategy_id"],
                        authorization_id=authorization["authorization_id"],
                        idempotency_key="restore-race-order",
                        orders=[
                            {
                                "symbol": "BTCUSDT",
                                "side": "BUY",
                                "type": "LIMIT",
                                "timeInForce": "GTC",
                                "quantity": "1",
                                "price": "10",
                            }
                        ],
                        risk_payload_provider=lambda leg: {
                            "partial": False,
                            "degraded_reasons": [],
                            "constraints": [],
                            "account_snapshot": complete_spot_account_snapshot(),
                        },
                        risk_evaluator=lambda payload: {"alerts": []},
                        facts_provider=lambda leg: {
                            "timestamp_ms": 1_700_000_000_000,
                            "symbol": complete_spot_symbol_facts(),
                        },
                        submitter=submitter,
                        confirm_live=True,
                        now=now,
                        now_ms=1_700_000_000_000,
                    )
                except Exception as exc:
                    errors.append(exc)

            def restore_snapshot():
                restore_started.set()
                try:
                    results["restore"] = state.restore_state(
                        snapshot_id=snapshot["snapshot_id"],
                        now=datetime(2026, 8, 16, 20, 1, tzinfo=UTC),
                    )
                except Exception as exc:
                    errors.append(exc)

            with patch.object(
                state,
                "_enable_restore_kill_switch",
                side_effect=observed_enable_kill_switch,
            ):
                submission_thread = threading.Thread(target=submit_order)
                submission_thread.start()
                self.assertTrue(submit_entered.wait(timeout=3))

                restore_thread = threading.Thread(target=restore_snapshot)
                restore_thread.start()
                self.assertTrue(restore_started.wait(timeout=3))
                restore_entered_early = restore_enabled_kill_switch.wait(timeout=0.2)
                allow_submit_to_finish.set()
                submission_thread.join(timeout=3)
                restore_thread.join(timeout=3)

            self.assertFalse(
                restore_entered_early,
                "restore entered its critical section before the in-flight order settled",
            )
            self.assertFalse(submission_thread.is_alive())
            self.assertFalse(restore_thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results["submission"]["status"], "ACCEPTED")
            self.assertEqual(results["restore"]["status"], "STATE_RESTORED_DISABLED")
            self.assertTrue(restore_enabled_kill_switch.is_set())

    def test_attached_tp_sl_and_conditional_presets_fall_back_before_any_side_effect(self) -> None:
        import weex_trade_guard as trade_guard

        for operation_key, order in (
            (
                "transaction.place_order",
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "type": "LIMIT",
                    "timeInForce": "GTC",
                    "quantity": "1",
                    "price": "60000",
                    "tpTriggerPrice": "65000",
                },
            ),
            (
                "transaction.place_pending_order",
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "type": "STOP",
                    "quantity": "1",
                    "price": "60000",
                    "triggerPrice": "59000",
                    "presetStopLossPrice": "58000",
                },
            ),
        ):
            with self.subTest(operation_key=operation_key):
                risk_provider = Mock()
                facts_provider = Mock()
                submitter = Mock()
                result = trade_guard.submit_authorized_order(
                    state=Mock(),
                    operation_key=operation_key,
                    strategy_id="str_fixture",
                    authorization_id="auth_fixture",
                    idempotency_key="attached-protection",
                    orders=[order],
                    risk_payload_provider=risk_provider,
                    risk_evaluator=Mock(),
                    facts_provider=facts_provider,
                    submitter=submitter,
                    confirm_live=True,
                )
                self.assertEqual(result["error"]["code"], "LEG_MAPPING_UNAVAILABLE")
                risk_provider.assert_not_called()
                facts_provider.assert_not_called()
                submitter.assert_not_called()

    def test_missing_risk_completeness_metadata_fails_closed(self) -> None:
        import weex_trade_guard as trade_guard

        facts_provider = Mock()
        submitter = Mock()
        result = trade_guard.submit_authorized_order(
            state=Mock(),
            operation_key="spot.order.place_order",
            strategy_id="str_fixture",
            authorization_id="auth_fixture",
            idempotency_key="missing-risk-metadata",
            orders=[
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "type": "LIMIT",
                    "timeInForce": "GTC",
                    "quantity": "1",
                    "price": "10",
                }
            ],
            risk_payload_provider=lambda leg: {},
            risk_evaluator=lambda payload: {},
            facts_provider=facts_provider,
            submitter=submitter,
            confirm_live=True,
        )
        self.assertEqual(result["error"]["code"], "RISK_DATA_INCOMPLETE")
        facts_provider.assert_not_called()
        submitter.assert_not_called()

    def test_unproven_rejection_keeps_quota_reserved_for_review(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state, strategy, authorization, now = self._authorized_state(
                state_module, Path(tempdir)
            )
            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.place_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="unproven-rejection",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "type": "LIMIT",
                        "timeInForce": "GTC",
                        "quantity": "1",
                        "price": "10",
                    }
                ],
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": complete_spot_account_snapshot(),
                },
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "symbol": complete_spot_symbol_facts(),
                },
                submitter=lambda operation_key, legs: [
                    {"clientOrderId": legs[0]["client_order_id"], "success": False}
                ],
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )
            self.assertEqual(result["status"], "REVIEW_REQUIRED")
            self.assertEqual(result["legs"][0]["reserved_amount_u"], "10")

    def test_idempotent_replay_returns_persisted_result_before_volatile_checks(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state, strategy, authorization, now = self._authorized_state(
                state_module, Path(tempdir)
            )
            order = {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": "1",
                "price": "10",
            }
            common = {
                "state": state,
                "operation_key": "spot.order.place_order",
                "strategy_id": strategy["strategy_id"],
                "authorization_id": authorization["authorization_id"],
                "idempotency_key": "stable-replay",
                "orders": [order],
                "confirm_live": True,
                "now": now,
                "now_ms": 1_700_000_000_000,
            }
            first = trade_guard.submit_authorized_order(
                **common,
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": complete_spot_account_snapshot(),
                },
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "symbol": complete_spot_symbol_facts(),
                },
                submitter=lambda operation_key, legs: [
                    {
                        "clientOrderId": legs[0]["client_order_id"],
                        "success": True,
                        "orderId": "stable-order",
                    }
                ],
            )
            replay_risk = Mock(side_effect=RuntimeError("must not refresh"))
            replay_submitter = Mock()
            replay = trade_guard.submit_authorized_order(
                **common,
                risk_payload_provider=replay_risk,
                risk_evaluator=Mock(),
                facts_provider=Mock(),
                submitter=replay_submitter,
            )
            self.assertEqual(first["status"], "ACCEPTED")
            self.assertEqual(replay["status"], "ACCEPTED")
            replay_risk.assert_not_called()
            replay_submitter.assert_not_called()

    def test_legacy_replay_verifies_persisted_order_fields_before_volatile_checks(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state, strategy, authorization, now = self._authorized_state(
                state_module, Path(tempdir)
            )
            order = {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": "1",
                "price": "10",
            }
            common = {
                "state": state,
                "operation_key": "spot.order.place_order",
                "strategy_id": strategy["strategy_id"],
                "authorization_id": authorization["authorization_id"],
                "idempotency_key": "legacy-guard-replay",
                "orders": [order],
                "confirm_live": True,
                "now": now,
                "now_ms": 1_700_000_000_000,
            }
            first = trade_guard.submit_authorized_order(
                **common,
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": complete_spot_account_snapshot(),
                },
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "symbol": complete_spot_symbol_facts(),
                },
                submitter=lambda operation_key, legs: [
                    {
                        "clientOrderId": legs[0]["client_order_id"],
                        "success": True,
                        "orderId": "legacy-order",
                    }
                ],
            )
            with closing(sqlite3.connect(state.db_path)) as connection:
                connection.execute(
                    "UPDATE authorization_usage SET request_fingerprint = NULL"
                )
                connection.commit()

            replay_risk = Mock(side_effect=RuntimeError("must not refresh"))
            replay_submitter = Mock()
            replay = trade_guard.submit_authorized_order(
                **common,
                risk_payload_provider=replay_risk,
                risk_evaluator=Mock(),
                facts_provider=Mock(),
                submitter=replay_submitter,
            )
            self.assertEqual(first["status"], "ACCEPTED")
            self.assertEqual(replay["status"], "ACCEPTED")
            replay_risk.assert_not_called()
            replay_submitter.assert_not_called()

            conflicting = trade_guard.submit_authorized_order(
                **{**common, "orders": [{**order, "quantity": "2"}]},
                risk_payload_provider=Mock(),
                risk_evaluator=Mock(),
                facts_provider=Mock(),
                submitter=Mock(),
            )
            self.assertEqual(conflicting["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_spot_batch_requires_one_envelope_symbol(self) -> None:
        import weex_trade_guard as trade_guard

        risk_provider = Mock()
        result = trade_guard.submit_authorized_order(
            state=Mock(),
            operation_key="spot.order.bulk_order",
            strategy_id="str_fixture",
            authorization_id="auth_fixture",
            idempotency_key="mixed-symbols",
            orders=[
                {
                    "symbol": symbol,
                    "side": "BUY",
                    "type": "LIMIT",
                    "timeInForce": "GTC",
                    "quantity": "1",
                    "price": "10",
                }
                for symbol in ("BTCUSDT", "ETHUSDT")
            ],
            risk_payload_provider=risk_provider,
            risk_evaluator=Mock(),
            facts_provider=Mock(),
            submitter=Mock(),
            confirm_live=True,
        )
        self.assertEqual(result["error"]["code"], "SPOT_BATCH_SYMBOL_MISMATCH")
        risk_provider.assert_not_called()

    def test_batch_result_leg_id_without_client_id_is_not_trusted(self) -> None:
        state_module = load_state_module()
        import weex_trade_guard as trade_guard

        with tempfile.TemporaryDirectory() as tempdir:
            state, strategy, authorization, now = self._authorized_state(
                state_module, Path(tempdir)
            )
            result = trade_guard.submit_authorized_order(
                state=state,
                operation_key="spot.order.bulk_order",
                strategy_id=strategy["strategy_id"],
                authorization_id=authorization["authorization_id"],
                idempotency_key="untrusted-leg-id",
                orders=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "type": "LIMIT",
                        "timeInForce": "GTC",
                        "quantity": "1",
                        "price": str(price),
                    }
                    for price in (10, 11)
                ],
                risk_payload_provider=lambda leg: {
                    "partial": False,
                    "degraded_reasons": [],
                    "constraints": [],
                    "account_snapshot": complete_spot_account_snapshot(),
                },
                risk_evaluator=lambda payload: {"alerts": []},
                facts_provider=lambda leg: {
                    "timestamp_ms": 1_700_000_000_000,
                    "symbol": complete_spot_symbol_facts(),
                },
                submitter=lambda operation_key, legs: [
                    {
                        "leg_id": leg["leg_id"],
                        "status": "ACCEPTED",
                        "orderId": f"order-{index}",
                    }
                    for index, leg in enumerate(legs)
                ],
                confirm_live=True,
                now=now,
                now_ms=1_700_000_000_000,
            )
            self.assertEqual(result["status"], "REVIEW_REQUIRED")
            self.assertEqual({leg["status"] for leg in result["legs"]}, {"REVIEW_REQUIRED"})

    def test_official_conditional_required_fields_fail_before_providers(self) -> None:
        import weex_trade_guard as trade_guard

        risk_provider = Mock()
        result = trade_guard.submit_authorized_order(
            state=Mock(),
            operation_key="spot.order.place_order",
            strategy_id="str_fixture",
            authorization_id="auth_fixture",
            idempotency_key="missing-time-in-force",
            orders=[
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "type": "LIMIT",
                    "quantity": "1",
                    "price": "10",
                }
            ],
            risk_payload_provider=risk_provider,
            risk_evaluator=Mock(),
            facts_provider=Mock(),
            submitter=Mock(),
            confirm_live=True,
        )
        self.assertEqual(result["error"]["code"], "HARD_CHECK_FAILED")
        risk_provider.assert_not_called()

class AutoTradeAmountConversionTests(unittest.TestCase):
    def test_asset_conversion_requires_fresh_direct_or_reverse_tradable_rate(self) -> None:
        amount_module = load_amount_module()
        now_ms = 1_700_000_000_000
        base = {
            "timestamp_ms": now_ms - 100,
            "symbol": {"quoteAsset": "USDC", "makerFeeRate": "0", "takerFeeRate": "0"},
            "conversion_rates": {
                "USDCUSDT": {
                    "price": "1.01",
                    "timestamp_ms": now_ms - 100,
                    "tradable": True,
                }
            },
        }
        direct = amount_module.estimate_order_amount(
            market="SPOT",
            order={"symbol": "BTCUSDC", "side": "BUY", "type": "LIMIT", "quantity": "1", "price": "10"},
            facts=base,
            now_ms=now_ms,
        )
        self.assertEqual(direct["estimated_amount_u"], "10.1")
        stale = {**base, "conversion_rates": {"USDCUSDT": {"price": "1.01", "timestamp_ms": now_ms - 10_000, "tradable": True}}}
        with self.assertRaises(amount_module.ValuationUnavailable):
            amount_module.estimate_order_amount(
                market="SPOT",
                order={"symbol": "BTCUSDC", "side": "BUY", "type": "LIMIT", "quantity": "1", "price": "10"},
                facts=stale,
                now_ms=now_ms,
            )
        reverse_fallback = {
            **base,
            "conversion_rates": {
                "USDCUSDT": {
                    "price": "1.01",
                    "timestamp_ms": now_ms - 10_000,
                    "tradable": True,
                },
                "USDTUSDC": {
                    "price": "0.98",
                    "timestamp_ms": now_ms - 100,
                    "tradable": True,
                },
            },
        }
        reverse = amount_module.estimate_order_amount(
            market="SPOT",
            order={
                "symbol": "BTCUSDC",
                "side": "BUY",
                "type": "LIMIT",
                "quantity": "1",
                "price": "10",
            },
            facts=reverse_fallback,
            now_ms=now_ms,
        )
        self.assertEqual(reverse["conversion_source"], "USDTUSDC_REVERSE")

    def test_futures_uses_contract_value_leverage_and_fee_and_rejects_missing_facts(self) -> None:
        amount_module = load_amount_module()
        now_ms = 1_700_000_000_000
        facts = {
            "timestamp_ms": now_ms - 100,
            "depth": {
                "timestamp_ms": now_ms - 100,
                "limit": 15,
                "asks": [["20000", "100"]],
                "bids": [["19900", "100"]],
            },
            "symbol": {
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
                "contractVal": "0.001",
                "makerFeeRate": "0.0002",
                "takerFeeRate": "0.0004",
                "crossLeverage": "10",
            },
        }
        result = amount_module.estimate_order_amount(
            market="FUTURES",
            order={
                "symbol": "cmt_btcusdt",
                "side": "BUY",
                "type": "MARKET",
                "quantity": "100",
                "marginType": "CROSSED",
            },
            facts=facts,
            now_ms=now_ms,
        )
        self.assertEqual(result["estimated_amount_u"], "200800")
        self.assertTrue(result["estimated"])
        self.assertIn("estimated", result["disclaimer"].lower())

        missing = {**facts, "symbol": {**facts["symbol"], "contractVal": None, "crossLeverage": None}}
        with self.assertRaises(amount_module.ValuationUnavailable):
            amount_module.estimate_order_amount(
                market="FUTURES",
                order={"symbol": "cmt_btcusdt", "side": "BUY", "type": "MARKET", "quantity": "100"},
                facts=missing,
                now_ms=now_ms,
            )
        missing_margin_asset = {
            **facts,
            "symbol": {key: value for key, value in facts["symbol"].items() if key != "marginAsset"},
        }
        with self.assertRaises(amount_module.ValuationUnavailable):
            amount_module.estimate_order_amount(
                market="FUTURES",
                order={
                    "symbol": "cmt_btcusdt",
                    "side": "BUY",
                    "type": "MARKET",
                    "quantity": "100",
                    "marginType": "CROSSED",
                },
                facts=missing_margin_asset,
                now_ms=now_ms,
            )

    def test_futures_quantity_is_not_double_scaled_by_subunit_contract_value(self) -> None:
        amount_module = load_amount_module()
        now_ms = 1_700_000_000_000

        result = amount_module.estimate_order_amount(
            market="FUTURES",
            order={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "MARKET",
                "quantity": "0.0001",
                "marginType": "ISOLATED",
            },
            facts={
                "timestamp_ms": now_ms,
                "depth": {
                    "timestamp_ms": now_ms,
                    "limit": 15,
                    "asks": [["60000", "1"]],
                    "bids": [["59900", "1"]],
                },
                "symbol": {
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "contractVal": "0.000001",
                    "makerFeeRate": "0",
                    "takerFeeRate": "0",
                    "isolatedLongLeverage": "20",
                    "isolatedShortLeverage": "10",
                },
            },
            now_ms=now_ms,
        )

        self.assertEqual(result["notional_quote"], "6")
        self.assertEqual(result["estimated_amount_u"], "0.3")


if __name__ == "__main__":
    unittest.main()
