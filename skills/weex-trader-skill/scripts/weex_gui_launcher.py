#!/usr/bin/env python3
"""Detached launcher for WEEX GUI entrypoints on tool-managed shells."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from weex_gui_bootstrap import (
    BOOTSTRAP_ACTIVE_ENV,
    _localized as gui_bootstrap_localized,
    managed_venv_python,
    probe_runtime,
)
from weex_profile_language import resolve_language


CONFIG_HOME_ENV = "WEEX_TRADER_SKILL_HOME"
DETACHED_ENV = "WEEX_GUI_DETACHED"
FORCE_FOREGROUND_ENV = "WEEX_GUI_FORCE_FOREGROUND"
DEFAULT_WAIT_TIMEOUT = 8.0
POLL_INTERVAL = 0.2
MAX_LAUNCH_RECORDS = 12
MAX_LOG_BYTES = 256 * 1024
SUPPORTED_VAULT_ACTIONS = ("setup", "unlock", "status", "lock")


TEXTS = {
    "en": {
        "parser_description": "Launch WEEX GUI entrypoints in a detached process.",
        "profile_manager_help": "Launch the profile manager UI in a detached process",
        "vault_manager_help": "Launch the vault manager UI in a detached process",
        "pretty_help": "Pretty-print JSON output",
        "requested_action_help": "Optional vault UI hint: setup and unlock may prompt immediately, while status and lock only focus that state in the UI",
        "unsupported_platform": "Detached GUI launch is only supported on macOS and Windows.",
        "open_missing": "Unable to detach the WEEX GUI because the macOS 'open' command is unavailable.",
        "launch_failed": "Unable to detach the WEEX GUI.",
        "launch_timeout": "Timed out waiting for the detached WEEX GUI process to start.",
    },
    "zh": {
        "parser_description": "以可分离进程方式启动 WEEX 图形界面入口。",
        "profile_manager_help": "以可分离进程方式启动账户管理器 UI",
        "vault_manager_help": "以可分离进程方式启动 Vault 管理 UI",
        "pretty_help": "以更易读的格式输出 JSON",
        "requested_action_help": "可选：指定 Vault UI 的请求动作；setup 和 unlock 可能直接弹出流程，status 和 lock 仅聚焦到对应状态",
        "unsupported_platform": "可分离 GUI 启动目前只支持 macOS 和 Windows。",
        "open_missing": "无法分离启动 WEEX GUI，因为 macOS 上缺少 'open' 命令。",
        "launch_failed": "无法分离启动 WEEX GUI。",
        "launch_timeout": "等待分离后的 WEEX GUI 进程启动超时。",
    },
}


class GuiLaunchError(RuntimeError):
    """Raised when the GUI cannot be detached from the current shell."""


def t(language: str, key: str, **kwargs: object) -> str:
    text = TEXTS.get(language, {}).get(key) or TEXTS["en"][key]
    if kwargs:
        return text.format(**kwargs)
    return text


def config_dir() -> Path:
    raw = os.getenv(CONFIG_HOME_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".weex-trader-skill"


def launch_records_dir() -> Path:
    return config_dir() / "gui-launchers"


def _ensure_launch_records_dir() -> Path:
    path = launch_records_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _isatty(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    if callable(isatty):
        try:
            return bool(isatty())
        except Exception:
            return False
    return False


def should_auto_detach() -> bool:
    if platform.system() not in {"Darwin", "Windows"}:
        return False
    if os.getenv(DETACHED_ENV) == "1":
        return False
    if os.getenv(FORCE_FOREGROUND_ENV) == "1":
        return False
    return not (_isatty(sys.stdin) and _isatty(sys.stdout))


def _prepare_relaunch_argv(language: str, argv: Optional[list[str]]) -> list[str]:
    prepared = [str(item) for item in (argv or [])]
    if "--language" not in prepared:
        prepared = ["--language", language, *prepared]
    return prepared


def _safe_label(label: str) -> str:
    return "".join(ch for ch in label if ch.isalnum() or ch in {"-", "_"}) or "gui"


def _artifact_group_base(path: Path) -> Optional[Path]:
    if path.is_dir():
        if path.suffix == ".app":
            return path.with_suffix("")
        return None
    if path.suffix in {".log", ".pid", ".cmd", ".command"}:
        return path.with_suffix("")
    return None


def _trim_log_file(path: Path, *, max_log_bytes: int = MAX_LOG_BYTES) -> None:
    if max_log_bytes <= 0:
        return
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    if size <= max_log_bytes:
        return
    with path.open("rb") as handle:
        handle.seek(-max_log_bytes, os.SEEK_END)
        data = handle.read()
    path.write_text(data.decode("utf-8", errors="ignore"), encoding="utf-8")


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _prune_launch_records(
    *,
    max_records: int = MAX_LAUNCH_RECORDS,
    max_log_bytes: int = MAX_LOG_BYTES,
) -> None:
    launch_dir = _ensure_launch_records_dir()
    grouped: dict[str, list[Path]] = {}
    for child in launch_dir.iterdir():
        base = _artifact_group_base(child)
        if base is None:
            continue
        grouped.setdefault(str(base), []).append(child)
    if not grouped:
        return

    records: list[tuple[float, str, list[Path]]] = []
    for base, paths in grouped.items():
        latest_mtime = 0.0
        for path in paths:
            try:
                latest_mtime = max(latest_mtime, path.stat().st_mtime)
            except OSError:
                continue
        records.append((latest_mtime, base, paths))
    records.sort(key=lambda item: (item[0], item[1]), reverse=True)

    keep_count = max(0, max_records)
    for _mtime, _base, paths in records[:keep_count]:
        for path in paths:
            if path.suffix == ".log":
                _trim_log_file(path, max_log_bytes=max_log_bytes)

    for _mtime, _base, paths in records[keep_count:]:
        for path in paths:
            _remove_path(path)


def _next_launch_paths(label: str) -> tuple[Path, Path, Path]:
    _prune_launch_records(max_records=max(0, MAX_LAUNCH_RECORDS - 1))
    safe_label = _safe_label(label)
    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    base = _ensure_launch_records_dir() / f"{safe_label}-{suffix}"
    return base, base.with_suffix(".log"), base.with_suffix(".pid")


def _read_pid(path: Path) -> Optional[int]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _require_managed_gui_python(language: str) -> Path:
    runtime_python = managed_venv_python()
    if not runtime_python.exists():
        raise GuiLaunchError(gui_bootstrap_localized(language, "explicit_runtime_required"))
    managed_probe = probe_runtime(str(runtime_python))
    if not managed_probe.usable:
        raise GuiLaunchError(
            f"{gui_bootstrap_localized(language, 'explicit_runtime_required')}\n"
            f"{managed_probe.summary(language)}"
        )
    return runtime_python


def _wait_for_pid(path: Path, timeout: float) -> Optional[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = _read_pid(path)
        if pid is not None:
            return pid
        time.sleep(POLL_INTERVAL)
    return _read_pid(path)


def _raise_launch_error(language: str, message: str, completed: Optional[subprocess.CompletedProcess[str]] = None) -> None:
    detail = ""
    if completed is not None:
        detail = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
    if detail:
        raise GuiLaunchError(f"{message}\n{detail}")
    raise GuiLaunchError(message)


def _launch_on_darwin(
    language: str,
    *,
    entrypoint_path: Path,
    argv: list[str],
    label: str,
    wait_timeout: float,
    python_executable: str | Path | None = None,
) -> dict[str, object]:
    open_binary = shutil_which("open")
    if not open_binary:
        raise GuiLaunchError(t(language, "open_missing"))

    wrapper_base, log_path, pid_path = _next_launch_paths(label)
    wrapper_path = wrapper_base.with_suffix(".app")
    contents_dir = wrapper_path / "Contents"
    macos_dir = contents_dir / "MacOS"
    launcher_path = macos_dir / "launcher"
    plist_path = contents_dir / "Info.plist"
    macos_dir.mkdir(parents=True, exist_ok=True)
    runtime_python = Path(python_executable) if python_executable is not None else Path(sys.executable)
    command_line = " ".join(
        shlex.quote(part)
        for part in [str(runtime_python), str(entrypoint_path), *argv]
    )
    launcher_path.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                f"cd {shlex.quote(str(entrypoint_path.parent))}",
                f"export {DETACHED_ENV}=1",
                f"export {BOOTSTRAP_ACTIVE_ENV}=1",
                f"echo $$ > {shlex.quote(str(pid_path))}",
                f"exec {command_line} >> {shlex.quote(str(log_path))} 2>&1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    launcher_path.chmod(0o700)
    bundle_identifier = f"local.weex-trader-skill.{_safe_label(wrapper_base.name).replace('-', '_')}"
    plist_path.write_text(
        "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
                '<plist version="1.0">',
                "<dict>",
                "  <key>CFBundleExecutable</key>",
                "  <string>launcher</string>",
                "  <key>CFBundleIdentifier</key>",
                f"  <string>{bundle_identifier}</string>",
                "  <key>CFBundleName</key>",
                f"  <string>{_safe_label(label)}</string>",
                "  <key>CFBundlePackageType</key>",
                "  <string>APPL</string>",
                "  <key>CFBundleShortVersionString</key>",
                "  <string>1.0</string>",
                "  <key>CFBundleVersion</key>",
                "  <string>1</string>",
                "</dict>",
                "</plist>",
                "",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [open_binary, str(wrapper_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        _raise_launch_error(language, t(language, "launch_failed"), completed)

    pid = _wait_for_pid(pid_path, timeout=wait_timeout)
    if pid is None:
        raise GuiLaunchError(t(language, "launch_timeout"))

    time.sleep(0.2)
    if not _process_exists(pid):
        raise GuiLaunchError(t(language, "launch_timeout"))

    return {
        "ok": True,
        "platform": "Darwin",
        "target": label,
        "entrypoint": str(entrypoint_path),
        "python": str(runtime_python),
        "wrapper_path": str(wrapper_path),
        "launcher_path": str(launcher_path),
        "log_path": str(log_path),
        "pid_path": str(pid_path),
        "pid": pid,
    }


def _launch_on_windows(
    language: str,
    *,
    entrypoint_path: Path,
    argv: list[str],
    label: str,
    wait_timeout: float,
    python_executable: str | Path | None = None,
) -> dict[str, object]:
    del wait_timeout
    wrapper_base, log_path, pid_path = _next_launch_paths(label)
    wrapper_path = Path(python_executable) if python_executable is not None else Path(sys.executable)
    pythonw_path = wrapper_path.with_name("pythonw.exe")
    if pythonw_path.exists():
        wrapper_path = pythonw_path
    env = os.environ.copy()
    env[DETACHED_ENV] = "1"
    env[BOOTSTRAP_ACTIVE_ENV] = "1"
    creationflags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    with log_path.open("ab") as log_handle:
        try:
            process = subprocess.Popen(
                [str(wrapper_path), str(entrypoint_path), *argv],
                cwd=str(entrypoint_path.parent),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                close_fds=True,
            )
        except OSError as exc:
            raise GuiLaunchError(f"{t(language, 'launch_failed')}\n{exc}") from exc
    pid_path.write_text(str(process.pid), encoding="utf-8")

    return {
        "ok": True,
        "platform": "Windows",
        "target": label,
        "entrypoint": str(entrypoint_path),
        "python": str(wrapper_path),
        "wrapper_path": str(wrapper_path),
        "log_path": str(log_path),
        "pid_path": str(pid_path),
        "pid": process.pid,
    }


def launch_detached_entrypoint(
    language: str,
    *,
    entrypoint_path: str | Path,
    argv: Optional[list[str]] = None,
    label: str,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
) -> dict[str, object]:
    resolved_language = resolve_language(language)
    target = Path(entrypoint_path).resolve()
    relaunch_argv = _prepare_relaunch_argv(resolved_language, argv)
    os_name = platform.system()
    if os_name == "Darwin":
        runtime_python = _require_managed_gui_python(resolved_language)
        return _launch_on_darwin(
            resolved_language,
            entrypoint_path=target,
            argv=relaunch_argv,
            label=label,
            wait_timeout=wait_timeout,
            python_executable=runtime_python,
        )
    if os_name == "Windows":
        runtime_python = _require_managed_gui_python(resolved_language)
        return _launch_on_windows(
            resolved_language,
            entrypoint_path=target,
            argv=relaunch_argv,
            label=label,
            wait_timeout=wait_timeout,
            python_executable=runtime_python,
        )
    raise GuiLaunchError(t(resolved_language, "unsupported_platform"))


def maybe_detach_gui_entrypoint(
    language: str,
    *,
    entrypoint_path: str | Path,
    argv: Optional[list[str]] = None,
    label: str,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
) -> None:
    if not should_auto_detach():
        return
    try:
        launch_detached_entrypoint(
            language,
            entrypoint_path=entrypoint_path,
            argv=argv,
            label=label,
            wait_timeout=wait_timeout,
        )
    except GuiLaunchError as exc:
        raise SystemExit(str(exc)) from exc
    raise SystemExit(0)


def shutil_which(binary: str) -> Optional[str]:
    for directory in os.getenv("PATH", "").split(os.pathsep):
        candidate = Path(directory) / binary
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _target_entrypoint(target: str) -> Path:
    base_dir = Path(__file__).resolve().parent
    if target == "profile-manager":
        return base_dir / "weex_profile_manager_app.py"
    if target == "vault-manager":
        return base_dir / "weex_vault_manager_app.py"
    raise ValueError(target)


def _output_json(payload: dict[str, object], pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=TEXTS["en"]["parser_description"],
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--language", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--pretty", action="store_true", help=TEXTS["en"]["pretty_help"])
    parser.add_argument("--wait-timeout", type=float, default=DEFAULT_WAIT_TIMEOUT, help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="target", required=True)

    p_profile = sub.add_parser(
        "profile-manager",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        help=TEXTS["en"]["profile_manager_help"],
    )
    p_profile.add_argument("--language", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_profile.add_argument(
        "--pretty",
        action="store_true",
        default=argparse.SUPPRESS,
        help=TEXTS["en"]["pretty_help"],
    )
    p_profile.add_argument("--wait-timeout", type=float, default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    p_vault = sub.add_parser(
        "vault-manager",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        help=TEXTS["en"]["vault_manager_help"],
    )
    p_vault.add_argument("--language", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_vault.add_argument(
        "--pretty",
        action="store_true",
        default=argparse.SUPPRESS,
        help=TEXTS["en"]["pretty_help"],
    )
    p_vault.add_argument("--wait-timeout", type=float, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p_vault.add_argument(
        "--requested-action",
        choices=SUPPORTED_VAULT_ACTIONS,
        default=None,
        help=TEXTS["en"]["requested_action_help"],
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    language = resolve_language(args.language)
    relaunch_argv = []
    if args.target == "vault-manager" and args.requested_action:
        relaunch_argv.extend(["--requested-action", args.requested_action])
    try:
        payload = launch_detached_entrypoint(
            language,
            entrypoint_path=_target_entrypoint(args.target),
            argv=relaunch_argv,
            label=args.target,
            wait_timeout=float(args.wait_timeout),
        )
    except GuiLaunchError as exc:
        _output_json({"ok": False, "error": str(exc)}, args.pretty)
        return 1
    else:
        _output_json(payload, args.pretty)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
