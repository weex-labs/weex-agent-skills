#!/usr/bin/env python3
"""Lightweight AI-facing state cache for WEEX skill entrypoints."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from weex_gui_bootstrap import (
    BOOTSTRAP_DISABLE_ENV,
    _localized as gui_bootstrap_localized,
    managed_runtime_setup_command,
    managed_venv_python,
    probe_runtime,
    requirements_lock_path,
)
from weex_profile_language import resolve_language_with_source
from weex_url_policy import BaseUrlPolicyError, validate_weex_base_url


CONFIG_HOME_ENV = "WEEX_TRADER_SKILL_HOME"
METADATA_FILENAME = "profiles.meta.json"
VAULT_CONFIG_FILENAME = "vault.config.json"
VAULT_SESSION_FILENAME = "vault.session.json"
AGENT_INIT_FILENAME = "agent-init.json"
AGENT_RUNTIME_FILENAME = "agent-runtime.json"
REQUIRED_MODULES = ("cryptography", "requests")
RUNTIME_ENV_VARS = (
    "WEEX_TRADER_SKILL_HOME",
    "WEEX_LOCALE",
    "WEEX_API_TIMEOUT",
    "WEEX_API_BASE",
    "WEEX_CONTRACT_API_BASE",
    "WEEX_SPOT_API_BASE",
)
BASE_URL_ENV_VARS = (
    "WEEX_API_BASE",
    "WEEX_CONTRACT_API_BASE",
    "WEEX_SPOT_API_BASE",
)


class RuntimePreflightError(RuntimeError):
    """Raised when the current runtime cannot safely execute private WEEX commands."""

    def __init__(
        self,
        message: str,
        *,
        missing_modules: Optional[list[str]] = None,
        env_issues: Optional[list[str]] = None,
        setup_result: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.missing_modules = tuple(missing_modules or ())
        self.env_issues = tuple(env_issues or ())
        self.setup_result = setup_result


def config_dir() -> Path:
    raw = os.getenv(CONFIG_HOME_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".weex-trader-skill"


def metadata_path() -> Path:
    return config_dir() / METADATA_FILENAME


def vault_config_path() -> Path:
    return config_dir() / VAULT_CONFIG_FILENAME


def vault_session_path() -> Path:
    return config_dir() / VAULT_SESSION_FILENAME


def agent_init_path() -> Path:
    return config_dir() / AGENT_INIT_FILENAME


def agent_runtime_path() -> Path:
    return config_dir() / AGENT_RUNTIME_FILENAME


def requirements_path() -> Path:
    return Path(__file__).resolve().parent.parent / "requirements.txt"


def runtime_setup_script_path() -> Path:
    return Path(__file__).resolve().parent / "weex_runtime_setup.py"


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_config_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_config_dir(path.parent)
    temp_path: Optional[Path] = None
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _load_store_module() -> Any:
    try:
        return importlib.import_module("weex_profile_store")
    except Exception:
        return None


def _detect_tkinter_available(os_family: str) -> tuple[bool, Optional[dict[str, Any]]]:
    if os_family not in {"Windows", "Darwin"}:
        try:
            import tkinter  # noqa: F401
        except Exception:
            return False, None
        return True, None
    probe = probe_runtime(sys.executable)
    return probe.usable, probe.to_dict()


def _prepare_gui_runtime(
    os_family: str,
    language: str,
    *,
    tkinter_available: bool,
) -> Optional[dict[str, Any]]:
    if os_family not in {"Windows", "Darwin"}:
        return None

    disabled = os.getenv(BOOTSTRAP_DISABLE_ENV) == "1"
    payload: dict[str, Any] = {
        "available": True,
        "disabled": disabled,
        "attempted": False,
        "ready": False,
        "action": None,
        "requires_user_consent": False,
        "setup_command": managed_runtime_setup_command(os_family),
        "managed_python_executable": None,
        "managed_probe": None,
        "error": None,
    }
    if disabled:
        return payload

    runtime_python = managed_venv_python()
    if not runtime_python.exists():
        payload["ready"] = False
        payload["action"] = "explicit_setup_required"
        payload["requires_user_consent"] = True
        payload["error"] = gui_bootstrap_localized(language, "explicit_runtime_required")
        return payload

    managed_probe = probe_runtime(str(runtime_python))
    if not managed_probe.usable:
        payload["ready"] = False
        payload["action"] = "explicit_setup_required"
        payload["requires_user_consent"] = True
        payload["managed_python_executable"] = str(runtime_python)
        payload["managed_probe"] = managed_probe.to_dict()
        payload["error"] = (
            f"{gui_bootstrap_localized(language, 'explicit_runtime_required')}\n"
            f"{managed_probe.summary(language)}"
        )
        return payload

    payload["ready"] = True
    payload["action"] = "reused"
    payload["managed_python_executable"] = str(runtime_python)
    payload["managed_probe"] = managed_probe.to_dict()
    return payload


def _detect_interaction_mode(os_family: str) -> str:
    if os_family == "Linux":
        if any(os.getenv(name) for name in ("DISPLAY", "WAYLAND_DISPLAY")):
            return "linux_interactive"
        return "headless_server"
    if os_family in {"Windows", "Darwin"}:
        return "desktop_interactive"
    return "terminal_only"


def _detect_gui_available(
    os_family: str,
    tkinter_available: bool,
    gui_runtime: Optional[dict[str, Any]],
) -> bool:
    del tkinter_available
    if os_family in {"Windows", "Darwin"}:
        return bool((gui_runtime or {}).get("ready"))
    return False


def _launcher_for_os(os_family: str) -> str:
    return "py -3" if os_family == "Windows" else "python3"


def _route_profile_management(os_family: str, language: str, gui_available: bool, interaction_mode: str) -> str:
    if os_family == "Windows":
        return f"windows_{'gui' if gui_available else 'cli'}_{language}"
    if os_family == "Darwin":
        return f"macos_{'gui' if gui_available else 'cli'}_{language}"
    if os_family == "Linux":
        if interaction_mode == "headless_server":
            return f"linux_cli_{language}"
        return f"linux_wizard_{language}"
    return f"cli_{language}"


def _route_vault_management(os_family: str, language: str, gui_available: bool, interaction_mode: str) -> str:
    if os_family == "Windows":
        return f"windows_{'gui' if gui_available else 'cli'}_{language}"
    if os_family == "Darwin":
        return f"macos_{'gui' if gui_available else 'cli'}_{language}"
    if os_family == "Linux":
        if interaction_mode == "headless_server":
            return f"linux_vault_cli_{language}"
        return f"linux_vault_cli_{language}"
    return f"vault_cli_{language}"


def _load_metadata_summary() -> dict[str, Any]:
    raw = _load_json(metadata_path()) or {}
    raw_profiles = raw.get("profiles")
    if not isinstance(raw_profiles, dict):
        raw_profiles = {}

    profiles_by_id: dict[str, dict[str, str]] = {}
    for key, profile_raw in raw_profiles.items():
        if not isinstance(key, str) or not isinstance(profile_raw, dict):
            continue
        profile_id = _clean_text(profile_raw.get("id")) or _clean_text(key)
        profile_name = _clean_text(profile_raw.get("name")) or _clean_text(key)
        if not profile_id or not profile_name:
            continue
        profiles_by_id[profile_id] = {
            "id": profile_id,
            "name": profile_name,
            "description": _clean_text(profile_raw.get("description")),
            "contract_base_url": _clean_text(profile_raw.get("contract_base_url")),
            "spot_base_url": _clean_text(profile_raw.get("spot_base_url")),
            "api_key_hint": _clean_text(profile_raw.get("api_key_hint")),
        }

    default_profile_id = _clean_text(raw.get("default_profile_id"))
    if default_profile_id and default_profile_id not in profiles_by_id:
        default_profile_id = ""
    if not default_profile_id:
        legacy_default = _clean_text(raw.get("default_profile"))
        if legacy_default in profiles_by_id:
            default_profile_id = legacy_default
        elif legacy_default:
            for profile in profiles_by_id.values():
                if profile["name"] == legacy_default:
                    default_profile_id = profile["id"]
                    break

    summary = sorted(profiles_by_id.values(), key=lambda item: item["name"].lower())
    default_profile = profiles_by_id.get(default_profile_id, {})
    return {
        "count": len(summary),
        "default_profile_id": default_profile_id or None,
        "default_profile_name": default_profile.get("name") or None,
        "summary": summary,
    }


def _load_vault_summary() -> dict[str, Any]:
    raw = _load_json(vault_config_path()) or {}
    mode = _clean_text(raw.get("mode")) or None
    configured = bool(raw)
    return {
        "configured": configured,
        "mode": mode,
    }


def _probe_required_modules() -> tuple[bool, list[str]]:
    missing: list[str] = []
    for module_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception:
            missing.append(module_name)
    return not missing, missing


def validate_runtime_environment(env: Optional[dict[str, str]] = None) -> dict[str, Any]:
    source = os.environ if env is None else env
    issues: list[str] = []

    raw_timeout = _clean_text(source.get("WEEX_API_TIMEOUT"))
    if raw_timeout:
        try:
            timeout_value = float(raw_timeout)
        except ValueError:
            issues.append(
                f"WEEX_API_TIMEOUT must be a positive number of seconds; got {raw_timeout!r}."
            )
        else:
            if not math.isfinite(timeout_value) or timeout_value <= 0:
                issues.append(
                    f"WEEX_API_TIMEOUT must be a positive finite number of seconds; got {raw_timeout!r}."
                )

    for env_name in BASE_URL_ENV_VARS:
        raw_url = _clean_text(source.get(env_name))
        if not raw_url:
            continue
        try:
            validate_weex_base_url(raw_url, label=env_name)
        except BaseUrlPolicyError as exc:
            issues.append(str(exc))

    return {
        "ok": not issues,
        "issues": issues,
    }


def _dependency_install_command(os_family: Optional[str] = None) -> str:
    launcher = _launcher_for_os(os_family or platform.system())
    return f"{launcher} -m pip install --require-hashes -r {requirements_lock_path()}"


def _run_runtime_setup(language: Optional[str] = None) -> dict[str, Any]:
    command = [sys.executable, str(runtime_setup_script_path())]
    if language:
        command.extend(["--language", language])
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout.strip()
    payload = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": completed.stderr.strip(),
        "payload": payload,
    }


def _clear_runtime_sensitive_module_cache() -> None:
    for module_name in ("weex_profile_store",):
        sys.modules.pop(module_name, None)


def _raise_private_runtime_preflight_error(
    *,
    command: Optional[str],
    missing_modules: list[str],
    env_validation: dict[str, Any],
    setup_result: Optional[dict[str, Any]] = None,
) -> None:
    lines = ["Private WEEX command preflight failed."]
    if command:
        lines.append(f"Command: {command}")
    if setup_result is not None:
        lines.append(
            f"Automatic runtime setup was attempted with: {' '.join(setup_result['command'])}"
        )
        payload = setup_result.get("payload")
        if setup_result["returncode"] != 0:
            lines.append("Automatic runtime setup did not complete successfully.")
            if setup_result.get("stderr"):
                lines.append(f"Runtime setup stderr: {setup_result['stderr']}")
            elif setup_result.get("stdout"):
                lines.append(f"Runtime setup output: {setup_result['stdout']}")
        elif isinstance(payload, dict) and payload.get("ok"):
            lines.append(
                "Automatic runtime setup completed, but this process is still missing runtime prerequisites."
            )
            lines.append("Retry the same private command in a fresh shell if the issue persists.")
        else:
            lines.append("Automatic runtime setup completed, but the interpreter is still not ready.")
    if missing_modules:
        modules = ", ".join(missing_modules)
        lines.append(f"Missing Python dependencies for this interpreter: {modules}.")
        lines.append(f"Install them with: {_dependency_install_command()}")
    env_issues = list(env_validation["issues"])
    if env_issues:
        lines.append("Invalid runtime environment:")
        lines.extend(f"- {issue}" for issue in env_issues)

    raise RuntimePreflightError(
        "\n".join(lines),
        missing_modules=missing_modules,
        env_issues=env_issues,
        setup_result=setup_result,
    )


def ensure_private_runtime_ready(
    command: Optional[str] = None,
    *,
    auto_setup: bool = False,
    language: Optional[str] = None,
) -> None:
    requirements_ready, missing_modules = _probe_required_modules()
    env_validation = validate_runtime_environment()
    if requirements_ready and env_validation["ok"]:
        return

    setup_result: Optional[dict[str, Any]] = None
    if auto_setup and missing_modules and env_validation["ok"]:
        setup_result = _run_runtime_setup(language=language)
        importlib.invalidate_caches()
        _clear_runtime_sensitive_module_cache()
        requirements_ready, missing_modules = _probe_required_modules()
        env_validation = validate_runtime_environment()
        if requirements_ready and env_validation["ok"]:
            return

    _raise_private_runtime_preflight_error(
        command=command,
        missing_modules=missing_modules,
        env_validation=env_validation,
        setup_result=setup_result,
    )


def _fallback_vault_runtime(vault_summary: dict[str, Any]) -> dict[str, Any]:
    configured = bool(vault_summary["configured"])
    mode = vault_summary["mode"]
    session_exists = vault_session_path().exists()
    if not configured:
        return {
            "backend": "Application Vault (setup required)",
            "configured": False,
            "mode": None,
            "state": "uninitialized",
            "action_required": "setup",
            "session_descriptor_present": session_exists,
        }
    if mode == "manual_once":
        return {
            "backend": f"Application Vault ({mode})",
            "configured": True,
            "mode": mode,
            "state": "unlocked" if session_exists else "locked",
            "action_required": None if session_exists else "unlock",
            "session_descriptor_present": session_exists,
        }
    return {
        "backend": "Application Vault (unknown)",
        "configured": True,
        "mode": mode,
        "state": "misconfigured",
        "action_required": "repair",
        "session_descriptor_present": session_exists,
    }


def _probe_default_profile_usable(store: Any, default_profile_id: Optional[str]) -> Optional[bool]:
    if not default_profile_id:
        return False
    if store is None:
        return None
    try:
        return bool(store.profile_has_credentials_by_id(default_profile_id))
    except Exception:
        return None


def build_agent_init_state(preferred_language: str | None = None) -> dict[str, Any]:
    resolved_language, language_source = resolve_language_with_source(preferred_language)
    os_family = platform.system()
    tkinter_available, tkinter_probe = _detect_tkinter_available(os_family)
    gui_runtime = _prepare_gui_runtime(os_family, resolved_language, tkinter_available=tkinter_available)
    interaction_mode = _detect_interaction_mode(os_family)
    gui_available = _detect_gui_available(os_family, tkinter_available, gui_runtime)
    metadata_summary = _load_metadata_summary()
    vault_summary = _load_vault_summary()

    return {
        "schema_version": 1,
        "last_refreshed_at": _now_iso(),
        "language": {
            "preferred": resolved_language,
            "source": language_source,
        },
        "host": {
            "os_family": os_family,
            "os_release": platform.release(),
            "launcher": _launcher_for_os(os_family),
            "python_executable": sys.executable,
            "interaction_mode": interaction_mode,
            "gui_available": gui_available,
            "tkinter_available": tkinter_available,
            "gui_bootstrap_available": os_family in {"Windows", "Darwin"},
            "gui_bootstrap_recommended": os_family in {"Windows", "Darwin"} and not gui_available,
            "tkinter_probe": tkinter_probe,
            "gui_runtime": gui_runtime,
            "config_dir": str(config_dir()),
        },
        "routes": {
            "profile_management": _route_profile_management(os_family, resolved_language, gui_available, interaction_mode),
            "vault_management": _route_vault_management(os_family, resolved_language, gui_available, interaction_mode),
            "public_api_launcher": _launcher_for_os(os_family),
            "private_api_requires": ["saved_profile", "vault_ready"],
        },
        "vault": vault_summary,
        "profiles": metadata_summary,
    }


def build_agent_runtime_state(preferred_language: str | None = None, command: Optional[str] = None) -> dict[str, Any]:
    resolved_language, _language_source = resolve_language_with_source(preferred_language)
    os_family = platform.system()
    requirements_ready, missing_modules = _probe_required_modules()
    env_validation = validate_runtime_environment()
    metadata_summary = _load_metadata_summary()
    vault_summary = _load_vault_summary()
    store = _load_store_module()

    if store is not None:
        try:
            status = store.vault_status()
            vault_runtime = {
                "backend": status.get("backend"),
                "configured": bool(status.get("configured")),
                "mode": status.get("mode"),
                "state": status.get("state"),
                "action_required": status.get("action_required"),
                "session_descriptor_present": bool(status.get("vault_session_path") and Path(str(status["vault_session_path"])).exists()),
            }
        except Exception:
            vault_runtime = _fallback_vault_runtime(vault_summary)
    else:
        vault_runtime = _fallback_vault_runtime(vault_summary)

    return {
        "schema_version": 1,
        "last_verified_at": _now_iso(),
        "command": command,
        "language": {
            "preferred": resolved_language,
        },
        "host": {
            "os_family": os_family,
            "launcher": _launcher_for_os(os_family),
            "python_executable": sys.executable,
            "requirements_ready": requirements_ready,
            "missing_modules": missing_modules,
        },
        "env": {
            env_name: bool(os.getenv(env_name))
            for env_name in RUNTIME_ENV_VARS
        },
        "env_validation": env_validation,
        "vault": vault_runtime,
        "profiles": {
            "count": metadata_summary["count"],
            "default_profile_id": metadata_summary["default_profile_id"],
            "default_profile_name": metadata_summary["default_profile_name"],
            "default_profile_usable": _probe_default_profile_usable(store, metadata_summary["default_profile_id"]),
        },
    }


def refresh_agent_init_state(preferred_language: str | None = None) -> dict[str, Any]:
    payload = build_agent_init_state(preferred_language=preferred_language)
    _atomic_write_json(agent_init_path(), payload)
    return payload


def refresh_agent_runtime_state(preferred_language: str | None = None, command: Optional[str] = None) -> dict[str, Any]:
    payload = build_agent_runtime_state(preferred_language=preferred_language, command=command)
    _atomic_write_json(agent_runtime_path(), payload)
    return payload


def refresh_agent_records(preferred_language: str | None = None, command: Optional[str] = None) -> dict[str, dict[str, Any]]:
    return {
        "init": refresh_agent_init_state(preferred_language=preferred_language),
        "runtime": refresh_agent_runtime_state(preferred_language=preferred_language, command=command),
    }


def _output_json(payload: dict[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh or inspect non-secret AI cache files for the WEEX trader skill.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Optional zh/en language value to persist into agent-init.json",
    )
    parser.add_argument(
        "--command",
        default="agent-state.refresh",
        help="Command label to store in agent-runtime.json",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    payload = refresh_agent_records(
        preferred_language=args.language,
        command=args.command,
    )
    _output_json(payload, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
