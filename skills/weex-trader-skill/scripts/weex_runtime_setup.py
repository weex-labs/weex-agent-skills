#!/usr/bin/env python3
"""Cross-platform runtime setup helper for WEEX private CLI flows."""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from typing import Any, Optional

from weex_agent_state import refresh_agent_records, requirements_path
from weex_gui_bootstrap import requirements_lock_path


def output_json(payload: dict[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
        return
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False))


def run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def build_setup_report(language: Optional[str] = None) -> dict[str, Any]:
    pip_version = run_command([sys.executable, "-m", "pip", "--version"])
    available_before = pip_version["returncode"] == 0

    ensurepip_attempted = False
    ensurepip_result: Optional[dict[str, Any]] = None
    if not available_before:
        ensurepip_attempted = True
        ensurepip_result = run_command([sys.executable, "-m", "ensurepip", "--upgrade"])
        if ensurepip_result["returncode"] == 0:
            pip_version = run_command([sys.executable, "-m", "pip", "--version"])

    install_result: dict[str, Any]
    if pip_version["returncode"] == 0:
        install_result = run_command(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--require-hashes",
                "-r",
                str(requirements_lock_path()),
            ]
        )
    else:
        install_result = {
            "command": [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "-r",
                str(requirements_lock_path()),
            ],
            "returncode": 1,
            "stdout": "",
            "stderr": "pip is unavailable for this interpreter. ensurepip could not recover it.",
        }

    importlib.invalidate_caches()
    runtime_records = refresh_agent_records(preferred_language=language, command="env.setup")
    runtime_state = runtime_records["runtime"]
    ok = (
        install_result["returncode"] == 0
        and bool(runtime_state["host"]["requirements_ready"])
        and bool(runtime_state["env_validation"]["ok"])
    )

    return {
        "ok": ok,
        "python_executable": sys.executable,
        "requirements_path": str(requirements_path()),
        "requirements_lock_path": str(requirements_lock_path()),
        "pip": {
            "available_before": available_before,
            "ensurepip_attempted": ensurepip_attempted,
            "version_check": pip_version,
            "ensurepip": ensurepip_result,
            "install": install_result,
        },
        "runtime": runtime_state,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install WEEX Python dependencies for the current interpreter and refresh runtime preflight state.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Optional zh/en language override used when refreshing agent state.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output for easier reading.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_setup_report(language=args.language)
    output_json(payload, args.pretty)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
