#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


MIN_FREQUENCY_SECONDS = 3
DEFAULT_FREQUENCY_SECONDS = 5
DEFAULT_AGENT_REPORTING_INTERVAL_SECONDS = 60
MIN_AGENT_REPORTING_INTERVAL_SECONDS = 60
DEFAULT_CODEX_REPORTING_INTERVAL_SECONDS = DEFAULT_AGENT_REPORTING_INTERVAL_SECONDS
MIN_CODEX_REPORTING_INTERVAL_SECONDS = MIN_AGENT_REPORTING_INTERVAL_SECONDS
TASK_STORE_FILENAME = "monitor-tasks.json"
TASK_DB_FILENAME = "monitor-tasks.sqlite3"
VALID_TASK_TYPES = {"position_pnl_monitor"}
VALID_POSITION_SIDES = {"LONG", "SHORT"}
VALID_OPERATORS = {">", ">=", "<", "<="}
VALID_CALLBACK_TYPES = {"current_thread"}
VALID_MARKETS = {"futures"}
CONFIRMATION_REPLY_TEXT_BY_LANGUAGE = {
    "zh": "确认",
    "en": "confirm",
}
VALID_LANGUAGES = {"zh", "en"}


class MonitorInputError(ValueError):
    """Raised when a monitor task cannot be safely normalized or evaluated."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def monitor_home() -> Path:
    configured = os.environ.get("WEEX_MONITOR_SKILL_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".weex-monitor-skill"


def tasks_path() -> Path:
    return monitor_home() / TASK_STORE_FILENAME


def db_path() -> Path:
    return monitor_home() / TASK_DB_FILENAME


def trader_scripts_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "weex-trader-skill" / "scripts"


def _trader_script_command(script_name: str, *args: str) -> list[str]:
    return [sys.executable, str(trader_scripts_dir() / script_name), *args]


def _run_json_command(command: list[str]) -> Any:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise MonitorInputError(f"delegated command failed ({completed.returncode}): {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MonitorInputError("delegated command did not return JSON") from exc


def load_tasks() -> list[dict[str, Any]]:
    database = db_path()
    if database.exists():
        with _connect() as conn:
            rows = conn.execute(
                "SELECT task_json FROM monitor_tasks ORDER BY created_at_ms, task_id"
            ).fetchall()
        return [json.loads(row["task_json"]) for row in rows]

    path = tasks_path()
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise MonitorInputError(f"{path} must contain a JSON array")
    return payload


def save_tasks(tasks: list[dict[str, Any]]) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM monitor_tasks")
        for task in tasks:
            _upsert_task(conn, task, updated_at_ms=_now_ms())


def load_events(task_id: str | None = None) -> list[dict[str, Any]]:
    if not db_path().exists():
        return []
    query = "SELECT event_id, task_id, event_type, created_at_ms, payload_json FROM monitor_events"
    params: tuple[Any, ...] = ()
    if task_id is not None:
        query += " WHERE task_id = ?"
        params = (task_id,)
    query += " ORDER BY event_id"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "task_id": row["task_id"],
            "event_type": row["event_type"],
            "created_at_ms": row["created_at_ms"],
            "payload": json.loads(row["payload_json"]),
        }
        for row in rows
    ]


def normalize_task(raw_task: dict[str, Any], *, now_ms: int | None = None) -> dict[str, Any]:
    if not isinstance(raw_task, dict):
        raise MonitorInputError("task must be a JSON object")

    task_type = _required_string(raw_task, "task_type")
    if task_type not in VALID_TASK_TYPES:
        raise MonitorInputError(f"unsupported task_type: {task_type}")

    profile = _required_string(raw_task, "profile")
    market = _normalize_market(raw_task.get("market"))
    symbol = _required_string(raw_task, "symbol").upper()
    position_side = _normalize_position_side(raw_task.get("position_side"))
    condition = _normalize_condition(raw_task.get("condition"), task_type)
    action = _normalize_action(raw_task.get("action"), position_side, task_type)
    callback = _normalize_callback(raw_task.get("callback"))
    frequency_seconds = _normalize_frequency(raw_task.get("frequency_seconds"))
    created_at_ms = now_ms if now_ms is not None else _now_ms()

    task_id = str(raw_task.get("task_id") or _new_task_id())
    task: dict[str, Any] = {
        "task_id": task_id,
        "task_type": task_type,
        "profile": profile,
        "market": market,
        "symbol": symbol,
        "position_side": position_side,
        "frequency_seconds": frequency_seconds,
        "condition": condition,
        "action": action,
        "callback": callback,
        "status": str(raw_task.get("status") or "draft"),
        "created_at_ms": created_at_ms,
        "execution_delegate": "weex-trader-skill",
    }

    return task


def evaluate_pnl_task(task: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_task(task)
    if normalized["task_type"] != "position_pnl_monitor":
        raise MonitorInputError("evaluate_pnl_task requires position_pnl_monitor")
    if not isinstance(positions, list):
        raise MonitorInputError("positions must be a JSON array")

    target = _find_position(normalized, positions)
    if target is None:
        return {
            "triggered": False,
            "reason": "position_not_found",
            "execution_delegate": "weex-trader-skill",
        }

    pnl_value = _decimal_from_any(_first_present(target, ("unrealizePnl", "unrealizedPnl", "unrealized_pnl")), "unrealized_pnl")
    threshold = Decimal(normalized["condition"]["threshold"])
    operator = normalized["condition"]["operator"]
    matched = _compare(pnl_value, operator, threshold)
    if not matched:
        return {
            "triggered": False,
            "reason": "condition_not_matched",
            "current_value": str(pnl_value),
            "threshold": str(threshold),
            "execution_delegate": "weex-trader-skill",
        }

    quantity = normalized["action"].get("quantity") or _position_size(target)
    return {
        "triggered": True,
        "reason": "condition_matched",
        "execution_delegate": "weex-trader-skill",
        "trigger_snapshot": {
            "symbol": normalized["symbol"],
            "position_side": normalized["position_side"],
            "unrealized_pnl": str(pnl_value),
            "threshold": normalized["condition"]["threshold"],
            "operator": operator,
        },
        "close_order": {
            "symbol": normalized["symbol"],
            "side": _close_order_side(normalized["position_side"]),
            "position_side": normalized["position_side"],
            "order_type": "MARKET",
            "quantity": str(quantity),
        },
    }


def prepare_confirmation(
    raw_task: dict[str, Any],
    *,
    now_ms: int | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    rendered_at_ms = now_ms if now_ms is not None else _now_ms()
    resolved_language = _normalize_language(language)
    task = normalize_task(raw_task, now_ms=rendered_at_ms)
    task["status"] = "draft"
    confirmation_text = render_confirmation_text(
        task,
        now_ms=rendered_at_ms,
        language=resolved_language,
    )
    confirmation_token = _new_confirmation_token()
    fingerprint = _confirmation_fingerprint(task)
    output = {
        "task": task,
        "confirmation_text": confirmation_text,
        "confirmation_token": confirmation_token,
        "confirmation_fingerprint": fingerprint,
    }
    with _connect() as conn:
        _ensure_can_write_draft_task(conn, task)
        _upsert_task(conn, task, updated_at_ms=rendered_at_ms)
        _store_confirmation(
            conn,
            confirmation_token=confirmation_token,
            task=task,
            task_hash=fingerprint,
            confirmation_text=confirmation_text,
            created_at_ms=rendered_at_ms,
        )
        _append_event(
            conn,
            task["task_id"],
            "task_confirmation_rendered",
            output,
            created_at_ms=rendered_at_ms,
        )
    return output


def prepare_live_confirmation(
    raw_task: dict[str, Any],
    *,
    duration_seconds: Any = None,
    reporting_interval_seconds: Any = None,
    now_ms: int | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    rendered_at_ms = now_ms if now_ms is not None else _now_ms()
    resolved_language = _normalize_language(language)
    task_type = _required_string(raw_task, "task_type")
    if task_type != "position_pnl_monitor":
        raise MonitorInputError("live position confirmation requires position_pnl_monitor")

    task = normalize_task(raw_task, now_ms=rendered_at_ms)
    duration_seconds_float = _normalize_duration_seconds(duration_seconds)
    with _connect() as conn:
        _ensure_can_write_draft_task(conn, task)
    live_position_confirmation = _collect_live_position_confirmation(
        task,
        snapshot_at_ms=rendered_at_ms,
        language=resolved_language,
    )
    task["status"] = "draft"
    task["live_position_confirmation"] = live_position_confirmation
    task["live_run_duration_seconds"] = duration_seconds_float
    reporting_interval = _normalize_reporting_interval_seconds(reporting_interval_seconds)
    agent_reporting = build_agent_reporting_metadata(task, interval_seconds=reporting_interval)
    reporting = agent_reporting["runtimes"]["codex"]
    task["codex_reporting"] = reporting
    task["agent_reporting"] = agent_reporting
    confirmation_text = render_confirmation_text(
        task,
        now_ms=rendered_at_ms,
        position_snapshot=live_position_confirmation,
        duration_seconds=duration_seconds_float,
        language=resolved_language,
    )
    confirmation_token = _new_confirmation_token()
    fingerprint = _confirmation_fingerprint(task)
    output = {
        "task": task,
        "confirmation_text": confirmation_text,
        "confirmation_token": confirmation_token,
        "confirmation_fingerprint": fingerprint,
        "live_position_confirmation": live_position_confirmation,
        "duration_seconds": duration_seconds_float,
        "reporting": reporting,
        "agent_reporting": agent_reporting,
    }
    with _connect() as conn:
        _ensure_can_write_draft_task(conn, task)
        _upsert_task(conn, task, updated_at_ms=rendered_at_ms)
        _store_confirmation(
            conn,
            confirmation_token=confirmation_token,
            task=task,
            task_hash=fingerprint,
            confirmation_text=confirmation_text,
            created_at_ms=rendered_at_ms,
        )
        _append_event(
            conn,
            task["task_id"],
            "task_confirmation_rendered",
            output,
            created_at_ms=rendered_at_ms,
        )
    return output


def confirm_task(
    raw_task: dict[str, Any],
    *,
    confirm_monitor: bool,
    confirmation_token: str | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    if not confirm_monitor:
        raise MonitorInputError("refusing to activate monitor task without --confirm-monitor")
    if confirmation_token is None or str(confirmation_token).strip() == "":
        raise MonitorInputError("confirmation-token is required before activating monitor task")
    confirmed_at_ms = now_ms if now_ms is not None else _now_ms()
    task = _merge_normalized_task(raw_task, now_ms=confirmed_at_ms)
    task["status"] = "active"
    task["confirmed_at_ms"] = confirmed_at_ms
    with _connect() as conn:
        _consume_confirmation_token(
            conn,
            confirmation_token=str(confirmation_token).strip(),
            task=task,
            used_at_ms=confirmed_at_ms,
        )
        _upsert_task(conn, task, updated_at_ms=confirmed_at_ms)
        _append_event(
            conn,
            task["task_id"],
            "task_confirmed",
            {"status": "active", "task": task},
            created_at_ms=confirmed_at_ms,
        )
    return task


def cancel_task(task_id: str, *, now_ms: int | None = None) -> dict[str, Any]:
    cancelled_at_ms = now_ms if now_ms is not None else _now_ms()
    tasks = load_tasks()
    for task in tasks:
        if task.get("task_id") == task_id:
            if task.get("status") in {"completed", "cancelled"}:
                return task
            task["status"] = "cancelled"
            task["cancelled_at_ms"] = cancelled_at_ms
            with _connect() as conn:
                _upsert_task(conn, task, updated_at_ms=cancelled_at_ms)
                _append_event(
                    conn,
                    task_id,
                    "task_cancelled",
                    {"status": "cancelled", "task": task},
                    created_at_ms=cancelled_at_ms,
                )
            return task
    raise MonitorInputError(f"task_id not found: {task_id}")


def render_confirmation_text(
    raw_task: dict[str, Any],
    *,
    now_ms: int | None = None,
    position_snapshot: dict[str, Any] | None = None,
    duration_seconds: float | None = None,
    language: str | None = None,
) -> str:
    resolved_language = _normalize_language(language)
    reply_text = _confirmation_reply_text(resolved_language)
    task = normalize_task(raw_task, now_ms=now_ms)
    condition = task["condition"]
    action = task["action"]
    position_snapshot = position_snapshot or raw_task.get("live_position_confirmation")
    duration_seconds = (
        duration_seconds
        if duration_seconds is not None
        else raw_task.get("live_run_duration_seconds")
    )
    if resolved_language == "en":
        parts = [
            "Automated Monitor Confirmation",
            f"Task ID: {task['task_id']}",
            f"Account: {task['profile']}",
            f"Monitor target: {task['symbol']} {_position_side_label(task['position_side'], language=resolved_language)}",
            f"Trigger condition: {_condition_label(condition, language=resolved_language)}",
            f"Trigger action: {_action_label(action, language=resolved_language)}",
            f"Callback: {task['callback']['type']}",
            "After confirmation, the local monitor rule will be saved; real positions will be read and a real order will be submitted only after you authorize real-account access.",
            f"If you confirm the monitor settings and authorization above, Reply: {reply_text}",
        ]
        parts.insert(6, f"Check frequency: every {task['frequency_seconds']} seconds")
    else:
        parts = [
            "自动化监控确认",
            f"任务编号: {task['task_id']}",
            f"账户: {task['profile']}",
            f"监控对象: {task['symbol']} {_position_side_label(task['position_side'])}",
            f"触发条件: {_condition_label(condition)}",
            f"触发动作: {_action_label(action)}",
            f"回报位置: {task['callback']['type']}",
            "确认后会先保存本地监控规则；只有在你授权使用真实账户后，才会读取真实仓位并在触发时提交真实委托。",
            f"如果你确认上述监控设置与授权，请回复：{reply_text}",
        ]
        parts.insert(6, f"检查频率: 每 {task['frequency_seconds']} 秒")
    if duration_seconds is not None:
        if resolved_language == "en":
            parts.insert(7, f"Run duration: {_duration_label(float(duration_seconds), language=resolved_language)}")
        else:
            parts.insert(7, f"运行时长: {_duration_label(float(duration_seconds))}")
    reporting = raw_task.get("codex_reporting")
    if isinstance(reporting, dict) and reporting.get("enabled"):
        interval_seconds = _normalize_codex_reporting_interval_seconds(reporting.get("interval_seconds"))
        reporting_line = (
            f"Status reporting: every {_duration_label(float(interval_seconds), language=resolved_language)} "
            "via Codex thread heartbeat"
            if resolved_language == "en"
            else f"状态汇报: 每 {_duration_label(float(interval_seconds))}，通过 Codex thread heartbeat"
        )
        insert_index = 8 if duration_seconds is not None else 7
        parts.insert(insert_index, reporting_line)
    if position_snapshot is not None:
        if resolved_language == "en":
            parts.insert(
                4,
                (
                    "Matched live position: "
                    f"{position_snapshot['symbol']} {_position_side_label(position_snapshot['position_side'], language=resolved_language)}, "
                    f"position size: {position_snapshot['quantity']}, "
                    f"current unrealized PnL: {position_snapshot.get('unrealized_pnl', 'unknown')}"
                ),
            )
        else:
            parts.insert(
                4,
                (
                    "已匹配真实持仓: "
                    f"{position_snapshot['symbol']} {_position_side_label(position_snapshot['position_side'])}, "
                    f"持仓数量: {position_snapshot['quantity']}, "
                    f"当前未实现盈亏: {position_snapshot.get('unrealized_pnl', 'unknown')}"
                ),
            )
        parts.insert(5, _position_detail_line(position_snapshot, language=resolved_language))
        current_condition_line = _current_condition_line(position_snapshot, language=resolved_language)
        if current_condition_line is not None:
            for index, part in enumerate(parts):
                condition_prefix = "Trigger condition:" if resolved_language == "en" else "触发条件:"
                if part.startswith(condition_prefix):
                    parts.insert(index + 1, current_condition_line)
                    break
    return "\n".join(parts)


def build_idempotency_key(raw_task: dict[str, Any], purpose: str) -> str:
    if not purpose or str(purpose).strip() == "":
        raise MonitorInputError("purpose is required")
    task = normalize_task(raw_task)
    fingerprint_payload = {
        "task_type": task["task_type"],
        "symbol": task["symbol"],
        "position_side": task["position_side"],
        "condition": task["condition"],
        "action": task["action"],
        "purpose": str(purpose).strip(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"monitor:{task['task_id']}:{purpose}:{fingerprint}"


def run_once_dry_run(
    positions: list[dict[str, Any]],
    *,
    task_id: str | None = None,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    evaluated_at_ms = now_ms if now_ms is not None else _now_ms()
    if not isinstance(positions, list):
        raise MonitorInputError("positions must be a JSON array")

    results: list[dict[str, Any]] = []
    for task in load_tasks():
        if task.get("status") != "active":
            continue
        if task_id is not None and task.get("task_id") != task_id:
            continue
        if task.get("task_type") != "position_pnl_monitor":
            continue

        result = evaluate_pnl_task(task, positions)
        idempotency_key = build_idempotency_key(task, "dry-run-trigger")
        output = {
            "task_id": task["task_id"],
            "dry_run": True,
            "idempotency_key": idempotency_key,
            "result": result,
        }
        if result.get("triggered"):
            output["live_delegate_plan"] = build_live_delegate_plan(
                task,
                result,
                purpose="dry-run-trigger",
            )
        output["thread_report"] = render_thread_report(output)
        with _connect() as conn:
            _append_event(
                conn,
                task["task_id"],
                "dry_run_evaluated",
                output,
                created_at_ms=evaluated_at_ms,
            )
            if result.get("triggered"):
                updated_task = dict(task)
                updated_task["status"] = "triggered"
                updated_task["triggered_at_ms"] = evaluated_at_ms
                updated_task["trigger_snapshot"] = result.get("trigger_snapshot", {})
                updated_task["last_dry_run"] = True
                _upsert_task(conn, updated_task, updated_at_ms=evaluated_at_ms)
                _append_event(
                    conn,
                    task["task_id"],
                    "dry_run_triggered",
                    output,
                    created_at_ms=evaluated_at_ms,
                )
        results.append(output)
    return results


def run_loop_dry_run(
    positions_sequence: list[list[dict[str, Any]]],
    *,
    iterations: int,
    task_id: str | None = None,
    sleep_seconds: float | None = 0,
    now_ms: int | None = None,
) -> dict[str, Any]:
    if sleep_seconds is None:
        sleep_seconds = 0
    if iterations < 1:
        raise MonitorInputError("iterations must be >= 1")
    if sleep_seconds < 0:
        raise MonitorInputError("sleep_seconds must be >= 0")
    if not isinstance(positions_sequence, list) or not positions_sequence:
        raise MonitorInputError("positions_sequence must be a non-empty JSON array")

    loop_started_at_ms = now_ms if now_ms is not None else _now_ms()
    iteration_outputs: list[dict[str, Any]] = []
    triggered_count = 0
    for index in range(iterations):
        positions = positions_sequence[min(index, len(positions_sequence) - 1)]
        if not isinstance(positions, list):
            raise MonitorInputError("each positions_sequence item must be a JSON array")
        results = run_once_dry_run(
            positions,
            task_id=task_id,
            now_ms=loop_started_at_ms + index,
        )
        triggered_count += sum(1 for item in results if item.get("result", {}).get("triggered"))
        iteration_outputs.append(
            {
                "iteration": index + 1,
                "results": results,
            }
        )
        if sleep_seconds and index + 1 < iterations:
            time.sleep(sleep_seconds)

    return {
        "dry_run": True,
        "iterations_requested": iterations,
        "iterations_completed": len(iteration_outputs),
        "triggered_count": triggered_count,
        "iterations": iteration_outputs,
        "mutating_request_submitted": False,
    }


def run_live_loop(
    *,
    confirm_live: bool,
    iterations: int,
    task_id: str | None = None,
    sleep_seconds: float | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    if not confirm_live:
        raise MonitorInputError("run-loop live mode requires --confirm-live")
    if iterations < 1:
        raise MonitorInputError("iterations must be >= 1")
    if sleep_seconds is not None and sleep_seconds < 0:
        raise MonitorInputError("sleep_seconds must be >= 0")

    effective_sleep_seconds = (
        sleep_seconds
        if sleep_seconds is not None
        else _minimum_active_pnl_frequency_seconds(task_id=task_id)
    )
    iteration_outputs: list[dict[str, Any]] = []
    submitted_count = 0
    for index in range(iterations):
        evaluated_at_ms = now_ms + index if now_ms is not None else _now_ms()
        results = run_live_once(
            confirm_live=True,
            task_id=task_id,
            now_ms=evaluated_at_ms,
        )
        submitted_count += sum(1 for item in results if item.get("status") == "completed")
        iteration_outputs.append(
            {
                "iteration": index + 1,
                "results": results,
            }
        )
        if not _has_active_pnl_tasks(task_id=task_id):
            break
        if effective_sleep_seconds and index + 1 < iterations:
            time.sleep(effective_sleep_seconds)

    return {
        "live": True,
        "iterations_requested": iterations,
        "iterations_completed": len(iteration_outputs),
        "submitted_count": submitted_count,
        "effective_sleep_seconds": effective_sleep_seconds,
        "iterations": iteration_outputs,
        "mutating_request_delegate": "weex-trader-skill",
    }


def confirm_and_run_live_loop(
    raw_task: dict[str, Any],
    *,
    confirm_monitor: bool,
    confirmation_token: str | None,
    confirm_live: bool,
    duration_seconds: Any = None,
    reporting_interval_seconds: Any = None,
    sleep_seconds: float | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    if not confirm_live:
        raise MonitorInputError("confirm-and-run-loop requires --confirm-live")
    duration_seconds_float = _normalize_duration_seconds(duration_seconds)
    if sleep_seconds is not None and sleep_seconds < 0:
        raise MonitorInputError("sleep_seconds must be >= 0")

    requested = normalize_task(raw_task)
    if requested["task_type"] != "position_pnl_monitor":
        raise MonitorInputError("confirm-and-run-loop requires position_pnl_monitor")
    if not isinstance(raw_task.get("live_position_confirmation"), dict):
        raise MonitorInputError("live position confirmation is required before starting live monitor")
    _validate_live_confirmation_token(raw_task, confirmation_token, duration_seconds=duration_seconds_float)
    iterations = _iterations_for_duration_seconds(
        duration_seconds_float,
        requested["frequency_seconds"],
    )

    confirmed = confirm_task(
        raw_task,
        confirm_monitor=confirm_monitor,
        confirmation_token=confirmation_token,
        now_ms=now_ms,
    )
    loop_result = run_live_loop(
        confirm_live=True,
        iterations=iterations,
        task_id=confirmed["task_id"],
        sleep_seconds=sleep_seconds,
        now_ms=now_ms,
    )
    agent_reporting = _agent_reporting_metadata_from_task(
        confirmed,
        reporting_interval_seconds=reporting_interval_seconds,
    )
    reporting = agent_reporting["runtimes"]["codex"]
    return {
        "combined_confirmation": True,
        "duration_seconds": duration_seconds_float,
        "derived_iterations": iterations,
        "confirmed_task": confirmed,
        "loop_result": loop_result,
        "reporting": reporting,
        "agent_reporting": agent_reporting,
    }


def build_agent_reporting_metadata(raw_task: dict[str, Any], *, interval_seconds: int) -> dict[str, Any]:
    task = normalize_task(raw_task)
    interval = _normalize_reporting_interval_seconds(interval_seconds)
    return {
        "enabled": True,
        "type": "agent_status_reporting",
        "interval_seconds": interval,
        "task_id": task["task_id"],
        "status_prompt": _build_status_reporting_prompt(task, runtime_label="agent session"),
        "runtimes": {
            "codex": build_codex_reporting_metadata(task, interval_seconds=interval),
            "claude_code": build_claude_code_reporting_metadata(task, interval_seconds=interval),
            "openclaw": build_openclaw_reporting_metadata(task, interval_seconds=interval),
        },
    }


def build_codex_reporting_metadata(raw_task: dict[str, Any], *, interval_seconds: int) -> dict[str, Any]:
    task = normalize_task(raw_task)
    interval = _normalize_reporting_interval_seconds(interval_seconds)
    interval_minutes = interval // 60
    task_id = task["task_id"]
    prompt = _build_status_reporting_prompt(task, runtime_label="Codex thread")
    return {
        "enabled": True,
        "type": "codex_thread_heartbeat",
        "interval_seconds": interval,
        "rrule": f"FREQ=MINUTELY;INTERVAL={interval_minutes}",
        "name": f"WEEX monitor {task_id} status",
        "task_id": task_id,
        "heartbeat_prompt": prompt,
    }


def build_claude_code_reporting_metadata(raw_task: dict[str, Any], *, interval_seconds: int) -> dict[str, Any]:
    task = normalize_task(raw_task)
    interval = _normalize_reporting_interval_seconds(interval_seconds)
    interval_label = _minute_interval_token(interval)
    task_id = task["task_id"]
    prompt = _build_status_reporting_prompt(task, runtime_label="Claude Code session")
    return {
        "enabled": True,
        "type": "claude_code_loop",
        "interval_seconds": interval,
        "interval": interval_label,
        "task_id": task_id,
        "name": f"WEEX monitor {task_id} status",
        "loop_prompt": prompt,
        "loop_command": f"/loop {interval_label} {_one_line(prompt)}",
        "list_instruction": "Ask Claude Code: what scheduled tasks do I have?",
        "cancel_instruction": f"Ask Claude Code to cancel the WEEX monitor {task_id} status loop.",
    }


def build_openclaw_reporting_metadata(raw_task: dict[str, Any], *, interval_seconds: int) -> dict[str, Any]:
    task = normalize_task(raw_task)
    interval = _normalize_reporting_interval_seconds(interval_seconds)
    interval_label = _minute_interval_token(interval)
    task_id = task["task_id"]
    name = f"WEEX monitor {task_id} status"
    prompt = _build_status_reporting_prompt(task, runtime_label="OpenClaw session")
    return {
        "enabled": True,
        "type": "openclaw_cron",
        "interval_seconds": interval,
        "interval": interval_label,
        "task_id": task_id,
        "name": name,
        "message": prompt,
        "create_job_args": [
            "openclaw",
            "cron",
            "add",
            "--name",
            name,
            "--every",
            interval_label,
            "--session",
            "current",
            "--message",
            prompt,
        ],
        "list_jobs_args": ["openclaw", "cron", "list"],
        "recent_runs_args": ["openclaw", "cron", "runs", "--id", "<job-id>", "--limit", "20"],
        "remove_job_args": ["openclaw", "cron", "remove", "<job-id>"],
        "heartbeat_fallback": {
            "file": "HEARTBEAT.md",
            "task_name": name,
            "note": "Use only when the OpenClaw heartbeat cadence can satisfy the requested interval.",
            "message": prompt,
        },
    }


def _build_status_reporting_prompt(raw_task: dict[str, Any], *, runtime_label: str) -> str:
    task = normalize_task(raw_task)
    skill_root = Path(__file__).resolve().parents[1]
    task_id = task["task_id"]
    return (
        f"Report WEEX monitor status for the current {runtime_label}.\n"
        f"Task id: {task_id}\n"
        f"Skill directory: {skill_root}\n"
        "Read-only commands to run from the skill directory:\n"
        f"- python3 scripts/weex_monitor_cli.py list\n"
        f"- python3 scripts/weex_monitor_cli.py events --task-id {task_id}\n"
        "Find the task by task_id. Summarize task status, symbol, position side, condition, "
        "latest evaluated current_value, threshold, trigger state, and reason. If the latest "
        "events include exchange_response, live_order_result, close_order, or error details, "
        "include those. Do not output HTML entities or entity spellings for less-than, greater-than, or "
        "ampersand characters; render "
        "comparison operators as readable words in the response language, for example less than "
        "or 小于 for '<', greater than or 大于 for '>', greater than or equal to for '>=', and "
        "less than or equal to for '<='. Do not submit, amend, or cancel WEEX orders. If task status is not "
        "active or executing, report the final state and stop or pause this status reporting job "
        "when the runtime automation tool allows it."
    )


def build_live_delegate_plan(
    raw_task: dict[str, Any],
    evaluation_result: dict[str, Any],
    *,
    purpose: str,
) -> dict[str, Any]:
    task = normalize_task(raw_task)
    if not isinstance(evaluation_result, dict):
        raise MonitorInputError("evaluation_result must be a JSON object")
    if not evaluation_result.get("triggered"):
        raise MonitorInputError("live delegate plan requires a triggered evaluation result")
    close_order = evaluation_result.get("close_order")
    if not isinstance(close_order, dict):
        raise MonitorInputError("triggered evaluation result is missing close_order")

    return {
        "delegate_skill": "weex-trader-skill",
        "requires_live_account_authorization": True,
        "mutating_request_submitted": False,
        "task_id": task["task_id"],
        "profile": task["profile"],
        "market": task["market"],
        "idempotency_key": build_idempotency_key(task, purpose),
        "close_order": close_order,
        "trigger_snapshot": evaluation_result.get("trigger_snapshot", {}),
        "instruction": "Submit only through weex-trader-skill after the user authorizes real account access and real order execution.",
    }


def _load_confirmed_active_task(
    raw_task: dict[str, Any],
    *,
    expected_task_type: str,
) -> dict[str, Any]:
    requested = normalize_task(raw_task)
    if requested["task_type"] != expected_task_type:
        raise MonitorInputError(f"confirmed live monitor requires {expected_task_type}")

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT task_json
            FROM monitor_tasks
            WHERE task_id = ? AND status = 'active'
            """,
            (requested["task_id"],),
        ).fetchone()
        if row is None:
            raise MonitorInputError(
                "confirmed active monitor task was not found or is no longer active"
            )
        stored_task = _merge_normalized_task(json.loads(row["task_json"]), now_ms=_now_ms())
        if stored_task["task_type"] != expected_task_type:
            raise MonitorInputError(f"confirmed active monitor task is not {expected_task_type}")
        if _confirmation_fingerprint(stored_task) != _confirmation_fingerprint(requested):
            raise MonitorInputError("task details do not match the confirmed active monitor task")
        confirmation_row = conn.execute(
            """
            SELECT 1
            FROM monitor_confirmations
            WHERE task_id = ? AND task_hash = ? AND used_at_ms IS NOT NULL
            """,
            (stored_task["task_id"], _confirmation_fingerprint(stored_task)),
        ).fetchone()
        if confirmation_row is None:
            raise MonitorInputError(
                "confirmed active monitor task was not found or is no longer active"
            )
    return stored_task


def run_live_once(
    *,
    confirm_live: bool,
    task_id: str | None = None,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    if not confirm_live:
        raise MonitorInputError("run-live-once requires --confirm-live")
    evaluated_at_ms = now_ms if now_ms is not None else _now_ms()
    outputs: list[dict[str, Any]] = []
    claimed_position_buckets: set[tuple[str, str, str, str]] = set()

    for task in load_tasks():
        if task.get("status") != "active":
            continue
        if task_id is not None and task.get("task_id") != task_id:
            continue
        if task.get("task_type") != "position_pnl_monitor":
            continue
        position_bucket = _position_execution_key(task)
        if position_bucket in claimed_position_buckets:
            current_task = _load_task_by_id(str(task["task_id"])) or task
            failure = current_task.get("last_failure")
            reason = "position_execution_already_claimed"
            detail = None
            if isinstance(failure, dict):
                reason = str(failure.get("reason") or reason)
                detail = failure.get("detail")
            outputs.append(
                _live_not_executed_output(
                    current_task,
                    reason,
                    status=str(current_task.get("status") or "review_required"),
                    detail=str(detail) if detail is not None else None,
                )
            )
            continue
        try:
            task = _load_confirmed_active_task(task, expected_task_type="position_pnl_monitor")
        except MonitorInputError as exc:
            outputs.append(
                _mark_task_review_required(
                    task,
                    reason="missing_monitor_confirmation",
                    detail=str(exc),
                    event_type="live_order_failed",
                    now_ms=evaluated_at_ms,
                )
            )
            continue

        first_payload = _collect_live_account_payload(task)
        first_blocker = _live_payload_blocker(first_payload)
        if first_blocker is not None:
            output = _live_not_executed_output(task, first_blocker)
            _append_live_event(task["task_id"], "live_evaluated", output, evaluated_at_ms)
            outputs.append(output)
            continue

        first_result = evaluate_pnl_task(task, _positions_from_account_payload(first_payload))
        if not first_result.get("triggered"):
            output = {
                "task_id": task["task_id"],
                "status": "active",
                "result": first_result,
                "thread_report": render_live_thread_report(task, first_result, None),
            }
            _append_live_event(task["task_id"], "live_evaluated", output, evaluated_at_ms)
            outputs.append(output)
            continue

        recheck_payload = _collect_live_account_payload(task)
        recheck_blocker = _live_payload_blocker(recheck_payload)
        if recheck_blocker is not None:
            output = _live_not_executed_output(task, f"revalidation_{recheck_blocker}")
            _append_live_event(task["task_id"], "live_revalidation_failed", output, evaluated_at_ms)
            outputs.append(output)
            continue

        recheck_result = evaluate_pnl_task(task, _positions_from_account_payload(recheck_payload))
        if not recheck_result.get("triggered"):
            output = {
                "task_id": task["task_id"],
                "status": "active",
                "result": recheck_result,
                "thread_report": "WEEX monitor live trigger revalidation did not match; no live close order was submitted.",
            }
            _append_live_event(task["task_id"], "live_revalidation_failed", output, evaluated_at_ms)
            outputs.append(output)
            continue

        close_order = dict(recheck_result["close_order"])
        close_order["new_client_order_id"] = _live_client_order_id(task["task_id"])
        if not claim_task_for_execution(task, now_ms=evaluated_at_ms):
            output = _live_not_executed_output(task, "execution_already_claimed")
            _append_live_event(task["task_id"], "live_execution_skipped", output, evaluated_at_ms)
            outputs.append(output)
            continue
        claimed_position_buckets.add(position_bucket)
        try:
            preview = _run_json_command(
                _trader_script_command(
                    "weex_trade_guard.py",
                    "preview-order",
                    "--profile",
                    task["profile"],
                    "--market",
                    task["market"],
                    "--order-json",
                    json.dumps(close_order, ensure_ascii=False, separators=(",", ":")),
                    "--ttl-seconds",
                    "300",
                    "--pretty",
                )
            )
            intent_id = _required_delegate_field(preview, "intent_id")
            risk_signature = _required_delegate_field(preview, "risk_signature")
        except MonitorInputError as exc:
            outputs.append(
                _mark_task_review_required(
                    task,
                    reason="live_order_preview_failed",
                    detail=str(exc),
                    event_type="live_order_failed",
                    now_ms=evaluated_at_ms,
                    extra={"result": recheck_result, "close_order": close_order},
                )
            )
            continue
        try:
            exchange_response = _run_json_command(
                _trader_script_command(
                    "weex_trade_guard.py",
                    "confirm-order",
                    "--intent-id",
                    intent_id,
                    "--risk-signature",
                    risk_signature,
                    "--confirm-live",
                    "--pretty",
                )
            )
        except MonitorInputError as exc:
            outputs.append(
                _mark_task_review_required(
                    task,
                    reason="live_order_confirm_failed",
                    detail=str(exc),
                    event_type="live_order_failed",
                    now_ms=evaluated_at_ms,
                    extra={"preview": preview, "result": recheck_result, "close_order": close_order},
                )
            )
            continue
        output = {
            "task_id": task["task_id"],
            "status": "completed",
            "result": recheck_result,
            "close_order": close_order,
            "exchange_response": exchange_response,
            "thread_report": render_live_thread_report(task, recheck_result, exchange_response),
        }
        updated_task = dict(task)
        updated_task["status"] = "completed"
        updated_task["triggered_at_ms"] = evaluated_at_ms
        updated_task["completed_at_ms"] = evaluated_at_ms
        updated_task["trigger_snapshot"] = recheck_result.get("trigger_snapshot", {})
        updated_task["close_order"] = close_order
        updated_task["exchange_response"] = exchange_response
        with _connect() as conn:
            _append_event(conn, task["task_id"], "live_evaluated", {"result": first_result}, created_at_ms=evaluated_at_ms)
            _append_event(conn, task["task_id"], "live_triggered", {"result": recheck_result}, created_at_ms=evaluated_at_ms)
            _append_event(conn, task["task_id"], "live_order_previewed", {"preview": preview, "close_order": close_order}, created_at_ms=evaluated_at_ms)
            _upsert_task(conn, updated_task, updated_at_ms=evaluated_at_ms)
            _append_event(conn, task["task_id"], "live_order_submitted", output, created_at_ms=evaluated_at_ms)
        outputs.append(output)
    return outputs


def _required_delegate_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        raise MonitorInputError(f"delegated preview response missing {key}")
    return str(value).strip()


def claim_task_for_execution(raw_task: dict[str, Any], *, now_ms: int | None = None) -> bool:
    claimed_at_ms = now_ms if now_ms is not None else _now_ms()
    task = _merge_normalized_task(raw_task, now_ms=claimed_at_ms)
    executing_task = dict(task)
    executing_task["status"] = "executing"
    executing_task["executing_at_ms"] = claimed_at_ms
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE monitor_tasks
            SET status = ?, updated_at_ms = ?, task_json = ?
            WHERE task_id = ? AND status = 'active'
            """,
            (
                "executing",
                claimed_at_ms,
                json.dumps(executing_task, ensure_ascii=False, sort_keys=True),
                task["task_id"],
            ),
        )
        if cursor.rowcount != 1:
            return False
        duplicate_rows = conn.execute(
            """
            SELECT task_json
            FROM monitor_tasks
            WHERE task_id <> ?
              AND status = 'active'
              AND task_type = 'position_pnl_monitor'
              AND profile = ?
              AND symbol = ?
              AND position_side = ?
            ORDER BY created_at_ms, task_id
            """,
            (
                task["task_id"],
                task["profile"],
                task["symbol"],
                task["position_side"],
            ),
        ).fetchall()
        for row in duplicate_rows:
            duplicate_task = _merge_normalized_task(
                json.loads(row["task_json"]),
                now_ms=claimed_at_ms,
            )
            duplicate_task["status"] = "review_required"
            duplicate_task["review_required_at_ms"] = claimed_at_ms
            duplicate_task["last_failure"] = {
                "reason": "position_execution_already_claimed",
                "detail": (
                    "another active monitor for this profile/symbol/position_side "
                    f"was claimed by {task['task_id']}"
                ),
            }
            result = {
                "triggered": False,
                "reason": "position_execution_already_claimed",
                "detail": duplicate_task["last_failure"]["detail"],
                "execution_delegate": "weex-trader-skill",
            }
            output = {
                "task_id": duplicate_task["task_id"],
                "status": "review_required",
                "result": result,
                "thread_report": render_live_thread_report(duplicate_task, result, None),
            }
            _upsert_task(conn, duplicate_task, updated_at_ms=claimed_at_ms)
            _append_event(
                conn,
                duplicate_task["task_id"],
                "live_execution_skipped",
                output,
                created_at_ms=claimed_at_ms,
            )
        _append_event(
            conn,
            task["task_id"],
            "live_execution_claimed",
            {"status": "executing", "task": executing_task},
            created_at_ms=claimed_at_ms,
        )
    return True


def _mark_task_review_required(
    raw_task: dict[str, Any],
    *,
    reason: str,
    detail: str,
    event_type: str,
    now_ms: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = _merge_normalized_task(raw_task, now_ms=now_ms)
    task["status"] = "review_required"
    task["review_required_at_ms"] = now_ms
    task["last_failure"] = {
        "reason": reason,
        "detail": detail,
    }
    result = {
        "triggered": False,
        "reason": reason,
        "detail": detail,
        "execution_delegate": "weex-trader-skill",
    }
    output: dict[str, Any] = {
        "task_id": task["task_id"],
        "status": "review_required",
        "result": result,
        "thread_report": render_live_thread_report(task, result, None),
    }
    if extra:
        output["delegate_context"] = extra
        task["last_failure"]["extra"] = extra
    with _connect() as conn:
        _upsert_task(conn, task, updated_at_ms=now_ms)
        _append_event(conn, task["task_id"], event_type, output, created_at_ms=now_ms)
    return output


def _merge_normalized_task(raw_task: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
    created_at_ms = raw_task.get("created_at_ms", now_ms)
    try:
        created_at_int = int(created_at_ms)
    except (TypeError, ValueError):
        created_at_int = now_ms
    normalized = normalize_task(raw_task, now_ms=created_at_int)
    task = dict(raw_task)
    task.update(normalized)
    return task


def _minimum_active_pnl_frequency_seconds(*, task_id: str | None = None) -> int:
    frequencies = [
        int(task.get("frequency_seconds", DEFAULT_FREQUENCY_SECONDS))
        for task in load_tasks()
        if task.get("status") == "active"
        and task.get("task_type") == "position_pnl_monitor"
        and (task_id is None or task.get("task_id") == task_id)
    ]
    return min(frequencies) if frequencies else DEFAULT_FREQUENCY_SECONDS


def _has_active_pnl_tasks(*, task_id: str | None = None) -> bool:
    return any(
        task.get("status") == "active"
        and task.get("task_type") == "position_pnl_monitor"
        and (task_id is None or task.get("task_id") == task_id)
        for task in load_tasks()
    )


def _collect_live_account_payload(task: dict[str, Any]) -> dict[str, Any]:
    payload = _run_json_command(
        _trader_script_command(
            "weex_trade_data_aggregator.py",
            "collect-account-risk",
            "--profile",
            str(task["profile"]),
            "--market",
            str(task["market"]),
            "--symbol",
            str(task["symbol"]),
            "--pretty",
        )
    )
    if not isinstance(payload, dict):
        raise MonitorInputError("live account payload must be a JSON object")
    return payload


def _positions_from_account_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    positions = payload.get("positions")
    if not isinstance(positions, list):
        raise MonitorInputError("live account payload positions must be a JSON array")
    return [item for item in positions if isinstance(item, dict)]


def _collect_live_position_confirmation(
    task: dict[str, Any],
    *,
    snapshot_at_ms: int | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    payload = _collect_live_account_payload(task)
    blocker = _live_payload_blocker(payload)
    if blocker is not None:
        raise MonitorInputError(f"live position confirmation failed: {blocker}")
    target = _find_position(task, _positions_from_account_payload(payload))
    if target is None:
        raise MonitorInputError("live position confirmation failed: live position not found")

    return _position_snapshot_for_task(
        task,
        target,
        account_payload=payload,
        snapshot_at_ms=snapshot_at_ms,
        language=language,
    )


def _position_snapshot_for_task(
    task: dict[str, Any],
    position: dict[str, Any],
    *,
    account_payload: dict[str, Any] | None = None,
    snapshot_at_ms: int | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    resolved_language = _normalize_language(language)
    quantity = _position_size(position)
    snapshot: dict[str, Any] = {
        "symbol": task["symbol"],
        "position_side": task["position_side"],
        "quantity": quantity,
        "entry_price": _snapshot_value(_entry_price_for_position(position), language=resolved_language),
        "current_price": _snapshot_value(_current_price_for_position(position, account_payload), language=resolved_language),
        "leverage": _snapshot_value(_first_present(position, ("leverage",)), language=resolved_language),
        "margin_type": _snapshot_value(_first_present(position, ("margin_type", "marginType")), language=resolved_language),
        "available_quantity": _snapshot_value(
            _first_present(
                position,
                ("available_quantity", "availableQuantity", "availableQty", "availQty", "available"),
            ),
            language=resolved_language,
        ),
        "liquidation_price": _snapshot_value(
            _first_present(
                position,
                ("liquidation_price", "liquidationPrice", "liq_price", "liqPrice"),
            ),
            language=resolved_language,
        ),
        "position_updated_time": _snapshot_value(
            _first_present(position, ("updated_time", "updatedTime", "updateTime", "updated_at_ms")),
            language=resolved_language,
        ),
        "account_available_balance": _snapshot_value(_account_available_balance(account_payload), language=resolved_language),
        "confirmation_snapshot_time": _snapshot_time_label(snapshot_at_ms, language=resolved_language),
    }
    pnl_value = _first_present(position, ("unrealizePnl", "unrealizedPnl", "unrealized_pnl"))
    if pnl_value is not None and str(pnl_value).strip() != "":
        snapshot["unrealized_pnl"] = str(pnl_value).strip()
    condition_snapshot = _condition_snapshot_for_task(
        task,
        position,
        account_payload=account_payload,
        language=resolved_language,
    )
    if condition_snapshot is not None:
        snapshot["condition_snapshot"] = condition_snapshot
    return snapshot


def _condition_snapshot_for_task(
    task: dict[str, Any],
    position: dict[str, Any],
    *,
    account_payload: dict[str, Any] | None = None,
    language: str | None = None,
) -> dict[str, str] | None:
    resolved_language = _normalize_language(language)
    metric = task["condition"]["metric"]
    current_value: Any | None = None
    if metric == "unrealized_pnl":
        current_value = _first_present(
            position,
            ("unrealizePnl", "unrealizedPnl", "unrealized_pnl"),
        )
    if current_value is None or str(current_value).strip() == "":
        return None
    return {
        "metric": metric,
        "label": _metric_label(metric, language=resolved_language),
        "current_value": str(current_value).strip(),
    }


def _entry_price_for_position(position: dict[str, Any]) -> Any:
    direct_value = _first_present(
        position,
        (
            "entry_price",
            "entryPrice",
            "avg_entry_price",
            "avgEntryPrice",
            "avgOpenPrice",
            "averageOpenPrice",
            "open_price",
            "openPrice",
        ),
    )
    if direct_value is not None:
        return direct_value

    notional_value = _first_present(position, ("openValue", "notional"))
    quantity_value = _first_present(position, ("size", "quantity", "qty"))
    try:
        notional = _decimal_from_any(notional_value, "position.notional")
        quantity = _decimal_from_any(quantity_value, "position.quantity")
    except MonitorInputError:
        return None
    if quantity <= 0:
        return None
    return str(notional / quantity)


def _current_price_for_position(
    position: dict[str, Any],
    account_payload: dict[str, Any] | None,
) -> Any:
    position_value = _first_present(
        position,
        ("mark_price", "markPrice", "last_price", "lastPrice", "current_price", "price"),
    )
    if position_value is not None:
        return position_value
    if not isinstance(account_payload, dict):
        return None
    market_snapshot = account_payload.get("market_snapshot")
    if not isinstance(market_snapshot, dict):
        return None
    return _first_present(market_snapshot, ("current_price", "currentPrice", "markPrice", "lastPrice", "price"))


def _account_available_balance(account_payload: dict[str, Any] | None) -> Any:
    if not isinstance(account_payload, dict):
        return None
    account_snapshot = account_payload.get("account_snapshot")
    if not isinstance(account_snapshot, dict):
        return None
    return _first_present(account_snapshot, ("available_balance", "availableBalance"))


def _missing_value_label(language: str = "zh") -> str:
    return "not returned" if _normalize_language(language) == "en" else "未返回"


def _snapshot_value(value: Any, *, language: str = "zh") -> str:
    text = "" if value is None else str(value).strip()
    if text in {"", "未返回", "not returned"}:
        return _missing_value_label(language)
    return text


def _snapshot_time_label(snapshot_at_ms: int | None, *, language: str = "zh") -> str:
    if snapshot_at_ms is None:
        return _missing_value_label(language)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snapshot_at_ms / 1000))


def _live_payload_blocker(payload: dict[str, Any]) -> str | None:
    if payload.get("partial"):
        return "live_data_partial"
    degraded_reasons = payload.get("degraded_reasons")
    if isinstance(degraded_reasons, list) and degraded_reasons:
        return "live_data_degraded"
    return None


def _live_not_executed_output(
    task: dict[str, Any],
    reason: str,
    *,
    status: str = "active",
    detail: str | None = None,
) -> dict[str, Any]:
    result = {
        "triggered": False,
        "reason": reason,
        "execution_delegate": "weex-trader-skill",
    }
    if detail:
        result["detail"] = detail
    return {
        "task_id": task["task_id"],
        "status": status,
        "result": result,
        "thread_report": render_live_thread_report(task, result, None),
    }


def _append_live_event(task_id: str, event_type: str, output: dict[str, Any], created_at_ms: int) -> None:
    with _connect() as conn:
        _append_event(conn, task_id, event_type, output, created_at_ms=created_at_ms)


def _live_client_order_id(task_id: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in f"monitor_{task_id}")
    return normalized[:36]


def render_live_thread_report(
    task: dict[str, Any],
    result: dict[str, Any],
    exchange_response: dict[str, Any] | None,
) -> str:
    if result.get("triggered") and exchange_response is not None:
        snapshot = result.get("trigger_snapshot", {})
        return (
            f"WEEX monitor {task['task_id']} Live close order submitted: "
            f"{snapshot.get('symbol')} {snapshot.get('position_side')} "
            f"{snapshot.get('unrealized_pnl')} {snapshot.get('operator')} {snapshot.get('threshold')}. "
            f"Exchange response: {exchange_response}."
        )
    return (
        f"WEEX monitor {task['task_id']} live check did not submit a close order: "
        f"{result.get('reason', 'unknown_reason')}."
    )


def render_thread_report(output: dict[str, Any]) -> str:
    task_id = str(output.get("task_id", "unknown"))
    result = output.get("result", {})
    if not isinstance(result, dict):
        raise MonitorInputError("result output must be a JSON object")
    if result.get("triggered"):
        snapshot = result.get("trigger_snapshot", {})
        close_order = result.get("close_order", {})
        return (
            f"WEEX monitor {task_id} dry-run triggered: "
            f"{snapshot.get('symbol')} {snapshot.get('position_side')} "
            f"{snapshot.get('unrealized_pnl')} {snapshot.get('operator')} {snapshot.get('threshold')}. "
            f"Planned close order: {close_order}. "
            "Real-account authorization is required before a real order can be submitted. "
            "No live order was submitted by weex-monitor-skill."
        )
    return (
        f"WEEX monitor {task_id} dry-run not triggered: "
        f"{result.get('reason', 'unknown_reason')}. "
        "No live order was submitted by weex-monitor-skill."
    )


@contextmanager
def _connect() -> Any:
    home = monitor_home()
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS monitor_tasks (
            task_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            task_type TEXT NOT NULL,
            profile TEXT NOT NULL,
            symbol TEXT NOT NULL,
            position_side TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            task_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS monitor_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS monitor_confirmations (
            confirmation_token TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            task_hash TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            used_at_ms INTEGER,
            task_json TEXT NOT NULL,
            confirmation_text TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_tasks_status ON monitor_tasks(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_events_task_id ON monitor_events(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_confirmations_task_id ON monitor_confirmations(task_id)")


def _upsert_task(conn: sqlite3.Connection, task: dict[str, Any], *, updated_at_ms: int) -> None:
    conn.execute(
        """
        INSERT INTO monitor_tasks (
            task_id, status, task_type, profile, symbol, position_side, created_at_ms, updated_at_ms, task_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            status = excluded.status,
            task_type = excluded.task_type,
            profile = excluded.profile,
            symbol = excluded.symbol,
            position_side = excluded.position_side,
            updated_at_ms = excluded.updated_at_ms,
            task_json = excluded.task_json
        """,
        (
            task["task_id"],
            task["status"],
            task["task_type"],
            task["profile"],
            task["symbol"],
            task["position_side"],
            int(task.get("created_at_ms", updated_at_ms)),
            updated_at_ms,
            json.dumps(task, ensure_ascii=False, sort_keys=True),
        ),
    )


def _ensure_can_write_draft_task(conn: sqlite3.Connection, task: dict[str, Any]) -> None:
    row = conn.execute(
        "SELECT status FROM monitor_tasks WHERE task_id = ?",
        (task["task_id"],),
    ).fetchone()
    if row is not None and row["status"] != "draft":
        raise MonitorInputError(
            f"task_id {task['task_id']} already exists as non-draft status {row['status']}"
        )


def _load_task_by_id(task_id: str) -> dict[str, Any] | None:
    if not db_path().exists():
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT task_json FROM monitor_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["task_json"])


def _append_event(
    conn: sqlite3.Connection,
    task_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    created_at_ms: int,
) -> None:
    conn.execute(
        """
        INSERT INTO monitor_events (task_id, event_type, created_at_ms, payload_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            task_id,
            event_type,
            created_at_ms,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )


def _store_confirmation(
    conn: sqlite3.Connection,
    *,
    confirmation_token: str,
    task: dict[str, Any],
    task_hash: str,
    confirmation_text: str,
    created_at_ms: int,
) -> None:
    conn.execute(
        """
        INSERT INTO monitor_confirmations (
            confirmation_token, task_id, task_hash, created_at_ms, used_at_ms, task_json, confirmation_text
        ) VALUES (?, ?, ?, ?, NULL, ?, ?)
        ON CONFLICT(confirmation_token) DO UPDATE SET
            task_id = excluded.task_id,
            task_hash = excluded.task_hash,
            created_at_ms = excluded.created_at_ms,
            used_at_ms = NULL,
            task_json = excluded.task_json,
            confirmation_text = excluded.confirmation_text
        """,
        (
            confirmation_token,
            task["task_id"],
            task_hash,
            created_at_ms,
            json.dumps(task, ensure_ascii=False, sort_keys=True),
            confirmation_text,
        ),
    )


def _consume_confirmation_token(
    conn: sqlite3.Connection,
    *,
    confirmation_token: str,
    task: dict[str, Any],
    used_at_ms: int,
) -> None:
    row = conn.execute(
        """
        SELECT task_id, task_hash, used_at_ms
        FROM monitor_confirmations
        WHERE confirmation_token = ?
        """,
        (confirmation_token,),
    ).fetchone()
    if row is None:
        raise MonitorInputError("confirmation-token was not rendered by confirm-text")
    if row["used_at_ms"] is not None:
        raise MonitorInputError("confirmation-token has already been used")
    if row["task_id"] != task["task_id"]:
        raise MonitorInputError("confirmation-token does not match task_id")
    if row["task_hash"] != _confirmation_fingerprint(task):
        raise MonitorInputError("confirmation-token does not match monitor task details")
    conn.execute(
        "UPDATE monitor_confirmations SET used_at_ms = ? WHERE confirmation_token = ?",
        (used_at_ms, confirmation_token),
    )


def _validate_live_confirmation_token(
    raw_task: dict[str, Any],
    confirmation_token: str | None,
    *,
    duration_seconds: float,
) -> None:
    if confirmation_token is None or str(confirmation_token).strip() == "":
        raise MonitorInputError("live confirmation token is required before starting live monitor")
    rendered_live_snapshot = raw_task.get("live_position_confirmation")
    if not isinstance(rendered_live_snapshot, dict):
        raise MonitorInputError("live confirmation snapshot is required before starting live monitor")

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT task_id, task_hash, used_at_ms, task_json
            FROM monitor_confirmations
            WHERE confirmation_token = ?
            """,
            (str(confirmation_token).strip(),),
        ).fetchone()
    if row is None:
        raise MonitorInputError("live confirmation token was not rendered by confirm-text-live")
    if row["used_at_ms"] is not None:
        raise MonitorInputError("live confirmation token has already been used")

    stored_task = json.loads(row["task_json"])
    if row["task_id"] != normalize_task(raw_task)["task_id"]:
        raise MonitorInputError("live confirmation token does not match task_id")
    if row["task_hash"] != _confirmation_fingerprint(raw_task):
        raise MonitorInputError("live confirmation token does not match monitor task details")
    stored_live_snapshot = stored_task.get("live_position_confirmation")
    if not isinstance(stored_live_snapshot, dict):
        raise MonitorInputError("live confirmation token was not rendered by confirm-text-live")
    if stored_live_snapshot != rendered_live_snapshot:
        raise MonitorInputError("live confirmation snapshot does not match rendered confirmation")
    stored_duration = stored_task.get("live_run_duration_seconds")
    if stored_duration is None:
        raise MonitorInputError("live confirmation duration is missing from rendered confirmation")
    if Decimal(str(stored_duration)) != Decimal(str(duration_seconds)):
        raise MonitorInputError("live confirmation duration does not match rendered confirmation")


def _confirmation_fingerprint(raw_task: dict[str, Any]) -> str:
    task = normalize_task(raw_task)
    payload: dict[str, Any] = {
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "profile": task["profile"],
        "market": task["market"],
        "symbol": task["symbol"],
        "position_side": task["position_side"],
        "frequency_seconds": task["frequency_seconds"],
        "condition": task["condition"],
        "action": task["action"],
        "callback": task["callback"],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _new_confirmation_token() -> str:
    return f"mconf_{uuid.uuid4().hex}"


def _position_execution_key(raw_task: dict[str, Any]) -> tuple[str, str, str, str]:
    task = normalize_task(raw_task)
    return (
        task["profile"],
        task["market"],
        task["symbol"],
        task["position_side"],
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        raise MonitorInputError(f"{key} is required")
    return str(value).strip()


def _normalize_position_side(value: Any) -> str:
    if value is None:
        raise MonitorInputError("position_side is required")
    side = str(value).strip().upper()
    if side not in VALID_POSITION_SIDES:
        raise MonitorInputError("position_side must be LONG or SHORT")
    return side


def _normalize_market(value: Any) -> str:
    market = str(value or "futures").strip().lower()
    if market not in VALID_MARKETS:
        raise MonitorInputError("market must be futures")
    return market


def _normalize_condition(value: Any, task_type: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise MonitorInputError("condition must be a JSON object")

    metric = _required_string(value, "metric")
    if metric != "unrealized_pnl":
        raise MonitorInputError(f"{task_type} condition metric must be unrealized_pnl")

    operator = _required_string(value, "operator")
    if operator not in VALID_OPERATORS:
        raise MonitorInputError("condition.operator must be one of >, >=, <, <=")

    threshold_value = _decimal_from_any(value.get("threshold"), "condition.threshold")
    if not threshold_value.is_finite():
        raise MonitorInputError("condition.threshold must be finite")
    threshold = str(value.get("threshold")).strip()
    return {
        "metric": metric,
        "operator": operator,
        "threshold": threshold,
    }


def _normalize_action(value: Any, position_side: str, task_type: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise MonitorInputError("action must be a JSON object")
    action_type = _required_string(value, "type")
    if action_type != "market_close":
        raise MonitorInputError("only market_close action is supported")
    target = _normalize_position_side(value.get("target"))
    if target != position_side:
        raise MonitorInputError("action.target must match position_side")

    action = {
        "type": action_type,
        "target": target,
    }
    quantity = value.get("quantity")
    if quantity is not None and str(quantity).strip() != "":
        action["quantity"] = _positive_decimal_text(quantity, "action.quantity")
    return action


def _normalize_callback(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise MonitorInputError("callback must be a JSON object")
    callback_type = _required_string(value, "type")
    if callback_type not in VALID_CALLBACK_TYPES:
        raise MonitorInputError("only current_thread callback is supported")
    return {"type": callback_type}


def _normalize_frequency(value: Any) -> int:
    if value is None:
        return DEFAULT_FREQUENCY_SECONDS
    try:
        frequency = int(value)
    except (TypeError, ValueError) as exc:
        raise MonitorInputError("frequency_seconds must be an integer") from exc
    if frequency < MIN_FREQUENCY_SECONDS:
        raise MonitorInputError(f"frequency_seconds must be >= {MIN_FREQUENCY_SECONDS}")
    return frequency


def _normalize_duration_seconds(value: Any) -> float:
    duration = _decimal_from_any(value, "duration-seconds")
    if not duration.is_finite():
        raise MonitorInputError("duration-seconds must be finite")
    if duration <= 0:
        raise MonitorInputError("duration-seconds must be > 0")
    return float(duration)


def _normalize_reporting_interval_seconds(value: Any) -> int:
    if value is None:
        return DEFAULT_AGENT_REPORTING_INTERVAL_SECONDS
    try:
        interval = int(value)
    except (TypeError, ValueError) as exc:
        raise MonitorInputError("reporting_interval_seconds must be an integer") from exc
    if interval < MIN_AGENT_REPORTING_INTERVAL_SECONDS:
        raise MonitorInputError(
            f"reporting_interval_seconds must be >= {MIN_AGENT_REPORTING_INTERVAL_SECONDS}"
        )
    if interval % 60 != 0:
        raise MonitorInputError("reporting_interval_seconds must be a whole-minute interval")
    return interval


def _normalize_codex_reporting_interval_seconds(value: Any) -> int:
    return _normalize_reporting_interval_seconds(value)


def _minute_interval_token(interval_seconds: int) -> str:
    interval = _normalize_reporting_interval_seconds(interval_seconds)
    return f"{interval // 60}m"


def _one_line(value: str) -> str:
    return " ".join(str(value).split())


def _agent_reporting_metadata_from_task(
    raw_task: dict[str, Any],
    *,
    reporting_interval_seconds: Any = None,
) -> dict[str, Any]:
    if reporting_interval_seconds is not None:
        return build_agent_reporting_metadata(
            raw_task,
            interval_seconds=_normalize_reporting_interval_seconds(reporting_interval_seconds),
        )
    agent_reporting = raw_task.get("agent_reporting")
    if isinstance(agent_reporting, dict) and agent_reporting.get("enabled"):
        return dict(agent_reporting)
    reporting = raw_task.get("codex_reporting")
    if isinstance(reporting, dict) and reporting.get("enabled"):
        interval = _normalize_reporting_interval_seconds(reporting.get("interval_seconds"))
        return build_agent_reporting_metadata(raw_task, interval_seconds=interval)
    return build_agent_reporting_metadata(
        raw_task,
        interval_seconds=DEFAULT_AGENT_REPORTING_INTERVAL_SECONDS,
    )


def _reporting_metadata_from_task(
    raw_task: dict[str, Any],
    *,
    reporting_interval_seconds: Any = None,
) -> dict[str, Any]:
    return _agent_reporting_metadata_from_task(
        raw_task,
        reporting_interval_seconds=reporting_interval_seconds,
    )["runtimes"]["codex"]


def _normalize_language(value: str | None) -> str:
    language = str(value or "zh").strip().lower()
    if language not in VALID_LANGUAGES:
        raise MonitorInputError("language must be zh or en")
    return language


def _confirmation_reply_text(language: str) -> str:
    return CONFIRMATION_REPLY_TEXT_BY_LANGUAGE[_normalize_language(language)]


def _iterations_for_duration_seconds(duration_seconds: float, frequency_seconds: int) -> int:
    if frequency_seconds < 1:
        raise MonitorInputError("frequency_seconds must be >= 1")
    return max(1, math.ceil(duration_seconds / frequency_seconds))


def _position_side_label(position_side: str, *, language: str = "zh") -> str:
    if language == "en":
        return "long position" if position_side == "LONG" else "short position"
    return "多单" if position_side == "LONG" else "空单"


def _metric_label(metric: str, *, language: str = "zh") -> str:
    metric_labels = {
        "unrealized_pnl": "未实现盈亏",
    }
    if language == "en":
        metric_labels = {
            "unrealized_pnl": "Unrealized PnL",
        }
    return metric_labels.get(metric, metric)


def _condition_label(condition: dict[str, str], *, language: str = "zh") -> str:
    metric = _metric_label(condition["metric"], language=language)
    return f"{metric} {condition['operator']} {condition['threshold']}"


def _current_condition_line(position_snapshot: dict[str, Any], *, language: str = "zh") -> str | None:
    condition_snapshot = position_snapshot.get("condition_snapshot")
    if not isinstance(condition_snapshot, dict):
        return None
    if condition_snapshot.get("metric") == "unrealized_pnl":
        return None
    label = condition_snapshot.get("label")
    current_value = condition_snapshot.get("current_value")
    if label is None or current_value is None or str(current_value).strip() == "":
        return None
    if language == "en":
        return f"Current {label}: {current_value}"
    return f"当前{label}: {current_value}"


def _position_detail_line(position_snapshot: dict[str, Any], *, language: str = "zh") -> str:
    detail_fields = (
        ("开仓均价", "entry_price"),
        ("标记/最新价", "current_price"),
        ("杠杆", "leverage"),
        ("保证金模式", "margin_type"),
        ("可平数量", "available_quantity"),
        ("强平价", "liquidation_price"),
        ("仓位更新时间", "position_updated_time"),
        ("账户可用余额", "account_available_balance"),
        ("确认快照时间", "confirmation_snapshot_time"),
    )
    if language == "en":
        detail_fields = (
            ("entry price", "entry_price"),
            ("mark/latest price", "current_price"),
            ("leverage", "leverage"),
            ("margin mode", "margin_type"),
            ("closable quantity", "available_quantity"),
            ("liquidation price", "liquidation_price"),
            ("position update time", "position_updated_time"),
            ("account available balance", "account_available_balance"),
            ("confirmation snapshot time", "confirmation_snapshot_time"),
        )
    details = [
        f"{label}: {_snapshot_value(position_snapshot.get(key), language=language)}"
        for label, key in detail_fields
    ]
    if language == "en":
        return "Position details: " + ", ".join(details)
    return "仓位明细: " + ", ".join(details)


def _action_label(action: dict[str, str], *, language: str = "zh") -> str:
    if action["type"] == "market_close":
        if action.get("quantity"):
            quantity_label = action["quantity"]
        else:
            quantity_label = "matched position size at trigger time" if language == "en" else "触发时匹配持仓数量"
        if language == "en":
            side_label = "long" if action["target"] == "LONG" else "short"
            return f"Submit a real market close-{side_label} order, close quantity: {quantity_label}"
        return f"提交真实市价平{_position_side_label(action['target'])}，平仓数量: {quantity_label}"
    return action["type"]


def _duration_label(duration_seconds: float, *, language: str = "zh") -> str:
    if duration_seconds % 3600 == 0:
        hours = duration_seconds / 3600
        if language == "en":
            unit = "hour" if hours == 1 else "hours"
            return f"{hours:g} {unit}"
        return f"{hours:g} 小时"
    if duration_seconds % 60 == 0:
        minutes = duration_seconds / 60
        if language == "en":
            unit = "minute" if minutes == 1 else "minutes"
            return f"{minutes:g} {unit}"
        return f"{minutes:g} 分钟"
    if language == "en":
        unit = "second" if duration_seconds == 1 else "seconds"
        return f"{duration_seconds:g} {unit}"
    return f"{duration_seconds:g} 秒"


def _positive_decimal_text(value: Any, field_name: str) -> str:
    decimal_value = _decimal_from_any(value, field_name)
    if not decimal_value.is_finite():
        raise MonitorInputError(f"{field_name} must be finite")
    if decimal_value <= 0:
        raise MonitorInputError(f"{field_name} must be > 0")
    return str(value).strip()


def _decimal_from_any(value: Any, field_name: str) -> Decimal:
    if value is None or str(value).strip() == "":
        raise MonitorInputError(f"{field_name} is required")
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise MonitorInputError(f"{field_name} must be numeric") from exc


def _new_task_id() -> str:
    return f"mon_{uuid.uuid4().hex[:24]}"


def _compare(left: Decimal, operator: str, right: Decimal) -> bool:
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    raise MonitorInputError(f"unsupported operator: {operator}")


def _find_position(task: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = str(_first_present(position, ("symbol", "contract", "instId")) or "").upper()
        side = str(_first_present(position, ("side", "positionSide", "position_side", "holdSide")) or "").upper()
        if symbol != task["symbol"] or side != task["position_side"]:
            continue
        try:
            if _decimal_from_any(_position_size(position), "position.size") <= 0:
                continue
        except MonitorInputError:
            continue
        return position
    return None


def _first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _position_size(position: dict[str, Any]) -> str:
    value = _first_present(position, ("size", "positionAmt", "quantity", "qty", "available"))
    _decimal_from_any(value, "position.size")
    return str(value)


def _close_order_side(position_side: str) -> str:
    return "SELL" if position_side == "LONG" else "BUY"


def _read_json_arg(value: str | None, file_path: str | None, *, name: str) -> Any:
    if value and file_path:
        raise MonitorInputError(f"use either --{name}-json or --{name}-file, not both")
    if file_path:
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    if value:
        return json.loads(value)
    raise MonitorInputError(f"--{name}-json or --{name}-file is required")


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WEEX monitor task helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("preview", "confirm", "confirm-text"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--task-json")
        sub.add_argument("--task-file")
        if name == "confirm-text":
            sub.add_argument("--language", choices=("zh", "en"), default=None)
        if name == "confirm":
            sub.add_argument("--confirm-monitor", action="store_true")
            sub.add_argument("--confirmation-token")

    live_confirm = subparsers.add_parser("confirm-text-live")
    live_confirm.add_argument("--task-json")
    live_confirm.add_argument("--task-file")
    live_confirm.add_argument("--duration-seconds", type=float)
    live_confirm.add_argument("--reporting-interval-seconds", type=int)
    live_confirm.add_argument("--language", choices=("zh", "en"), default=None)

    eval_pnl = subparsers.add_parser("evaluate-pnl")
    eval_pnl.add_argument("--task-json")
    eval_pnl.add_argument("--task-file")
    eval_pnl.add_argument("--positions-json")
    eval_pnl.add_argument("--positions-file")

    subparsers.add_parser("list")

    events = subparsers.add_parser("events")
    events.add_argument("--task-id")

    run_once = subparsers.add_parser("run-once")
    run_once.add_argument("--dry-run", action="store_true")
    run_once.add_argument("--task-id")
    run_once.add_argument("--positions-json")
    run_once.add_argument("--positions-file")

    run_live_once_parser = subparsers.add_parser("run-live-once")
    run_live_once_parser.add_argument("--confirm-live", action="store_true")
    run_live_once_parser.add_argument("--task-id")

    run_loop = subparsers.add_parser("run-loop")
    run_loop.add_argument("--dry-run", action="store_true")
    run_loop.add_argument("--confirm-live", action="store_true")
    run_loop.add_argument("--task-id")
    run_loop.add_argument("--iterations", type=int, default=1)
    run_loop.add_argument("--sleep-seconds", type=float)
    run_loop.add_argument("--positions-sequence-json")
    run_loop.add_argument("--positions-sequence-file")

    confirm_and_run = subparsers.add_parser("confirm-and-run-loop")
    confirm_and_run.add_argument("--task-json")
    confirm_and_run.add_argument("--task-file")
    confirm_and_run.add_argument("--confirm-monitor", action="store_true")
    confirm_and_run.add_argument("--confirmation-token")
    confirm_and_run.add_argument("--confirm-live", action="store_true")
    confirm_and_run.add_argument("--duration-seconds", type=float, required=True)
    confirm_and_run.add_argument("--reporting-interval-seconds", type=int)
    confirm_and_run.add_argument("--sleep-seconds", type=float)

    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("--task-id", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "preview":
            task = _read_json_arg(args.task_json, args.task_file, name="task")
            _print_json(normalize_task(task))
        elif args.command == "confirm":
            task = _read_json_arg(args.task_json, args.task_file, name="task")
            _print_json(
                confirm_task(
                    task,
                    confirm_monitor=args.confirm_monitor,
                    confirmation_token=args.confirmation_token,
                )
            )
        elif args.command == "confirm-text":
            task = _read_json_arg(args.task_json, args.task_file, name="task")
            _print_json(prepare_confirmation(task, language=args.language))
        elif args.command == "confirm-text-live":
            task = _read_json_arg(args.task_json, args.task_file, name="task")
            _print_json(
                prepare_live_confirmation(
                    task,
                    duration_seconds=args.duration_seconds,
                    reporting_interval_seconds=args.reporting_interval_seconds,
                    language=args.language,
                )
            )
        elif args.command == "evaluate-pnl":
            task = _read_json_arg(args.task_json, args.task_file, name="task")
            positions = _read_json_arg(args.positions_json, args.positions_file, name="positions")
            _print_json(evaluate_pnl_task(task, positions))
        elif args.command == "list":
            _print_json(load_tasks())
        elif args.command == "events":
            _print_json(load_events(args.task_id))
        elif args.command == "run-once":
            if not args.dry_run:
                raise MonitorInputError("run-once currently requires --dry-run")
            positions = _read_json_arg(args.positions_json, args.positions_file, name="positions")
            _print_json(run_once_dry_run(positions, task_id=args.task_id))
        elif args.command == "run-live-once":
            _print_json(run_live_once(confirm_live=args.confirm_live, task_id=args.task_id))
        elif args.command == "run-loop":
            if args.dry_run and args.confirm_live:
                raise MonitorInputError("run-loop uses either --dry-run or --confirm-live, not both")
            if args.confirm_live:
                _print_json(
                    run_live_loop(
                        confirm_live=True,
                        iterations=args.iterations,
                        task_id=args.task_id,
                        sleep_seconds=args.sleep_seconds,
                    )
                )
            else:
                if not args.dry_run:
                    raise MonitorInputError("run-loop requires --dry-run or --confirm-live")
                positions_sequence = _read_json_arg(
                    args.positions_sequence_json,
                    args.positions_sequence_file,
                    name="positions-sequence",
                )
                _print_json(
                    run_loop_dry_run(
                        positions_sequence,
                        iterations=args.iterations,
                        task_id=args.task_id,
                        sleep_seconds=args.sleep_seconds,
                    )
                )
        elif args.command == "confirm-and-run-loop":
            task = _read_json_arg(args.task_json, args.task_file, name="task")
            _print_json(
                confirm_and_run_live_loop(
                    task,
                    confirm_monitor=args.confirm_monitor,
                    confirmation_token=args.confirmation_token,
                    confirm_live=args.confirm_live,
                    duration_seconds=args.duration_seconds,
                    reporting_interval_seconds=args.reporting_interval_seconds,
                    sleep_seconds=args.sleep_seconds,
                )
            )
        elif args.command == "cancel":
            _print_json(cancel_task(args.task_id))
        else:
            parser.error(f"unsupported command: {args.command}")
    except (MonitorInputError, json.JSONDecodeError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
