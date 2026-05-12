#!/usr/bin/env python3
"""Bootstrap a managed Python runtime for WEEX GUI entrypoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from weex_profile_language import resolve_language


CONFIG_HOME_ENV = "WEEX_TRADER_SKILL_HOME"
BOOTSTRAP_ACTIVE_ENV = "WEEX_GUI_RUNTIME_ACTIVE"
BOOTSTRAP_DISABLE_ENV = "WEEX_GUI_RUNTIME_DISABLE"
DEFAULT_GUI_PYTHON = "3.12.13"
GUI_REQUIRED_MODULES = ("cryptography",)
PINNED_UV_VERSION = "0.11.12"
PINNED_UV_INSTALLER_SHA256 = {
    "Darwin": "396c0e0bc4e9fa1001359f83de5e07ef71fecdd8a3371b992a5d4b866533fa99",
    "Windows": "78e8dd13f72db891df271aee1a6ef95c71bcd93180e3d23a41c605e4952a144e",
}
UV_RELEASE_BASE_URL = "https://github.com/astral-sh/uv/releases/download"


TEXTS = {
    "en": {
        "probe_description": "Probe whether the current Python runtime can launch the WEEX GUI.",
        "ensure_description": "Provision the managed GUI runtime without launching the GUI.",
        "bootstrap_failed": "Unable to prepare the managed WEEX GUI runtime.",
        "current_runtime_crashes": "The current Python runtime crashes while initializing Tk.",
        "current_runtime_missing_tk": "The current Python runtime does not provide tkinter.",
        "current_runtime_missing_modules": "The current Python runtime is missing GUI dependencies: {modules}.",
        "managed_runtime_failed": "The managed GUI runtime was created, but Tk still could not initialize.",
        "disabled": "Managed GUI runtime bootstrap is disabled by environment.",
        "explicit_runtime_required": (
            "The managed GUI runtime is not installed. Ask the AI to install it; after your confirmation it will run "
            "scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty to download and verify the pinned runtime."
        ),
        "managed_runtime_accept_help": (
            "Explicitly allow downloading the pinned uv installer and Python packages for the managed GUI runtime."
        ),
        "installer_hash_mismatch": "Downloaded uv installer checksum did not match the pinned value.",
        "pinned_uv_unavailable": "Pinned uv {version} is unavailable after installation.",
        "unsupported_platform": "Managed GUI runtime bootstrap is only supported on macOS and Windows.",
    },
    "zh": {
        "probe_description": "检测当前 Python 运行时是否可以启动 WEEX 图形界面。",
        "ensure_description": "预创建受管 GUI 运行时，但不启动图形界面。",
        "bootstrap_failed": "无法准备受管 WEEX GUI 运行时。",
        "current_runtime_crashes": "当前 Python 运行时在初始化 Tk 时会直接崩溃。",
        "current_runtime_missing_tk": "当前 Python 运行时没有可用的 tkinter。",
        "current_runtime_missing_modules": "当前 Python 运行时缺少 GUI 依赖：{modules}。",
        "managed_runtime_failed": "受管 GUI 运行时已经创建，但 Tk 仍然无法初始化。",
        "disabled": "环境变量已禁用受管 GUI 运行时 bootstrap。",
        "explicit_runtime_required": (
            "尚未安装受管 GUI 运行时。可以让 AI 询问并在你确认后代为安装；安装时会执行 "
            "scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty 下载并校验固定版本运行时。"
        ),
        "managed_runtime_accept_help": "明确允许下载固定版本 uv 安装器和 Python 依赖以创建受管 GUI 运行时。",
        "installer_hash_mismatch": "下载的 uv 安装器 checksum 与固定值不一致。",
        "pinned_uv_unavailable": "安装后无法使用固定版本 uv {version}。",
        "unsupported_platform": "受管 GUI 运行时 bootstrap 仅支持 macOS 和 Windows。",
    },
}


def config_dir() -> Path:
    raw = os.getenv(CONFIG_HOME_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".weex-trader-skill"


def bootstrap_root() -> Path:
    return config_dir() / "gui-runtime"


def uv_install_dir() -> Path:
    return bootstrap_root() / "uv-bin"


def managed_python_install_dir() -> Path:
    return bootstrap_root() / "python"


def managed_venv_dir() -> Path:
    major_minor = DEFAULT_GUI_PYTHON.replace(".", "")
    return bootstrap_root() / f"venv-py{major_minor}"


def managed_venv_python() -> Path:
    if platform.system() == "Windows":
        return managed_venv_dir() / "Scripts" / "python.exe"
    return managed_venv_dir() / "bin" / "python"


def _same_executable(left: str | Path, right: str | Path) -> bool:
    left_path = Path(left)
    right_path = Path(right)
    try:
        return left_path.samefile(right_path)
    except OSError:
        return left_path.resolve() == right_path.resolve()


def _is_managed_gui_executable(executable: str | Path, runtime_python: str | Path) -> bool:
    if _same_executable(executable, runtime_python):
        return True
    if platform.system() == "Windows":
        runtime_pythonw = Path(runtime_python).with_name("pythonw.exe")
        return _same_executable(executable, runtime_pythonw)
    return False


def requirements_path() -> Path:
    return Path(__file__).resolve().parents[1] / "requirements.txt"


def requirements_lock_path() -> Path:
    return Path(__file__).resolve().parents[1] / "requirements.lock"


def managed_runtime_setup_command(os_family: Optional[str] = None) -> str:
    launcher = "py -3" if (os_family or platform.system()) == "Windows" else "python3"
    return f"{launcher} scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty"


def _localized(language: str, key: str, **kwargs: object) -> str:
    bundle = TEXTS["zh" if language == "zh" else "en"]
    value = bundle[key]
    if kwargs:
        return value.format(**kwargs)
    return value


class GuiBootstrapError(RuntimeError):
    """Raised when the managed GUI runtime cannot be prepared."""


@dataclass(frozen=True)
class RuntimeProbe:
    usable: bool
    reason: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    tk_version: Optional[str] = None
    tcl_version: Optional[str] = None
    missing_modules: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "usable": self.usable,
            "reason": self.reason,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "tk_version": self.tk_version,
            "tcl_version": self.tcl_version,
            "missing_modules": list(self.missing_modules),
        }

    def summary(self, language: str) -> str:
        if self.reason == "missing_tk":
            return _localized(language, "current_runtime_missing_tk")
        if self.reason == "missing_modules":
            modules = ", ".join(self.missing_modules) or "unknown"
            return _localized(language, "current_runtime_missing_modules", modules=modules)
        if self.reason == "tk_crashed":
            return _localized(language, "current_runtime_crashes")
        return _localized(language, "bootstrap_failed")


def _probe_script(modules: tuple[str, ...]) -> str:
    return textwrap.dedent(
        f"""
        import importlib
        import json
        import platform
        import sys

        payload = {{}}
        try:
            import tkinter as tk
            import _tkinter
            payload["tk_version"] = getattr(tk, "TkVersion", None)
            payload["tcl_version"] = getattr(tk, "TclVersion", None)
            payload["tkinter_path"] = getattr(_tkinter, "__file__", None)
            payload["python_executable"] = sys.executable
            payload["platform"] = platform.system()
            missing = []
            for module_name in {modules!r}:
                try:
                    importlib.import_module(module_name)
                except Exception:
                    missing.append(module_name)
            payload["missing_modules"] = missing
            payload["usable"] = not missing
            print(json.dumps(payload))
            if missing:
                raise SystemExit(2)
        except Exception as exc:
            payload.setdefault("usable", False)
            payload["error"] = f"{{type(exc).__name__}}: {{exc}}"
            print(json.dumps(payload))
            raise SystemExit(1)
        """
    )


def _parse_json_lines(text: str) -> dict[str, object]:
    for raw_line in reversed(text.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _linked_library_paths(binary_path: str) -> tuple[str, ...]:
    if platform.system() != "Darwin" or not binary_path:
        return ()
    otool = shutil.which("otool")
    if not otool:
        return ()
    completed = subprocess.run(
        [otool, "-L", binary_path],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return ()
    paths: list[str] = []
    for raw_line in completed.stdout.splitlines()[1:]:
        line = raw_line.strip()
        if not line:
            continue
        paths.append(line.split(" ", 1)[0])
    return tuple(paths)


def _is_legacy_macos_tk(
    tk_version: Optional[str],
    tkinter_path: Optional[str],
    linked_paths: tuple[str, ...],
) -> bool:
    if platform.system() != "Darwin":
        return False
    if tk_version and str(tk_version).startswith("8.5"):
        return True
    joined = "\n".join(linked_paths)
    if "/System/Library/Frameworks/Tk.framework/Versions/8.5/Tk" in joined:
        return True
    if "/System/Library/Frameworks/Tcl.framework/Versions/8.5/Tcl" in joined:
        return True
    if tkinter_path and "CommandLineTools/Library/Frameworks/Python3.framework" in tkinter_path and linked_paths:
        return True
    return False


def probe_runtime(python_executable: str) -> RuntimeProbe:
    try:
        completed = subprocess.run(
            [python_executable, "-c", _probe_script(GUI_REQUIRED_MODULES)],
            text=True,
            capture_output=True,
            check=False,
            timeout=8.0,
        )
    except subprocess.TimeoutExpired as exc:
        return RuntimeProbe(
            usable=False,
            reason="timeout",
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
        )

    payload = _parse_json_lines(completed.stdout)
    tk_version = payload.get("tk_version")
    tcl_version = payload.get("tcl_version")
    error = payload.get("error") if isinstance(payload.get("error"), str) else None
    tkinter_path = payload.get("tkinter_path") if isinstance(payload.get("tkinter_path"), str) else None
    raw_missing = payload.get("missing_modules")
    missing_modules = tuple(item for item in raw_missing if isinstance(item, str)) if isinstance(raw_missing, list) else ()
    linked_paths = _linked_library_paths(tkinter_path or "")

    if completed.returncode == 0 and payload.get("usable") is True and not _is_legacy_macos_tk(
        str(tk_version) if tk_version is not None else None,
        tkinter_path,
        linked_paths,
    ):
        return RuntimeProbe(
            usable=True,
            reason="ok",
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            tk_version=str(tk_version) if tk_version is not None else None,
            tcl_version=str(tcl_version) if tcl_version is not None else None,
            missing_modules=missing_modules,
        )

    combined = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part).lower()
    if missing_modules:
        reason = "missing_modules"
    elif "no module named 'tkinter'" in combined or "no module named '_tkinter'" in combined:
        reason = "missing_tk"
    elif _is_legacy_macos_tk(
        str(tk_version) if tk_version is not None else None,
        tkinter_path,
        linked_paths,
    ):
        reason = "tk_crashed"
    elif "later required" in combined and "macos" in combined:
        reason = "tk_crashed"
    elif completed.returncode < 0:
        reason = "tk_crashed"
    else:
        reason = "tk_unusable"

    return RuntimeProbe(
        usable=False,
        reason=reason,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        error=error,
        tk_version=str(tk_version) if tk_version is not None else None,
        tcl_version=str(tcl_version) if tcl_version is not None else None,
        missing_modules=missing_modules,
    )


def _run_command(
    args: list[str],
    *,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        capture_output=True,
        check=False,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_url_to_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "weex-trader-skill-bootstrap"})
    with urllib.request.urlopen(request, timeout=30) as response:
        destination.write_bytes(response.read())


def _raise_command_error(language: str, completed: subprocess.CompletedProcess[str]) -> None:
    detail = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
    if detail:
        raise GuiBootstrapError(f"{_localized(language, 'bootstrap_failed')}\n{detail}")
    raise GuiBootstrapError(_localized(language, "bootstrap_failed"))


def _uv_binary_from_install_dir() -> Path:
    name = "uv.exe" if platform.system() == "Windows" else "uv"
    return uv_install_dir() / name


def _uv_version(uv_binary: Path) -> Optional[str]:
    completed = _run_command([str(uv_binary), "--version"])
    if completed.returncode != 0:
        return None
    parts = completed.stdout.strip().split()
    if len(parts) >= 2 and parts[0] == "uv":
        return parts[1]
    return None


def _installer_asset_for_platform(language: str) -> tuple[str, str]:
    os_family = platform.system()
    if os_family == "Windows":
        asset = "uv-installer.ps1"
    elif os_family == "Darwin":
        asset = "uv-installer.sh"
    else:
        raise GuiBootstrapError(_localized(language, "unsupported_platform"))
    return asset, PINNED_UV_INSTALLER_SHA256[os_family]


def _install_uv(language: str, *, allow_network_install: bool = False) -> Path:
    uv_binary = _uv_binary_from_install_dir()
    if uv_binary.exists() and _uv_version(uv_binary) == PINNED_UV_VERSION:
        return uv_binary

    if not allow_network_install:
        raise GuiBootstrapError(_localized(language, "explicit_runtime_required"))

    uv_dir = uv_install_dir()
    uv_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["UV_UNMANAGED_INSTALL"] = str(uv_dir)
    env["INSTALLER_NO_MODIFY_PATH"] = "1"

    asset, expected_sha256 = _installer_asset_for_platform(language)
    installer_path = uv_dir / f"uv-installer-{PINNED_UV_VERSION}{Path(asset).suffix}"
    installer_url = f"{UV_RELEASE_BASE_URL}/{PINNED_UV_VERSION}/{asset}"
    _download_url_to_file(installer_url, installer_path)
    if _sha256_file(installer_path) != expected_sha256:
        raise GuiBootstrapError(_localized(language, "installer_hash_mismatch"))

    if platform.system() == "Windows":
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer_path),
        ]
    else:
        command = ["/bin/sh", str(installer_path)]

    completed = _run_command(command, env=env)
    if completed.returncode != 0:
        _raise_command_error(language, completed)

    if not uv_binary.exists() or _uv_version(uv_binary) != PINNED_UV_VERSION:
        raise GuiBootstrapError(_localized(language, "pinned_uv_unavailable", version=PINNED_UV_VERSION))
    return uv_binary


def ensure_managed_gui_runtime(language: str, *, allow_network_install: bool = False) -> tuple[Path, RuntimeProbe, str]:
    runtime_python = managed_venv_python()
    action = "created"
    if runtime_python.exists():
        current_probe = probe_runtime(str(runtime_python))
        if current_probe.usable:
            return runtime_python, current_probe, "reused"
        action = "repaired"

    if not allow_network_install:
        raise GuiBootstrapError(_localized(language, "explicit_runtime_required"))

    uv_binary = _install_uv(language, allow_network_install=allow_network_install)
    env = os.environ.copy()
    env["UV_PYTHON_INSTALL_DIR"] = str(managed_python_install_dir())
    env.setdefault("UV_NO_PROGRESS", "1")

    completed = _run_command(
        [
            str(uv_binary),
            "venv",
            "--managed-python",
            "--python",
            DEFAULT_GUI_PYTHON,
            str(managed_venv_dir()),
        ],
        env=env,
    )
    if completed.returncode != 0:
        _raise_command_error(language, completed)

    completed = _run_command(
        [
            str(uv_binary),
            "pip",
            "install",
            "--python",
            str(runtime_python),
            "--require-hashes",
            "-r",
            str(requirements_lock_path()),
        ],
        env=env,
    )
    if completed.returncode != 0:
        _raise_command_error(language, completed)

    probe = probe_runtime(str(runtime_python))
    if not probe.usable:
        raise GuiBootstrapError(
            f"{_localized(language, 'managed_runtime_failed')}\n{probe.summary(language)}"
        )
    return runtime_python, probe, action


def maybe_reexec_under_managed_gui_runtime(
    language: str,
    *,
    entrypoint_path: str | Path,
    argv: Optional[list[str]] = None,
) -> None:
    if platform.system() not in {"Windows", "Darwin"}:
        return
    runtime_python = managed_venv_python()
    if _is_managed_gui_executable(sys.executable, runtime_python):
        os.environ[BOOTSTRAP_ACTIVE_ENV] = "1"
        return
    if os.getenv(BOOTSTRAP_DISABLE_ENV) == "1":
        raise SystemExit(_localized(language, "disabled"))

    if not runtime_python.exists():
        raise SystemExit(_localized(language, "explicit_runtime_required"))
    managed_probe = probe_runtime(str(runtime_python))
    if not managed_probe.usable:
        raise SystemExit(
            f"{_localized(language, 'explicit_runtime_required')}\n{managed_probe.summary(language)}"
        )
    env = os.environ.copy()
    env[BOOTSTRAP_ACTIVE_ENV] = "1"
    env["WEEX_GUI_RUNTIME_REASON"] = "managed_runtime_required"
    target = Path(entrypoint_path).resolve()
    relaunch_args = [str(runtime_python), str(target), *(argv if argv is not None else sys.argv[1:])]
    os.execve(str(runtime_python), relaunch_args, env)


def build_status_payload(language: str) -> dict[str, Any]:
    current_probe = probe_runtime(sys.executable)
    payload: dict[str, Any] = {
        "language": language,
        "platform": platform.system(),
        "current_python": sys.executable,
        "current_probe": current_probe.to_dict(),
        "bootstrap_root": str(bootstrap_root()),
        "managed_venv": str(managed_venv_dir()),
        "managed_python": str(managed_venv_python()),
        "requirements_path": str(requirements_path()),
        "requirements_lock_path": str(requirements_lock_path()),
        "setup_command": managed_runtime_setup_command(),
    }
    return payload


def ensure_status_payload(language: str, *, allow_network_install: bool = False) -> dict[str, Any]:
    runtime_python, probe, action = ensure_managed_gui_runtime(
        language,
        allow_network_install=allow_network_install,
    )
    payload = build_status_payload(language)
    payload["action"] = action
    payload["managed_python"] = str(runtime_python)
    payload["managed_probe"] = probe.to_dict()
    return payload


def _output_json(payload: dict[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False))


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--language", default=argparse.SUPPRESS, help="Optional zh/en language override.")
    common.add_argument(
        "--pretty",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Pretty-print JSON output.",
    )

    parser = argparse.ArgumentParser(
        description="Provision a managed Python runtime for WEEX GUI entrypoints.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--language", default=None, help="Optional zh/en language override.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "probe",
        parents=[common],
        help="Inspect the current GUI runtime.",
        description="Inspect the current GUI runtime.",
    )
    ensure_parser = sub.add_parser(
        "ensure",
        parents=[common],
        help="Provision the managed GUI runtime.",
        description="Provision the managed GUI runtime.",
    )
    ensure_parser.add_argument(
        "--accept-managed-runtime",
        action="store_true",
        help=TEXTS["en"]["managed_runtime_accept_help"],
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    language = resolve_language(args.language)

    if args.command == "probe":
        payload = build_status_payload(language)
    else:
        try:
            payload = ensure_status_payload(language, allow_network_install=args.accept_managed_runtime)
        except GuiBootstrapError as exc:
            payload = {"ok": False, "error": str(exc)}
            if not args.accept_managed_runtime:
                payload["requires_user_consent"] = True
                payload["setup_command"] = managed_runtime_setup_command()
            _output_json(payload, args.pretty)
            return 1
    _output_json(payload, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
