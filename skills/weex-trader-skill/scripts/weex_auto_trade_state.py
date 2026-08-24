#!/usr/bin/env python3
"""Deterministic local state for WEEX automated-trading authorization."""

from __future__ import annotations

import errno
import os
import hashlib
import json
import re
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any


CURRENT_SCHEMA_VERSION = 5
EVENT_SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5_000
MAX_VALID_HOURS = Decimal("720")
MAX_VALID_SECONDS = 2_592_000
DEFAULT_REQUEST_TTL_SECONDS = 900
SNAPSHOT_INDEX_VERSION = 2
DEFAULT_SNAPSHOT_RETENTION_COUNT = 10
MIN_SNAPSHOT_RETENTION_COUNT = 1
MAX_SNAPSHOT_RETENTION_COUNT = 100
SNAPSHOT_ID_PATTERN = re.compile(r"^snap_[0-9a-f]{32}$")
_SNAPSHOT_PROCESS_LOCK = threading.RLock()
_FILE_LOCK_STATE = threading.local()
SCOPE_FIELDS = frozenset(
    {
        "trade_types",
        "symbols",
        "all_symbols",
        "max_single_amount",
        "max_total_amount",
        "valid_hours",
    }
)
DEPRECATED_EXPANDED_SCOPE_COLUMNS = (
    "allowed_sides_csv",
    "allowed_order_types_csv",
    "min_single_amount_u",
    "max_order_count",
)
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9:_-]+$")
EXPECTED_TABLES = frozenset(
    {
        "strategies",
        "authorization_requests",
        "authorizations",
        "authorization_usage",
        "auto_trade_orders",
        "authorization_events",
    }
)
_OFFICIAL_USAGE_EVIDENCE_SOURCES = frozenset(
    {
        "WEEX_SPOT_ORDER_ACCEPTED",
        "WEEX_SPOT_ORDER_NOT_FOUND",
        "WEEX_FUTURES_ORDER_ACCEPTED",
        "WEEX_FUTURES_ORDER_NOT_FOUND",
    }
)


_VERIFIED_EVIDENCE_TOKEN = object()


class VerifiedUsageEvidence:
    """Evidence issued by the official read-only reconciliation boundary."""

    __slots__ = (
        "outcome", "evidence_source", "weex_order_id", "usage_id", "strategy_id",
        "authorization_id", "submission_group_id", "leg_id", "client_order_id",
        "module", "symbol", "side", "order_type", "quantity", "price", "_token",
    )

    def __init__(
        self,
        *,
        outcome: str,
        evidence_source: str,
        weex_order_id: str | None,
        usage_id: str,
        strategy_id: str,
        authorization_id: str,
        submission_group_id: str,
        leg_id: str,
        client_order_id: str,
        module: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: str,
        price: str | None,
        _token: object,
    ) -> None:
        if _token is not _VERIFIED_EVIDENCE_TOKEN:
            raise ValueError("UNTRUSTED_USAGE_EVIDENCE")
        values = locals()
        for field_name in self.__slots__:
            if field_name != "_token":
                object.__setattr__(self, field_name, values[field_name])
        object.__setattr__(self, "_token", _token)

    def __setattr__(self, name: str, value: Any) -> None:
        if hasattr(self, name):
            raise AttributeError("verified usage evidence is immutable")
        object.__setattr__(self, name, value)


def build_verified_usage_evidence(facts: dict[str, Any]) -> VerifiedUsageEvidence:
    """Create an opaque evidence value from a trusted provider's exact result."""
    if not isinstance(facts, dict):
        raise ValueError("UNTRUSTED_USAGE_EVIDENCE")
    required = {
        "outcome",
        "evidence_source",
        "weex_order_id",
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
    }
    if set(facts) != required:
        raise ValueError("UNTRUSTED_USAGE_EVIDENCE")
    outcome = _required_text(facts["outcome"], "outcome").upper()
    source = _required_text(facts["evidence_source"], "evidence_source")
    if source not in _OFFICIAL_USAGE_EVIDENCE_SOURCES:
        raise ValueError("UNTRUSTED_USAGE_EVIDENCE")
    module = _required_text(facts["module"], "module").upper()
    if not source.startswith(f"WEEX_{module}_"):
        raise ValueError("UNTRUSTED_USAGE_EVIDENCE")
    order_id = facts["weex_order_id"]
    if order_id is not None:
        order_id = _required_text(order_id, "weex_order_id")
    if outcome == "ACCEPTED":
        if order_id is None or not source.endswith("_ORDER_ACCEPTED"):
            raise ValueError("UNTRUSTED_USAGE_EVIDENCE")
    elif outcome == "RELEASED":
        if order_id is not None or not source.endswith("_ORDER_NOT_FOUND"):
            raise ValueError("UNTRUSTED_USAGE_EVIDENCE")
    else:
        raise ValueError("UNTRUSTED_USAGE_EVIDENCE")
    normalized = {
        key: _required_text(facts[key], key)
        for key in required
        if key not in {"outcome", "evidence_source", "weex_order_id", "price"}
    }
    normalized["module"] = module
    normalized["symbol"] = normalized["symbol"].upper()
    normalized["side"] = normalized["side"].upper()
    normalized["order_type"] = normalized["order_type"].upper()
    price = facts["price"]
    if price is not None:
        price = _required_text(price, "price")
    return VerifiedUsageEvidence(
        outcome=outcome,
        evidence_source=source,
        weex_order_id=order_id,
        price=price,
        _token=_VERIFIED_EVIDENCE_TOKEN,
        **normalized,
    )


class StateConflictError(RuntimeError):
    """Raised when local state cannot be used without weakening the guard."""

    code = "STATE_CONFLICT"


SCHEMA_V1 = """
CREATE TABLE strategies (
    strategy_id TEXT PRIMARY KEY,
    distribution TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    trading_mode TEXT NOT NULL CHECK (trading_mode = 'live'),
    owner_key TEXT NOT NULL UNIQUE,
    strategy_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retired_at TEXT
    , UNIQUE (strategy_id, owner_key)
);

CREATE TABLE authorization_requests (
    request_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
    owner_key TEXT NOT NULL,
    scope_signature TEXT NOT NULL,
    spot_allowed INTEGER NOT NULL CHECK (spot_allowed IN (0, 1)),
    futures_allowed INTEGER NOT NULL CHECK (futures_allowed IN (0, 1)),
    symbols_csv TEXT NOT NULL,
    all_symbols INTEGER NOT NULL CHECK (all_symbols IN (0, 1)),
    max_single_amount_u TEXT NOT NULL,
    max_total_amount_u TEXT NOT NULL,
    valid_seconds INTEGER NOT NULL CHECK (valid_seconds > 0 AND valid_seconds <= 2592000),
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'GRANTED', 'EXPIRED', 'REJECTED')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (request_id, strategy_id),
    FOREIGN KEY (strategy_id, owner_key) REFERENCES strategies(strategy_id, owner_key)
);

CREATE TABLE authorizations (
    authorization_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE REFERENCES authorization_requests(request_id),
    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
    owner_key TEXT NOT NULL,
    scope_signature TEXT NOT NULL,
    spot_allowed INTEGER NOT NULL CHECK (spot_allowed IN (0, 1)),
    futures_allowed INTEGER NOT NULL CHECK (futures_allowed IN (0, 1)),
    symbols_csv TEXT NOT NULL,
    all_symbols INTEGER NOT NULL CHECK (all_symbols IN (0, 1)),
    max_single_amount_u TEXT NOT NULL,
    max_total_amount_u TEXT NOT NULL,
    valid_seconds INTEGER NOT NULL CHECK (valid_seconds > 0 AND valid_seconds <= 2592000),
    accepted_amount_u TEXT NOT NULL,
    reserved_amount_u TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'EXPIRED', 'REVOKED', 'REPLACED')),
    starts_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE (authorization_id, strategy_id),
    FOREIGN KEY (strategy_id, owner_key) REFERENCES strategies(strategy_id, owner_key)
);

CREATE TABLE authorization_usage (
    usage_id TEXT PRIMARY KEY,
    authorization_id TEXT NOT NULL REFERENCES authorizations(authorization_id),
    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
    owner_key TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    submission_group_id TEXT NOT NULL,
    leg_id TEXT NOT NULL,
    leg_index INTEGER NOT NULL CHECK (leg_index >= 0),
    leg_type TEXT NOT NULL CHECK (leg_type IN ('PRIMARY', 'BATCH_CHILD', 'CONDITIONAL', 'TAKE_PROFIT', 'STOP_LOSS')),
    module TEXT NOT NULL CHECK (module IN ('SPOT', 'FUTURES')),
    symbol TEXT NOT NULL,
    estimated_amount_u TEXT NOT NULL,
    quota_before_u TEXT NOT NULL,
    quota_after_u TEXT NOT NULL,
    valuation_source TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RESERVED', 'ACCEPTED', 'RELEASED', 'REVIEW_REQUIRED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE (authorization_id, idempotency_key, leg_id),
    UNIQUE (usage_id, authorization_id, strategy_id),
    FOREIGN KEY (authorization_id, strategy_id) REFERENCES authorizations(authorization_id, strategy_id),
    FOREIGN KEY (strategy_id, owner_key) REFERENCES strategies(strategy_id, owner_key)
);

CREATE TABLE auto_trade_orders (
    auto_trade_order_id TEXT PRIMARY KEY,
    usage_id TEXT NOT NULL UNIQUE REFERENCES authorization_usage(usage_id),
    authorization_id TEXT NOT NULL REFERENCES authorizations(authorization_id),
    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
    submission_group_id TEXT NOT NULL,
    leg_id TEXT NOT NULL,
    leg_index INTEGER NOT NULL CHECK (leg_index >= 0),
    leg_type TEXT NOT NULL,
    client_order_id TEXT NOT NULL UNIQUE,
    weex_order_id TEXT UNIQUE,
    module TEXT NOT NULL CHECK (module IN ('SPOT', 'FUTURES')),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT,
    exchange_status TEXT,
    executed_quantity TEXT,
    executed_quote_amount TEXT,
    fee_amount TEXT,
    fee_asset TEXT,
    reconciliation_status TEXT NOT NULL CHECK (reconciliation_status IN ('NOT_REQUESTED', 'COMPLETE', 'PARTIAL', 'UNAVAILABLE')),
    reconciliation_source TEXT,
    reconciled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (usage_id, authorization_id, strategy_id),
    FOREIGN KEY (usage_id, authorization_id, strategy_id)
        REFERENCES authorization_usage(usage_id, authorization_id, strategy_id)
);

CREATE TABLE authorization_events (
    event_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
    request_id TEXT REFERENCES authorization_requests(request_id),
    authorization_id TEXT REFERENCES authorizations(authorization_id),
    usage_id TEXT REFERENCES authorization_usage(usage_id),
    auto_trade_order_id TEXT REFERENCES auto_trade_orders(auto_trade_order_id),
    event_type TEXT NOT NULL,
    event_schema_version INTEGER NOT NULL CHECK (event_schema_version > 0),
    severity TEXT NOT NULL CHECK (severity IN ('NORMAL', 'EXCEPTION')),
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    notification_key TEXT UNIQUE,
    notification_status TEXT NOT NULL CHECK (
        notification_status IN ('NOT_APPLICABLE', 'PENDING', 'CLAIMED', 'DELIVERED', 'FAILED', 'UNKNOWN')
    ),
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX one_pending_request_per_owner_scope
    ON authorization_requests(owner_key, scope_signature)
    WHERE status = 'PENDING';

CREATE UNIQUE INDEX one_active_authorization_per_owner
    ON authorizations(owner_key)
    WHERE status = 'ACTIVE';
"""


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Add only a non-authoritative event lookup index in schema v2."""
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS authorization_events_strategy_time_idx
        ON authorization_events(strategy_id, occurred_at, event_id)
        """
    )


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Bind every new submission group to a canonical caller request fingerprint."""
    connection.execute(
        "ALTER TABLE authorization_usage ADD COLUMN request_fingerprint TEXT"
    )


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    """Retain the historical expanded-scope columns for audit compatibility."""
    for table in ("authorization_requests", "authorizations"):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN allowed_sides_csv TEXT")
        connection.execute(f"ALTER TABLE {table} ADD COLUMN allowed_order_types_csv TEXT")
        connection.execute(f"ALTER TABLE {table} ADD COLUMN min_single_amount_u TEXT")
        connection.execute(f"ALTER TABLE {table} ADD COLUMN max_order_count INTEGER")


def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
    """Raise the persisted authorization validity limit from 24 hours to 30 days."""
    migration_sql = """
        CREATE TABLE authorization_requests_v5 (
            request_id TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
            owner_key TEXT NOT NULL,
            scope_signature TEXT NOT NULL,
            spot_allowed INTEGER NOT NULL CHECK (spot_allowed IN (0, 1)),
            futures_allowed INTEGER NOT NULL CHECK (futures_allowed IN (0, 1)),
            symbols_csv TEXT NOT NULL,
            all_symbols INTEGER NOT NULL CHECK (all_symbols IN (0, 1)),
            max_single_amount_u TEXT NOT NULL,
            max_total_amount_u TEXT NOT NULL,
            valid_seconds INTEGER NOT NULL CHECK (
                valid_seconds > 0 AND valid_seconds <= 2592000
            ),
            status TEXT NOT NULL CHECK (
                status IN ('PENDING', 'GRANTED', 'EXPIRED', 'REJECTED')
            ),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            allowed_sides_csv TEXT,
            allowed_order_types_csv TEXT,
            min_single_amount_u TEXT,
            max_order_count INTEGER,
            UNIQUE (request_id, strategy_id),
            FOREIGN KEY (strategy_id, owner_key)
                REFERENCES strategies(strategy_id, owner_key)
        );

        CREATE TABLE authorizations_v5 (
            authorization_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE REFERENCES authorization_requests(request_id),
            strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
            owner_key TEXT NOT NULL,
            scope_signature TEXT NOT NULL,
            spot_allowed INTEGER NOT NULL CHECK (spot_allowed IN (0, 1)),
            futures_allowed INTEGER NOT NULL CHECK (futures_allowed IN (0, 1)),
            symbols_csv TEXT NOT NULL,
            all_symbols INTEGER NOT NULL CHECK (all_symbols IN (0, 1)),
            max_single_amount_u TEXT NOT NULL,
            max_total_amount_u TEXT NOT NULL,
            valid_seconds INTEGER NOT NULL CHECK (
                valid_seconds > 0 AND valid_seconds <= 2592000
            ),
            accepted_amount_u TEXT NOT NULL,
            reserved_amount_u TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('ACTIVE', 'EXPIRED', 'REVOKED', 'REPLACED')
            ),
            starts_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            ended_at TEXT,
            allowed_sides_csv TEXT,
            allowed_order_types_csv TEXT,
            min_single_amount_u TEXT,
            max_order_count INTEGER,
            UNIQUE (authorization_id, strategy_id),
            FOREIGN KEY (strategy_id, owner_key)
                REFERENCES strategies(strategy_id, owner_key)
        );

        INSERT INTO authorization_requests_v5 (
            request_id, strategy_id, owner_key, scope_signature,
            spot_allowed, futures_allowed, symbols_csv, all_symbols,
            max_single_amount_u, max_total_amount_u, valid_seconds,
            status, created_at, expires_at, updated_at,
            allowed_sides_csv, allowed_order_types_csv,
            min_single_amount_u, max_order_count
        )
        SELECT
            request_id, strategy_id, owner_key, scope_signature,
            spot_allowed, futures_allowed, symbols_csv, all_symbols,
            max_single_amount_u, max_total_amount_u, valid_seconds,
            status, created_at, expires_at, updated_at,
            allowed_sides_csv, allowed_order_types_csv,
            min_single_amount_u, max_order_count
        FROM authorization_requests;

        INSERT INTO authorizations_v5 (
            authorization_id, request_id, strategy_id, owner_key, scope_signature,
            spot_allowed, futures_allowed, symbols_csv, all_symbols,
            max_single_amount_u, max_total_amount_u, valid_seconds,
            accepted_amount_u, reserved_amount_u, status,
            starts_at, expires_at, created_at, updated_at, ended_at,
            allowed_sides_csv, allowed_order_types_csv,
            min_single_amount_u, max_order_count
        )
        SELECT
            authorization_id, request_id, strategy_id, owner_key, scope_signature,
            spot_allowed, futures_allowed, symbols_csv, all_symbols,
            max_single_amount_u, max_total_amount_u, valid_seconds,
            accepted_amount_u, reserved_amount_u, status,
            starts_at, expires_at, created_at, updated_at, ended_at,
            allowed_sides_csv, allowed_order_types_csv,
            min_single_amount_u, max_order_count
        FROM authorizations;

        DROP TABLE authorizations;
        DROP TABLE authorization_requests;
        ALTER TABLE authorization_requests_v5 RENAME TO authorization_requests;
        ALTER TABLE authorizations_v5 RENAME TO authorizations;

        CREATE UNIQUE INDEX one_pending_request_per_owner_scope
            ON authorization_requests(owner_key, scope_signature)
            WHERE status = 'PENDING';
        CREATE UNIQUE INDEX one_active_authorization_per_owner
            ON authorizations(owner_key)
            WHERE status = 'ACTIVE';
        """
    for statement in migration_sql.split(";"):
        if statement.strip():
            connection.execute(statement)


MIGRATION_REGISTRY = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
    4: _migrate_v4_to_v5,
}
MIGRATIONS_REQUIRING_FOREIGN_KEYS_OFF = frozenset({4})


class AutoTradeState:
    """Owner-only SQLite facade for automated-trading authorization state."""

    def __init__(self, db_path: str | os.PathLike[str], *, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return connection

    def operation_lock(self):
        """Serialize supported state operations with restore and auto submission."""
        return _exclusive_file_lock(
            self.db_path.parent / ".automatic-trading.lock",
            timeout_ms=self.busy_timeout_ms,
        )

    def initialize(self) -> None:
        if not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
            os.chmod(self.db_path.parent, 0o700)
        _require_owner_only(self.db_path.parent, expected_mode=0o700, label="state directory")
        with self.operation_lock():
            self._initialize_unlocked()

    def _initialize_unlocked(self) -> None:
        created_directory = not self.db_path.parent.exists()
        if created_directory:
            self.db_path.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
            os.chmod(self.db_path.parent, 0o700)
        _require_owner_only(self.db_path.parent, expected_mode=0o700, label="state directory")

        existed = self.db_path.exists()
        if existed:
            _require_owner_only(self.db_path, expected_mode=0o600, label="state database")
        else:
            try:
                descriptor = os.open(
                    self.db_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                os.close(descriptor)
                os.chmod(self.db_path, 0o600)
            except OSError as exc:
                raise StateConflictError("unable to create owner-only automated-trading state") from exc
        try:
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version == 0:
                    try:
                        connection.executescript(
                            "BEGIN IMMEDIATE;\n"
                            + SCHEMA_V1
                            + "\nPRAGMA user_version = 1;\n"
                            + "COMMIT;\n"
                        )
                    except Exception:
                        if connection.in_transaction:
                            connection.execute("ROLLBACK")
                        raise
                    version = 1
                if version > CURRENT_SCHEMA_VERSION:
                    raise StateConflictError(
                        f"unsupported automated-trading state schema version: {version}"
                    )
                while version < CURRENT_SCHEMA_VERSION:
                    migration = MIGRATION_REGISTRY.get(version)
                    if migration is None:
                        raise StateConflictError(
                            f"missing migration from automated-trading state schema version {version}"
                        )
                    disable_foreign_keys = version in MIGRATIONS_REQUIRING_FOREIGN_KEYS_OFF
                    try:
                        if disable_foreign_keys:
                            connection.execute("PRAGMA foreign_keys = OFF")
                            if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0:
                                raise StateConflictError(
                                    "unable to disable SQLite foreign keys for schema migration"
                                )
                        connection.execute("BEGIN IMMEDIATE")
                        migration(connection)
                        next_version = version + 1
                        connection.execute(f"PRAGMA user_version = {next_version}")
                        _validate_connection(
                            connection,
                            full=True,
                            expected_version=next_version,
                            require_foreign_keys=not disable_foreign_keys,
                        )
                        connection.execute("COMMIT")
                        if disable_foreign_keys:
                            connection.execute("PRAGMA foreign_keys = ON")
                            if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
                                raise StateConflictError(
                                    "unable to restore SQLite foreign keys after schema migration"
                                )
                            _validate_connection(
                                connection,
                                full=True,
                                expected_version=next_version,
                            )
                        version = next_version
                    except StateConflictError:
                        if connection.in_transaction:
                            connection.execute("ROLLBACK")
                        if disable_foreign_keys:
                            connection.execute("PRAGMA foreign_keys = ON")
                        raise
                    except Exception as exc:
                        if connection.in_transaction:
                            connection.execute("ROLLBACK")
                        if disable_foreign_keys:
                            connection.execute("PRAGMA foreign_keys = ON")
                        raise StateConflictError("automated-trading schema migration failed") from exc
                _validate_connection(connection, full=not existed)
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to initialize automated-trading state") from exc
        _require_owner_only(self.db_path, expected_mode=0o600, label="state database")

    def health_check(self, *, full: bool = False) -> dict[str, Any]:
        _require_owner_only(self.db_path.parent, expected_mode=0o700, label="state directory")
        _require_owner_only(self.db_path, expected_mode=0o600, label="state database")
        try:
            with closing(self._connect()) as connection:
                _validate_connection(connection, full=full)
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        except StateConflictError:
            raise
        except (InvalidOperation, OSError, sqlite3.Error) as exc:
            raise StateConflictError("automated-trading state health check failed") from exc
        return {
            "status": "HEALTHY",
            "user_version": version,
            "foreign_keys": True,
            "foreign_key_violations": 0,
            "integrity_check": "integrity_check" if full else "quick_check",
        }

    def snapshot_state(
        self,
        *,
        retention_count: int = DEFAULT_SNAPSHOT_RETENTION_COUNT,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if (
            isinstance(retention_count, bool)
            or not isinstance(retention_count, int)
            or not MIN_SNAPSHOT_RETENTION_COUNT
            <= retention_count
            <= MAX_SNAPSHOT_RETENTION_COUNT
        ):
            raise ValueError("retention_count must be an integer from 1 through 100")
        created_at = _format_time(_coerce_now(now))
        snapshot_id = "snap_" + uuid.uuid4().hex
        relative_path = f"auto-trade-{snapshot_id}.sqlite3"
        snapshots_dir = self._snapshots_dir()
        lock_path = self.db_path.parent / ".automatic-trading.lock"

        try:
            _ensure_owner_only_directory(snapshots_dir)
            with _exclusive_file_lock(lock_path):
                self.health_check(full=True)
                index = _load_snapshot_index(snapshots_dir)
                temp_path = snapshots_dir / f".{snapshot_id}.tmp"
                final_path = snapshots_dir / relative_path
                descriptor = os.open(
                    temp_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                os.close(descriptor)
                try:
                    with closing(self._connect()) as source, closing(
                        sqlite3.connect(temp_path, isolation_level=None)
                    ) as target:
                        target.row_factory = sqlite3.Row
                        target.execute("PRAGMA foreign_keys = ON")
                        source.backup(target)
                        target.execute("PRAGMA journal_mode = DELETE")
                        _validate_connection(target, full=True)
                    os.chmod(temp_path, 0o600)
                    _fsync_regular_file(temp_path)
                    os.replace(temp_path, final_path)
                    os.chmod(final_path, 0o600)
                    _fsync_directory(snapshots_dir)
                except Exception:
                    _unlink_if_exists(temp_path)
                    raise

                record = {
                    "snapshot_id": snapshot_id,
                    "created_at": created_at,
                    "relative_path": relative_path,
                    "database_schema_version": CURRENT_SCHEMA_VERSION,
                    "size_bytes": final_path.stat().st_size,
                    "sha256": _sha256_regular_file(final_path),
                }
                records = sorted(
                    [*index["snapshots"], record],
                    key=lambda item: (item["created_at"], item["snapshot_id"]),
                )
                try:
                    _write_snapshot_index(snapshots_dir, records)
                except Exception:
                    # The published database remains as unregistered failure evidence and is
                    # intentionally excluded from future retention.
                    raise

                retention_status = "COMPLETE"
                warnings: list[str] = []
                while len(records) > retention_count:
                    oldest = records[0]
                    oldest_path = snapshots_dir / oldest["relative_path"]
                    tombstone_path = snapshots_dir / f".{oldest['snapshot_id']}.deleting"
                    try:
                        os.replace(oldest_path, tombstone_path)
                        candidate_records = records[1:]
                        _write_snapshot_index(snapshots_dir, candidate_records)
                    except Exception:
                        if tombstone_path.exists() and not oldest_path.exists():
                            os.replace(tombstone_path, oldest_path)
                        retention_status = "INCOMPLETE"
                        warnings.append("oldest managed snapshot could not be prepared for deletion")
                        break
                    try:
                        os.unlink(tombstone_path)
                    except OSError:
                        try:
                            os.replace(tombstone_path, oldest_path)
                            _write_snapshot_index(snapshots_dir, records)
                        except Exception as exc:
                            raise StateConflictError(
                                "snapshot retention rollback failed"
                            ) from exc
                        retention_status = "INCOMPLETE"
                        warnings.append("oldest managed snapshot could not be deleted")
                        break
                    records = candidate_records

                return {
                    "ok": True,
                    "status": "SNAPSHOT_CREATED",
                    **record,
                    "retention_count": retention_count,
                    "retention_status": retention_status,
                    "retained_count": len(records),
                    "snapshots": records,
                    "warnings": warnings,
                    "protection": "OWNER_ONLY_NOT_ENCRYPTED",
                }
        except (ValueError, StateConflictError):
            raise
        except (InvalidOperation, OSError, sqlite3.Error, UnicodeError, json.JSONDecodeError) as exc:
            raise StateConflictError("unable to create automated-trading state snapshot") from exc

    def restore_state(
        self,
        *,
        snapshot_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        snapshot_id = _required_text(snapshot_id, "snapshot_id")
        if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
            raise ValueError("INVALID_SNAPSHOT_ID")
        current_text = _format_time(_coerce_now(now))
        snapshots_dir = self._snapshots_dir()
        lock_path = self.db_path.parent / ".automatic-trading.lock"
        temp_restore_path = self.db_path.parent / f".restore-{uuid.uuid4().hex}.tmp"
        replaced = False
        preserved_path: Path | None = None

        try:
            with _exclusive_file_lock(lock_path):
                self._enable_restore_kill_switch(current_text)
                _ensure_owner_only_directory(snapshots_dir)
                index = _load_snapshot_index(snapshots_dir, verify_contents=False)
                record = next(
                    (
                        item
                        for item in index["snapshots"]
                        if item["snapshot_id"] == snapshot_id
                    ),
                    None,
                )
                if record is None:
                    raise ValueError("UNKNOWN_SNAPSHOT")
                source_snapshot_path = snapshots_dir / record["relative_path"]
                _require_owner_only_regular_file(
                    source_snapshot_path, label="managed snapshot"
                )
                _verify_snapshot_record(source_snapshot_path, record)
                self.health_check(full=True)

                preserved_dir = snapshots_dir / "preserved"
                _ensure_owner_only_directory(preserved_dir)
                preserved_name = f"pre-restore-{snapshot_id}-{uuid.uuid4().hex}.sqlite3"
                preserved_path = preserved_dir / preserved_name
                self._backup_active_database(preserved_path)

                descriptor = os.open(
                    temp_restore_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                os.close(descriptor)
                with closing(sqlite3.connect(source_snapshot_path)) as source, closing(
                    sqlite3.connect(temp_restore_path, isolation_level=None)
                ) as target:
                    target.execute("PRAGMA foreign_keys = ON")
                    source.backup(target)
                os.chmod(temp_restore_path, 0o600)

                restored_state = AutoTradeState(
                    temp_restore_path,
                    busy_timeout_ms=self.busy_timeout_ms,
                )
                restored_state.initialize()
                revoked_count, pending_count, review_count = (
                    restored_state._apply_restore_safety(
                        snapshot_id=snapshot_id,
                        now_text=current_text,
                    )
                )
                with closing(restored_state._connect()) as restored_connection:
                    restored_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    restored_connection.execute("PRAGMA journal_mode = DELETE")
                    _validate_connection(restored_connection, full=True)
                for suffix in ("-wal", "-shm"):
                    _unlink_if_exists(Path(str(temp_restore_path) + suffix))
                os.chmod(temp_restore_path, 0o600)
                _fsync_regular_file(temp_restore_path)

                with closing(self._connect()) as current_connection:
                    current_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                for suffix in ("-wal", "-shm"):
                    _unlink_if_exists(Path(str(self.db_path) + suffix))
                os.replace(temp_restore_path, self.db_path)
                replaced = True
                os.chmod(self.db_path, 0o600)
                self.initialize()

                config_dir = snapshots_dir.parent
                return {
                    "ok": True,
                    "status": "STATE_RESTORED_DISABLED",
                    "snapshot_id": snapshot_id,
                    "restored_at": current_text,
                    "kill_switch_enabled": True,
                    "revoked_active_authorizations": revoked_count,
                    "rejected_pending_requests": pending_count,
                    "manual_reconciliation_records": review_count,
                    "preserved_database_relative_path": preserved_path.relative_to(
                        config_dir
                    ).as_posix(),
                    "next_action": "REAUTHORIZE_AND_RECONCILE_MANUALLY",
                }
        except (ValueError, StateConflictError):
            if replaced and preserved_path is not None:
                self._restore_preserved_database_after_failure(preserved_path)
            raise
        except (InvalidOperation, OSError, sqlite3.Error, UnicodeError) as exc:
            if replaced and preserved_path is not None:
                self._restore_preserved_database_after_failure(preserved_path)
            raise StateConflictError("unable to restore automated-trading state") from exc
        finally:
            _unlink_if_exists(temp_restore_path)
            for suffix in ("-wal", "-shm"):
                _unlink_if_exists(Path(str(temp_restore_path) + suffix))

    def _backup_active_database(self, target_path: Path) -> None:
        descriptor = os.open(
            target_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(descriptor)
        try:
            with closing(self._connect()) as source, closing(
                sqlite3.connect(target_path, isolation_level=None)
            ) as target:
                target.row_factory = sqlite3.Row
                target.execute("PRAGMA foreign_keys = ON")
                source.backup(target)
                target.execute("PRAGMA journal_mode = DELETE")
                _validate_connection(target, full=True)
            os.chmod(target_path, 0o600)
            _fsync_regular_file(target_path)
        except Exception:
            _unlink_if_exists(target_path)
            raise

    def _apply_restore_safety(
        self,
        *,
        snapshot_id: str,
        now_text: str,
    ) -> tuple[int, int, int]:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                active_rows = connection.execute(
                    "SELECT authorization_id FROM authorizations WHERE status = 'ACTIVE'"
                ).fetchall()
                pending_rows = connection.execute(
                    "SELECT request_id FROM authorization_requests WHERE status = 'PENDING'"
                ).fetchall()
                review_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM authorization_usage
                        WHERE status IN ('RESERVED', 'REVIEW_REQUIRED')
                        """
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    UPDATE authorizations
                    SET status = 'REVOKED', ended_at = ?, updated_at = ?
                    WHERE status = 'ACTIVE'
                    """,
                    (now_text, now_text),
                )
                connection.execute(
                    """
                    UPDATE authorization_requests
                    SET status = 'REJECTED', updated_at = ?
                    WHERE status = 'PENDING'
                    """,
                    (now_text,),
                )
                for strategy in connection.execute("SELECT strategy_id FROM strategies"):
                    _append_event(
                        connection,
                        strategy_id=strategy["strategy_id"],
                        event_type="STATE_RESTORED_DISABLED",
                        occurred_at=now_text,
                        payload={
                            "status": "AUTOMATIC_TRADING_DISABLED",
                            "snapshot_id": snapshot_id,
                            "fresh_authorization_required": True,
                            "manual_reconciliation_records": review_count,
                        },
                        severity="EXCEPTION",
                    )
                _validate_business_invariants(connection)
                connection.execute("COMMIT")
                return len(active_rows), len(pending_rows), review_count
        except (StateConflictError, ValueError):
            raise
        except (InvalidOperation, OSError, sqlite3.Error) as exc:
            raise StateConflictError("restore safety transition failed") from exc

    def _restore_preserved_database_after_failure(self, preserved_path: Path) -> None:
        rollback_temp = self.db_path.parent / f".rollback-{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                rollback_temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(descriptor)
            with closing(sqlite3.connect(preserved_path)) as source, closing(
                sqlite3.connect(rollback_temp, isolation_level=None)
            ) as target:
                source.backup(target)
            os.chmod(rollback_temp, 0o600)
            for suffix in ("-wal", "-shm"):
                _unlink_if_exists(Path(str(self.db_path) + suffix))
            os.replace(rollback_temp, self.db_path)
            os.chmod(self.db_path, 0o600)
            self.initialize()
        except Exception as exc:
            raise StateConflictError("active database rollback after restore failed") from exc
        finally:
            _unlink_if_exists(rollback_temp)

    def _enable_restore_kill_switch(self, now_text: str) -> None:
        self._write_automatic_trading_kill_switch(
            reason="STATE_RESTORE", now_text=now_text
        )

    def _write_automatic_trading_kill_switch(
        self, *, reason: str, now_text: str
    ) -> None:
        if reason not in {"STATE_RESTORE", "SUBMISSION_STATE_UNCERTAIN"}:
            raise ValueError("INVALID_KILL_SWITCH_REASON")
        kill_switch_path = self._kill_switch_path()
        if kill_switch_path.exists():
            _load_automatic_trading_kill_switch(kill_switch_path)
            return
        _write_owner_only_json(
            kill_switch_path,
            {
                "schema_version": 1,
                "status": "DISABLED",
                "reason": reason,
                "enabled_at": now_text,
            },
        )

    def disable_automatic_trading(
        self, *, reason: str, now: datetime | None = None
    ) -> dict[str, Any]:
        """Durably block new automatic submissions independently of SQLite health."""
        current_text = _format_time(_coerce_now(now))
        with self.operation_lock():
            already_disabled = self._kill_switch_path().exists()
            self._write_automatic_trading_kill_switch(
                reason=_required_text(reason, "reason"), now_text=current_text
            )
        return {
            "ok": True,
            "status": (
                "AUTOMATIC_TRADING_ALREADY_DISABLED"
                if already_disabled
                else "AUTOMATIC_TRADING_DISABLED"
            ),
        }

    def _clear_restore_kill_switch(self) -> None:
        _unlink_if_exists(self._kill_switch_path())

    def _assert_automatic_trading_enabled(self) -> None:
        kill_switch_path = self._kill_switch_path()
        if not kill_switch_path.exists():
            return
        _load_automatic_trading_kill_switch(kill_switch_path)
        raise ValueError("AUTO_TRADE_DISABLED")

    def enable_auto_trading_after_restore(
        self, *, confirm_live: bool, now: datetime | None = None
    ) -> dict[str, Any]:
        """Clear the restore latch only after reauthorization and manual reconciliation."""
        if confirm_live is not True:
            raise ValueError("LIVE_CONFIRMATION_REQUIRED")
        kill_switch_path = self._kill_switch_path()
        if not kill_switch_path.exists():
            return {"ok": True, "status": "AUTOMATIC_TRADING_ALREADY_ENABLED"}
        current_text = _format_time(_coerce_now(now))
        with self.operation_lock():
            kill_switch = _load_automatic_trading_kill_switch(kill_switch_path)
            enabled_at = kill_switch["enabled_at"]
            failure_code: str | None = None
            try:
                with closing(self._connect()) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    for row in connection.execute(
                        "SELECT owner_key FROM strategies ORDER BY owner_key"
                    ).fetchall():
                        _expire_owner_records(
                            connection,
                            owner_key=row["owner_key"],
                            now_text=current_text,
                        )
                    unresolved = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM authorization_usage "
                            "WHERE status IN ('RESERVED', 'REVIEW_REQUIRED')"
                        ).fetchone()[0]
                    )
                    if unresolved:
                        failure_code = "UNRESOLVED_USAGE_REQUIRES_RECONCILIATION"
                    active = connection.execute(
                        "SELECT starts_at, expires_at FROM authorizations "
                        "WHERE status = 'ACTIVE'"
                    ).fetchall()
                    if not active or any(
                        row["starts_at"] <= enabled_at or row["expires_at"] <= current_text
                        for row in active
                    ):
                        failure_code = failure_code or "FRESH_ACTIVE_AUTHORIZATION_REQUIRED"
                    _validate_business_invariants(connection)
                    connection.execute("COMMIT")
            except (ValueError, StateConflictError):
                raise
            except (InvalidOperation, OSError, sqlite3.Error) as exc:
                raise StateConflictError("unable to enable automatic trading") from exc
            if failure_code is not None:
                raise ValueError(failure_code)
            _unlink_if_exists(kill_switch_path)
            _fsync_directory(kill_switch_path.parent)
        return {"ok": True, "status": "AUTOMATIC_TRADING_ENABLED"}

    def _kill_switch_path(self) -> Path:
        return self.db_path.parent / "automatic-trading.disabled"

    def _snapshots_dir(self) -> Path:
        config_dir = (
            self.db_path.parent.parent
            if self.db_path.parent.name == "auto-trade"
            else self.db_path.parent
        )
        return config_dir / "snapshots"

    def register_strategy(
        self,
        *,
        profile_id: str,
        strategy_name: str,
        distribution: str = "official",
        trading_mode: str = "live",
        strategy_id: str | None = None,
    ) -> dict[str, str]:
        profile_id = _required_text(profile_id, "profile_id")
        strategy_name = _required_text(strategy_name, "strategy_name")
        distribution = _required_text(distribution, "distribution")
        if trading_mode != "live":
            raise ValueError("automated-trading strategies only support live trading")

        now = _utc_now()
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if strategy_id is not None:
                    row = connection.execute(
                        "SELECT * FROM strategies WHERE strategy_id = ?",
                        (strategy_id,),
                    ).fetchone()
                    if row is None:
                        raise ValueError("unknown strategy_id; omit it when registering a new strategy")
                    if row["status"] == "RETIRED":
                        raise ValueError("retired strategy_id cannot be reused")
                    if (
                        row["profile_id"] != profile_id
                        or row["distribution"] != distribution
                        or row["trading_mode"] != trading_mode
                    ):
                        raise ValueError("strategy_id owner does not match the registration request")
                    connection.execute(
                        "UPDATE strategies SET strategy_name = ?, updated_at = ? WHERE strategy_id = ?",
                        (strategy_name, now, strategy_id),
                    )
                    _append_event(
                        connection,
                        strategy_id=strategy_id,
                        event_type="STRATEGY_UPDATED",
                        occurred_at=now,
                        payload={"strategy_name": strategy_name, "status": "ACTIVE"},
                    )
                else:
                    strategy_id = "str_" + uuid.uuid4().hex
                    owner_key = _owner_key(
                        distribution=distribution,
                        profile_id=profile_id,
                        trading_mode=trading_mode,
                        strategy_id=strategy_id,
                    )
                    connection.execute(
                        """
                        INSERT INTO strategies (
                            strategy_id, distribution, profile_id, trading_mode, owner_key,
                            strategy_name, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                        """,
                        (
                            strategy_id,
                            distribution,
                            profile_id,
                            trading_mode,
                            owner_key,
                            strategy_name,
                            now,
                            now,
                        ),
                    )
                    _append_event(
                        connection,
                        strategy_id=strategy_id,
                        event_type="STRATEGY_REGISTERED",
                        occurred_at=now,
                        payload={"strategy_name": strategy_name, "status": "ACTIVE"},
                    )
                result = connection.execute(
                    "SELECT * FROM strategies WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
                connection.execute("COMMIT")
        except ValueError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to register automated-trading strategy") from exc

        assert result is not None
        return _strategy_result(result)

    def get_strategy(self, *, strategy_id: str) -> dict[str, str]:
        strategy_id = _required_text(strategy_id, "strategy_id")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM strategies WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to read automated-trading strategy") from exc
        if row is None:
            raise ValueError("UNKNOWN_STRATEGY")
        return _strategy_result(row)

    def list_strategies(
        self,
        *,
        profile_id: str | None = None,
        include_retired: bool = True,
    ) -> list[dict[str, str]]:
        clauses: list[str] = []
        parameters: list[str] = []
        if profile_id is not None:
            clauses.append("profile_id = ?")
            parameters.append(_required_text(profile_id, "profile_id"))
        if not include_retired:
            clauses.append("status = 'ACTIVE'")
        where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT * FROM strategies" + where_clause + " ORDER BY created_at, strategy_id",
                    tuple(parameters),
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to list automated-trading strategies") from exc
        return [_strategy_result(row) for row in rows]

    def ensure_authorization(
        self,
        *,
        strategy_id: str,
        scope: dict[str, Any],
        now: datetime | None = None,
        request_ttl_seconds: int = DEFAULT_REQUEST_TTL_SECONDS,
    ) -> dict[str, Any]:
        strategy_id = _required_text(strategy_id, "strategy_id")
        normalized_scope = normalize_scope(scope)
        scope_signature = _scope_signature(normalized_scope)
        current = _coerce_now(now)
        current_text = _format_time(current)
        if isinstance(request_ttl_seconds, bool) or not isinstance(request_ttl_seconds, int):
            raise ValueError("request_ttl_seconds must be a positive integer")
        if request_ttl_seconds <= 0:
            raise ValueError("request_ttl_seconds must be a positive integer")
        request_expires_at = _format_time(current + timedelta(seconds=request_ttl_seconds))

        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                strategy = connection.execute(
                    "SELECT * FROM strategies WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
                if strategy is None:
                    raise ValueError("UNKNOWN_STRATEGY")
                if strategy["status"] == "RETIRED":
                    raise ValueError("STRATEGY_RETIRED")

                _expire_owner_records(
                    connection,
                    owner_key=strategy["owner_key"],
                    now_text=current_text,
                )
                stale_requests = connection.execute(
                    """
                    SELECT * FROM authorization_requests
                    WHERE owner_key = ? AND scope_signature != ? AND status = 'PENDING'
                    ORDER BY created_at, request_id
                    """,
                    (strategy["owner_key"], scope_signature),
                ).fetchall()
                for stale_request in stale_requests:
                    updated = connection.execute(
                        """
                        UPDATE authorization_requests
                        SET status = 'REJECTED', updated_at = ?
                        WHERE request_id = ? AND status = 'PENDING'
                        """,
                        (current_text, stale_request["request_id"]),
                    )
                    if updated.rowcount:
                        _append_event(
                            connection,
                            strategy_id=strategy_id,
                            request_id=stale_request["request_id"],
                            event_type="AUTHORIZATION_REQUEST_REJECTED",
                            occurred_at=current_text,
                            payload={"status": "REJECTED", "reason": "SCOPE_CHANGED"},
                            severity="EXCEPTION",
                        )
                active = connection.execute(
                    """
                    SELECT * FROM authorizations
                    WHERE owner_key = ? AND status = 'ACTIVE' AND expires_at > ?
                    """,
                    (strategy["owner_key"], current_text),
                ).fetchone()
                if active is not None and active["scope_signature"] == scope_signature:
                    result = _authorization_result(
                        active,
                        strategy,
                        next_action=self._authorization_next_action(
                            connection, active, now_text=current_text
                        ),
                    )
                    connection.execute("COMMIT")
                    return result

                pending = connection.execute(
                    """
                    SELECT * FROM authorization_requests
                    WHERE owner_key = ? AND scope_signature = ? AND status = 'PENDING'
                    """,
                    (strategy["owner_key"], scope_signature),
                ).fetchone()
                if pending is None:
                    request_id = "req_" + uuid.uuid4().hex
                    connection.execute(
                        """
                        INSERT INTO authorization_requests (
                            request_id, strategy_id, owner_key, scope_signature,
                            spot_allowed, futures_allowed, symbols_csv, all_symbols,
                            max_single_amount_u, max_total_amount_u, valid_seconds,
                            status, created_at, expires_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)
                        """,
                        (
                            request_id,
                            strategy_id,
                            strategy["owner_key"],
                            scope_signature,
                            int("SPOT" in normalized_scope["trade_types"]),
                            int("FUTURES" in normalized_scope["trade_types"]),
                            ",".join(normalized_scope["symbols"]),
                            int(normalized_scope["all_symbols"]),
                            normalized_scope["max_single_amount"],
                            normalized_scope["max_total_amount"],
                            _hours_to_seconds(normalized_scope["valid_hours"]),
                            current_text,
                            request_expires_at,
                            current_text,
                        ),
                    )
                    pending = connection.execute(
                        "SELECT * FROM authorization_requests WHERE request_id = ?",
                        (request_id,),
                    ).fetchone()
                    _append_event(
                        connection,
                        strategy_id=strategy_id,
                        request_id=request_id,
                        event_type="AUTHORIZATION_REQUESTED",
                        occurred_at=current_text,
                        payload={
                            "status": "PENDING",
                            "trade_types": normalized_scope["trade_types"],
                            "symbols": normalized_scope["symbols"],
                            "all_symbols": normalized_scope["all_symbols"],
                            "max_single_amount_u": normalized_scope["max_single_amount"],
                            "max_total_amount_u": normalized_scope["max_total_amount"],
                            "valid_hours": normalized_scope["valid_hours"],
                        },
                        severity="EXCEPTION",
                    )
                result = _request_result(pending, strategy)
                connection.execute("COMMIT")
                return result
        except ValueError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to ensure automated-trading authorization") from exc

    def grant_authorization(
        self,
        *,
        strategy_id: str,
        request_id: str,
        scope_signature: str,
        confirm_live: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        strategy_id = _required_text(strategy_id, "strategy_id")
        request_id = _required_text(request_id, "request_id")
        scope_signature = _required_text(scope_signature, "scope_signature")
        if confirm_live is not True:
            raise ValueError("LIVE_CONFIRMATION_REQUIRED")
        current = _coerce_now(now)
        current_text = _format_time(current)

        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                strategy = connection.execute(
                    "SELECT * FROM strategies WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
                if strategy is None:
                    raise ValueError("UNKNOWN_STRATEGY")
                if strategy["status"] == "RETIRED":
                    raise ValueError("STRATEGY_RETIRED")
                request = connection.execute(
                    "SELECT * FROM authorization_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if request is None:
                    raise ValueError("UNKNOWN_AUTHORIZATION_REQUEST")
                if request["strategy_id"] != strategy_id or request["owner_key"] != strategy["owner_key"]:
                    raise ValueError("STRATEGY_AUTHORIZATION_MISMATCH")
                if request["scope_signature"] != scope_signature:
                    raise ValueError("SCOPE_MISMATCH")
                if _has_deprecated_expanded_scope(request):
                    raise ValueError("AUTHORIZATION_SCOPE_REAUTHORIZATION_REQUIRED")
                _expire_owner_records(
                    connection,
                    owner_key=strategy["owner_key"],
                    now_text=current_text,
                )
                request = connection.execute(
                    "SELECT * FROM authorization_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if request["status"] == "GRANTED":
                    existing = connection.execute(
                        "SELECT * FROM authorizations WHERE request_id = ?",
                        (request_id,),
                    ).fetchone()
                    if existing is None:
                        raise StateConflictError("granted request has no authorization")
                    result = _authorization_result(
                        existing,
                        strategy,
                        next_action=self._authorization_next_action(
                            connection, existing, now_text=current_text
                        ),
                    )
                    result["status"] = existing["status"]
                    connection.execute("COMMIT")
                    return result
                if request["status"] != "PENDING" or request["expires_at"] <= current_text:
                    connection.execute("COMMIT")
                    raise ValueError("AUTHORIZATION_REQUEST_NOT_PENDING")

                active_rows = connection.execute(
                    "SELECT * FROM authorizations WHERE owner_key = ? AND status = 'ACTIVE'",
                    (strategy["owner_key"],),
                ).fetchall()
                for active in active_rows:
                    connection.execute(
                        """
                        UPDATE authorizations
                        SET status = 'REPLACED', ended_at = ?, updated_at = ?
                        WHERE authorization_id = ? AND status = 'ACTIVE'
                        """,
                        (current_text, current_text, active["authorization_id"]),
                    )
                    _append_event(
                        connection,
                        strategy_id=strategy_id,
                        request_id=active["request_id"],
                        authorization_id=active["authorization_id"],
                        event_type="AUTHORIZATION_REPLACED",
                        occurred_at=current_text,
                        payload={"status": "REPLACED"},
                        severity="EXCEPTION",
                    )
                authorization_id = "auth_" + uuid.uuid4().hex
                expires_at = _format_time(
                    current + timedelta(seconds=int(request["valid_seconds"]))
                )
                connection.execute(
                    """
                    INSERT INTO authorizations (
                        authorization_id, request_id, strategy_id, owner_key, scope_signature,
                        spot_allowed, futures_allowed, symbols_csv, all_symbols,
                        max_single_amount_u, max_total_amount_u, valid_seconds,
                        accepted_amount_u, reserved_amount_u, status,
                        starts_at, expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '0', '0', 'ACTIVE', ?, ?, ?, ?)
                    """,
                    (
                        authorization_id,
                        request_id,
                        strategy_id,
                        strategy["owner_key"],
                        request["scope_signature"],
                        request["spot_allowed"],
                        request["futures_allowed"],
                        request["symbols_csv"],
                        request["all_symbols"],
                        request["max_single_amount_u"],
                        request["max_total_amount_u"],
                        request["valid_seconds"],
                        current_text,
                        expires_at,
                        current_text,
                        current_text,
                    ),
                )
                connection.execute(
                    "UPDATE authorization_requests SET status = 'GRANTED', updated_at = ? WHERE request_id = ?",
                    (current_text, request_id),
                )
                _append_event(
                    connection,
                    strategy_id=strategy_id,
                    request_id=request_id,
                    authorization_id=authorization_id,
                    event_type="AUTHORIZATION_GRANTED",
                    occurred_at=current_text,
                    payload={
                        "status": "ACTIVE",
                        "starts_at": current_text,
                        "expires_at": expires_at,
                    },
                    severity="EXCEPTION",
                )
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                result = _authorization_result(
                    authorization,
                    strategy,
                    next_action=self._authorization_next_action(
                        connection, authorization, now_text=current_text
                    ),
                )
                connection.execute("COMMIT")
                return result
        except (ValueError, StateConflictError):
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to grant automated-trading authorization") from exc

    def get_authorization_request(
        self,
        *,
        strategy_id: str,
        request_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        strategy_id = _required_text(strategy_id, "strategy_id")
        request_id = _required_text(request_id, "request_id")
        current_text = _format_time(_coerce_now(now))
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                strategy = connection.execute(
                    "SELECT * FROM strategies WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
                if strategy is None:
                    raise ValueError("UNKNOWN_STRATEGY")
                _expire_owner_records(
                    connection,
                    owner_key=strategy["owner_key"],
                    now_text=current_text,
                )
                request = connection.execute(
                    "SELECT * FROM authorization_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                _validate_business_invariants(connection)
                connection.execute("COMMIT")
        except ValueError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to read authorization request") from exc
        if request is None:
            raise ValueError("UNKNOWN_AUTHORIZATION_REQUEST")
        if request["strategy_id"] != strategy_id or request["owner_key"] != strategy["owner_key"]:
            raise ValueError("STRATEGY_AUTHORIZATION_MISMATCH")
        result = _request_result(request, strategy)
        result["request_status"] = request["status"]
        result["request_created_at"] = request["created_at"]
        result["request_expires_at"] = request["expires_at"]
        return result

    def list_authorizations(
        self,
        *,
        strategy_id: str | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        parameters: tuple[str, ...] = ()
        where_clause = ""
        if strategy_id is not None:
            strategy_id = _required_text(strategy_id, "strategy_id")
            where_clause = "WHERE a.strategy_id = ?"
            parameters = (strategy_id,)
        current_text = _format_time(_coerce_now(now))
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if strategy_id is None:
                    owner_rows = connection.execute(
                        "SELECT owner_key FROM strategies ORDER BY owner_key"
                    ).fetchall()
                else:
                    owner_rows = connection.execute(
                        "SELECT owner_key FROM strategies WHERE strategy_id = ?",
                        (strategy_id,),
                    ).fetchall()
                for owner_row in owner_rows:
                    _expire_owner_records(
                        connection,
                        owner_key=owner_row["owner_key"],
                        now_text=current_text,
                    )
                rows = connection.execute(
                    f"""
                    SELECT a.*, s.strategy_name, s.profile_id, s.distribution, s.trading_mode
                    FROM authorizations a
                    JOIN strategies s ON s.strategy_id = a.strategy_id
                    {where_clause}
                    ORDER BY a.created_at, a.authorization_id
                    """,
                    parameters,
                ).fetchall()
                results = [
                    _authorization_result(
                        row,
                        row,
                        next_action=self._authorization_next_action(
                            connection, row, now_text=current_text
                        ),
                    )
                    for row in rows
                ]
                _validate_business_invariants(connection)
                connection.execute("COMMIT")
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to list automated-trading authorizations") from exc
        return results

    def revoke_authorization(
        self,
        *,
        strategy_id: str,
        authorization_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        strategy_id = _required_text(strategy_id, "strategy_id")
        authorization_id = _required_text(authorization_id, "authorization_id")
        current_text = _format_time(_coerce_now(now))
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                strategy = connection.execute(
                    "SELECT * FROM strategies WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
                if strategy is None:
                    raise ValueError("UNKNOWN_STRATEGY")
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                if authorization is None:
                    raise ValueError("UNKNOWN_AUTHORIZATION")
                if (
                    authorization["strategy_id"] != strategy_id
                    or authorization["owner_key"] != strategy["owner_key"]
                ):
                    raise ValueError("STRATEGY_AUTHORIZATION_MISMATCH")
                if authorization["status"] == "ACTIVE":
                    connection.execute(
                        """
                        UPDATE authorizations
                        SET status = 'REVOKED', ended_at = ?, updated_at = ?
                        WHERE authorization_id = ? AND status = 'ACTIVE'
                        """,
                        (current_text, current_text, authorization_id),
                    )
                    _append_event(
                        connection,
                        strategy_id=strategy_id,
                        request_id=authorization["request_id"],
                        authorization_id=authorization_id,
                        event_type="AUTHORIZATION_REVOKED",
                        occurred_at=current_text,
                        payload={"status": "REVOKED"},
                        severity="EXCEPTION",
                    )
                    authorization = connection.execute(
                        "SELECT * FROM authorizations WHERE authorization_id = ?",
                        (authorization_id,),
                    ).fetchone()
                result = _authorization_result(authorization, strategy)
                result["status"] = authorization["status"]
                result["next_action"] = "REQUEST_NEW_AUTHORIZATION"
                connection.execute("COMMIT")
                return result
        except ValueError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to revoke automated-trading authorization") from exc

    def retire_strategy(
        self,
        *,
        strategy_id: str,
        now: datetime | None = None,
    ) -> dict[str, str]:
        strategy_id = _required_text(strategy_id, "strategy_id")
        current_text = _format_time(_coerce_now(now))
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                strategy = connection.execute(
                    "SELECT * FROM strategies WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
                if strategy is None:
                    raise ValueError("UNKNOWN_STRATEGY")
                if strategy["status"] != "RETIRED":
                    connection.execute(
                        """
                        UPDATE authorizations
                        SET status = 'REVOKED', ended_at = ?, updated_at = ?
                        WHERE owner_key = ? AND status = 'ACTIVE'
                        """,
                        (current_text, current_text, strategy["owner_key"]),
                    )
                    connection.execute(
                        """
                        UPDATE strategies
                        SET status = 'RETIRED', retired_at = ?, updated_at = ?
                        WHERE strategy_id = ?
                        """,
                        (current_text, current_text, strategy_id),
                    )
                    _append_event(
                        connection,
                        strategy_id=strategy_id,
                        event_type="STRATEGY_RETIRED",
                        occurred_at=current_text,
                        payload={"status": "RETIRED"},
                        severity="EXCEPTION",
                    )
                    strategy = connection.execute(
                        "SELECT * FROM strategies WHERE strategy_id = ?",
                        (strategy_id,),
                    ).fetchone()
                result = _strategy_result(strategy)
                connection.execute("COMMIT")
                return result
        except ValueError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to retire automated-trading strategy") from exc

    def reserve_usage(
        self,
        *,
        strategy_id: str,
        authorization_id: str,
        idempotency_key: str,
        estimated_amount_u: str,
        module: str,
        symbol: str,
        valuation_source: str,
        now: datetime | None = None,
        submission_group_id: str | None = None,
        leg_id: str = "primary",
        leg_index: int = 0,
        leg_type: str = "PRIMARY",
    ) -> dict[str, Any]:
        strategy_id = _required_text(strategy_id, "strategy_id")
        authorization_id = _required_text(authorization_id, "authorization_id")
        idempotency_key = _required_text(idempotency_key, "idempotency_key")
        valuation_source = _required_text(valuation_source, "valuation_source")
        leg_id = _required_text(leg_id, "leg_id")
        amount_text = _positive_decimal_text(estimated_amount_u, "estimated_amount_u")
        amount = Decimal(amount_text)
        if module not in {"SPOT", "FUTURES"}:
            raise ValueError("UNSUPPORTED_OPERATION")
        symbol = _required_text(symbol, "symbol").upper()
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError("SCOPE_MISMATCH")
        if isinstance(leg_index, bool) or not isinstance(leg_index, int) or leg_index < 0:
            raise ValueError("leg_index must be a non-negative integer")
        if leg_type not in {"PRIMARY", "BATCH_CHILD", "CONDITIONAL", "TAKE_PROFIT", "STOP_LOSS"}:
            raise ValueError("leg_type is invalid")
        current_text = _format_time(_coerce_now(now))
        self._assert_automatic_trading_enabled()

        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_automatic_trading_enabled()
                strategy = connection.execute(
                    "SELECT * FROM strategies WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
                if strategy is None:
                    raise ValueError("UNKNOWN_STRATEGY")
                if strategy["status"] == "RETIRED":
                    raise ValueError("STRATEGY_RETIRED")
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                if authorization is None:
                    raise ValueError("UNKNOWN_AUTHORIZATION")
                if (
                    authorization["strategy_id"] != strategy_id
                    or authorization["owner_key"] != strategy["owner_key"]
                ):
                    raise ValueError("STRATEGY_AUTHORIZATION_MISMATCH")

                existing = connection.execute(
                    """
                    SELECT * FROM authorization_usage
                    WHERE authorization_id = ? AND idempotency_key = ? AND leg_id = ?
                    """,
                    (authorization_id, idempotency_key, leg_id),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["estimated_amount_u"] != amount_text
                        or existing["module"] != module
                        or existing["symbol"] != symbol
                    ):
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                    result = _usage_result(existing, authorization)
                    connection.execute("COMMIT")
                    return result

                _expire_owner_records(
                    connection,
                    owner_key=strategy["owner_key"],
                    now_text=current_text,
                )
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                if authorization["status"] != "ACTIVE":
                    connection.execute("COMMIT")
                    raise ValueError("AUTHORIZATION_NOT_ACTIVE")
                if _has_deprecated_expanded_scope(authorization):
                    connection.execute("COMMIT")
                    raise ValueError("AUTHORIZATION_SCOPE_REAUTHORIZATION_REQUIRED")
                if module == "SPOT" and not authorization["spot_allowed"]:
                    raise ValueError("SCOPE_MISMATCH")
                if module == "FUTURES" and not authorization["futures_allowed"]:
                    raise ValueError("SCOPE_MISMATCH")
                allowed_symbols = set(authorization["symbols_csv"].split(","))
                if not authorization["all_symbols"] and symbol not in allowed_symbols:
                    raise ValueError("SCOPE_MISMATCH")

                max_single = Decimal(authorization["max_single_amount_u"])
                max_total = Decimal(authorization["max_total_amount_u"])
                accepted = Decimal(authorization["accepted_amount_u"])
                reserved = Decimal(authorization["reserved_amount_u"])
                if amount > max_single:
                    raise ValueError("SINGLE_LIMIT_EXCEEDED")
                if _exact_decimal_sum(accepted, reserved, amount) > max_total:
                    raise ValueError("TOTAL_LIMIT_EXCEEDED")

                usage_id = "use_" + uuid.uuid4().hex
                group_id = submission_group_id or ("grp_" + uuid.uuid4().hex)
                quota_before = _exact_decimal_sum(max_total, -accepted, -reserved)
                quota_after = _exact_decimal_sum(quota_before, -amount)
                request_fingerprint = hashlib.sha256(
                    json.dumps(
                        {
                            "authorization_id": authorization_id,
                            "idempotency_key": idempotency_key,
                            "leg_id": leg_id,
                            "leg_index": leg_index,
                            "leg_type": leg_type,
                            "module": module,
                            "symbol": symbol,
                            "estimated_amount_u": amount_text,
                            "valuation_source": valuation_source,
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("ascii")
                ).hexdigest()
                connection.execute(
                    """
                    INSERT INTO authorization_usage (
                        usage_id, authorization_id, strategy_id, owner_key,
                        idempotency_key, submission_group_id, leg_id, leg_index, leg_type,
                        module, symbol, estimated_amount_u, quota_before_u, quota_after_u,
                        valuation_source, request_fingerprint, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?)
                    """,
                    (
                        usage_id,
                        authorization_id,
                        strategy_id,
                        strategy["owner_key"],
                        idempotency_key,
                        group_id,
                        leg_id,
                        leg_index,
                        leg_type,
                        module,
                        symbol,
                        amount_text,
                        _decimal_text(quota_before),
                        _decimal_text(quota_after),
                        valuation_source,
                        request_fingerprint,
                        current_text,
                        current_text,
                    ),
                )
                connection.execute(
                    """
                    UPDATE authorizations
                    SET reserved_amount_u = ?, updated_at = ?
                    WHERE authorization_id = ?
                    """,
                    (
                        _decimal_text(_exact_decimal_sum(reserved, amount)),
                        current_text,
                        authorization_id,
                    ),
                )
                _append_event(
                    connection,
                    strategy_id=strategy_id,
                    authorization_id=authorization_id,
                    usage_id=usage_id,
                    event_type="USAGE_RESERVED",
                    occurred_at=current_text,
                    payload={
                        "status": "RESERVED",
                        "module": module,
                        "symbol": symbol,
                        "estimated_amount_u": amount_text,
                        "quota_before_u": _decimal_text(quota_before),
                        "quota_after_u": _decimal_text(quota_after),
                    },
                )
                usage = connection.execute(
                    "SELECT * FROM authorization_usage WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                result = _usage_result(usage, authorization)
                connection.execute("COMMIT")
                return result
        except (ValueError, StateConflictError):
            raise
        except (InvalidOperation, OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to reserve automated-trading usage") from exc

    def prepare_submission_group(
        self,
        *,
        strategy_id: str,
        authorization_id: str,
        idempotency_key: str,
        legs: list[dict[str, Any]],
        request_fingerprint: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Atomically reserve quota and create client-order mappings for every leg."""
        strategy_id = _required_text(strategy_id, "strategy_id")
        authorization_id = _required_text(authorization_id, "authorization_id")
        idempotency_key = _required_text(idempotency_key, "idempotency_key")
        normalized_request_fingerprint = _required_sha256(
            request_fingerprint, "request_fingerprint"
        )
        if not isinstance(legs, list) or not legs:
            raise ValueError("legs must be a non-empty array")

        normalized_legs: list[dict[str, Any]] = []
        leg_ids: set[str] = set()
        leg_indexes: set[int] = set()
        for leg in legs:
            if not isinstance(leg, dict):
                raise ValueError("legs entries must be objects")
            leg_id = _required_text(leg.get("leg_id"), "leg_id")
            if leg_id in leg_ids:
                raise ValueError("DUPLICATE_LEG_ID")
            leg_ids.add(leg_id)
            leg_index = leg.get("leg_index")
            if isinstance(leg_index, bool) or not isinstance(leg_index, int) or leg_index < 0:
                raise ValueError("leg_index must be a non-negative integer")
            if leg_index in leg_indexes:
                raise ValueError("DUPLICATE_LEG_INDEX")
            leg_indexes.add(leg_index)
            leg_type = leg.get("leg_type")
            if leg_type not in {
                "PRIMARY",
                "BATCH_CHILD",
                "CONDITIONAL",
                "TAKE_PROFIT",
                "STOP_LOSS",
            }:
                raise ValueError("leg_type is invalid")
            module = leg.get("module")
            if module not in {"SPOT", "FUTURES"}:
                raise ValueError("UNSUPPORTED_OPERATION")
            symbol = _required_text(leg.get("symbol"), "symbol").upper()
            if not SYMBOL_PATTERN.fullmatch(symbol):
                raise ValueError("SCOPE_MISMATCH")
            normalized_legs.append(
                {
                    "leg_id": leg_id,
                    "leg_index": leg_index,
                    "leg_type": leg_type,
                    "module": module,
                    "symbol": symbol,
                    "estimated_amount_u": _positive_decimal_text(
                        leg.get("estimated_amount_u"), "estimated_amount_u"
                    ),
                    "valuation_source": _required_text(
                        leg.get("valuation_source"), "valuation_source"
                    ),
                    "side": _required_text(leg.get("side"), "side").upper(),
                    "order_type": _required_text(
                        leg.get("order_type"), "order_type"
                    ).upper(),
                    "quantity": _positive_decimal_text(leg.get("quantity"), "quantity"),
                    "price": (
                        None
                        if leg.get("price") is None
                        else _positive_decimal_text(leg.get("price"), "price")
                    ),
                    "advisory_alerts": _normalize_advisory_alerts(
                        leg.get("advisory_alerts", [])
                    ),
                    "risk_rule_version": _required_text(
                        leg.get("risk_rule_version", "unknown"), "risk_rule_version"
                    ),
                    "risk_input_timestamp": (
                        None
                        if leg.get("risk_input_timestamp") in (None, "")
                        else _required_text(
                            leg.get("risk_input_timestamp"), "risk_input_timestamp"
                        )
                    ),
                }
            )
        normalized_legs.sort(key=lambda item: item["leg_index"])
        current_text = _format_time(_coerce_now(now))
        self._assert_automatic_trading_enabled()

        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_automatic_trading_enabled()
                strategy = connection.execute(
                    "SELECT * FROM strategies WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
                if strategy is None:
                    raise ValueError("UNKNOWN_STRATEGY")
                if strategy["status"] == "RETIRED":
                    raise ValueError("STRATEGY_RETIRED")
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                if authorization is None:
                    raise ValueError("UNKNOWN_AUTHORIZATION")
                if (
                    authorization["strategy_id"] != strategy_id
                    or authorization["owner_key"] != strategy["owner_key"]
                ):
                    raise ValueError("STRATEGY_AUTHORIZATION_MISMATCH")

                existing_usages = connection.execute(
                    """
                    SELECT * FROM authorization_usage
                    WHERE authorization_id = ? AND idempotency_key = ?
                    ORDER BY leg_index, usage_id
                    """,
                    (authorization_id, idempotency_key),
                ).fetchall()
                if existing_usages:
                    existing_by_leg = {row["leg_id"]: row for row in existing_usages}
                    if len(existing_by_leg) != len(normalized_legs):
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                    existing_results: list[dict[str, Any]] = []
                    for leg in normalized_legs:
                        usage = existing_by_leg.get(leg["leg_id"])
                        if usage is None or any(
                            (
                                usage["leg_index"] != leg["leg_index"],
                                usage["leg_type"] != leg["leg_type"],
                                usage["module"] != leg["module"],
                                usage["symbol"] != leg["symbol"],
                                usage["estimated_amount_u"] != leg["estimated_amount_u"],
                                usage["valuation_source"] != leg["valuation_source"],
                                usage["request_fingerprint"]
                                != normalized_request_fingerprint,
                            )
                        ):
                            raise ValueError("IDEMPOTENCY_CONFLICT")
                        order = connection.execute(
                            "SELECT * FROM auto_trade_orders WHERE usage_id = ?",
                            (usage["usage_id"],),
                        ).fetchone()
                        if order is None or any(
                            (
                                order["side"] != leg["side"],
                                order["order_type"] != leg["order_type"],
                                order["quantity"] != leg["quantity"],
                                order["price"] != leg["price"],
                            )
                        ):
                            raise ValueError("IDEMPOTENCY_CONFLICT")
                        existing_results.append(_order_result(order, usage, authorization))
                    connection.execute("COMMIT")
                    return {
                        "ok": True,
                        "status": "EXISTING_SUBMISSION_GROUP",
                        "submission_group_id": existing_usages[0]["submission_group_id"],
                        "replayed": True,
                        "legs": existing_results,
                    }

                _expire_owner_records(
                    connection,
                    owner_key=strategy["owner_key"],
                    now_text=current_text,
                )
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                if authorization["status"] != "ACTIVE":
                    connection.execute("COMMIT")
                    raise ValueError("AUTHORIZATION_NOT_ACTIVE")

                if _has_deprecated_expanded_scope(authorization):
                    connection.execute("COMMIT")
                    raise ValueError("AUTHORIZATION_SCOPE_REAUTHORIZATION_REQUIRED")

                allowed_symbols = set(authorization["symbols_csv"].split(","))
                max_single = Decimal(authorization["max_single_amount_u"])
                max_total = Decimal(authorization["max_total_amount_u"])
                accepted = Decimal(authorization["accepted_amount_u"])
                reserved = Decimal(authorization["reserved_amount_u"])
                group_amount = Decimal("0")
                for leg in normalized_legs:
                    if leg["module"] == "SPOT" and not authorization["spot_allowed"]:
                        raise ValueError("SCOPE_MISMATCH")
                    if leg["module"] == "FUTURES" and not authorization["futures_allowed"]:
                        raise ValueError("SCOPE_MISMATCH")
                    if not authorization["all_symbols"] and leg["symbol"] not in allowed_symbols:
                        raise ValueError("SCOPE_MISMATCH")
                    amount = Decimal(leg["estimated_amount_u"])
                    if amount > max_single:
                        raise ValueError("SINGLE_LIMIT_EXCEEDED")
                    group_amount = _exact_decimal_sum(group_amount, amount)
                if _exact_decimal_sum(accepted, reserved, group_amount) > max_total:
                    raise ValueError("TOTAL_LIMIT_EXCEEDED")

                group_id = "grp_" + uuid.uuid4().hex
                remaining_before_group = _exact_decimal_sum(max_total, -accepted, -reserved)
                consumed_in_group = Decimal("0")
                created_pairs: list[tuple[str, str]] = []
                for leg in normalized_legs:
                    amount = Decimal(leg["estimated_amount_u"])
                    quota_before = _exact_decimal_sum(remaining_before_group, -consumed_in_group)
                    quota_after = _exact_decimal_sum(quota_before, -amount)
                    usage_id = "use_" + uuid.uuid4().hex
                    auto_trade_order_id = "ord_" + uuid.uuid4().hex
                    client_order_id = "wxa_" + uuid.uuid4().hex[:24]
                    connection.execute(
                        """
                        INSERT INTO authorization_usage (
                            usage_id, authorization_id, strategy_id, owner_key,
                            idempotency_key, submission_group_id, leg_id, leg_index, leg_type,
                            module, symbol, estimated_amount_u, quota_before_u, quota_after_u,
                            valuation_source, request_fingerprint, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?)
                        """,
                        (
                            usage_id,
                            authorization_id,
                            strategy_id,
                            strategy["owner_key"],
                            idempotency_key,
                            group_id,
                            leg["leg_id"],
                            leg["leg_index"],
                            leg["leg_type"],
                            leg["module"],
                            leg["symbol"],
                            leg["estimated_amount_u"],
                            _decimal_text(quota_before),
                            _decimal_text(quota_after),
                            leg["valuation_source"],
                            normalized_request_fingerprint,
                            current_text,
                            current_text,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO auto_trade_orders (
                            auto_trade_order_id, usage_id, authorization_id, strategy_id,
                            submission_group_id, leg_id, leg_index, leg_type,
                            client_order_id, weex_order_id, module, symbol,
                            side, order_type, quantity, price,
                            reconciliation_status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 'NOT_REQUESTED', ?, ?)
                        """,
                        (
                            auto_trade_order_id,
                            usage_id,
                            authorization_id,
                            strategy_id,
                            group_id,
                            leg["leg_id"],
                            leg["leg_index"],
                            leg["leg_type"],
                            client_order_id,
                            leg["module"],
                            leg["symbol"],
                            leg["side"],
                            leg["order_type"],
                            leg["quantity"],
                            leg["price"],
                            current_text,
                            current_text,
                        ),
                    )
                    _append_event(
                        connection,
                        strategy_id=strategy_id,
                        authorization_id=authorization_id,
                        usage_id=usage_id,
                        event_type="USAGE_RESERVED",
                        occurred_at=current_text,
                        payload={
                            "status": "RESERVED",
                            "module": leg["module"],
                            "symbol": leg["symbol"],
                            "estimated_amount_u": leg["estimated_amount_u"],
                            "quota_before_u": _decimal_text(quota_before),
                            "quota_after_u": _decimal_text(quota_after),
                            "submission_group_id": group_id,
                            "leg_id": leg["leg_id"],
                            "advisory_alerts": leg["advisory_alerts"],
                            "risk_rule_version": leg["risk_rule_version"],
                            "risk_input_timestamp": leg["risk_input_timestamp"],
                        },
                    )
                    _append_event(
                        connection,
                        strategy_id=strategy_id,
                        authorization_id=authorization_id,
                        usage_id=usage_id,
                        auto_trade_order_id=auto_trade_order_id,
                        event_type="ORDER_RECORDED",
                        occurred_at=current_text,
                        payload={
                            "module": leg["module"],
                            "symbol": leg["symbol"],
                            "side": leg["side"],
                            "order_type": leg["order_type"],
                            "quantity": leg["quantity"],
                            "price": leg["price"],
                            "mapping_status": "CLIENT_ORDER_ID_ONLY",
                            "submission_group_id": group_id,
                            "leg_id": leg["leg_id"],
                        },
                    )
                    created_pairs.append((usage_id, auto_trade_order_id))
                    consumed_in_group = _exact_decimal_sum(consumed_in_group, amount)

                connection.execute(
                    """
                    UPDATE authorizations
                    SET reserved_amount_u = ?, updated_at = ?
                    WHERE authorization_id = ?
                    """,
                    (
                        _decimal_text(_exact_decimal_sum(reserved, group_amount)),
                        current_text,
                        authorization_id,
                    ),
                )
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                results: list[dict[str, Any]] = []
                for usage_id, auto_trade_order_id in created_pairs:
                    usage = connection.execute(
                        "SELECT * FROM authorization_usage WHERE usage_id = ?",
                        (usage_id,),
                    ).fetchone()
                    order = connection.execute(
                        "SELECT * FROM auto_trade_orders WHERE auto_trade_order_id = ?",
                        (auto_trade_order_id,),
                    ).fetchone()
                    results.append(_order_result(order, usage, authorization))
                _validate_business_invariants(connection)
                connection.execute("COMMIT")
                return {
                    "ok": True,
                    "status": "RESERVED",
                    "submission_group_id": group_id,
                    "replayed": False,
                    "legs": results,
                }
        except (ValueError, StateConflictError):
            raise
        except (InvalidOperation, OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to prepare automated-trading submission group") from exc

    def get_submission_group_by_idempotency(
        self,
        *,
        authorization_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        legacy_legs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Read a prior group before volatile checks so replay cannot create a duplicate."""
        authorization_id = _required_text(authorization_id, "authorization_id")
        idempotency_key = _required_text(idempotency_key, "idempotency_key")
        request_fingerprint = _required_sha256(request_fingerprint, "request_fingerprint")
        try:
            with closing(self._connect()) as connection:
                usages = connection.execute(
                    """
                    SELECT * FROM authorization_usage
                    WHERE authorization_id = ? AND idempotency_key = ?
                    ORDER BY leg_index, usage_id
                    """,
                    (authorization_id, idempotency_key),
                ).fetchall()
                if not usages:
                    return None
                stored_fingerprints = {row["request_fingerprint"] for row in usages}
                legacy_replay = stored_fingerprints == {None}
                if None in stored_fingerprints and not legacy_replay:
                    raise StateConflictError("submission group has mixed fingerprint provenance")
                if legacy_replay:
                    if not isinstance(legacy_legs, list) or len(legacy_legs) != len(usages):
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                elif stored_fingerprints != {request_fingerprint}:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                if authorization is None:
                    raise StateConflictError("submission group has no authorization")
                results: list[dict[str, Any]] = []
                for index, usage in enumerate(usages):
                    order = connection.execute(
                        "SELECT * FROM auto_trade_orders WHERE usage_id = ?",
                        (usage["usage_id"],),
                    ).fetchone()
                    if order is None:
                        raise StateConflictError("submission group has no order mapping")
                    if legacy_replay and not _legacy_replay_leg_matches(
                        usage, order, legacy_legs[index]
                    ):
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                    results.append(_order_result(order, usage, authorization))
                return {
                    "ok": True,
                    "status": "EXISTING_SUBMISSION_GROUP",
                    "submission_group_id": usages[0]["submission_group_id"],
                    "replayed": True,
                    "legs": results,
                }
        except (ValueError, StateConflictError):
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to read prior automated-trading submission") from exc

    def settle_usage(
        self,
        *,
        usage_id: str,
        outcome: str,
        error_code: str | None = None,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        usage_id = _required_text(usage_id, "usage_id")
        if outcome not in {"ACCEPTED", "RELEASED", "REVIEW_REQUIRED"}:
            raise ValueError("usage outcome is invalid")
        normalized_error_code = _bounded_event_text(error_code, max_length=128)
        normalized_error_message = _bounded_event_text(error_message, max_length=512)
        current_text = _format_time(_coerce_now(now))
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                usage = connection.execute(
                    "SELECT * FROM authorization_usage WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                if usage is None:
                    raise ValueError("UNKNOWN_USAGE")
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (usage["authorization_id"],),
                ).fetchone()
                if authorization is None:
                    raise StateConflictError("usage has no authorization")
                if usage["status"] == outcome:
                    result = _usage_result(usage, authorization)
                    connection.execute("COMMIT")
                    return result
                if usage["status"] != "RESERVED":
                    raise ValueError("USAGE_STATE_CONFLICT")

                amount = Decimal(usage["estimated_amount_u"])
                accepted = Decimal(authorization["accepted_amount_u"])
                reserved = Decimal(authorization["reserved_amount_u"])
                if reserved < amount:
                    raise StateConflictError("authorization reserved amount is inconsistent")
                new_accepted = (
                    _exact_decimal_sum(accepted, amount) if outcome == "ACCEPTED" else accepted
                )
                new_reserved = (
                    reserved
                    if outcome == "REVIEW_REQUIRED"
                    else _exact_decimal_sum(reserved, -amount)
                )
                connection.execute(
                    """
                    UPDATE authorization_usage
                    SET status = ?, updated_at = ?, resolved_at = ?
                    WHERE usage_id = ? AND status = 'RESERVED'
                    """,
                    (outcome, current_text, current_text, usage_id),
                )
                connection.execute(
                    """
                    UPDATE authorizations
                    SET accepted_amount_u = ?, reserved_amount_u = ?, updated_at = ?
                    WHERE authorization_id = ?
                    """,
                    (
                        _decimal_text(new_accepted),
                        _decimal_text(new_reserved),
                        current_text,
                        authorization["authorization_id"],
                    ),
                )
                linked_order = connection.execute(
                    "SELECT auto_trade_order_id FROM auto_trade_orders WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                event_payload = {
                    "status": outcome,
                    "estimated_amount_u": usage["estimated_amount_u"],
                    "accepted_amount_u": _decimal_text(new_accepted),
                    "reserved_amount_u": _decimal_text(new_reserved),
                }
                if outcome == "RELEASED" and normalized_error_code is not None:
                    event_payload["error_code"] = normalized_error_code
                if outcome == "RELEASED" and normalized_error_message is not None:
                    event_payload["error_message"] = normalized_error_message
                _append_event(
                    connection,
                    strategy_id=usage["strategy_id"],
                    authorization_id=usage["authorization_id"],
                    usage_id=usage_id,
                    auto_trade_order_id=(
                        linked_order["auto_trade_order_id"] if linked_order is not None else None
                    ),
                    event_type=f"USAGE_{outcome}",
                    occurred_at=current_text,
                    payload=event_payload,
                    severity=(
                        "EXCEPTION"
                        if outcome in {"RELEASED", "REVIEW_REQUIRED"}
                        else "NORMAL"
                    ),
                )
                usage = connection.execute(
                    "SELECT * FROM authorization_usage WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization["authorization_id"],),
                ).fetchone()
                result = _usage_result(usage, authorization)
                connection.execute("COMMIT")
                return result
        except (ValueError, StateConflictError):
            raise
        except (InvalidOperation, OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to settle automated-trading usage") from exc

    def _authorization_next_action(
        self,
        connection: sqlite3.Connection,
        authorization: sqlite3.Row,
        *,
        now_text: str,
    ) -> str:
        if authorization["status"] != "ACTIVE":
            return "REQUEST_NEW_AUTHORIZATION"
        if _has_deprecated_expanded_scope(authorization):
            return "REQUEST_NEW_AUTHORIZATION"
        kill_switch_path = self._kill_switch_path()
        if not kill_switch_path.exists():
            return "SUBMIT_ALLOWED"
        kill_switch = _load_automatic_trading_kill_switch(kill_switch_path)
        if kill_switch["reason"] == "SUBMISSION_STATE_UNCERTAIN":
            return "INSPECT_AND_RECONCILE_MANUALLY"
        if (
            authorization["starts_at"] <= kill_switch["enabled_at"]
            or authorization["expires_at"] <= now_text
        ):
            return "REQUEST_NEW_AUTHORIZATION_AFTER_RESTORE"
        unresolved = int(
            connection.execute(
                "SELECT COUNT(*) FROM authorization_usage "
                "WHERE status IN ('RESERVED', 'REVIEW_REQUIRED')"
            ).fetchone()[0]
        )
        if unresolved:
            return "RESOLVE_AUTO_USAGE_AND_ENABLE_AUTO_TRADING_AFTER_RESTORE"
        return "ENABLE_AUTO_TRADING_AFTER_RESTORE"

    def record_order(
        self,
        *,
        usage_id: str,
        weex_order_id: str | None,
        side: str,
        order_type: str,
        quantity: str,
        price: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        usage_id = _required_text(usage_id, "usage_id")
        side = _required_text(side, "side").upper()
        order_type = _required_text(order_type, "order_type").upper()
        quantity_text = _positive_decimal_text(quantity, "quantity")
        price_text = None if price is None else _positive_decimal_text(price, "price")
        normalized_weex_order_id = None
        if weex_order_id is not None:
            normalized_weex_order_id = _required_text(weex_order_id, "weex_order_id")
        current_text = _format_time(_coerce_now(now))
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                usage = connection.execute(
                    "SELECT * FROM authorization_usage WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                if usage is None:
                    raise ValueError("UNKNOWN_USAGE")
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (usage["authorization_id"],),
                ).fetchone()
                if authorization is None:
                    raise StateConflictError("usage has no authorization")
                existing = connection.execute(
                    "SELECT * FROM auto_trade_orders WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["weex_order_id"] not in (None, normalized_weex_order_id)
                        or existing["side"] != side
                        or existing["order_type"] != order_type
                        or existing["quantity"] != quantity_text
                        or existing["price"] != price_text
                    ):
                        raise ValueError("ORDER_MAPPING_CONFLICT")
                    if existing["weex_order_id"] is None and normalized_weex_order_id is not None:
                        connection.execute(
                            """
                            UPDATE auto_trade_orders
                            SET weex_order_id = ?, updated_at = ?
                            WHERE auto_trade_order_id = ? AND weex_order_id IS NULL
                            """,
                            (
                                normalized_weex_order_id,
                                current_text,
                                existing["auto_trade_order_id"],
                            ),
                        )
                        existing = connection.execute(
                            "SELECT * FROM auto_trade_orders WHERE auto_trade_order_id = ?",
                            (existing["auto_trade_order_id"],),
                        ).fetchone()
                    result = _order_result(existing, usage, authorization)
                    connection.execute("COMMIT")
                    return result

                auto_trade_order_id = "ord_" + uuid.uuid4().hex
                client_order_id = "wxa_" + uuid.uuid4().hex[:24]
                connection.execute(
                    """
                    INSERT INTO auto_trade_orders (
                        auto_trade_order_id, usage_id, authorization_id, strategy_id,
                        submission_group_id, leg_id, leg_index, leg_type,
                        client_order_id, weex_order_id, module, symbol,
                        side, order_type, quantity, price,
                        reconciliation_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NOT_REQUESTED', ?, ?)
                    """,
                    (
                        auto_trade_order_id,
                        usage_id,
                        usage["authorization_id"],
                        usage["strategy_id"],
                        usage["submission_group_id"],
                        usage["leg_id"],
                        usage["leg_index"],
                        usage["leg_type"],
                        client_order_id,
                        normalized_weex_order_id,
                        usage["module"],
                        usage["symbol"],
                        side,
                        order_type,
                        quantity_text,
                        price_text,
                        current_text,
                        current_text,
                    ),
                )
                _append_event(
                    connection,
                    strategy_id=usage["strategy_id"],
                    authorization_id=usage["authorization_id"],
                    usage_id=usage_id,
                    auto_trade_order_id=auto_trade_order_id,
                    event_type="ORDER_RECORDED",
                    occurred_at=current_text,
                    payload={
                        "module": usage["module"],
                        "symbol": usage["symbol"],
                        "side": side,
                        "order_type": order_type,
                        "quantity": quantity_text,
                        "price": price_text,
                        "mapping_status": (
                            "WEEX_ORDER_ID_RECORDED"
                            if normalized_weex_order_id is not None
                            else "CLIENT_ORDER_ID_ONLY"
                        ),
                    },
                )
                order = connection.execute(
                    "SELECT * FROM auto_trade_orders WHERE auto_trade_order_id = ?",
                    (auto_trade_order_id,),
                ).fetchone()
                result = _order_result(order, usage, authorization)
                connection.execute("COMMIT")
                return result
        except (ValueError, StateConflictError):
            raise
        except (InvalidOperation, OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to record automated-trading order") from exc

    def resolve_uncertain_usage(
        self,
        *,
        verified_evidence: VerifiedUsageEvidence | None = None,
        usage_id: str | None = None,
        strategy_id: str | None = None,
        outcome: str | None = None,
        evidence_source: str | None = None,
        weex_order_id: str | None = None,
        confirm_live: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Resolve uncertainty only from an internally issued, bound evidence value."""
        if (
            verified_evidence is None
            or type(verified_evidence).__name__ != "VerifiedUsageEvidence"
            or not all(
                hasattr(verified_evidence, field_name)
                for field_name in (
                    "outcome",
                    "evidence_source",
                    "weex_order_id",
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
            )
        ):
            raise ValueError("VERIFIED_EVIDENCE_REQUIRED")
        if any(
            value is not None
            for value in (usage_id, strategy_id, outcome, evidence_source, weex_order_id)
        ):
            raise ValueError("CALLER_EVIDENCE_NOT_ALLOWED")
        usage_id = verified_evidence.usage_id
        if confirm_live is not True:
            raise ValueError("LIVE_CONFIRMATION_REQUIRED")
        evidence = verified_evidence
        outcome = evidence.outcome
        normalized_order_id = evidence.weex_order_id
        current_text = _format_time(_coerce_now(now))

        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                usage = connection.execute(
                    "SELECT * FROM authorization_usage WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                if usage is None:
                    raise ValueError("UNKNOWN_USAGE")
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (usage["authorization_id"],),
                ).fetchone()
                order = connection.execute(
                    "SELECT * FROM auto_trade_orders WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                if authorization is None:
                    raise StateConflictError("uncertain usage ownership chain is incomplete")
                if order is None:
                    raise StateConflictError("uncertain usage has no order mapping")
                binding_values = {
                    "strategy_id": usage["strategy_id"],
                    "authorization_id": usage["authorization_id"],
                    "submission_group_id": usage["submission_group_id"],
                    "leg_id": usage["leg_id"],
                    "client_order_id": order["client_order_id"],
                    "module": order["module"],
                    "symbol": order["symbol"],
                    "side": order["side"],
                    "order_type": order["order_type"],
                    "quantity": order["quantity"],
                    "price": order["price"],
                }
                for field_name, expected in binding_values.items():
                    if getattr(evidence, field_name) != expected:
                        raise ValueError("USAGE_EVIDENCE_BINDING_MISMATCH")
                if outcome == "RELEASED" and order["weex_order_id"] is not None:
                    raise ValueError("ORDER_MAPPING_CONFLICT")
                if usage["status"] == outcome:
                    result = (
                        _order_result(order, usage, authorization)
                        if order is not None
                        else _usage_result(usage, authorization)
                    )
                    connection.execute("COMMIT")
                    return result
                if usage["status"] not in {"RESERVED", "REVIEW_REQUIRED"}:
                    raise ValueError("USAGE_STATE_CONFLICT")
                if normalized_order_id is not None:
                    existing = connection.execute(
                        "SELECT usage_id FROM auto_trade_orders WHERE weex_order_id = ?",
                        (normalized_order_id,),
                    ).fetchone()
                    if existing is not None and existing["usage_id"] != usage_id:
                        raise ValueError("ORDER_MAPPING_CONFLICT")
                    if order["weex_order_id"] not in (None, normalized_order_id):
                        raise ValueError("ORDER_MAPPING_CONFLICT")
                    connection.execute(
                        "UPDATE auto_trade_orders SET weex_order_id = ?, updated_at = ? "
                        "WHERE usage_id = ?",
                        (normalized_order_id, current_text, usage_id),
                    )

                amount = Decimal(usage["estimated_amount_u"])
                accepted = Decimal(authorization["accepted_amount_u"])
                reserved = Decimal(authorization["reserved_amount_u"])
                if reserved < amount:
                    raise StateConflictError("authorization reserved amount is inconsistent")
                new_accepted = (
                    _exact_decimal_sum(accepted, amount) if outcome == "ACCEPTED" else accepted
                )
                new_reserved = _exact_decimal_sum(reserved, -amount)
                connection.execute(
                    """
                    UPDATE authorization_usage
                    SET status = ?, updated_at = ?, resolved_at = ?
                    WHERE usage_id = ? AND status IN ('RESERVED', 'REVIEW_REQUIRED')
                    """,
                    (outcome, current_text, current_text, usage_id),
                )
                connection.execute(
                    """
                    UPDATE authorizations
                    SET accepted_amount_u = ?, reserved_amount_u = ?, updated_at = ?
                    WHERE authorization_id = ?
                    """,
                    (
                        _decimal_text(new_accepted),
                        _decimal_text(new_reserved),
                        current_text,
                        authorization["authorization_id"],
                    ),
                )
                _append_event(
                    connection,
                    strategy_id=usage["strategy_id"],
                    authorization_id=usage["authorization_id"],
                    usage_id=usage_id,
                    auto_trade_order_id=(
                        order["auto_trade_order_id"] if order is not None else None
                    ),
                    event_type="USAGE_UNCERTAINTY_RESOLVED",
                    occurred_at=current_text,
                    payload={
                        "status": outcome,
                        "previous_status": usage["status"],
                        "evidence_source": evidence.evidence_source,
                        "weex_order_id_recorded": normalized_order_id is not None,
                    },
                    severity="EXCEPTION",
                )
                usage = connection.execute(
                    "SELECT * FROM authorization_usage WHERE usage_id = ?", (usage_id,)
                ).fetchone()
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (usage["authorization_id"],),
                ).fetchone()
                order = connection.execute(
                    "SELECT * FROM auto_trade_orders WHERE usage_id = ?", (usage_id,)
                ).fetchone()
                _validate_business_invariants(connection)
                result = (
                    _order_result(order, usage, authorization)
                    if order is not None
                    else _usage_result(usage, authorization)
                )
                connection.execute("COMMIT")
                return result
        except (ValueError, StateConflictError):
            raise
        except (InvalidOperation, OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to resolve uncertain automated-trading usage") from exc

    def reconcile_order(
        self,
        *,
        auto_trade_order_id: str,
        reconciliation_status: str,
        exchange_status: str | None,
        executed_quantity: str | None,
        executed_quote_amount: str | None,
        fee_amount: str | None,
        fee_asset: str | None,
        reconciliation_source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        auto_trade_order_id = _required_text(auto_trade_order_id, "auto_trade_order_id")
        if reconciliation_status not in {"COMPLETE", "PARTIAL", "UNAVAILABLE"}:
            raise ValueError("reconciliation_status is invalid")
        normalized_exchange_status = (
            None if exchange_status is None else _required_text(exchange_status, "exchange_status")
        )
        executed_quantity_text = _optional_nonnegative_decimal_text(
            executed_quantity, "executed_quantity"
        )
        executed_quote_text = _optional_nonnegative_decimal_text(
            executed_quote_amount, "executed_quote_amount"
        )
        fee_amount_text = _optional_nonnegative_decimal_text(fee_amount, "fee_amount")
        normalized_fee_asset = None if fee_asset is None else _required_text(fee_asset, "fee_asset").upper()
        reconciliation_source = _required_text(reconciliation_source, "reconciliation_source")
        if reconciliation_status == "COMPLETE" and any(
            item is None
            for item in (
                normalized_exchange_status,
                executed_quantity_text,
                executed_quote_text,
                fee_amount_text,
                normalized_fee_asset,
            )
        ):
            raise ValueError("complete reconciliation requires complete verified facts")
        current_text = _format_time(_coerce_now(now))

        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                order = connection.execute(
                    "SELECT * FROM auto_trade_orders WHERE auto_trade_order_id = ?",
                    (auto_trade_order_id,),
                ).fetchone()
                if order is None:
                    raise ValueError("UNKNOWN_AUTO_TRADE_ORDER")
                usage = connection.execute(
                    "SELECT * FROM authorization_usage WHERE usage_id = ?",
                    (order["usage_id"],),
                ).fetchone()
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (order["authorization_id"],),
                ).fetchone()
                if usage is None or authorization is None:
                    raise StateConflictError("order ownership chain is incomplete")
                if usage["status"] != "ACCEPTED":
                    raise ValueError("ORDER_NOT_ACCEPTED")
                connection.execute(
                    """
                    UPDATE auto_trade_orders
                    SET exchange_status = COALESCE(?, exchange_status),
                        executed_quantity = COALESCE(?, executed_quantity),
                        executed_quote_amount = COALESCE(?, executed_quote_amount),
                        fee_amount = COALESCE(?, fee_amount),
                        fee_asset = COALESCE(?, fee_asset),
                        reconciliation_status = ?, reconciliation_source = ?,
                        reconciled_at = ?, updated_at = ?
                    WHERE auto_trade_order_id = ?
                    """,
                    (
                        normalized_exchange_status,
                        executed_quantity_text,
                        executed_quote_text,
                        fee_amount_text,
                        normalized_fee_asset,
                        reconciliation_status,
                        reconciliation_source,
                        current_text,
                        current_text,
                        auto_trade_order_id,
                    ),
                )
                _append_event(
                    connection,
                    strategy_id=usage["strategy_id"],
                    authorization_id=usage["authorization_id"],
                    usage_id=usage["usage_id"],
                    auto_trade_order_id=auto_trade_order_id,
                    event_type=f"ORDER_RECONCILIATION_{reconciliation_status}",
                    occurred_at=current_text,
                    payload={
                        "reconciliation_status": reconciliation_status,
                        "exchange_status": normalized_exchange_status,
                        "executed_quantity": executed_quantity_text,
                        "executed_quote_amount": executed_quote_text,
                        "fee_amount": fee_amount_text,
                        "fee_asset": normalized_fee_asset,
                    },
                    severity=(
                        "NORMAL" if reconciliation_status == "COMPLETE" else "EXCEPTION"
                    ),
                )
                order = connection.execute(
                    "SELECT * FROM auto_trade_orders WHERE auto_trade_order_id = ?",
                    (auto_trade_order_id,),
                ).fetchone()
                result = _order_result(order, usage, authorization)
                connection.execute("COMMIT")
                return result
        except (ValueError, StateConflictError):
            raise
        except (InvalidOperation, OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to reconcile automated-trading order") from exc

    def get_order(self, *, auto_trade_order_id: str) -> dict[str, Any]:
        auto_trade_order_id = _required_text(auto_trade_order_id, "auto_trade_order_id")
        try:
            with closing(self._connect()) as connection:
                order = connection.execute(
                    "SELECT * FROM auto_trade_orders WHERE auto_trade_order_id = ?",
                    (auto_trade_order_id,),
                ).fetchone()
                if order is None:
                    raise ValueError("UNKNOWN_AUTO_TRADE_ORDER")
                usage = connection.execute(
                    "SELECT * FROM authorization_usage WHERE usage_id = ?",
                    (order["usage_id"],),
                ).fetchone()
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (order["authorization_id"],),
                ).fetchone()
        except ValueError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to read automated-trading order") from exc
        if usage is None or authorization is None:
            raise StateConflictError("order ownership chain is incomplete")
        return _order_result(order, usage, authorization)

    def get_usage(self, *, usage_id: str) -> dict[str, Any]:
        """Read a usage row for diagnostics without exposing mutable state access."""
        usage_id = _required_text(usage_id, "usage_id")
        try:
            with closing(self._connect()) as connection:
                usage = connection.execute(
                    "SELECT * FROM authorization_usage WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                if usage is None:
                    raise ValueError("UNKNOWN_USAGE")
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (usage["authorization_id"],),
                ).fetchone()
        except ValueError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to read automated-trading usage") from exc
        if authorization is None:
            raise StateConflictError("usage ownership chain is incomplete")
        return _usage_result(usage, authorization)

    def get_order_for_usage(self, *, usage_id: str) -> dict[str, Any]:
        """Read the canonical local order mapping for a usage record."""
        usage_id = _required_text(usage_id, "usage_id")
        try:
            with closing(self._connect()) as connection:
                order = connection.execute(
                    "SELECT * FROM auto_trade_orders WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                if order is None:
                    raise ValueError("UNKNOWN_AUTO_TRADE_ORDER")
                usage = connection.execute(
                    "SELECT * FROM authorization_usage WHERE usage_id = ?",
                    (usage_id,),
                ).fetchone()
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (order["authorization_id"],),
                ).fetchone()
        except ValueError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to read automated-trading order mapping") from exc
        if usage is None or authorization is None:
            raise StateConflictError("order ownership chain is incomplete")
        return _order_result(order, usage, authorization)

    def record_manual_fallback(
        self,
        *,
        strategy_id: str,
        authorization_id: str,
        operation_key: str,
        idempotency_key: str,
        error_code: str,
        blocking_reasons: list[Any],
        advisory_alerts: list[Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist a sanitized pre-submit hard-block event before manual handoff."""
        strategy_id = _required_text(strategy_id, "strategy_id")
        authorization_id = _required_text(authorization_id, "authorization_id")
        operation_key = _required_text(operation_key, "operation_key")
        idempotency_key = _required_text(idempotency_key, "idempotency_key")
        error_code = _required_text(error_code, "error_code")
        current_text = _format_time(_coerce_now(now))
        normalized_reasons = _normalize_blocking_reasons(blocking_reasons)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                if authorization is None:
                    raise ValueError("UNKNOWN_AUTHORIZATION")
                if authorization["strategy_id"] != strategy_id:
                    raise ValueError("STRATEGY_AUTHORIZATION_MISMATCH")
                event_id = _append_event(
                    connection,
                    strategy_id=strategy_id,
                    authorization_id=authorization_id,
                    event_type="AUTO_TRADE_MANUAL_FALLBACK",
                    occurred_at=current_text,
                    payload={
                        "operation_key": operation_key,
                        "idempotency_fingerprint": hashlib.sha256(
                            idempotency_key.encode("utf-8")
                        ).hexdigest(),
                        "error_code": error_code,
                        "blocking_reasons": normalized_reasons,
                        "advisory_alerts": _normalize_advisory_alerts(advisory_alerts),
                        "next_action": "PREVIEW_AND_CONFIRM_ORDER_MANUALLY",
                    },
                    severity="EXCEPTION",
                )
                _validate_business_invariants(connection)
                connection.execute("COMMIT")
        except (ValueError, StateConflictError):
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to record automated-trading manual fallback") from exc
        return {"ok": True, "event_id": event_id, "status": "MANUAL_FALLBACK_RECORDED"}

    def record_submission_state_uncertain(
        self,
        *,
        strategy_id: str,
        authorization_id: str,
        operation_key: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Best-effort audit for a submission whose local final state could not be persisted."""
        strategy_id = _required_text(strategy_id, "strategy_id")
        authorization_id = _required_text(authorization_id, "authorization_id")
        operation_key = _required_text(operation_key, "operation_key")
        idempotency_key = _required_text(idempotency_key, "idempotency_key")
        current_text = _format_time(_coerce_now(now))
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                authorization = connection.execute(
                    "SELECT * FROM authorizations WHERE authorization_id = ?",
                    (authorization_id,),
                ).fetchone()
                if authorization is None:
                    raise ValueError("UNKNOWN_AUTHORIZATION")
                if authorization["strategy_id"] != strategy_id:
                    raise ValueError("STRATEGY_AUTHORIZATION_MISMATCH")
                event_id = _append_event(
                    connection,
                    strategy_id=strategy_id,
                    authorization_id=authorization_id,
                    event_type="SUBMISSION_STATE_UNCERTAIN",
                    occurred_at=current_text,
                    payload={
                        "operation_key": operation_key,
                        "idempotency_fingerprint": hashlib.sha256(
                            idempotency_key.encode("utf-8")
                        ).hexdigest(),
                        "error_code": "SUBMISSION_STATE_UNCERTAIN",
                        "next_action": "INSPECT_AND_RECONCILE_MANUALLY",
                    },
                    severity="EXCEPTION",
                )
                _validate_business_invariants(connection)
                connection.execute("COMMIT")
        except (ValueError, StateConflictError):
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to record uncertain submission state") from exc
        return {"ok": True, "event_id": event_id, "status": "SUBMISSION_UNCERTAINTY_RECORDED"}

    def accepted_summary_notification_target(
        self,
        *,
        strategy_id: str,
    ) -> dict[str, Any]:
        strategy = self.get_strategy(strategy_id=strategy_id)
        try:
            with closing(self._connect()) as connection:
                accepted_event = connection.execute(
                    """
                    SELECT occurred_at
                    FROM authorization_events
                    WHERE strategy_id = ? AND event_type = 'USAGE_ACCEPTED'
                    ORDER BY occurred_at DESC, event_id DESC
                    LIMIT 1
                    """,
                    (strategy_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StateConflictError(
                "unable to resolve accepted-summary notification target"
            ) from exc
        if accepted_event is None:
            raise ValueError("ACCEPTED_USAGE_NOT_FOUND")
        window_start = _parse_time(accepted_event["occurred_at"]).replace(
            second=0,
            microsecond=0,
        )
        owner_key = _owner_key(
            distribution=strategy["distribution"],
            profile_id=strategy["profile_id"],
            trading_mode=strategy["trading_mode"],
            strategy_id=strategy["strategy_id"],
        )
        return {
            "notification_key": _accepted_summary_notification_key(
                owner_key=owner_key,
                window_start=window_start,
            ),
            "not_before": window_start + timedelta(seconds=60),
        }

    def claim_notifications(
        self,
        *,
        now: datetime | None = None,
        notification_key: str | None = None,
    ) -> list[dict[str, Any]]:
        current = _coerce_now(now)
        current_text = _format_time(current)
        closed_before = current.replace(second=0, microsecond=0)
        closed_before_text = _format_time(closed_before)
        selected_key = (
            None
            if notification_key is None
            else _required_text(notification_key, "notification_key")
        )
        claims: list[dict[str, Any]] = []
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing_keys = {
                    row[0]
                    for row in connection.execute(
                        "SELECT notification_key FROM authorization_events WHERE notification_key IS NOT NULL"
                    ).fetchall()
                }
                accepted_rows = connection.execute(
                    """
                    SELECT e.*, u.owner_key, u.module, u.symbol, u.estimated_amount_u,
                           s.strategy_name, a.max_total_amount_u,
                           a.accepted_amount_u, a.reserved_amount_u
                    FROM authorization_events e
                    JOIN authorization_usage u ON u.usage_id = e.usage_id
                    JOIN strategies s ON s.strategy_id = e.strategy_id
                    JOIN authorizations a ON a.authorization_id = e.authorization_id
                    WHERE e.event_type = 'USAGE_ACCEPTED' AND e.occurred_at < ?
                    ORDER BY e.occurred_at, e.event_id
                    """,
                    (closed_before_text,),
                ).fetchall()
                grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
                for row in accepted_rows:
                    bucket = _format_time(_parse_time(row["occurred_at"]).replace(second=0, microsecond=0))
                    grouped.setdefault((row["owner_key"], bucket), []).append(row)
                for (owner_key, bucket), rows in grouped.items():
                    summary_key = _accepted_summary_notification_key(
                        owner_key=owner_key,
                        window_start=_parse_time(bucket),
                    )
                    if summary_key in existing_keys or (
                        selected_key is not None and summary_key != selected_key
                    ):
                        continue
                    estimated_total = _exact_decimal_sum(
                        *(Decimal(row["estimated_amount_u"]) for row in rows)
                    )
                    latest = rows[-1]
                    remaining = _exact_decimal_sum(
                        Decimal(latest["max_total_amount_u"]),
                        -Decimal(latest["accepted_amount_u"]),
                        -Decimal(latest["reserved_amount_u"]),
                    )
                    window_start = _parse_time(bucket)
                    claim = {
                        "kind": "ACCEPTED_SUMMARY",
                        "notification_key": summary_key,
                        "strategy_name": latest["strategy_name"],
                        "strategy_id": _mask_identifier(latest["strategy_id"]),
                        "window_start": bucket,
                        "window_end": _format_time(window_start + timedelta(seconds=60)),
                        "order_count": len(rows),
                        "modules": sorted({row["module"] for row in rows}),
                        "symbols": sorted({row["symbol"] for row in rows}),
                        "estimated_amount_u": _decimal_text(estimated_total),
                        "remaining_amount_u": _decimal_text(remaining),
                        "event_cursor": latest["event_id"],
                    }
                    claim_event_id = _append_event(
                        connection,
                        strategy_id=latest["strategy_id"],
                        event_type="NOTIFICATION_CLAIMED",
                        occurred_at=current_text,
                        payload=claim,
                        notification_key=summary_key,
                        notification_status="CLAIMED",
                    )
                    claims.append({**claim, "claim_event_id": claim_event_id})
                    existing_keys.add(summary_key)

                exception_rows = connection.execute(
                    """
                    SELECT e.*, s.strategy_name
                    FROM authorization_events e
                    JOIN strategies s ON s.strategy_id = e.strategy_id
                    WHERE e.severity = 'EXCEPTION'
                      AND e.event_type NOT LIKE 'NOTIFICATION_%'
                    ORDER BY e.occurred_at, e.event_id
                    """
                ).fetchall()
                for row in exception_rows:
                    notification_key = f"event:{row['event_id']}"
                    if notification_key in existing_keys or (
                        selected_key is not None and notification_key != selected_key
                    ):
                        continue
                    claim = {
                        "kind": "EXCEPTION",
                        "notification_key": notification_key,
                        "strategy_name": row["strategy_name"],
                        "strategy_id": _mask_identifier(row["strategy_id"]),
                        "event_type": row["event_type"],
                        "event_id": row["event_id"],
                        "occurred_at": row["occurred_at"],
                        "next_action": "INSPECT_EVENT_AND_RECONCILE_MANUALLY",
                    }
                    claim_event_id = _append_event(
                        connection,
                        strategy_id=row["strategy_id"],
                        request_id=row["request_id"],
                        authorization_id=row["authorization_id"],
                        usage_id=row["usage_id"],
                        auto_trade_order_id=row["auto_trade_order_id"],
                        event_type="NOTIFICATION_CLAIMED",
                        occurred_at=current_text,
                        payload=claim,
                        notification_key=notification_key,
                        notification_status="CLAIMED",
                    )
                    claims.append({**claim, "claim_event_id": claim_event_id})
                    existing_keys.add(notification_key)
                connection.execute("COMMIT")
        except (InvalidOperation, OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to claim automated-trading notifications") from exc
        return claims

    def complete_notification(
        self,
        *,
        notification_key: str,
        outcome: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        notification_key = _required_text(notification_key, "notification_key")
        if outcome not in {"DELIVERED", "FAILED", "UNKNOWN"}:
            raise ValueError("notification outcome is invalid")
        current_text = _format_time(_coerce_now(now))
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                claim = connection.execute(
                    """
                    SELECT * FROM authorization_events
                    WHERE notification_key = ? AND event_type = 'NOTIFICATION_CLAIMED'
                    """,
                    (notification_key,),
                ).fetchone()
                if claim is None:
                    raise ValueError("UNKNOWN_NOTIFICATION_CLAIM")
                if claim["notification_status"] == outcome:
                    connection.execute("COMMIT")
                    return {
                        "ok": True,
                        "notification_key": notification_key,
                        "status": outcome,
                    }
                if claim["notification_status"] != "CLAIMED":
                    raise ValueError("NOTIFICATION_ALREADY_COMPLETED")
                connection.execute(
                    """
                    UPDATE authorization_events
                    SET notification_status = ?
                    WHERE event_id = ? AND notification_status = 'CLAIMED'
                    """,
                    (outcome, claim["event_id"]),
                )
                result_key = "result:" + hashlib.sha256(notification_key.encode("utf-8")).hexdigest()
                _append_event(
                    connection,
                    strategy_id=claim["strategy_id"],
                    request_id=claim["request_id"],
                    authorization_id=claim["authorization_id"],
                    usage_id=claim["usage_id"],
                    auto_trade_order_id=claim["auto_trade_order_id"],
                    event_type=f"NOTIFICATION_{outcome}",
                    occurred_at=current_text,
                    payload={"status": outcome, "claim_event_id": claim["event_id"]},
                    severity="NORMAL",
                    notification_key=result_key,
                    notification_status=outcome,
                )
                connection.execute("COMMIT")
        except ValueError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to complete automated-trading notification") from exc
        return {"ok": True, "notification_key": notification_key, "status": outcome}

    def list_events(self, *, strategy_id: str | None = None) -> list[dict[str, Any]]:
        parameters: tuple[str, ...] = ()
        where_clause = ""
        if strategy_id is not None:
            strategy_id = _required_text(strategy_id, "strategy_id")
            where_clause = "WHERE strategy_id = ?"
            parameters = (strategy_id,)
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"""
                    SELECT * FROM authorization_events
                    {where_clause}
                    ORDER BY rowid ASC
                    """,
                    parameters,
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            raise StateConflictError("unable to list automated-trading events") from exc

        events: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise StateConflictError("automated-trading event payload is invalid") from exc
            if not isinstance(payload, dict) or payload.get("event_schema_version") != row["event_schema_version"]:
                raise StateConflictError("automated-trading event schema version is inconsistent")
            events.append(
                {
                    "event_id": row["event_id"],
                    "strategy_id": row["strategy_id"],
                    "request_id": row["request_id"],
                    "authorization_id": row["authorization_id"],
                    "usage_id": row["usage_id"],
                    "auto_trade_order_id": row["auto_trade_order_id"],
                    "event_type": row["event_type"],
                    "event_schema_version": row["event_schema_version"],
                    "severity": row["severity"],
                    "occurred_at": row["occurred_at"],
                    "payload": payload,
                    "notification_key": row["notification_key"],
                    "notification_status": row["notification_status"],
                }
            )
        return events


def _ensure_owner_only_directory(path: Path) -> None:
    try:
        created = False
        try:
            path.mkdir(parents=True, exist_ok=False, mode=0o700)
            created = True
        except FileExistsError:
            pass
        if created:
            os.chmod(path, 0o700)
        metadata = path.lstat()
    except OSError as exc:
        raise StateConflictError("snapshot directory is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise StateConflictError("snapshot directory must be a real directory")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise StateConflictError("snapshot directory permissions are not owner-only")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise StateConflictError("snapshot directory is not owned by the current user")


def _require_owner_only_regular_file(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise StateConflictError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise StateConflictError(f"{label} must be a regular file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise StateConflictError(f"{label} permissions are not owner-only")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise StateConflictError(f"{label} is not owned by the current user")


@contextmanager
def _exclusive_file_lock(path: Path, *, timeout_ms: int = BUSY_TIMEOUT_MS):
    """Acquire a bounded, re-entrant process/inter-process exclusive lock."""
    lock_path = Path(path)
    lock_key = str(lock_path.absolute())
    held = getattr(_FILE_LOCK_STATE, "held", None)
    if held is None:
        held = {}
        _FILE_LOCK_STATE.held = held
    if lock_key in held:
        held[lock_key] += 1
        try:
            yield
        finally:
            held[lock_key] -= 1
            if held[lock_key] == 0:
                del held[lock_key]
        return

    timeout_seconds = max(int(timeout_ms), 0) / 1_000
    if not _SNAPSHOT_PROCESS_LOCK.acquire(timeout=timeout_seconds):
        raise StateConflictError("automated-trading operation lock timed out")
    descriptor = None
    lock_kind = "none"
    acquired = False
    try:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        os.chmod(lock_path, 0o600)
        deadline = time.monotonic() + timeout_seconds
        try:
            import fcntl

            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    lock_kind = "fcntl"
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise
                    if time.monotonic() >= deadline:
                        raise StateConflictError(
                            "automated-trading operation lock timed out"
                        ) from exc
                    time.sleep(min(0.01, max(deadline - time.monotonic(), 0)))
        except ImportError:
            try:
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                    os.lseek(descriptor, 0, os.SEEK_SET)
                while True:
                    try:
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                        lock_kind = "msvcrt"
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise StateConflictError(
                                "automated-trading operation lock timed out"
                            ) from exc
                        time.sleep(min(0.01, max(deadline - time.monotonic(), 0)))
            except ImportError:
                lock_kind = "process"
        held[lock_key] = 1
        acquired = True
        yield
    finally:
        try:
            if descriptor is not None and lock_kind == "fcntl":
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
            elif descriptor is not None and lock_kind == "msvcrt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            if acquired:
                held.pop(lock_key, None)
            if descriptor is not None:
                os.close(descriptor)
            _SNAPSHOT_PROCESS_LOCK.release()


def _load_snapshot_index(
    snapshots_dir: Path,
    *,
    verify_contents: bool = True,
) -> dict[str, Any]:
    index_path = snapshots_dir / "index.json"
    if not index_path.exists():
        return {"schema_version": SNAPSHOT_INDEX_VERSION, "snapshots": []}
    _require_owner_only_regular_file(index_path, label="snapshot index")
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("snapshot index is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "snapshots"}:
        raise StateConflictError("snapshot index shape is invalid")
    if payload["schema_version"] != SNAPSHOT_INDEX_VERSION or not isinstance(
        payload["snapshots"], list
    ):
        raise StateConflictError("snapshot index version is unsupported")
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    records: list[dict[str, str]] = []
    for raw_record in payload["snapshots"]:
        if not isinstance(raw_record, dict) or set(raw_record) != {
            "snapshot_id",
            "created_at",
            "relative_path",
            "database_schema_version",
            "size_bytes",
            "sha256",
        }:
            raise StateConflictError("snapshot index record shape is invalid")
        snapshot_id = raw_record["snapshot_id"]
        created_at = raw_record["created_at"]
        relative_path = raw_record["relative_path"]
        database_schema_version = raw_record["database_schema_version"]
        size_bytes = raw_record["size_bytes"]
        sha256 = raw_record["sha256"]
        if not isinstance(snapshot_id, str) or not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
            raise StateConflictError("snapshot index contains an invalid snapshot ID")
        if not isinstance(created_at, str):
            raise StateConflictError("snapshot index contains an invalid timestamp")
        _parse_time(created_at)
        expected_relative_path = f"auto-trade-{snapshot_id}.sqlite3"
        if relative_path != expected_relative_path:
            raise StateConflictError("snapshot index contains an unsafe relative path")
        if not _has_complete_migration_path(database_schema_version):
            raise StateConflictError("snapshot index contains an unsupported database version")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
            raise StateConflictError("snapshot index contains an invalid file size")
        if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise StateConflictError("snapshot index contains an invalid checksum")
        if snapshot_id in seen_ids or relative_path in seen_paths:
            raise StateConflictError("snapshot index contains duplicate records")
        snapshot_path = snapshots_dir / relative_path
        _require_owner_only_regular_file(snapshot_path, label="managed snapshot")
        if verify_contents:
            _verify_snapshot_record(snapshot_path, raw_record)
        seen_ids.add(snapshot_id)
        seen_paths.add(relative_path)
        records.append(
            {
                "snapshot_id": snapshot_id,
                "created_at": created_at,
                "relative_path": relative_path,
                "database_schema_version": database_schema_version,
                "size_bytes": size_bytes,
                "sha256": sha256,
            }
        )
    expected_order = sorted(records, key=lambda item: (item["created_at"], item["snapshot_id"]))
    if records != expected_order:
        raise StateConflictError("snapshot index order is invalid")
    return {"schema_version": SNAPSHOT_INDEX_VERSION, "snapshots": records}


def _has_complete_migration_path(version: Any) -> bool:
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or version > CURRENT_SCHEMA_VERSION
    ):
        return False
    while version < CURRENT_SCHEMA_VERSION:
        if version not in MIGRATION_REGISTRY:
            return False
        version += 1
    return True


def _write_snapshot_index(snapshots_dir: Path, records: list[dict[str, str]]) -> None:
    index_path = snapshots_dir / "index.json"
    temp_path = snapshots_dir / f".index-{uuid.uuid4().hex}.tmp"
    payload = json.dumps(
        {"schema_version": SNAPSHOT_INDEX_VERSION, "snapshots": records},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("snapshot index write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, index_path)
        os.chmod(index_path, 0o600)
        _fsync_directory(snapshots_dir)
    except Exception:
        _unlink_if_exists(temp_path)
        raise


def _write_owner_only_json(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.parent / f".{path.name}-{uuid.uuid4().hex}.tmp"
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("owner-only JSON write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    except Exception:
        _unlink_if_exists(temp_path)
        raise


def _load_automatic_trading_kill_switch(path: Path) -> dict[str, str]:
    _require_owner_only_regular_file(path, label="automatic-trading kill switch")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("automatic-trading kill switch is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "status",
        "reason",
        "enabled_at",
    }:
        raise StateConflictError("automatic-trading kill switch shape is invalid")
    if (
        payload["schema_version"] != 1
        or payload["status"] != "DISABLED"
        or payload["reason"] not in {"STATE_RESTORE", "SUBMISSION_STATE_UNCERTAIN"}
        or not isinstance(payload["enabled_at"], str)
    ):
        raise StateConflictError("automatic-trading kill switch content is invalid")
    try:
        _parse_time(payload["enabled_at"])
    except (TypeError, ValueError) as exc:
        raise StateConflictError("automatic-trading kill switch timestamp is invalid") from exc
    return payload


def _fsync_regular_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256_regular_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_snapshot_record(path: Path, record: dict[str, Any]) -> None:
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        raise StateConflictError("managed snapshot is unavailable") from exc
    if size_bytes != record.get("size_bytes"):
        raise StateConflictError("managed snapshot size does not match its index record")
    if _sha256_regular_file(path) != record.get("sha256"):
        raise StateConflictError("managed snapshot checksum does not match its index record")
    try:
        snapshot_uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(snapshot_uri, uri=True)) as connection:
            database_schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
    except (OSError, sqlite3.Error) as exc:
        raise StateConflictError("managed snapshot database header is invalid") from exc
    if database_schema_version != record.get("database_schema_version"):
        raise StateConflictError(
            "managed snapshot database version does not match its index record"
        )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _bounded_event_text(value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(_required_text(value, "event text").split())
    return normalized[:max_length]


def _required_sha256(value: str, field: str) -> str:
    normalized = _required_text(value, field).lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return normalized


def _normalize_advisory_alerts(value: Any) -> list[dict[str, str]]:
    """Keep only bounded, display-safe advisory fields in the audit ledger."""
    if not isinstance(value, list):
        return []
    allowed_fields = ("type", "level", "code", "reason", "suggestion")
    normalized: list[dict[str, str]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        alert: dict[str, str] = {}
        for field in allowed_fields:
            raw = item.get(field)
            if isinstance(raw, str) and raw.strip():
                alert[field] = raw.strip()[:256]
        if alert:
            normalized.append(alert)
    return normalized


def _normalize_blocking_reasons(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        reason: dict[str, str] = {}
        for field in ("code", "message"):
            raw = item.get(field)
            if isinstance(raw, str) and raw.strip():
                reason[field] = raw.strip()[:256]
        if reason:
            normalized.append(reason)
    return normalized


def _require_owner_only(path: Path, *, expected_mode: int, label: str) -> None:
    if os.name == "nt":
        raise StateConflictError(
            "Windows owner-only DACL enforcement is not supported for automated-trading state"
        )
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise StateConflictError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise StateConflictError(f"{label} must not be a symbolic link")
    if not stat.S_ISDIR(metadata.st_mode) and label == "state directory":
        raise StateConflictError("state directory is not a directory")
    if not stat.S_ISREG(metadata.st_mode) and label == "state database":
        raise StateConflictError("state database is not a regular file")
    if stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise StateConflictError(f"{label} permissions are not owner-only")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise StateConflictError(f"{label} is not owned by the current user")


def _validate_connection(
    connection: sqlite3.Connection,
    *,
    full: bool,
    expected_version: int = CURRENT_SCHEMA_VERSION,
    require_foreign_keys: bool = True,
) -> None:
    if require_foreign_keys and int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise StateConflictError("SQLite foreign_keys pragma is disabled")
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != expected_version:
        raise StateConflictError(f"unsupported automated-trading state schema version: {version}")
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if tables != EXPECTED_TABLES:
        raise StateConflictError("automated-trading state table set is not supported")

    pragma_name = "integrity_check" if full else "quick_check"
    integrity_rows = connection.execute(f"PRAGMA {pragma_name}").fetchall()
    if not integrity_rows or any(row[0] != "ok" for row in integrity_rows):
        raise StateConflictError(f"SQLite {pragma_name} failed")
    foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_rows:
        raise StateConflictError("SQLite foreign_key_check failed")
    _validate_business_invariants(connection, schema_version=version)


def _validate_business_invariants(
    connection: sqlite3.Connection,
    *,
    schema_version: int | None = None,
) -> None:
    if schema_version is None:
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    strategies = {
        row["strategy_id"]: row
        for row in connection.execute("SELECT * FROM strategies").fetchall()
    }
    for strategy in strategies.values():
        if strategy["owner_key"] != _owner_key(
            distribution=strategy["distribution"],
            profile_id=strategy["profile_id"],
            trading_mode=strategy["trading_mode"],
            strategy_id=strategy["strategy_id"],
        ):
            raise StateConflictError("strategy owner key invariant failed")

    requests = {
        row["request_id"]: row
        for row in connection.execute("SELECT * FROM authorization_requests").fetchall()
    }
    for request in requests.values():
        strategy = strategies.get(request["strategy_id"])
        if strategy is None or request["owner_key"] != strategy["owner_key"]:
            raise StateConflictError("authorization request owner invariant failed")

    authorization_totals: dict[str, tuple[Decimal, Decimal]] = {}
    authorizations = {
        row["authorization_id"]: row
        for row in connection.execute("SELECT * FROM authorizations").fetchall()
    }
    for authorization in authorizations.values():
        if authorization["strategy_id"] not in strategies:
            raise StateConflictError("authorization strategy invariant failed")
        request = requests.get(authorization["request_id"])
        if request is None or any(
            (
                request["strategy_id"] != authorization["strategy_id"],
                request["owner_key"] != authorization["owner_key"],
                request["scope_signature"] != authorization["scope_signature"],
                request["spot_allowed"] != authorization["spot_allowed"],
                request["futures_allowed"] != authorization["futures_allowed"],
                request["symbols_csv"] != authorization["symbols_csv"],
                request["all_symbols"] != authorization["all_symbols"],
                request["max_single_amount_u"] != authorization["max_single_amount_u"],
                request["max_total_amount_u"] != authorization["max_total_amount_u"],
                request["valid_seconds"] != authorization["valid_seconds"],
            )
        ):
            raise StateConflictError("authorization request linkage invariant failed")
        if schema_version >= 4:
            if any(
                (
                    request["allowed_sides_csv"]
                    != authorization["allowed_sides_csv"],
                    request["allowed_order_types_csv"]
                    != authorization["allowed_order_types_csv"],
                    request["min_single_amount_u"]
                    != authorization["min_single_amount_u"],
                    request["max_order_count"] != authorization["max_order_count"],
                )
            ):
                raise StateConflictError(
                    "authorization request strategy scope linkage invariant failed"
                )
        max_single = _stored_decimal(authorization["max_single_amount_u"], positive=True)
        max_total = _stored_decimal(authorization["max_total_amount_u"], positive=True)
        accepted = _stored_decimal(authorization["accepted_amount_u"], positive=False)
        reserved = _stored_decimal(authorization["reserved_amount_u"], positive=False)
        if max_total < max_single or _exact_decimal_sum(accepted, reserved) > max_total:
            raise StateConflictError("authorization quota invariant failed")
        authorization_totals[authorization["authorization_id"]] = (accepted, reserved)

    usage_totals: dict[str, list[Decimal]] = {}
    usages = {
        row["usage_id"]: row
        for row in connection.execute("SELECT * FROM authorization_usage").fetchall()
    }
    for usage in usages.values():
        authorization = authorizations.get(usage["authorization_id"])
        if authorization is None or any(
            (
                usage["strategy_id"] != authorization["strategy_id"],
                usage["owner_key"] != authorization["owner_key"],
            )
        ):
            raise StateConflictError("usage authorization linkage invariant failed")
        request_fingerprint = usage["request_fingerprint"]
        if request_fingerprint is not None and (
            not isinstance(request_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", request_fingerprint) is None
        ):
            raise StateConflictError("usage request fingerprint invariant failed")
        amount = _stored_decimal(usage["estimated_amount_u"], positive=True)
        before = _stored_decimal(usage["quota_before_u"], positive=False)
        after = _stored_decimal(usage["quota_after_u"], positive=False)
        if _exact_decimal_sum(before, -amount) != after:
            raise StateConflictError("usage quota snapshot invariant failed")
        totals = usage_totals.setdefault(usage["authorization_id"], [Decimal("0"), Decimal("0")])
        if usage["status"] == "ACCEPTED":
            totals[0] = _exact_decimal_sum(totals[0], amount)
        elif usage["status"] in {"RESERVED", "REVIEW_REQUIRED"}:
            totals[1] = _exact_decimal_sum(totals[1], amount)

    for authorization_id, (accepted, reserved) in authorization_totals.items():
        observed = usage_totals.get(authorization_id, [Decimal("0"), Decimal("0")])
        if observed[0] != accepted or observed[1] != reserved:
            raise StateConflictError("authorization usage aggregate invariant failed")

    orders = {
        row["auto_trade_order_id"]: row
        for row in connection.execute("SELECT * FROM auto_trade_orders").fetchall()
    }
    for order in orders.values():
        usage = usages.get(order["usage_id"])
        if usage is None or any(
            (
                order["authorization_id"] != usage["authorization_id"],
                order["strategy_id"] != usage["strategy_id"],
                order["submission_group_id"] != usage["submission_group_id"],
                order["leg_id"] != usage["leg_id"],
                order["leg_index"] != usage["leg_index"],
                order["leg_type"] != usage["leg_type"],
                order["module"] != usage["module"],
                order["symbol"] != usage["symbol"],
            )
        ):
            raise StateConflictError("order usage linkage invariant failed")

    for event in connection.execute("SELECT * FROM authorization_events").fetchall():
        try:
            payload = json.loads(event["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise StateConflictError("authorization event payload is invalid") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("event_schema_version") != event["event_schema_version"]
            or event["event_schema_version"] != EVENT_SCHEMA_VERSION
        ):
            raise StateConflictError("authorization event schema invariant failed")
        if event["request_id"] is not None and (
            event["request_id"] not in requests
            or requests[event["request_id"]]["strategy_id"] != event["strategy_id"]
        ):
            raise StateConflictError("event request linkage invariant failed")
        if event["authorization_id"] is not None and (
            event["authorization_id"] not in authorizations
            or authorizations[event["authorization_id"]]["strategy_id"] != event["strategy_id"]
        ):
            raise StateConflictError("event authorization linkage invariant failed")
        if event["usage_id"] is not None and (
            event["usage_id"] not in usages
            or usages[event["usage_id"]]["strategy_id"] != event["strategy_id"]
        ):
            raise StateConflictError("event usage linkage invariant failed")
        if event["auto_trade_order_id"] is not None and (
            event["auto_trade_order_id"] not in orders
            or orders[event["auto_trade_order_id"]]["strategy_id"] != event["strategy_id"]
        ):
            raise StateConflictError("event order linkage invariant failed")

        request = requests.get(event["request_id"]) if event["request_id"] else None
        authorization = (
            authorizations.get(event["authorization_id"])
            if event["authorization_id"]
            else None
        )
        usage = usages.get(event["usage_id"]) if event["usage_id"] else None
        order = orders.get(event["auto_trade_order_id"]) if event["auto_trade_order_id"] else None
        if request is not None and authorization is not None and (
            authorization["request_id"] != request["request_id"]
        ):
            raise StateConflictError("event request authorization chain invariant failed")
        if authorization is not None and usage is not None and (
            usage["authorization_id"] != authorization["authorization_id"]
        ):
            raise StateConflictError("event authorization usage chain invariant failed")
        if usage is not None and order is not None and (
            order["usage_id"] != usage["usage_id"]
        ):
            raise StateConflictError("event usage order chain invariant failed")
        if authorization is not None and order is not None and (
            order["authorization_id"] != authorization["authorization_id"]
        ):
            raise StateConflictError("event authorization order chain invariant failed")


def _legacy_replay_leg_matches(
    usage: sqlite3.Row, order: sqlite3.Row, expected: Any
) -> bool:
    """Compare every caller-determinable field retained by the v2 ledger."""
    if not isinstance(expected, dict):
        return False
    text_fields = (
        (usage, "leg_id"),
        (usage, "leg_type"),
        (usage, "module"),
        (usage, "symbol"),
        (order, "side"),
        (order, "order_type"),
    )
    if any(row[field] != expected.get(field) for row, field in text_fields):
        return False
    if usage["leg_index"] != expected.get("leg_index"):
        return False
    try:
        quantity = _positive_decimal_text(expected.get("quantity"), "quantity")
        price = (
            None
            if expected.get("price") is None
            else _positive_decimal_text(expected.get("price"), "price")
        )
    except ValueError:
        return False
    return order["quantity"] == quantity and order["price"] == price


def _stored_decimal(value: Any, *, positive: bool) -> Decimal:
    if not isinstance(value, str):
        raise StateConflictError("stored amount is not a Decimal string")
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise StateConflictError("stored amount is not a valid Decimal string") from exc
    if not decimal_value.is_finite() or (positive and decimal_value <= 0) or (
        not positive and decimal_value < 0
    ):
        raise StateConflictError("stored amount violates Decimal bounds")
    if _decimal_text(decimal_value) != value:
        raise StateConflictError("stored amount is not normalized")
    return decimal_value


def _owner_key(*, distribution: str, profile_id: str, trading_mode: str, strategy_id: str) -> str:
    return f"{distribution}:{profile_id}:{trading_mode}:{strategy_id}"


def _accepted_summary_notification_key(*, owner_key: str, window_start: datetime) -> str:
    bucket = _format_time(window_start.replace(second=0, microsecond=0))
    return "summary:" + hashlib.sha256(f"{owner_key}:{bucket}".encode("utf-8")).hexdigest()


def _expire_owner_records(
    connection: sqlite3.Connection,
    *,
    owner_key: str,
    now_text: str,
) -> None:
    expired_requests = connection.execute(
        """
        SELECT * FROM authorization_requests
        WHERE owner_key = ? AND status = 'PENDING' AND expires_at <= ?
        ORDER BY created_at, request_id
        """,
        (owner_key, now_text),
    ).fetchall()
    for request in expired_requests:
        updated = connection.execute(
            """
            UPDATE authorization_requests
            SET status = 'EXPIRED', updated_at = ?
            WHERE request_id = ? AND status = 'PENDING'
            """,
            (now_text, request["request_id"]),
        )
        if updated.rowcount:
            _append_event(
                connection,
                strategy_id=request["strategy_id"],
                request_id=request["request_id"],
                event_type="AUTHORIZATION_REQUEST_EXPIRED",
                occurred_at=now_text,
                payload={"status": "EXPIRED"},
                severity="EXCEPTION",
            )

    expired_authorizations = connection.execute(
        """
        SELECT * FROM authorizations
        WHERE owner_key = ? AND status = 'ACTIVE' AND expires_at <= ?
        ORDER BY created_at, authorization_id
        """,
        (owner_key, now_text),
    ).fetchall()
    for authorization in expired_authorizations:
        updated = connection.execute(
            """
            UPDATE authorizations
            SET status = 'EXPIRED', ended_at = ?, updated_at = ?
            WHERE authorization_id = ? AND status = 'ACTIVE'
            """,
            (now_text, now_text, authorization["authorization_id"]),
        )
        if updated.rowcount:
            _append_event(
                connection,
                strategy_id=authorization["strategy_id"],
                request_id=authorization["request_id"],
                authorization_id=authorization["authorization_id"],
                event_type="AUTHORIZATION_EXPIRED",
                occurred_at=now_text,
                payload={"status": "EXPIRED", "expired_at": now_text},
                severity="EXCEPTION",
            )


def _append_event(
    connection: sqlite3.Connection,
    *,
    strategy_id: str,
    event_type: str,
    occurred_at: str,
    payload: dict[str, Any],
    request_id: str | None = None,
    authorization_id: str | None = None,
    usage_id: str | None = None,
    auto_trade_order_id: str | None = None,
    severity: str = "NORMAL",
    notification_key: str | None = None,
    notification_status: str = "NOT_APPLICABLE",
) -> str:
    event_id = "evt_" + uuid.uuid4().hex
    display_payload = {"event_schema_version": EVENT_SCHEMA_VERSION, **payload}
    connection.execute(
        """
        INSERT INTO authorization_events (
            event_id, strategy_id, request_id, authorization_id, usage_id,
            auto_trade_order_id, event_type, event_schema_version, severity,
            occurred_at, payload_json, notification_key, notification_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            strategy_id,
            request_id,
            authorization_id,
            usage_id,
            auto_trade_order_id,
            event_type,
            EVENT_SCHEMA_VERSION,
            severity,
            occurred_at,
            json.dumps(display_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
            notification_key,
            notification_status,
            occurred_at,
        ),
    )
    return event_id


def _utc_now() -> str:
    return _format_time(datetime.now(UTC))


def _strategy_result(row: sqlite3.Row) -> dict[str, str]:
    return {
        "strategy_id": row["strategy_id"],
        "strategy_name": row["strategy_name"],
        "status": row["status"],
        "profile_id": row["profile_id"],
        "distribution": row["distribution"],
        "trading_mode": row["trading_mode"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def normalize_scope(scope: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(scope, dict):
        raise ValueError("scope must be an object")
    unknown = set(scope) - SCOPE_FIELDS
    missing = SCOPE_FIELDS - set(scope)
    if unknown:
        raise ValueError(f"unknown scope fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing scope fields: {', '.join(sorted(missing))}")
    raw_trade_types = scope["trade_types"]
    if not isinstance(raw_trade_types, list) or not raw_trade_types:
        raise ValueError("trade_types must be a non-empty array")
    if any(not isinstance(item, str) for item in raw_trade_types):
        raise ValueError("trade_types entries must be strings")
    trade_type_set = set(raw_trade_types)
    if not trade_type_set <= {"SPOT", "FUTURES"}:
        raise ValueError("trade_types only accepts SPOT and FUTURES")
    trade_types = [item for item in ("SPOT", "FUTURES") if item in trade_type_set]

    if not isinstance(scope["all_symbols"], bool):
        raise ValueError("all_symbols must be a boolean")
    all_symbols = scope["all_symbols"]
    raw_symbols = scope["symbols"]
    if not isinstance(raw_symbols, list) or any(not isinstance(item, str) for item in raw_symbols):
        raise ValueError("symbols must be an array of strings")
    symbols = sorted({item.strip().upper() for item in raw_symbols if item.strip()})
    if any(not SYMBOL_PATTERN.fullmatch(item) for item in symbols):
        raise ValueError("symbols contains an invalid symbol")
    if all_symbols and symbols:
        raise ValueError("symbols must be empty when all_symbols is true")
    if not all_symbols and not symbols:
        raise ValueError("symbols must not be empty when all_symbols is false")

    max_single = _positive_decimal_text(scope["max_single_amount"], "max_single_amount")
    max_total = _positive_decimal_text(scope["max_total_amount"], "max_total_amount")
    if Decimal(max_total) < Decimal(max_single):
        raise ValueError("max_total_amount must be greater than or equal to max_single_amount")

    raw_valid_hours = scope["valid_hours"]
    if isinstance(raw_valid_hours, bool) or not isinstance(raw_valid_hours, (str, int)):
        raise ValueError("valid_hours must be an integer or decimal string")
    try:
        valid_hours = Decimal(str(raw_valid_hours))
    except InvalidOperation as exc:
        raise ValueError("valid_hours must be a valid decimal") from exc
    valid_seconds = valid_hours * Decimal(3600)
    if (
        not valid_hours.is_finite()
        or valid_hours <= 0
        or valid_hours > MAX_VALID_HOURS
        or valid_seconds != valid_seconds.to_integral_value()
    ):
        raise ValueError("valid_hours must resolve to whole seconds between 0 and 720 hours")

    return {
        "trade_types": trade_types,
        "symbols": symbols,
        "all_symbols": all_symbols,
        "max_single_amount": _decimal_text(max_single),
        "max_total_amount": _decimal_text(max_total),
        "valid_hours": _decimal_text(valid_hours),
    }


def _positive_decimal_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a valid decimal string") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f"{field} must be greater than zero")
    return _decimal_text(amount)


def _optional_nonnegative_decimal_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a decimal string or null")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a valid decimal string") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{field} must be greater than or equal to zero")
    return _decimal_text(amount)


def _decimal_text(value: Decimal | str) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(value)
    text = format(decimal_value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _exact_decimal_sum(*values: Decimal) -> Decimal:
    if not values:
        return Decimal("0")
    decimals = [value if isinstance(value, Decimal) else Decimal(value) for value in values]
    max_integer_digits = 1
    max_fraction_digits = 0
    for value in decimals:
        sign, digits, exponent = value.as_tuple()
        del sign
        max_integer_digits = max(max_integer_digits, len(digits) + exponent, 1)
        max_fraction_digits = max(max_fraction_digits, -exponent, 0)
    carry_digits = len(str(len(decimals))) + 2
    with localcontext() as context:
        context.prec = max(28, max_integer_digits + max_fraction_digits + carry_digits)
        total = Decimal("0")
        for value in decimals:
            total += value
        return total


def _hours_to_seconds(hours: str) -> int:
    return int(Decimal(hours) * Decimal(3600))


def _scope_signature(scope: dict[str, Any]) -> str:
    serialized = json.dumps(scope, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("ascii")).hexdigest()


def _coerce_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise StateConflictError("stored timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise StateConflictError("stored timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def _mask_identifier(value: str) -> str:
    prefix = value.split("_", 1)[0] + "_" if "_" in value else "id_"
    return prefix + "***" + value[-6:]


def _scope_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "trade_types": [
            item
            for item, allowed in (
                ("SPOT", row["spot_allowed"]),
                ("FUTURES", row["futures_allowed"]),
            )
            if allowed
        ],
        "symbols": row["symbols_csv"].split(",") if row["symbols_csv"] else [],
        "all_symbols": bool(row["all_symbols"]),
        "max_single_amount": row["max_single_amount_u"],
        "max_total_amount": row["max_total_amount_u"],
        "valid_hours": _decimal_text(Decimal(row["valid_seconds"]) / Decimal(3600)),
    }


def _has_deprecated_expanded_scope(row: sqlite3.Row) -> bool:
    return any(row[column] is not None for column in DEPRECATED_EXPANDED_SCOPE_COLUMNS)


def _request_result(request: sqlite3.Row, strategy: sqlite3.Row) -> dict[str, Any]:
    scope = _scope_from_row(request)
    return {
        "ok": True,
        "status": "AUTHORIZATION_REQUIRED",
        "strategy_id": strategy["strategy_id"],
        "strategy_name": strategy["strategy_name"],
        "request_id": request["request_id"],
        "authorization_id": None,
        "scope_signature": request["scope_signature"],
        "scope": scope,
        "expires_at": None,
        "consumed_amount_u": "0",
        "reserved_amount_u": "0",
        "remaining_amount_u": scope["max_total_amount"],
        "next_action": "WAIT_FOR_AUTHORIZATION",
    }


def _authorization_result(
    authorization: sqlite3.Row,
    strategy: sqlite3.Row,
    *,
    next_action: str | None = None,
) -> dict[str, Any]:
    total = Decimal(authorization["max_total_amount_u"])
    accepted = Decimal(authorization["accepted_amount_u"])
    reserved = Decimal(authorization["reserved_amount_u"])
    return {
        "ok": True,
        "status": authorization["status"],
        "strategy_id": strategy["strategy_id"],
        "strategy_name": strategy["strategy_name"],
        "request_id": authorization["request_id"],
        "authorization_id": authorization["authorization_id"],
        "scope_signature": authorization["scope_signature"],
        "scope": _scope_from_row(authorization),
        "starts_at": authorization["starts_at"],
        "expires_at": authorization["expires_at"],
        "consumed_amount_u": _decimal_text(accepted),
        "reserved_amount_u": _decimal_text(reserved),
        "remaining_amount_u": _decimal_text(_exact_decimal_sum(total, -accepted, -reserved)),
        "next_action": next_action
        or (
            "SUBMIT_ALLOWED"
            if authorization["status"] == "ACTIVE"
            else "REQUEST_NEW_AUTHORIZATION"
        ),
    }


def _usage_result(usage: sqlite3.Row, authorization: sqlite3.Row) -> dict[str, Any]:
    total = Decimal(authorization["max_total_amount_u"])
    accepted = Decimal(authorization["accepted_amount_u"])
    reserved = Decimal(authorization["reserved_amount_u"])
    return {
        "ok": True,
        "usage_id": usage["usage_id"],
        "authorization_id": authorization["authorization_id"],
        "strategy_id": usage["strategy_id"],
        "idempotency_key": usage["idempotency_key"],
        "submission_group_id": usage["submission_group_id"],
        "leg_id": usage["leg_id"],
        "status": usage["status"],
        "estimated_amount_u": usage["estimated_amount_u"],
        "quota_before_u": usage["quota_before_u"],
        "quota_after_u": usage["quota_after_u"],
        "accepted_amount_u": _decimal_text(accepted),
        "reserved_amount_u": _decimal_text(reserved),
        "remaining_amount_u": _decimal_text(_exact_decimal_sum(total, -accepted, -reserved)),
    }


def _order_result(
    order: sqlite3.Row,
    usage: sqlite3.Row,
    authorization: sqlite3.Row,
) -> dict[str, Any]:
    result = _usage_result(usage, authorization)
    result.update(
        {
            "auto_trade_order_id": order["auto_trade_order_id"],
            "client_order_id": order["client_order_id"],
            "weex_order_id": order["weex_order_id"],
            "module": order["module"],
            "symbol": order["symbol"],
            "leg_type": order["leg_type"],
            "side": order["side"],
            "order_type": order["order_type"],
            "quantity": order["quantity"],
            "price": order["price"],
            "exchange_status": order["exchange_status"],
            "executed_quantity": order["executed_quantity"],
            "executed_quote_amount": order["executed_quote_amount"],
            "fee_amount": order["fee_amount"],
            "fee_asset": order["fee_asset"],
            "reconciliation_status": order["reconciliation_status"],
            "reconciliation_source": order["reconciliation_source"],
            "reconciled_at": order["reconciled_at"],
            "usage_status": usage["status"],
        }
    )
    return result


__all__ = [
    "AutoTradeState",
    "BUSY_TIMEOUT_MS",
    "CURRENT_SCHEMA_VERSION",
    "EVENT_SCHEMA_VERSION",
    "StateConflictError",
    "VerifiedUsageEvidence",
    "build_verified_usage_evidence",
]
