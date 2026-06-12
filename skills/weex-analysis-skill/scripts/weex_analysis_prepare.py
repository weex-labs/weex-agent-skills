#!/usr/bin/env python3
"""Prepare large WEEX replay payloads for downstream analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import weex_analysis_cli as analysis


class PreparationInputError(ValueError):
    """Raised when a replay preparation request is malformed."""


def _load_json_input(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _output_json(payload: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


def _normalize_symbol_filters(values: set[str] | list[str] | tuple[str, ...] | None) -> set[str]:
    normalized: set[str] = set()
    for value in values or ():
        for item in str(value).split(","):
            token = item.strip().upper()
            if token:
                normalized.add(token)
    return normalized


def _normalize_account_scope_filters(values: set[str] | list[str] | tuple[str, ...] | None) -> set[str]:
    normalized: set[str] = set()
    for value in values or ():
        token = str(value).strip()
        if token:
            normalized.add(token)
    return normalized


def _merge_degraded_reasons(target: list[str], reasons: list[str]) -> None:
    for reason in reasons:
        if reason and reason not in target:
            target.append(reason)


def _merge_constraints(target: list[dict[str, Any]], constraints: list[dict[str, Any]]) -> None:
    seen = {
        (str(item.get("code") or ""), str(item.get("message") or ""))
        for item in target
        if isinstance(item, dict)
    }
    for item in constraints:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        message = str(item.get("message") or "")
        identity = (code, message)
        if identity in seen:
            continue
        seen.add(identity)
        target.append({"code": code, "message": message})


def _append_constraint(target: list[dict[str, Any]], *, code: str, message: str) -> None:
    _merge_constraints(target, [{"code": code, "message": message}])


def _copy_top_level_value(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        return None
    return payload[key]


def _copy_row_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
    value = _copy_top_level_value(payload, key)
    if value is None:
        return None
    if not isinstance(value, list):
        return value
    return [dict(item) for item in value if isinstance(item, dict)]


def _row_time(row: dict[str, Any]) -> int | None:
    return (
        analysis._coerce_time(row.get("time"))
        or analysis._coerce_time(row.get("update_time"))
        or analysis._coerce_time(row.get("updated_time"))
        or analysis._coerce_time(row.get("updatedTime"))
        or analysis._coerce_time(row.get("created_time"))
        or analysis._coerce_time(row.get("createdTime"))
    )


def _sort_orders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda item: (_row_time(item) or 0, str(item.get("order_id") or "")))


def _sort_fills(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda item: (_row_time(item) or 0, str(item.get("order_id") or "")))


def _filter_by_symbols(rows: list[dict[str, Any]], symbols: set[str]) -> list[dict[str, Any]]:
    if not symbols:
        return rows
    return [row for row in rows if str(row.get("symbol") or "").upper() in symbols]


def _filter_by_account_scopes(rows: list[dict[str, Any]], account_scopes: set[str]) -> list[dict[str, Any]]:
    if not account_scopes:
        return rows
    return [row for row in rows if str(row.get("account_scope") or "") in account_scopes]


def _inherit_account_scope(rows: list[dict[str, Any]], account_scope: str | None) -> list[dict[str, Any]]:
    if not account_scope:
        return rows
    inherited: list[dict[str, Any]] = []
    for row in rows:
        if row.get("account_scope") in (None, ""):
            updated = dict(row)
            updated["account_scope"] = account_scope
            inherited.append(updated)
        else:
            inherited.append(row)
    return inherited


def _filter_by_time_range(
    rows: list[dict[str, Any]],
    *,
    start_time_ms: int | None,
    end_time_ms: int | None,
) -> list[dict[str, Any]]:
    if start_time_ms is None and end_time_ms is None:
        return rows

    filtered: list[dict[str, Any]] = []
    for row in rows:
        row_time = _row_time(row)
        if row_time is None:
            continue
        if start_time_ms is not None and row_time < start_time_ms:
            continue
        if end_time_ms is not None and row_time > end_time_ms:
            continue
        filtered.append(row)
    return filtered


def _filter_price_series_by_time_range(
    rows: list[dict[str, Any]],
    *,
    start_time_ms: int | None,
    end_time_ms: int | None,
) -> list[dict[str, Any]]:
    if start_time_ms is None and end_time_ms is None:
        return rows

    filtered: list[dict[str, Any]] = []
    for row in rows:
        open_time = analysis._coerce_time(row.get("open_time")) or _row_time(row)
        close_time = analysis._coerce_time(row.get("close_time")) or open_time
        if open_time is None or close_time is None:
            continue
        if start_time_ms is not None and close_time < start_time_ms:
            continue
        if end_time_ms is not None and open_time > end_time_ms:
            continue
        filtered.append(row)
    return filtered


def _truncate_recent_rows(rows: list[dict[str, Any]], max_items: int | None) -> tuple[list[dict[str, Any]], bool]:
    if max_items is None:
        return rows, False
    if max_items <= 0:
        raise PreparationInputError("max_items must be positive when provided.")
    if len(rows) <= max_items:
        return rows, False
    return rows[-max_items:], True


def _recompute_closed_trade_count(
    *,
    market: str,
    orders: list[dict[str, Any]],
    fills: list[dict[str, Any]],
) -> tuple[int, bool, list[str]]:
    episodes, reconstructed_partial, reconstruction_reasons = analysis._reconstruct_trade_episodes(
        {"market": market},
        orders,
        fills,
    )
    return len(episodes), reconstructed_partial, reconstruction_reasons


def prepare_replay_payload(
    payload: Any,
    *,
    symbols: set[str] | list[str] | tuple[str, ...] | None = None,
    account_scopes: set[str] | list[str] | tuple[str, ...] | None = None,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
    max_orders: int | None = None,
    max_fills: int | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PreparationInputError("replay payload must be an object.")
    if start_time_ms is not None and end_time_ms is not None and end_time_ms < start_time_ms:
        raise PreparationInputError("end_time_ms must be greater than or equal to start_time_ms.")

    analysis_type = str(payload.get("analysis_type") or "replay")
    if analysis_type not in {"", "replay"}:
        raise PreparationInputError("prepare-replay only accepts replay payloads.")

    normalized_symbols = _normalize_symbol_filters(symbols)
    normalized_account_scopes = _normalize_account_scope_filters(account_scopes)

    orders = _sort_orders([analysis.normalize_order(item) for item in analysis._extract_list(payload, analysis.ORDER_KEYS)])
    fills = _sort_fills([analysis.normalize_fill(item) for item in analysis._extract_list(payload, analysis.FILL_KEYS)])
    balances = _copy_row_list(payload, "balances")
    positions = _copy_row_list(payload, "positions")
    bills = _copy_row_list(payload, "bills")
    price_series = _copy_row_list(payload, "price_series")

    constraints = [item for item in payload.get("constraints", []) if isinstance(item, dict)]
    degraded_reasons = [str(item) for item in payload.get("degraded_reasons", []) if str(item)]
    partial = bool(payload.get("partial"))

    if normalized_symbols:
        orders = _filter_by_symbols(orders, normalized_symbols)
        fills = _filter_by_symbols(fills, normalized_symbols)
        if positions is not None:
            positions = _filter_by_symbols(positions, normalized_symbols)
        if bills is not None:
            bills = _filter_by_symbols(bills, normalized_symbols)
        if price_series is not None:
            price_series = _filter_by_symbols(price_series, normalized_symbols)
        _append_constraint(
            constraints,
            code="analysis_symbol_filter_applied",
            message=f"Prepared payload keeps only symbol(s): {', '.join(sorted(normalized_symbols))}.",
        )

    if normalized_account_scopes:
        top_level_account_scope = str(payload.get("account_scope") or "")
        if top_level_account_scope in normalized_account_scopes:
            orders = _inherit_account_scope(orders, top_level_account_scope)
            fills = _inherit_account_scope(fills, top_level_account_scope)
            if balances is not None:
                balances = _inherit_account_scope(balances, top_level_account_scope)
            if positions is not None:
                positions = _inherit_account_scope(positions, top_level_account_scope)
            if bills is not None:
                bills = _inherit_account_scope(bills, top_level_account_scope)
        orders = _filter_by_account_scopes(orders, normalized_account_scopes)
        fills = _filter_by_account_scopes(fills, normalized_account_scopes)
        if balances is not None:
            balances = _filter_by_account_scopes(balances, normalized_account_scopes)
        if positions is not None:
            positions = _filter_by_account_scopes(positions, normalized_account_scopes)
        if bills is not None:
            bills = _filter_by_account_scopes(bills, normalized_account_scopes)
        _append_constraint(
            constraints,
            code="analysis_account_scope_filter_applied",
            message=f"Prepared payload keeps only account_scope(s): {', '.join(sorted(normalized_account_scopes))}.",
        )

    if start_time_ms is not None or end_time_ms is not None:
        orders = _filter_by_time_range(orders, start_time_ms=start_time_ms, end_time_ms=end_time_ms)
        fills = _filter_by_time_range(fills, start_time_ms=start_time_ms, end_time_ms=end_time_ms)
        if positions is not None:
            positions = _filter_by_time_range(positions, start_time_ms=start_time_ms, end_time_ms=end_time_ms)
        if bills is not None:
            bills = _filter_by_time_range(bills, start_time_ms=start_time_ms, end_time_ms=end_time_ms)
        if price_series is not None:
            price_series = _filter_price_series_by_time_range(
                price_series,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
        window_bits: list[str] = []
        if start_time_ms is not None:
            window_bits.append(f"start={start_time_ms}")
        if end_time_ms is not None:
            window_bits.append(f"end={end_time_ms}")
        _append_constraint(
            constraints,
            code="analysis_time_window_applied",
            message=f"Prepared payload keeps only rows inside {' '.join(window_bits)}.",
        )

    orders, orders_truncated = _truncate_recent_rows(orders, max_orders)
    fills, fills_truncated = _truncate_recent_rows(fills, max_fills)

    if orders_truncated:
        partial = True
        _merge_degraded_reasons(degraded_reasons, ["analysis_orders_truncated"])
    if fills_truncated:
        partial = True
        _merge_degraded_reasons(degraded_reasons, ["analysis_fills_truncated"])

    market = str(payload.get("market") or (fills[0].get("market") if fills else orders[0].get("market") if orders else "unknown"))
    closed_trade_count, reconstructed_partial, reconstruction_reasons = _recompute_closed_trade_count(
        market=market,
        orders=orders,
        fills=fills,
    )
    partial = partial or reconstructed_partial
    _merge_degraded_reasons(degraded_reasons, reconstruction_reasons)

    prepared: dict[str, Any] = {
        "analysis_type": "replay",
        "market": market,
        "orders": orders,
        "fills": fills,
        "closed_trade_count": closed_trade_count,
        "constraints": constraints,
        "partial": partial,
        "degraded_reasons": degraded_reasons,
    }
    for key in ("trading_mode", "environment", "account_scope"):
        value = _copy_top_level_value(payload, key)
        if value not in (None, ""):
            prepared[key] = value
    for key in ("period", "focus", "time_range"):
        value = _copy_top_level_value(payload, key)
        if value not in (None, ""):
            prepared[key] = value
    original_symbol = _copy_top_level_value(payload, "symbol")
    if normalized_symbols and len(normalized_symbols) == 1:
        prepared["symbol"] = next(iter(sorted(normalized_symbols)))
    elif original_symbol not in (None, ""):
        prepared["symbol"] = original_symbol
    if balances is not None:
        prepared["balances"] = balances
    if positions is not None:
        prepared["positions"] = positions
    if bills is not None:
        prepared["bills"] = bills
    if price_series is not None:
        prepared["price_series"] = price_series
    return prepared


def cmd_prepare_replay(args: argparse.Namespace) -> int:
    payload = _load_json_input(args.input)
    prepared = prepare_replay_payload(
        payload,
        symbols=args.symbol,
        account_scopes=args.account_scope,
        start_time_ms=args.start_time_ms,
        end_time_ms=args.end_time_ms,
        max_orders=args.max_orders,
        max_fills=args.max_fills,
    )
    _output_json(prepared, args.pretty)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare large WEEX replay payloads for downstream analysis."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_replay = subparsers.add_parser("prepare-replay", help="Normalize and trim a replay payload.")
    prepare_replay.add_argument("--input", default="-", help="JSON input path or '-' for stdin.")
    prepare_replay.add_argument("--symbol", action="append", default=[], help="Keep only the given symbol(s).")
    prepare_replay.add_argument(
        "--account-scope",
        action="append",
        default=[],
        help="Keep only the given account_scope value(s).",
    )
    prepare_replay.add_argument("--start-time-ms", type=int, default=None, help="Keep rows on or after this timestamp.")
    prepare_replay.add_argument("--end-time-ms", type=int, default=None, help="Keep rows on or before this timestamp.")
    prepare_replay.add_argument("--max-orders", type=int, default=None, help="Keep only the most recent N orders.")
    prepare_replay.add_argument("--max-fills", type=int, default=None, help="Keep only the most recent N fills.")
    prepare_replay.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare-replay":
        return cmd_prepare_replay(args)
    raise SystemExit(f"Unsupported command: {args.command}")


__all__ = [
    "PreparationInputError",
    "build_parser",
    "cmd_prepare_replay",
    "main",
    "prepare_replay_payload",
]


if __name__ == "__main__":
    raise SystemExit(main())
