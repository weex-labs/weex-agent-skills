#!/usr/bin/env python3
from __future__ import annotations

import os
import hashlib
import json
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import weex_gui_bootstrap as bootstrap  # noqa: E402


class GuiBootstrapTests(unittest.TestCase):
    def test_probe_runtime_classifies_macos_tk_crash(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["python3", "-c", "probe"],
            returncode=0,
            stdout='{"usable": true, "tk_version": 8.5, "tcl_version": 8.5, "tkinter_path": "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/lib-dynload/_tkinter.cpython-39-darwin.so", "missing_modules": []}\n',
            stderr="",
        )

        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"):
            with mock.patch.object(bootstrap.subprocess, "run", return_value=completed):
                with mock.patch.object(
                    bootstrap,
                    "_linked_library_paths",
                    return_value=(
                        "/System/Library/Frameworks/Tcl.framework/Versions/8.5/Tcl",
                        "/System/Library/Frameworks/Tk.framework/Versions/8.5/Tk",
                    ),
                ):
                    probe = bootstrap.probe_runtime("/usr/bin/python3")

        self.assertFalse(probe.usable)
        self.assertEqual(probe.reason, "tk_crashed")

    def test_probe_runtime_reports_missing_modules(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["python3", "-c", "probe"],
            returncode=2,
            stdout='{"usable": false, "missing_modules": ["cryptography"], "tk_version": 8.6, "tcl_version": 8.6}\n',
            stderr="",
        )

        with mock.patch.object(bootstrap.subprocess, "run", return_value=completed):
            with mock.patch.object(bootstrap, "_linked_library_paths", return_value=()):
                probe = bootstrap.probe_runtime("/usr/bin/python3")

        self.assertFalse(probe.usable)
        self.assertEqual(probe.reason, "missing_modules")
        self.assertEqual(probe.missing_modules, ("cryptography",))

    def test_maybe_reexec_skips_when_already_active(self) -> None:
        with mock.patch.dict(os.environ, {bootstrap.BOOTSTRAP_ACTIVE_ENV: "1"}, clear=False):
            with mock.patch.object(bootstrap, "managed_venv_python", return_value=Path(sys.executable)):
                with mock.patch.object(bootstrap, "probe_runtime") as probe_mock:
                    bootstrap.maybe_reexec_under_managed_gui_runtime(
                        "en",
                        entrypoint_path=SCRIPTS / "weex_profile_manager_app.py",
                        argv=[],
                    )

        probe_mock.assert_not_called()

    def test_maybe_reexec_treats_windows_pythonw_as_managed_runtime(self) -> None:
        runtime_python = Path("/tmp/weex-gui-runtime/Scripts/python.exe")
        runtime_pythonw = runtime_python.with_name("pythonw.exe")

        with mock.patch.object(bootstrap.platform, "system", return_value="Windows"):
            with mock.patch.object(bootstrap, "managed_venv_python", return_value=runtime_python):
                with mock.patch.object(bootstrap.sys, "executable", str(runtime_pythonw)):
                    with mock.patch.object(bootstrap, "probe_runtime") as probe_mock:
                        with mock.patch.object(bootstrap.os, "execve") as exec_mock:
                            bootstrap.maybe_reexec_under_managed_gui_runtime(
                                "en",
                                entrypoint_path=SCRIPTS / "weex_profile_manager_app.py",
                                argv=[],
                            )

        probe_mock.assert_not_called()
        exec_mock.assert_not_called()

    def test_maybe_reexec_requires_explicit_setup_when_managed_runtime_is_missing(self) -> None:
        failing_probe = bootstrap.RuntimeProbe(usable=False, reason="tk_crashed", returncode=-6)

        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"):
            with mock.patch.object(bootstrap, "probe_runtime", return_value=failing_probe):
                with mock.patch.object(bootstrap, "managed_venv_python", return_value=Path("/tmp/missing-gui-python")):
                    with mock.patch.object(bootstrap.os, "execve") as exec_mock:
                        with self.assertRaises(SystemExit) as exc_info:
                            bootstrap.maybe_reexec_under_managed_gui_runtime(
                                "en",
                                entrypoint_path=SCRIPTS / "weex_profile_manager_app.py",
                                argv=["--help"],
                            )

        exec_mock.assert_not_called()
        self.assertIn("--accept-managed-runtime", str(exc_info.exception))
        self.assertIn("Ask the AI", str(exc_info.exception))

    def test_maybe_reexec_uses_managed_runtime_even_when_current_runtime_is_usable(self) -> None:
        current_probe = bootstrap.RuntimeProbe(usable=True, reason="ok", returncode=0, tk_version="8.6", tcl_version="8.6")
        managed_probe = bootstrap.RuntimeProbe(usable=True, reason="ok", returncode=0, tk_version="9.0", tcl_version="9.0")
        runtime_python = Path("/tmp/gui-python")

        def fake_probe(path: str) -> bootstrap.RuntimeProbe:
            return managed_probe if path == str(runtime_python) else current_probe

        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"):
            with mock.patch.object(bootstrap, "probe_runtime", side_effect=fake_probe):
                with mock.patch.object(bootstrap, "managed_venv_python", return_value=runtime_python):
                    with mock.patch.object(Path, "exists", return_value=True):
                        with mock.patch.object(bootstrap.os, "execve", side_effect=RuntimeError("exec")) as exec_mock:
                            with self.assertRaises(RuntimeError):
                                bootstrap.maybe_reexec_under_managed_gui_runtime(
                                    "en",
                                    entrypoint_path=SCRIPTS / "weex_profile_manager_app.py",
                                    argv=["--help"],
                                )

        exec_mock.assert_called_once()
        args, _kwargs = exec_mock.call_args
        self.assertEqual(args[0], str(runtime_python))
        self.assertEqual(args[1][:3], [str(runtime_python), str(SCRIPTS / "weex_profile_manager_app.py"), "--help"])

    def test_maybe_reexec_reuses_existing_managed_runtime_on_darwin(self) -> None:
        failing_probe = bootstrap.RuntimeProbe(usable=False, reason="tk_crashed", returncode=-6)
        managed_probe = bootstrap.RuntimeProbe(usable=True, reason="ok", returncode=0, tk_version="9.0", tcl_version="9.0")
        runtime_python = Path("/tmp/gui-python")

        def fake_probe(path: str) -> bootstrap.RuntimeProbe:
            return managed_probe if path == str(runtime_python) else failing_probe

        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"):
            with mock.patch.object(bootstrap, "probe_runtime", side_effect=fake_probe):
                with mock.patch.object(bootstrap, "managed_venv_python", return_value=runtime_python):
                    with mock.patch.object(Path, "exists", return_value=True):
                        with mock.patch.object(bootstrap.os, "execve", side_effect=RuntimeError("exec")) as exec_mock:
                            with self.assertRaises(RuntimeError):
                                bootstrap.maybe_reexec_under_managed_gui_runtime(
                                    "en",
                                    entrypoint_path=SCRIPTS / "weex_profile_manager_app.py",
                                    argv=["--help"],
                                )

        exec_mock.assert_called_once()
        args, _kwargs = exec_mock.call_args
        self.assertEqual(args[0], str(runtime_python))
        self.assertEqual(args[1][:3], [str(runtime_python), str(SCRIPTS / "weex_profile_manager_app.py"), "--help"])

    def test_ensure_managed_runtime_requires_explicit_network_consent(self) -> None:
        failing_probe = bootstrap.RuntimeProbe(usable=False, reason="missing_tk", returncode=1)

        with mock.patch.object(bootstrap, "managed_venv_python", return_value=Path("/tmp/missing-gui-python")):
            with mock.patch.object(bootstrap, "probe_runtime", return_value=failing_probe):
                with mock.patch.object(bootstrap, "_install_uv") as install_mock:
                    with self.assertRaises(bootstrap.GuiBootstrapError) as exc_info:
                        bootstrap.ensure_managed_gui_runtime("en")

        install_mock.assert_not_called()
        self.assertIn("--accept-managed-runtime", str(exc_info.exception))
        self.assertIn("Ask the AI", str(exc_info.exception))

    def test_install_uv_downloads_pinned_installer_and_verifies_hash(self) -> None:
        payload = b"#!/bin/sh\nexit 0\n"
        digest = hashlib.sha256(payload).hexdigest()
        commands: list[list[str]] = []

        def fake_download(_url: str, destination: Path) -> None:
            destination.write_bytes(payload)

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            uv_binary = bootstrap._uv_binary_from_install_dir()
            uv_binary.parent.mkdir(parents=True, exist_ok=True)
            uv_binary.write_text("#!/bin/sh\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {bootstrap.CONFIG_HOME_ENV: tempdir}, clear=False):
                with mock.patch.object(bootstrap, "PINNED_UV_INSTALLER_SHA256", {"Darwin": digest}):
                    with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"):
                        with mock.patch.object(bootstrap, "_download_url_to_file", side_effect=fake_download) as download_mock:
                            with mock.patch.object(bootstrap, "_run_command", side_effect=fake_run):
                                with mock.patch.object(bootstrap, "_uv_version", return_value=bootstrap.PINNED_UV_VERSION):
                                    uv_binary = bootstrap._install_uv("en", allow_network_install=True)

        self.assertTrue(str(uv_binary).endswith("uv"))
        download_mock.assert_called_once()
        self.assertEqual(commands[0][0], "/bin/sh")
        self.assertTrue(commands[0][1].endswith(f"uv-installer-{bootstrap.PINNED_UV_VERSION}.sh"))
        self.assertNotIn("|", " ".join(commands[0]))

    def test_install_uv_rejects_installer_hash_mismatch(self) -> None:
        def fake_download(_url: str, destination: Path) -> None:
            destination.write_text("tampered", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {bootstrap.CONFIG_HOME_ENV: tempdir}, clear=False):
                with mock.patch.object(bootstrap, "PINNED_UV_INSTALLER_SHA256", {"Darwin": "0" * 64}):
                    with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"):
                        with mock.patch.object(bootstrap, "_download_url_to_file", side_effect=fake_download):
                            with mock.patch.object(bootstrap, "_run_command") as run_mock:
                                with self.assertRaises(bootstrap.GuiBootstrapError):
                                    bootstrap._install_uv("en", allow_network_install=True)

        run_mock.assert_not_called()

    def test_ensure_managed_runtime_installs_locked_requirements_with_hashes(self) -> None:
        managed_probe = bootstrap.RuntimeProbe(usable=True, reason="ok", returncode=0, tk_version="9.0", tcl_version="9.0")
        runtime_python = Path("/tmp/gui-python")
        uv_binary = Path("/tmp/uv")
        commands: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(bootstrap, "managed_venv_python", return_value=runtime_python):
            with mock.patch.object(bootstrap, "probe_runtime", return_value=managed_probe):
                with mock.patch.object(bootstrap, "_install_uv", return_value=uv_binary) as install_mock:
                    with mock.patch.object(bootstrap, "_run_command", side_effect=fake_run):
                        with mock.patch.object(Path, "exists", return_value=False):
                            result_python, _probe, action = bootstrap.ensure_managed_gui_runtime(
                                "en",
                                allow_network_install=True,
                            )

        install_mock.assert_called_once_with("en", allow_network_install=True)
        self.assertEqual(result_python, runtime_python)
        self.assertEqual(action, "created")
        pip_install = commands[1]
        self.assertIn("--require-hashes", pip_install)
        self.assertIn(str(bootstrap.requirements_lock_path()), pip_install)

    def test_cli_help_renders(self) -> None:
        parser = bootstrap.build_parser()

        with self.assertRaises(SystemExit) as exc_info:
            parser.parse_args(["--help"])

        self.assertEqual(exc_info.exception.code, 0)

    def test_cli_ensure_without_accept_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {bootstrap.CONFIG_HOME_ENV: tempdir}, clear=False):
                stream = io.StringIO()
                with mock.patch.object(sys, "stdout", stream):
                    exit_code = bootstrap.main(["ensure", "--pretty"])

        self.assertEqual(exit_code, 1)
        payload = json.loads(stream.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("--accept-managed-runtime", payload["error"])
        self.assertIn("Ask the AI", payload["error"])
        self.assertTrue(payload["requires_user_consent"])
        self.assertEqual(
            payload["setup_command"],
            "python3 scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty",
        )

    def test_cli_honors_top_level_language_and_pretty_options(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {bootstrap.CONFIG_HOME_ENV: tempdir}, clear=False):
                stream = io.StringIO()
                with mock.patch.object(sys, "stdout", stream):
                    exit_code = bootstrap.main(["--language", "zh", "--pretty", "ensure"])

        self.assertEqual(exit_code, 1)
        text = stream.getvalue()
        self.assertIn("\n  ", text)
        payload = json.loads(text)
        self.assertIn("尚未安装受管 GUI 运行时", payload["error"])


if __name__ == "__main__":
    unittest.main()
