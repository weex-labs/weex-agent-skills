#!/usr/bin/env python3
"""Localized CLI profile manager for WEEX Trader Skill."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any, Dict, Optional

from weex_agent_state import refresh_agent_records
from weex_profile_language import resolve_language
from weex_url_policy import BaseUrlPolicyError, validate_weex_base_url


ProfileError = RuntimeError
delete_profile = None
get_default_profile_id = None
get_default_profile_name = None
list_profiles = None
metadata_path = None
profile_has_credentials_by_id = None
resolve_profile = None
secure_store_backend_name = None
set_default_profile = None
upsert_profile = None


TEXTS = {
    "en": {
        "parser_description": "Manage secure WEEX trading profiles",
        "list_help": "List saved profiles",
        "list_description": "List all saved profiles, including metadata and whether secure credentials exist.",
        "show_help": "Show one profile or the default profile",
        "show_description": "Show a specific profile. If --profile is omitted, the current default profile is shown.",
        "show_profile_help": "Profile name to inspect; leave empty to use the default profile",
        "save_help": "Create or update a profile",
        "save_description": "Create or update one profile. Metadata is saved in the local profile file, while secrets are stored in the application vault.",
        "save_profile_help": "Unique profile name. Later commands use this name to represent the account, for example: main, grid_bot, readonly",
        "save_description_help": "Plain-language note describing what this account is used for, for example: main manual trading account",
        "save_contract_base_url_help": "Optional contract API base URL; leave empty to use the built-in official default https://api-contract.weex.com",
        "save_spot_base_url_help": "Optional spot API base URL; leave empty to use the built-in official default https://api-spot.weex.com",
        "save_api_key_help": "WEEX API Key; required when creating a new profile unless --prompt-secrets is used",
        "save_api_secret_help": "WEEX Secret Key; required together with --api-key and --api-passphrase",
        "save_api_passphrase_help": "WEEX API Passphrase; required together with --api-key and --api-secret",
        "save_api_key_env_help": "Environment variable name containing the API Key; safer than passing the secret on the command line",
        "save_api_secret_env_help": "Environment variable name containing the Secret Key; safer than passing the secret on the command line",
        "save_api_passphrase_env_help": "Environment variable name containing the Passphrase; safer than passing the secret on the command line",
        "save_secrets_stdin_json_help": "Read a JSON object from stdin with api_key, api_secret, and api_passphrase",
        "save_prompt_secrets_help": "Prompt interactively for missing secret fields instead of passing them on the command line",
        "save_set_default_help": "Mark this profile as the default profile after saving",
        "save_clear_default_help": "If this profile is currently the default, clear the default setting after saving",
        "delete_help": "Delete a profile and its secure credentials",
        "delete_description": "Delete one profile from the metadata file and remove its secrets from the application vault.",
        "delete_profile_help": "Profile name or id to delete",
        "set_default_help": "Set the default profile",
        "set_default_description": "Set which profile should be used automatically when --profile is omitted.",
        "set_default_profile_help": "Profile name or id to use as the default profile",
        "clear_default_help": "Clear the default profile",
        "clear_default_description": "Remove the current default profile so future commands must specify --profile explicitly.",
        "pretty_help": "Pretty-print JSON output for easier reading",
        "error_no_default_profile": "No default profile has been configured yet",
        "api_key_label": "API Key",
        "api_secret_label": "Secret Key",
        "api_passphrase_label": "Passphrase",
        "error_secret_empty": "{label} cannot be empty",
        "error_secret_env_empty": "{label} environment variable {env_var} is empty or not set",
        "error_stdin_json_empty": "Expected secret JSON on stdin, but stdin was empty",
        "error_stdin_json_invalid": "Secret JSON on stdin is invalid: {reason}",
        "error_stdin_json_shape": "Secret JSON on stdin must be an object with api_key, api_secret, and api_passphrase",
        "contract_base_url_label": "Contract Base URL",
        "spot_base_url_label": "Spot Base URL",
        "runtime_dependency_missing": "Unable to start the WEEX profile CLI because Python dependency '{module_name}' is missing. Run scripts/weex_runtime_setup.py --pretty or install requirements.lock with --require-hashes using this interpreter and retry.",
        "runtime_unavailable": "Unable to start the WEEX profile CLI because its runtime dependencies are unavailable.",
    },
    "zh": {
        "parser_description": "管理 WEEX 安全交易账号",
        "list_help": "列出已保存账号",
        "list_description": "列出所有已保存账号，包括备注信息以及是否存在安全存储中的密钥。",
        "show_help": "显示指定账号或默认账号",
        "show_description": "显示一个指定账号；如果省略 --profile，则显示当前默认账号。",
        "show_profile_help": "要查看的账号名；留空时使用默认账号",
        "save_help": "创建或更新账号",
        "save_description": "创建或更新一个账号。备注信息保存在本地 profile 文件中，密钥保存在当前安全后端中。",
        "save_profile_help": "唯一账号名。后续命令会用这个名字代表账号，例如：main、grid_bot、readonly",
        "save_description_help": "用自然语言描述账号用途，例如：主手动交易账号",
        "save_contract_base_url_help": "可选的合约 API Base URL；留空时使用内置官方默认值 https://api-contract.weex.com",
        "save_spot_base_url_help": "可选的现货 API Base URL；留空时使用内置官方默认值 https://api-spot.weex.com",
        "save_api_key_help": "WEEX API Key；新建账号时必须提供，除非使用 --prompt-secrets 交互输入",
        "save_api_secret_help": "WEEX Secret Key；必须与 --api-key 和 --api-passphrase 一起提供",
        "save_api_passphrase_help": "WEEX API Passphrase；必须与 --api-key 和 --api-secret 一起提供",
        "save_api_key_env_help": "从指定环境变量读取 API Key；比把密钥直接放到命令行更安全",
        "save_api_secret_env_help": "从指定环境变量读取 Secret Key；比把密钥直接放到命令行更安全",
        "save_api_passphrase_env_help": "从指定环境变量读取 Passphrase；比把密钥直接放到命令行更安全",
        "save_secrets_stdin_json_help": "从 stdin 读取 JSON 对象，字段包含 api_key、api_secret、api_passphrase",
        "save_prompt_secrets_help": "缺少密钥字段时，通过交互方式提示输入，而不是直接把密钥放到命令行参数中",
        "save_set_default_help": "保存后将该账号设为默认账号",
        "save_clear_default_help": "如果该账号当前是默认账号，则在保存后清除默认设置",
        "delete_help": "删除账号及其安全存储中的密钥",
        "delete_description": "从 metadata 文件中删除一个账号，并同时删除安全后端中的密钥。",
        "delete_profile_help": "要删除的账号名或 id",
        "set_default_help": "设置默认账号",
        "set_default_description": "设置在省略 --profile 时自动使用的账号。",
        "set_default_profile_help": "要设为默认账号的账号名或 id",
        "clear_default_help": "清除默认账号",
        "clear_default_description": "移除当前默认账号，后续命令必须显式指定 --profile。",
        "pretty_help": "以更易读的格式输出 JSON",
        "error_no_default_profile": "当前还没有配置默认账号",
        "api_key_label": "API Key",
        "api_secret_label": "Secret Key",
        "api_passphrase_label": "Passphrase",
        "error_secret_empty": "{label} 不能为空",
        "error_secret_env_empty": "{label} 对应的环境变量 {env_var} 为空或未设置",
        "error_stdin_json_empty": "需要从 stdin 读取密钥 JSON，但 stdin 为空",
        "error_stdin_json_invalid": "stdin 中的密钥 JSON 无效：{reason}",
        "error_stdin_json_shape": "stdin 中的密钥 JSON 必须是对象，并包含 api_key、api_secret、api_passphrase 字段",
        "contract_base_url_label": "合约 Base URL",
        "spot_base_url_label": "现货 Base URL",
        "runtime_dependency_missing": "无法启动 WEEX 账号命令行工具，因为缺少 Python 依赖“{module_name}”。请先运行 scripts/weex_runtime_setup.py --pretty，或使用当前解释器通过 --require-hashes 安装 requirements.lock 后重试。",
        "runtime_unavailable": "无法启动 WEEX 账号命令行工具，因为运行时依赖不可用。",
    },
}


def _load_runtime_dependencies(language: str) -> None:
    global ProfileError
    global delete_profile, get_default_profile_id, get_default_profile_name
    global list_profiles, metadata_path, profile_has_credentials_by_id
    global resolve_profile, secure_store_backend_name, set_default_profile, upsert_profile

    try:
        from weex_profile_store import (
            ProfileError as profile_error_type,
            delete_profile as delete_profile_fn,
            get_default_profile_id as get_default_profile_id_fn,
            get_default_profile_name as get_default_profile_name_fn,
            list_profiles as list_profiles_fn,
            metadata_path as metadata_path_fn,
            profile_has_credentials_by_id as profile_has_credentials_by_id_fn,
            resolve_profile as resolve_profile_fn,
            secure_store_backend_name as secure_store_backend_name_fn,
            set_default_profile as set_default_profile_fn,
            upsert_profile as upsert_profile_fn,
        )
    except ModuleNotFoundError as exc:
        module_name = exc.name or "unknown"
        raise SystemExit(t(language, "runtime_dependency_missing", module_name=module_name)) from exc
    except ImportError as exc:
        raise SystemExit(t(language, "runtime_unavailable")) from exc

    ProfileError = profile_error_type
    delete_profile = delete_profile_fn
    get_default_profile_id = get_default_profile_id_fn
    get_default_profile_name = get_default_profile_name_fn
    list_profiles = list_profiles_fn
    metadata_path = metadata_path_fn
    profile_has_credentials_by_id = profile_has_credentials_by_id_fn
    resolve_profile = resolve_profile_fn
    secure_store_backend_name = secure_store_backend_name_fn
    set_default_profile = set_default_profile_fn
    upsert_profile = upsert_profile_fn


def t(language: str, key: str, **kwargs: object) -> str:
    text = TEXTS[language][key]
    if kwargs:
        return text.format(**kwargs)
    return text


def output_json(payload: Dict[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
        return
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False))


def prompt_secret(language: str, label_key: str) -> str:
    label = t(language, label_key)
    value = getpass.getpass(f"{label}: ").strip()
    if not value:
        raise ProfileError(t(language, "error_secret_empty", label=label))
    return value


def secret_value_from_env(language: str, env_var: Optional[str], label_key: str) -> Optional[str]:
    if not env_var:
        return None
    value = os.getenv(env_var, "").strip()
    if not value:
        raise ProfileError(t(language, "error_secret_env_empty", label=t(language, label_key), env_var=env_var))
    return value


def secret_values_from_stdin_json(language: str, enabled: bool) -> Dict[str, str]:
    if not enabled:
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        raise ProfileError(t(language, "error_stdin_json_empty"))
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileError(t(language, "error_stdin_json_invalid", reason=str(exc))) from exc
    if not isinstance(obj, dict):
        raise ProfileError(t(language, "error_stdin_json_shape"))
    values: Dict[str, str] = {}
    for field_name in ("api_key", "api_secret", "api_passphrase"):
        value = obj.get(field_name)
        if isinstance(value, str) and value.strip():
            values[field_name] = value.strip()
    return values


def validate_base_url_arg(language: str, raw_url: Optional[str], label_key: str) -> Optional[str]:
    if raw_url is None:
        return None
    if not raw_url.strip():
        return ""
    try:
        return validate_weex_base_url(raw_url, label=t(language, label_key))
    except BaseUrlPolicyError as exc:
        raise ProfileError(exc.localized_message(language)) from exc


def describe_credentials(profile_id: str) -> Dict[str, Any]:
    try:
        has_credentials = profile_has_credentials_by_id(profile_id)
    except ProfileError as exc:
        message = str(exc).lower()
        if "vault is locked" in message:
            status = "unknown_locked"
        elif "requires environment variable" in message:
            status = "unknown_unavailable"
        else:
            status = "unknown_error"
        return {
            "has_credentials": None,
            "credentials_status": status,
        }
    return {
        "has_credentials": has_credentials,
        "credentials_status": "present" if has_credentials else "missing",
    }


def profile_payload(profile: Any) -> Dict[str, Any]:
    return {
        **profile.to_dict(),
        **describe_credentials(profile.profile_id),
    }


def cmd_list(args: argparse.Namespace) -> int:
    payload = {
        "ok": True,
        "backend": secure_store_backend_name(),
        "metadata_path": str(metadata_path()),
        "default_profile_id": get_default_profile_id(),
        "default_profile": get_default_profile_name(),
        "profiles": [
            profile_payload(profile)
            for profile in list_profiles()
        ],
    }
    output_json(payload, args.pretty)
    return 0


def cmd_show(args: argparse.Namespace, language: str) -> int:
    profile = resolve_profile(args.profile)
    if profile is None:
        raise ProfileError(t(language, "error_no_default_profile"))
    payload = {
        "ok": True,
        "backend": secure_store_backend_name(),
        "metadata_path": str(metadata_path()),
        "default_profile_id": get_default_profile_id(),
        "default_profile": get_default_profile_name(),
        "profile": profile_payload(profile),
    }
    output_json(payload, args.pretty)
    return 0


def cmd_save(args: argparse.Namespace, language: str) -> int:
    contract_base_url = validate_base_url_arg(language, args.contract_base_url, "contract_base_url_label")
    spot_base_url = validate_base_url_arg(language, args.spot_base_url, "spot_base_url_label")

    api_key = args.api_key
    api_secret = args.api_secret
    api_passphrase = args.api_passphrase
    stdin_secrets = secret_values_from_stdin_json(language, args.secrets_stdin_json)

    api_key = api_key or secret_value_from_env(language, args.api_key_env, "api_key_label") or stdin_secrets.get("api_key")
    api_secret = api_secret or secret_value_from_env(language, args.api_secret_env, "api_secret_label") or stdin_secrets.get("api_secret")
    api_passphrase = api_passphrase or secret_value_from_env(language, args.api_passphrase_env, "api_passphrase_label") or stdin_secrets.get("api_passphrase")

    if args.prompt_secrets:
        api_key = api_key or prompt_secret(language, "api_key_label")
        api_secret = api_secret or prompt_secret(language, "api_secret_label")
        api_passphrase = api_passphrase or prompt_secret(language, "api_passphrase_label")

    profile = upsert_profile(
        name=args.profile,
        description=args.description,
        contract_base_url=contract_base_url,
        spot_base_url=spot_base_url,
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
        set_default=True if args.set_default else None,
    )

    if args.clear_default and get_default_profile_id() == profile.profile_id:
        set_default_profile(None)

    payload = {
        "ok": True,
        "backend": secure_store_backend_name(),
        "metadata_path": str(metadata_path()),
        "default_profile_id": get_default_profile_id(),
        "default_profile": get_default_profile_name(),
        "profile": profile_payload(profile),
    }
    output_json(payload, args.pretty)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    try:
        profile = resolve_profile(args.profile)
    except ProfileError:
        profile = None
    deleted = delete_profile(args.profile)
    payload = {
        "ok": deleted,
        "deleted": deleted,
        "profile": args.profile,
        "profile_id": profile.profile_id if profile is not None else None,
        "default_profile_id": get_default_profile_id(),
        "default_profile": get_default_profile_name(),
    }
    output_json(payload, args.pretty)
    return 0 if deleted else 1


def cmd_set_default(args: argparse.Namespace) -> int:
    set_default_profile(args.profile)
    profile = resolve_profile(args.profile)
    payload = {
        "ok": True,
        "default_profile_id": get_default_profile_id(),
        "default_profile": get_default_profile_name(),
        "profile": profile.to_dict() if profile else None,
    }
    output_json(payload, args.pretty)
    return 0


def cmd_clear_default(args: argparse.Namespace) -> int:
    set_default_profile(None)
    output_json({"ok": True, "default_profile_id": None, "default_profile": None}, args.pretty)
    return 0


def build_parser(language: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=t(language, "parser_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser(
        "list",
        help=t(language, "list_help"),
        description=t(language, "list_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_list.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    p_show = sub.add_parser(
        "show",
        help=t(language, "show_help"),
        description=t(language, "show_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_show.add_argument("--profile", default=None, help=t(language, "show_profile_help"))
    p_show.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    p_save = sub.add_parser(
        "save",
        help=t(language, "save_help"),
        description=t(language, "save_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_save.add_argument("--profile", required=True, help=t(language, "save_profile_help"))
    p_save.add_argument("--description", default=None, help=t(language, "save_description_help"))
    p_save.add_argument("--contract-base-url", default=None, help=t(language, "save_contract_base_url_help"))
    p_save.add_argument("--spot-base-url", default=None, help=t(language, "save_spot_base_url_help"))
    p_save.add_argument("--api-key", default=None, help=t(language, "save_api_key_help"))
    p_save.add_argument("--api-secret", default=None, help=t(language, "save_api_secret_help"))
    p_save.add_argument("--api-passphrase", default=None, help=t(language, "save_api_passphrase_help"))
    p_save.add_argument("--api-key-env", default=None, help=t(language, "save_api_key_env_help"))
    p_save.add_argument("--api-secret-env", default=None, help=t(language, "save_api_secret_env_help"))
    p_save.add_argument("--api-passphrase-env", default=None, help=t(language, "save_api_passphrase_env_help"))
    p_save.add_argument("--secrets-stdin-json", action="store_true", help=t(language, "save_secrets_stdin_json_help"))
    p_save.add_argument("--prompt-secrets", action="store_true", help=t(language, "save_prompt_secrets_help"))
    p_save_default_group = p_save.add_mutually_exclusive_group()
    p_save_default_group.add_argument("--set-default", action="store_true", help=t(language, "save_set_default_help"))
    p_save_default_group.add_argument("--clear-default", action="store_true", help=t(language, "save_clear_default_help"))
    p_save.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    p_delete = sub.add_parser(
        "delete",
        help=t(language, "delete_help"),
        description=t(language, "delete_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_delete.add_argument("--profile", required=True, help=t(language, "delete_profile_help"))
    p_delete.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    p_default = sub.add_parser(
        "set-default",
        help=t(language, "set_default_help"),
        description=t(language, "set_default_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_default.add_argument("--profile", required=True, help=t(language, "set_default_profile_help"))
    p_default.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    p_clear = sub.add_parser(
        "clear-default",
        help=t(language, "clear_default_help"),
        description=t(language, "clear_default_description"),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_clear.add_argument("--pretty", action="store_true", help=t(language, "pretty_help"))

    return parser


def main(language: str | None = None) -> int:
    resolved_language = resolve_language(language)
    args = build_parser(resolved_language).parse_args()
    command_name = f"profiles.{args.command}"
    try:
        refresh_agent_records(preferred_language=resolved_language, command=command_name)
    except Exception:
        pass
    _load_runtime_dependencies(resolved_language)
    try:
        if args.command == "list":
            result = cmd_list(args)
        elif args.command == "show":
            result = cmd_show(args, resolved_language)
        elif args.command == "save":
            result = cmd_save(args, resolved_language)
        elif args.command == "delete":
            result = cmd_delete(args)
        elif args.command == "set-default":
            result = cmd_set_default(args)
        elif args.command == "clear-default":
            result = cmd_clear_default(args)
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
