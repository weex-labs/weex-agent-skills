#!/usr/bin/env python3
from __future__ import annotations

import os
import io
import json
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

import weex_gui_launcher as gui_launcher  # noqa: E402


class GuiLauncherTests(unittest.TestCase):
    def test_main_returns_structured_error_when_managed_runtime_is_missing(self) -> None:
        with mock.patch.object(gui_launcher, "launch_detached_entrypoint", side_effect=gui_launcher.GuiLaunchError("managed missing")):
            stream = io.StringIO()
            with mock.patch.object(sys, "stdout", stream):
                exit_code = gui_launcher.main(["profile-manager", "--pretty"])

        self.assertEqual(exit_code, 1)
        payload = json.loads(stream.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "managed missing")

    def test_main_honors_top_level_language_and_pretty_options(self) -> None:
        with mock.patch.object(gui_launcher, "launch_detached_entrypoint", side_effect=gui_launcher.GuiLaunchError("managed missing")) as launch_mock:
            stream = io.StringIO()
            with mock.patch.object(sys, "stdout", stream):
                exit_code = gui_launcher.main(["--language", "zh", "--pretty", "profile-manager"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(launch_mock.call_args.args[0], "zh")
        text = stream.getvalue()
        self.assertIn("\n  ", text)
        self.assertEqual(json.loads(text)["error"], "managed missing")

    def test_launch_detached_entrypoint_requires_and_uses_managed_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            entrypoint_path = temp_root / "entrypoint.py"
            managed_python = temp_root / "gui-runtime" / "bin" / "python"
            entrypoint_path.write_text("print('ok')\n", encoding="utf-8")
            managed_python.parent.mkdir(parents=True)
            managed_python.write_text("", encoding="utf-8")
            managed_probe = mock.Mock(usable=True)

            with mock.patch.object(gui_launcher.platform, "system", return_value="Darwin"):
                with mock.patch.object(gui_launcher, "managed_venv_python", return_value=managed_python, create=True):
                    with mock.patch.object(gui_launcher, "probe_runtime", return_value=managed_probe, create=True):
                        with mock.patch.object(gui_launcher, "_launch_on_darwin", return_value={"ok": True}) as launch_mock:
                            payload = gui_launcher.launch_detached_entrypoint(
                                "en",
                                entrypoint_path=entrypoint_path,
                                argv=[],
                                label="profile-manager",
                                wait_timeout=0.1,
                            )

        self.assertEqual(payload, {"ok": True})
        launch_mock.assert_called_once()
        self.assertIn("python_executable", launch_mock.call_args.kwargs)
        self.assertEqual(launch_mock.call_args.kwargs["python_executable"], managed_python)

    def test_launch_on_darwin_uses_app_bundle_instead_of_command_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            base = temp_root / "profile-manager-launch"
            log_path = base.with_suffix(".log")
            pid_path = base.with_suffix(".pid")
            entrypoint_path = temp_root / "entrypoint.py"
            entrypoint_path.write_text("print('ok')\n", encoding="utf-8")
            open_result = subprocess.CompletedProcess(
                args=["/usr/bin/open", "launcher"],
                returncode=0,
                stdout="",
                stderr="",
            )

            with mock.patch.object(gui_launcher, "shutil_which", return_value="/usr/bin/open"):
                with mock.patch.object(gui_launcher, "_next_launch_paths", return_value=(base, log_path, pid_path)):
                    with mock.patch.object(gui_launcher.subprocess, "run", return_value=open_result) as run_mock:
                        with mock.patch.object(gui_launcher, "_wait_for_pid", return_value=4242):
                            with mock.patch.object(gui_launcher, "_process_exists", return_value=True):
                                payload = gui_launcher._launch_on_darwin(
                                    "zh",
                                    entrypoint_path=entrypoint_path,
                                    argv=["--language", "zh"],
                                    label="profile-manager",
                                    wait_timeout=0.1,
                                )

            app_path = base.with_suffix(".app")
            launcher_path = app_path / "Contents" / "MacOS" / "launcher"
            plist_path = app_path / "Contents" / "Info.plist"

            self.assertEqual(payload["wrapper_path"], str(app_path))
            self.assertEqual(payload["pid"], 4242)
            self.assertNotIn(".command", payload["wrapper_path"])
            self.assertTrue(launcher_path.exists())
            self.assertTrue(plist_path.exists())
            run_mock.assert_called_once_with(
                ["/usr/bin/open", str(app_path)],
                text=True,
                capture_output=True,
                check=False,
            )

            launcher_text = launcher_path.read_text(encoding="utf-8")
            self.assertIn(f"export {gui_launcher.DETACHED_ENV}=1", launcher_text)
            self.assertIn(str(entrypoint_path.parent), launcher_text)
            self.assertIn(str(entrypoint_path), launcher_text)
            self.assertIn(str(log_path), launcher_text)
            self.assertIn(str(pid_path), launcher_text)

            plist_text = plist_path.read_text(encoding="utf-8")
            self.assertIn("<string>launcher</string>", plist_text)
            self.assertIn("<string>APPL</string>", plist_text)

    def test_launch_on_windows_uses_hidden_python_process_instead_of_cmd_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            base = temp_root / "vault-manager-launch"
            log_path = base.with_suffix(".log")
            pid_path = base.with_suffix(".pid")
            entrypoint_path = temp_root / "entrypoint.py"
            entrypoint_path.write_text("print('ok')\n", encoding="utf-8")
            python_exe = temp_root / "python.exe"
            pythonw_exe = temp_root / "pythonw.exe"
            python_exe.write_text("", encoding="utf-8")
            pythonw_exe.write_text("", encoding="utf-8")

            process = mock.Mock()
            process.pid = 5252

            with mock.patch.object(gui_launcher, "_next_launch_paths", return_value=(base, log_path, pid_path)):
                with mock.patch.object(gui_launcher.sys, "executable", str(python_exe)):
                    with mock.patch.object(gui_launcher.subprocess, "Popen", return_value=process) as popen_mock:
                        with mock.patch.object(gui_launcher.subprocess, "DETACHED_PROCESS", 0x8, create=True):
                            with mock.patch.object(gui_launcher.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True):
                                with mock.patch.object(gui_launcher.subprocess, "CREATE_NO_WINDOW", 0x8000000, create=True):
                                    payload = gui_launcher._launch_on_windows(
                                        "zh",
                                        entrypoint_path=entrypoint_path,
                                        argv=["--language", "zh"],
                                        label="vault-manager",
                                        wait_timeout=0.1,
                                    )

            self.assertEqual(payload["wrapper_path"], str(pythonw_exe))
            self.assertEqual(payload["pid"], 5252)
            self.assertNotIn(".cmd", payload["wrapper_path"])
            self.assertFalse(base.with_suffix(".cmd").exists())
            self.assertEqual(pid_path.read_text(encoding="utf-8"), "5252")

            call_args = popen_mock.call_args
            self.assertEqual(
                call_args.args[0],
                [str(pythonw_exe), str(entrypoint_path), "--language", "zh"],
            )
            self.assertEqual(call_args.kwargs["cwd"], str(entrypoint_path.parent))
            self.assertEqual(call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(call_args.kwargs["stderr"], subprocess.STDOUT)
            self.assertEqual(call_args.kwargs["creationflags"], 0x8 | 0x200 | 0x8000000)

    def test_prune_launch_records_keeps_recent_entries_and_trims_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launch_dir = Path(tmpdir) / "gui-launchers"
            launch_dir.mkdir(parents=True, exist_ok=True)

            group0 = launch_dir / "profile-manager-old"
            group1 = launch_dir / "profile-manager-mid"
            group2 = launch_dir / "profile-manager-new"

            old_log = group0.with_suffix(".log")
            old_pid = group0.with_suffix(".pid")
            mid_log = group1.with_suffix(".log")
            new_log = group2.with_suffix(".log")

            old_log.write_text("old-log", encoding="utf-8")
            old_pid.write_text("1", encoding="utf-8")
            mid_log.write_text("middle", encoding="utf-8")
            new_log.write_text("abcdefghijklmnopqrstuvwxyz", encoding="utf-8")

            now = 1_700_000_000
            os.utime(old_log, (now - 30, now - 30))
            os.utime(old_pid, (now - 30, now - 30))
            os.utime(mid_log, (now - 20, now - 20))
            os.utime(new_log, (now - 10, now - 10))

            with mock.patch.object(gui_launcher, "_ensure_launch_records_dir", return_value=launch_dir):
                gui_launcher._prune_launch_records(max_records=2, max_log_bytes=8)

            self.assertFalse(old_log.exists())
            self.assertFalse(old_pid.exists())
            self.assertTrue(mid_log.exists())
            self.assertTrue(new_log.exists())
            self.assertEqual(new_log.read_text(encoding="utf-8"), "stuvwxyz")


if __name__ == "__main__":
    unittest.main()
