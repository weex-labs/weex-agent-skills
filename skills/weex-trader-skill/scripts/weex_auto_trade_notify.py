#!/usr/bin/env python3
"""One-shot, best-effort local notification delivery for auto-trade claims."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol


class NotificationState(Protocol):
    def claim_notifications(
        self,
        *,
        now: datetime | None = None,
        notification_key: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def complete_notification(
        self,
        *,
        notification_key: str,
        outcome: str,
        now: datetime | None = None,
    ) -> dict[str, Any]: ...


NotificationAdapter = Callable[[dict[str, Any]], Any]


def dispatch_notification_claims(
    state: NotificationState,
    adapter: NotificationAdapter,
    *,
    now: datetime | None = None,
    notification_key: str | None = None,
) -> list[dict[str, Any]]:
    """Claim, attempt once, and record a result without changing business state."""
    results: list[dict[str, Any]] = []
    if notification_key is None:
        claims = state.claim_notifications(now=now)
    else:
        claims = state.claim_notifications(now=now, notification_key=notification_key)
    for claim in claims:
        try:
            adapter_result = adapter(claim)
            outcome = "UNKNOWN" if adapter_result == "UNKNOWN" else "DELIVERED"
        except Exception:
            outcome = "FAILED"
        state.complete_notification(
            notification_key=claim["notification_key"],
            outcome=outcome,
            now=now,
        )
        results.append(
            {
                "notification_key": claim["notification_key"],
                "kind": claim["kind"],
                "status": outcome,
            }
        )
    return results


def run_notification_worker(
    *,
    state_path: str | Path,
    notification_key: str,
    not_before: datetime,
    adapter: NotificationAdapter,
    now_provider: Callable[[], datetime] | None = None,
    sleep: Callable[[float], Any] = time.sleep,
) -> list[dict[str, Any]]:
    """Wait for one summary window, attempt its notification once, then exit."""
    if not isinstance(notification_key, str) or not notification_key.startswith("summary:"):
        raise ValueError("notification_key must identify an accepted summary")
    deadline = _aware_utc(not_before, "not_before")
    clock = now_provider or (lambda: datetime.now(UTC))
    while True:
        current = _aware_utc(clock(), "now_provider result")
        remaining_seconds = (deadline - current).total_seconds()
        if remaining_seconds <= 0:
            break
        sleep(remaining_seconds)

    from weex_auto_trade_state import AutoTradeState

    state = AutoTradeState(Path(state_path))
    state.initialize()
    return dispatch_notification_claims(
        state,
        adapter,
        now=current,
        notification_key=notification_key,
    )


def launch_notification_worker(
    *,
    state_path: str | Path,
    notification_key: str,
    not_before: datetime,
) -> None:
    """Detach a credential-free worker for one accepted-summary notification key."""
    deadline = _aware_utc(not_before, "not_before")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--state-path",
        str(Path(state_path)),
        "--notification-key",
        notification_key,
        "--not-before",
        deadline.isoformat(timespec="microseconds").replace("+00:00", "Z"),
    ]
    popen_options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_options["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_options["start_new_session"] = True
    subprocess.Popen(command, **popen_options)


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("not_before must be an ISO-8601 timestamp") from exc
    return _aware_utc(parsed, "not_before")


def build_notification_text(claim: dict[str, Any]) -> tuple[str, str]:
    strategy = str(claim.get("strategy_name") or "WEEX strategy")
    if claim.get("kind") == "ACCEPTED_SUMMARY":
        title = f"WEEX auto-trade summary: {strategy}"
        modules = ", ".join(str(item) for item in claim.get("modules", []))
        symbols = ", ".join(str(item) for item in claim.get("symbols", []))
        body = (
            f"{claim.get('order_count', 0)} orders; {modules}; {symbols}; "
            f"estimated {claim.get('estimated_amount_u', 'unknown')} U; "
            f"remaining {claim.get('remaining_amount_u', 'unknown')} U"
        )
        return title, body
    title = f"WEEX auto-trade attention: {strategy}"
    body = f"{claim.get('event_type', 'UNKNOWN_EVENT')}; inspect the local event timeline"
    return title, body


class SystemNotificationAdapter:
    """Local OS adapter. Each invocation performs one bounded attempt and never retries."""

    def __init__(self, *, timeout_seconds: float = 7.0) -> None:
        if timeout_seconds <= 5.5:
            raise ValueError("timeout_seconds must exceed the Windows notification display duration")
        self.timeout_seconds = timeout_seconds

    def __call__(self, claim: dict[str, Any]) -> str | None:
        title, body = build_notification_text(claim)
        system = platform.system()
        if system == "Darwin":
            command = [
                "osascript",
                "-e",
                "on run argv",
                "-e",
                "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e",
                "end run",
                title,
                body,
            ]
        elif system == "Windows":
            executable = shutil.which("powershell") or shutil.which("pwsh")
            if executable is None:
                raise RuntimeError("local notification adapter is unavailable")
            command = [
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "param($title,$body) Add-Type -AssemblyName System.Windows.Forms; "
                "$n=New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon=[System.Drawing.SystemIcons]::Information; $n.Visible=$true; "
                "$n.ShowBalloonTip(5000,$title,$body,[System.Windows.Forms.ToolTipIcon]::Info); "
                "Start-Sleep -Milliseconds 5500; $n.Dispose()",
                title,
                body,
            ]
        else:
            executable = shutil.which("notify-send")
            if executable is None:
                raise RuntimeError("local notification adapter is unavailable")
            command = [executable, title, body]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "UNKNOWN"
        if completed.returncode != 0:
            raise RuntimeError("local notification delivery failed")
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deliver one delayed WEEX automated-trading summary notification"
    )
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--notification-key", required=True)
    parser.add_argument("--not-before", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run_notification_worker(
            state_path=args.state_path,
            notification_key=args.notification_key,
            not_before=_parse_time(args.not_before),
            adapter=SystemNotificationAdapter(),
        )
    except Exception:
        return 1
    return 0


__all__ = [
    "NotificationAdapter",
    "SystemNotificationAdapter",
    "build_notification_text",
    "dispatch_notification_claims",
    "launch_notification_worker",
    "run_notification_worker",
]


if __name__ == "__main__":
    raise SystemExit(main())
