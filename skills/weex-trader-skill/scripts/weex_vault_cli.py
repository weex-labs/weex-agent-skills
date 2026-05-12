#!/usr/bin/env python3
"""Localized CLI for the WEEX application vault backend."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
from pathlib import Path
from typing import Any, Dict, Optional

from weex_agent_state import refresh_agent_records
from weex_profile_language import resolve_language


change_linux_vault_password = None
ProfileError = RuntimeError
lock_linux_vault = None
setup_linux_vault = None
unlock_linux_vault = None
vault_status = None

WINDOWS_MACOS_BARE_UI_COMMANDS = frozenset({"setup", "unlock"})


TEXTS = {
    "en": {
        "parser_description": "Manage the WEEX application vault across Windows, macOS, and Linux. On Windows/macOS, bare setup and unlock commands open the vault UI unless --cli is used.",
        "cli_help": "Force terminal mode on Windows/macOS when bare setup or unlock would otherwise open the vault UI",
        "pretty_help": "Pretty-print JSON output for easier reading",
        "setup_help": "Create the encrypted vault",
        "setup_description": "Initialize the encrypted vault in manual_once mode.",
        "setup_mode_help": "Vault mode: manual_once",
        "setup_env_var_help": "Unused",
        "setup_password_env_help": "Read the initial vault passphrase from this environment variable",
        "setup_password_file_help": "Read the initial vault passphrase from this file",
        "setup_force_help": "Overwrite an existing vault configuration",
        "setup_no_unlock_help": "Do not keep the vault unlocked after setup (manual_once only)",
        "status_help": "Show current vault backend and lock state",
        "unlock_help": "Unlock the vault for the current login session",
        "unlock_description": "Unlock a manual_once vault so profile commands can read credentials.",
        "unlock_password_env_help": "Read the vault passphrase from this environment variable",
        "unlock_password_file_help": "Read the vault passphrase from this file",
        "change_password_help": "Change the vault passphrase",
        "change_password_description": "Re-encrypt the vault with a new passphrase without deleting saved profiles.",
        "change_password_current_env_help": "Read the current vault passphrase from this environment variable",
        "change_password_current_file_help": "Read the current vault passphrase from this file",
        "change_password_new_env_help": "Read the new vault passphrase from this environment variable",
        "change_password_new_file_help": "Read the new vault passphrase from this file",
        "change_password_env_var_help": "Unused",
        "lock_help": "Lock the vault by ending the current manual_once session",
        "mode_help": "Show the current vault mode",
        "mode_set_help": "Vault mode is fixed to manual_once",
        "mode_env_var_help": "Unused",
        "prompt_mode": "Vault mode [manual_once]: ",
        "prompt_passphrase": "Vault passphrase: ",
        "prompt_passphrase_confirm": "Confirm vault passphrase: ",
        "error_passphrase_mismatch": "Vault passphrase confirmation did not match.",
        "error_password_env_missing": "Vault passphrase environment variable is empty or not set.",
        "error_password_file_missing": "Vault passphrase file was not found or is empty.",
        "error_mode_required": "Vault mode must be manual_once.",
        "runtime_dependency_missing": "Unable to start the WEEX vault CLI because Python dependency '{module_name}' is missing. Run scripts/weex_runtime_setup.py --pretty or install requirements.lock with --require-hashes using this interpreter and retry.",
        "runtime_unavailable": "Unable to start the WEEX vault CLI because its runtime dependencies are unavailable.",
    },
    "zh": {
        "parser_description": "管理 WEEX 应用保险库，可在 Windows、macOS 和 Linux 上使用。在 Windows/macOS 上，未附加其他 CLI 参数的 setup 和 unlock 默认会打开 Vault UI，除非使用 --cli。",
        "cli_help": "在 Windows/macOS 上强制使用终端模式，而不是让无附加参数的 setup 或 unlock 默认打开 Vault UI",
        "pretty_help": "以更易读的格式输出 JSON",
        "setup_help": "创建加密保险库",
        "setup_description": "初始化加密保险库并选择解锁模式。",
        "setup_mode_help": "保险库模式：manual_once",
        "setup_env_var_help": "未使用",
        "setup_password_env_help": "从这个环境变量读取初始化保险库密码",
        "setup_password_file_help": "从这个文件读取初始化保险库密码",
        "setup_force_help": "覆盖已有保险库配置",
        "setup_no_unlock_help": "创建后不要保持解锁状态（仅 manual_once 有效）",
        "status_help": "查看当前保险库后端与锁定状态",
        "unlock_help": "为当前服务器会话解锁保险库",
        "unlock_description": "解锁 manual_once 模式的保险库，让 profile 命令可以读取凭据。",
        "unlock_password_env_help": "从这个环境变量读取保险库密码",
        "unlock_password_file_help": "从这个文件读取保险库密码",
        "change_password_help": "更改保险库密码",
        "change_password_description": "使用新密码重新加密保险库，同时保留已保存的 profiles。",
        "change_password_current_env_help": "从这个环境变量读取当前保险库密码",
        "change_password_current_file_help": "从这个文件读取当前保险库密码",
        "change_password_new_env_help": "从这个环境变量读取新保险库密码",
        "change_password_new_file_help": "从这个文件读取新保险库密码",
        "change_password_env_var_help": "未使用",
        "lock_help": "删除当前会话密钥并重新锁定保险库",
        "mode_help": "查看或修改当前保险库模式",
        "mode_set_help": "保险库模式固定为 manual_once",
        "mode_env_var_help": "未使用",
        "prompt_mode": "请选择保险库模式 [manual_once]: ",
        "prompt_passphrase": "请输入保险库密码：",
        "prompt_passphrase_confirm": "请再次输入保险库密码：",
        "error_passphrase_mismatch": "两次输入的保险库密码不一致。",
        "error_password_env_missing": "保险库密码环境变量为空或未设置。",
        "error_password_file_missing": "保险库密码文件不存在、不可读或内容为空。",
        "error_mode_required": "保险库模式必须是 manual_once。",
        "runtime_dependency_missing": "无法启动 WEEX 保险库命令行工具，因为缺少 Python 依赖 '{module_name}'。请先运行 scripts/weex_runtime_setup.py --pretty，或使用当前解释器通过 --require-hashes 安装 requirements.lock 后重试。",
        "runtime_unavailable": "无法启动 WEEX 保险库命令行工具，因为运行时依赖不可用。",
    },
}


def _load_runtime_dependencies(language: str) -> None:
    global ProfileError
    global change_linux_vault_password, lock_linux_vault, setup_linux_vault
    global unlock_linux_vault, vault_status

    try:
        from weex_profile_store import (
            ProfileError as profile_error_type,
            change_linux_vault_password as change_linux_vault_password_fn,
            lock_linux_vault as lock_linux_vault_fn,
            setup_linux_vault as setup_linux_vault_fn,
            unlock_linux_vault as unlock_linux_vault_fn,
            vault_status as vault_status_fn,
        )
    except ModuleNotFoundError as exc:
        module_name = exc.name or "unknown"
        raise SystemExit(t(language, "runtime_dependency_missing", module_name=module_name)) from exc
    except ImportError as exc:
        raise SystemExit(t(language, "runtime_unavailable")) from exc

    ProfileError = profile_error_type
    change_linux_vault_password = change_linux_vault_password_fn
    lock_linux_vault = lock_linux_vault_fn
    setup_linux_vault = setup_linux_vault_fn
    unlock_linux_vault = unlock_linux_vault_fn
    vault_status = vault_status_fn


def t(language: str, key: str, **kwargs: object) -> str:
    text = TEXTS.get(language, {}).get(key) or TEXTS["en"][key]
    if kwargs:
        return text.format(**kwargs)
    return text


def output_json(payload: Dict[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False))


def _normalize_mode(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if raw == "manual_once":
        return raw
    return ""


def _prompt_mode(language: str) -> str:
    while True:
        value = _normalize_mode(input(t(language, "prompt_mode")))
        if value:
            return value
        print(t(language, "error_mode_required"))


def _prompt_passphrase(language: str, confirm: bool) -> str:
    first = getpass.getpass(t(language, "prompt_passphrase")).strip()
    if not first:
        raise ProfileError(t(language, "error_passphrase_mismatch"))
    if not confirm:
        return first
    second = getpass.getpass(t(language, "prompt_passphrase_confirm")).strip()
    if first != second:
        raise ProfileError(t(language, "error_passphrase_mismatch"))
    return first


def _read_passphrase_from_file(language: str, path_value: Optional[str]) -> str:
    path = Path((path_value or "").strip()).expanduser()
    if not str(path_value or "").strip() or not path.is_file():
        raise ProfileError(t(language, "error_password_file_missing"))
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ProfileError(t(language, "error_password_file_missing"))
    return content


def _resolve_passphrase(
    language: str,
    *,
    password_env: Optional[str],
    password_file: Optional[str],
    confirm_prompt: bool,
) -> str:
    if password_env:
        passphrase = os.getenv(password_env, "")
        if not passphrase:
            raise ProfileError(t(language, "error_password_env_missing"))
        return passphrase
    if password_file:
        return _read_passphrase_from_file(language, password_file)
    return _prompt_passphrase(language, confirm=confirm_prompt)


def cmd_setup(args: argparse.Namespace, language: str) -> int:
    mode = _normalize_mode(args.mode) or "manual_once"
    passphrase = _resolve_passphrase(
        language,
        password_env=args.password_env,
        password_file=args.password_file,
        confirm_prompt=True,
    )
    payload = setup_linux_vault(
        mode=mode,
        passphrase=passphrase,
        unlock=not args.no_unlock,
        force=args.force,
    )
    output_json(payload, args.pretty)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    output_json(vault_status(), args.pretty)
    return 0


def cmd_unlock(args: argparse.Namespace, language: str) -> int:
    passphrase = _resolve_passphrase(
        language,
        password_env=args.password_env,
        password_file=args.password_file,
        confirm_prompt=False,
    )
    payload = unlock_linux_vault(passphrase)
    output_json(payload, args.pretty)
    return 0


def cmd_change_password(args: argparse.Namespace, language: str) -> int:
    current_passphrase = _resolve_passphrase(
        language,
        password_env=args.current_password_env,
        password_file=args.current_password_file,
        confirm_prompt=False,
    )
    new_passphrase = _resolve_passphrase(
        language,
        password_env=args.new_password_env,
        password_file=args.new_password_file,
        confirm_prompt=True,
    )
    payload = change_linux_vault_password(
        current_passphrase=current_passphrase,
        new_passphrase=new_passphrase,
    )
    output_json(payload, args.pretty)
    return 0


def cmd_lock(args: argparse.Namespace) -> int:
    output_json(lock_linux_vault(), args.pretty)
    return 0


def cmd_mode(args: argparse.Namespace, _language: str) -> int:
    payload = vault_status()
    output_json(payload, args.pretty)
    return 0


def build_parser(language: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=t(language, "parser_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--cli", action="store_true", help=t(language, "cli_help"))
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser(
        "setup",
        help=t(language, "setup_help"),
        description=t(language, "setup_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_setup.add_argument("--mode", choices=["manual_once"], default="manual_once", help=t(language, "setup_mode_help"))
    p_setup_secret_group = p_setup.add_mutually_exclusive_group()
    p_setup_secret_group.add_argument("--password-env", default=None, help=t(language, "setup_password_env_help"))
    p_setup_secret_group.add_argument("--password-file", default=None, help=t(language, "setup_password_file_help"))
    p_setup.add_argument("--force", action="store_true", help=t(language, "setup_force_help"))
    p_setup.add_argument("--no-unlock", action="store_true", help=t(language, "setup_no_unlock_help"))
    p_setup.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    p_status = sub.add_parser(
        "status",
        help=t(language, "status_help"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_status.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    p_unlock = sub.add_parser(
        "unlock",
        help=t(language, "unlock_help"),
        description=t(language, "unlock_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_unlock_secret_group = p_unlock.add_mutually_exclusive_group()
    p_unlock_secret_group.add_argument("--password-env", default=None, help=t(language, "unlock_password_env_help"))
    p_unlock_secret_group.add_argument("--password-file", default=None, help=t(language, "unlock_password_file_help"))
    p_unlock.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    p_change_password = sub.add_parser(
        "change-password",
        help=t(language, "change_password_help"),
        description=t(language, "change_password_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_change_current_group = p_change_password.add_mutually_exclusive_group()
    p_change_current_group.add_argument("--current-password-env", default=None, help=t(language, "change_password_current_env_help"))
    p_change_current_group.add_argument("--current-password-file", default=None, help=t(language, "change_password_current_file_help"))
    p_change_new_group = p_change_password.add_mutually_exclusive_group()
    p_change_new_group.add_argument("--new-password-env", default=None, help=t(language, "change_password_new_env_help"))
    p_change_new_group.add_argument("--new-password-file", default=None, help=t(language, "change_password_new_file_help"))
    p_change_password.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    p_lock = sub.add_parser(
        "lock",
        help=t(language, "lock_help"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_lock.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    p_mode = sub.add_parser(
        "mode",
        help=t(language, "mode_help"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_mode.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    return parser


def launch_vault_ui(language: str, *, requested_action: Optional[str]) -> int:
    from weex_vault_manager_app import main as vault_ui_main

    return vault_ui_main(language=language, requested_action=requested_action, argv=[])


def _resolve_platform_vault_ui_request(raw_args: list[str]) -> tuple[bool, Optional[str]]:
    if platform.system() not in {"Windows", "Darwin"}:
        return False, None
    if "--cli" in raw_args or "-h" in raw_args or "--help" in raw_args:
        return False, None
    if not raw_args:
        return True, None
    if raw_args[0] in WINDOWS_MACOS_BARE_UI_COMMANDS and len(raw_args) == 1:
        return True, raw_args[0]
    return False, None


def main(language: str | None = None, argv: Optional[list[str]] = None) -> int:
    resolved_language = resolve_language(language)
    raw_args = list(argv if argv is not None else os.sys.argv[1:])
    use_ui, requested_action = _resolve_platform_vault_ui_request(raw_args)
    if use_ui:
        try:
            refresh_agent_records(
                preferred_language=resolved_language,
                command=f"vault.ui.{requested_action or 'launch'}",
            )
        except Exception:
            pass
        return launch_vault_ui(resolved_language, requested_action=requested_action)

    args = build_parser(resolved_language).parse_args(raw_args)
    command_name = f"vault.{args.command}"
    try:
        refresh_agent_records(preferred_language=resolved_language, command=command_name)
    except Exception:
        pass
    _load_runtime_dependencies(resolved_language)
    try:
        if args.command == "setup":
            result = cmd_setup(args, resolved_language)
        elif args.command == "status":
            result = cmd_status(args)
        elif args.command == "unlock":
            result = cmd_unlock(args, resolved_language)
        elif args.command == "change-password":
            result = cmd_change_password(args, resolved_language)
        elif args.command == "lock":
            result = cmd_lock(args)
        elif args.command == "mode":
            result = cmd_mode(args, resolved_language)
        else:
            result = 1
        if result == 0:
            try:
                refresh_agent_records(preferred_language=resolved_language, command=command_name)
            except Exception:
                pass
        return result
    except ProfileError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
