#!/usr/bin/env python3
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import weex_doctor as doctor  # noqa: E402
from weex_gui_bootstrap import RuntimeProbe  # noqa: E402


class DoctorGuiTests(unittest.TestCase):
    def test_build_gui_report_requires_managed_runtime_even_when_system_runtime_is_usable(self) -> None:
        current_probe = RuntimeProbe(usable=True, reason="ok", returncode=0, tk_version="8.6", tcl_version="8.6")

        with mock.patch.object(doctor.platform, "system", return_value="Darwin"):
            with mock.patch.object(doctor, "probe_runtime", return_value=current_probe):
                with mock.patch.object(doctor, "_managed_runtime_status", return_value={
                    "exists": False,
                    "python_executable": "/tmp/managed-python",
                    "usable": False,
                    "probe": None,
                }):
                    payload = doctor.build_gui_report("en", fix=False)

        self.assertFalse(payload["ok"])
        self.assertIn("--accept-managed-runtime", payload["recommendation"])
        self.assertTrue(payload["requires_user_consent"])
        self.assertEqual(payload["summary"], "GUI is not ready yet.")

    def test_build_gui_report_recommends_fix_when_no_gui_runtime_is_ready(self) -> None:
        failing_probe = RuntimeProbe(usable=False, reason="tk_crashed", returncode=-6)

        with mock.patch.object(doctor.platform, "system", return_value="Darwin"):
            with mock.patch.object(doctor, "probe_runtime", return_value=failing_probe):
                with mock.patch.object(doctor, "_managed_runtime_status", return_value={
                    "exists": False,
                    "python_executable": "/tmp/managed-python",
                    "usable": False,
                    "probe": None,
                }):
                    with mock.patch.object(doctor, "ensure_managed_gui_runtime") as ensure_mock:
                        payload = doctor.build_gui_report("en", fix=False)

        ensure_mock.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertIn("--accept-managed-runtime", payload["recommendation"])
        self.assertIn("Ask the AI", payload["recommendation"])
        self.assertTrue(payload["requires_user_consent"])
        self.assertEqual(
            payload["setup_command"],
            "python3 scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty",
        )
        self.assertEqual(payload["current_runtime"]["reason"], "tk_crashed")

    def test_build_gui_report_repairs_managed_runtime_when_fix_is_requested(self) -> None:
        failing_probe = RuntimeProbe(usable=False, reason="tk_crashed", returncode=-6)
        managed_probe = RuntimeProbe(usable=True, reason="ok", returncode=0, tk_version="9.0", tcl_version="9.0")

        with mock.patch.object(doctor.platform, "system", return_value="Darwin"):
            with mock.patch.object(doctor, "probe_runtime", return_value=failing_probe):
                with mock.patch.object(doctor, "_managed_runtime_status", return_value={
                    "exists": False,
                    "python_executable": "/tmp/managed-python",
                    "usable": False,
                    "probe": None,
                }):
                    with mock.patch.object(
                        doctor,
                        "ensure_managed_gui_runtime",
                        return_value=(Path("/tmp/managed-python"), managed_probe, "created"),
                    ) as ensure_mock:
                        with mock.patch.object(doctor, "refresh_agent_records") as refresh_mock:
                            payload = doctor.build_gui_report("zh", fix=True, accept_managed_runtime=True)

        ensure_mock.assert_called_once_with("zh", allow_network_install=True)
        refresh_mock.assert_called_once_with(preferred_language="zh", command="doctor.gui.fix")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["fix"]["result"], "created")
        self.assertFalse(payload["requires_user_consent"])
        self.assertTrue(payload["managed_runtime"]["usable"])

    def test_render_gui_report_is_human_readable(self) -> None:
        payload = {
            "summary": "GUI is healthy on the current Python runtime.",
            "current_python": "/tmp/python",
            "current_runtime": {
                "detail": "ok",
            },
            "managed_runtime": {
                "exists": False,
                "usable": False,
                "python_executable": "/tmp/managed-python",
            },
            "fix": {
                "requested": False,
                "result": None,
                "error": None,
            },
            "recommendation": "No action needed.",
        }

        report = doctor.render_gui_report("en", payload)

        self.assertIn("Current Python", report)
        self.assertIn("/tmp/python", report)
        self.assertIn("Recommendation", report)

    def test_cmd_gui_pretty_prints_json(self) -> None:
        payload = {
            "ok": True,
            "summary": "ok",
            "current_python": "/tmp/python",
            "current_runtime": {"detail": "ok"},
            "managed_runtime": {
                "exists": False,
                "usable": False,
                "python_executable": "/tmp/managed-python",
            },
            "fix": {"requested": False, "result": None, "error": None},
            "recommendation": "none",
        }
        args = mock.Mock(fix=False, pretty=True, accept_managed_runtime=False)

        with mock.patch.object(doctor, "build_gui_report", return_value=payload):
            stream = io.StringIO()
            with mock.patch.object(sys, "stdout", stream):
                exit_code = doctor.cmd_gui(args, "en")

        self.assertEqual(exit_code, 0)
        self.assertIn('"summary": "ok"', stream.getvalue())


if __name__ == "__main__":
    unittest.main()
