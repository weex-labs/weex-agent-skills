#!/usr/bin/env python3
"""Persist and validate pending order confirmations for WEEX trade guard flows."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from weex_agent_state import config_dir


INTENT_FILENAME = "order-intent.json"


def intent_path() -> Path:
    path = config_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path / INTENT_FILENAME


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_risk_signature(
    *,
    profile_name: str,
    market: str,
    trading_mode: str,
    order_preview: dict[str, Any],
    analysis_output: dict[str, Any],
) -> str:
    serialized = json.dumps(
        {
            "profile_name": profile_name,
            "market": market,
            "trading_mode": trading_mode,
            "order_preview": order_preview,
            "alerts": analysis_output.get("alerts", []),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_intent(
    *,
    profile_name: str,
    market: str,
    trading_mode: str = "live",
    environment: dict[str, Any] | None = None,
    order_preview: dict[str, Any],
    raw_order: dict[str, Any],
    analysis_output: dict[str, Any],
    now_ms: int | None = None,
    ttl_seconds: int = 300,
    intent_type: str = "order",
    tp_sl_order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    expires_at = current_ms + (ttl_seconds * 1000)
    payload = {
        "intent_id": uuid.uuid4().hex,
        "intent_type": intent_type,
        "profile_name": profile_name,
        "market": market,
        "trading_mode": trading_mode,
        "created_at": current_ms,
        "expires_at": expires_at,
        "ttl_seconds": ttl_seconds,
        "order_preview": order_preview,
        "raw_order": raw_order,
        "analysis_output": analysis_output,
        "risk_signature": build_risk_signature(
            profile_name=profile_name,
            market=market,
            trading_mode=trading_mode,
            order_preview=order_preview,
            analysis_output=analysis_output,
        ),
    }
    if environment is not None:
        payload["environment"] = environment
    if tp_sl_order is not None:
        payload["tp_sl_order"] = tp_sl_order
    return payload


def save_intent(payload: dict[str, Any]) -> None:
    _atomic_write_json(intent_path(), payload)


def load_intent() -> dict[str, Any] | None:
    path = intent_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def clear_intent() -> None:
    path = intent_path()
    if path.exists():
        path.unlink()


def intent_is_expired(payload: dict[str, Any], *, now_ms: int | None = None) -> bool:
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    expires_at = int(payload.get("expires_at") or 0)
    return expires_at <= current_ms
