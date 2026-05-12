#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import weex_agent_state as agent_state  # noqa: E402
from weex_gui_bootstrap import RuntimeProbe  # noqa: E402


class AgentStateGuiRuntimeTests(unittest.TestCase):
    def test_preflight_requires_managed_runtime_even_when_current_runtime_has_tk(self) -> None:
        current_probe = RuntimeProbe(usable=True, reason="ok", returncode=0, tk_version="8.6", tcl_version="8.6")
        missing_managed_python = Path("/tmp/missing-weex-managed-python")

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(agent_state.platform, "system", return_value="Darwin"):
                    with mock.patch.object(agent_state.platform, "release", return_value="25.2.0"):
                        with mock.patch.object(agent_state, "probe_runtime", return_value=current_probe):
                            with mock.patch.object(agent_state, "managed_venv_python", return_value=missing_managed_python):
                                payload = agent_state.build_agent_init_state(preferred_language="en")

        self.assertTrue(payload["host"]["tkinter_available"])
        self.assertFalse(payload["host"]["gui_available"])
        self.assertEqual(payload["routes"]["profile_management"], "macos_cli_en")
        self.assertTrue(payload["host"]["gui_bootstrap_recommended"])
        self.assertFalse(payload["host"]["gui_runtime"]["ready"])
        self.assertEqual(payload["host"]["gui_runtime"]["action"], "explicit_setup_required")
        self.assertTrue(payload["host"]["gui_runtime"]["requires_user_consent"])
        self.assertIn("--accept-managed-runtime", payload["host"]["gui_runtime"]["error"])

    def test_preflight_reports_explicit_setup_required_without_auto_prepare(self) -> None:
        failing_probe = RuntimeProbe(usable=False, reason="tk_crashed", returncode=-6)

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(agent_state.platform, "system", return_value="Darwin"):
                    with mock.patch.object(agent_state.platform, "release", return_value="25.2.0"):
                        with mock.patch.object(agent_state, "probe_runtime", return_value=failing_probe):
                            payload = agent_state.build_agent_init_state(preferred_language="zh")

        self.assertFalse(payload["host"]["tkinter_available"])
        self.assertFalse(payload["host"]["gui_available"])
        self.assertEqual(payload["routes"]["profile_management"], "macos_cli_zh")
        self.assertTrue(payload["host"]["gui_bootstrap_recommended"])
        self.assertEqual(payload["host"]["gui_runtime"]["action"], "explicit_setup_required")
        self.assertFalse(payload["host"]["gui_runtime"]["attempted"])
        self.assertTrue(payload["host"]["gui_runtime"]["requires_user_consent"])
        self.assertEqual(
            payload["host"]["gui_runtime"]["setup_command"],
            "python3 scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty",
        )
        self.assertIn("--accept-managed-runtime", payload["host"]["gui_runtime"]["error"])
        self.assertIn("让 AI", payload["host"]["gui_runtime"]["error"])

    def test_preflight_falls_back_to_cli_when_existing_managed_runtime_is_broken(self) -> None:
        current_probe = RuntimeProbe(usable=False, reason="tk_crashed", returncode=-6)
        managed_probe = RuntimeProbe(usable=False, reason="missing_modules", returncode=2, missing_modules=("cryptography",))
        managed_python = Path("/tmp/weex-managed-python")

        def fake_probe(path: str) -> RuntimeProbe:
            return managed_probe if path == str(managed_python) else current_probe

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(os.environ, {"WEEX_TRADER_SKILL_HOME": tempdir}, clear=False):
                with mock.patch.object(agent_state.platform, "system", return_value="Darwin"):
                    with mock.patch.object(agent_state.platform, "release", return_value="25.2.0"):
                        with mock.patch.object(agent_state, "probe_runtime", side_effect=fake_probe):
                            with mock.patch.object(agent_state, "managed_venv_python", return_value=managed_python):
                                with mock.patch.object(Path, "exists", return_value=True):
                                    payload = agent_state.build_agent_init_state(preferred_language="en")

        self.assertFalse(payload["host"]["gui_available"])
        self.assertEqual(payload["routes"]["profile_management"], "macos_cli_en")
        self.assertTrue(payload["host"]["gui_bootstrap_recommended"])
        self.assertEqual(payload["host"]["gui_runtime"]["action"], "explicit_setup_required")
        self.assertTrue(payload["host"]["gui_runtime"]["requires_user_consent"])
        self.assertIn("--accept-managed-runtime", payload["host"]["gui_runtime"]["error"])
        self.assertIn("Ask the AI", payload["host"]["gui_runtime"]["error"])
        self.assertIn("cryptography", payload["host"]["gui_runtime"]["error"])

    def test_preflight_respects_disabled_gui_bootstrap_env(self) -> None:
        failing_probe = RuntimeProbe(usable=False, reason="tk_crashed", returncode=-6)

        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(
                os.environ,
                {
                    "WEEX_TRADER_SKILL_HOME": tempdir,
                    "WEEX_GUI_RUNTIME_DISABLE": "1",
                },
                clear=False,
            ):
                with mock.patch.object(agent_state.platform, "system", return_value="Darwin"):
                    with mock.patch.object(agent_state.platform, "release", return_value="25.2.0"):
                        with mock.patch.object(agent_state, "probe_runtime", return_value=failing_probe):
                            payload = agent_state.build_agent_init_state(preferred_language="en")

        self.assertFalse(payload["host"]["gui_available"])
        self.assertTrue(payload["host"]["gui_runtime"]["disabled"])
        self.assertFalse(payload["host"]["gui_runtime"]["attempted"])
        self.assertEqual(payload["routes"]["profile_management"], "macos_cli_en")

    def test_runtime_state_tracks_api_override_env_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(
                os.environ,
                {
                    "WEEX_TRADER_SKILL_HOME": tempdir,
                    "WEEX_LOCALE": "zh-CN",
                    "WEEX_API_BASE": "https://api.weex.tech",
                    "WEEX_CONTRACT_API_BASE": "https://contract.weex.tech",
                    "WEEX_SPOT_API_BASE": "https://spot.weex.com",
                },
                clear=False,
            ):
                with mock.patch.object(agent_state, "_probe_required_modules", return_value=(True, [])):
                    with mock.patch.object(agent_state, "_load_store_module", return_value=None):
                        payload = agent_state.build_agent_runtime_state(
                            preferred_language="en",
                            command="contract.list-endpoints",
                        )

        self.assertTrue(payload["env"]["WEEX_LOCALE"])
        self.assertTrue(payload["env"]["WEEX_API_BASE"])
        self.assertTrue(payload["env"]["WEEX_CONTRACT_API_BASE"])
        self.assertTrue(payload["env"]["WEEX_SPOT_API_BASE"])
        self.assertTrue(payload["env_validation"]["ok"])

    def test_runtime_state_reports_invalid_runtime_environment_values(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with mock.patch.dict(
                os.environ,
                {
                    "WEEX_TRADER_SKILL_HOME": tempdir,
                    "WEEX_API_TIMEOUT": "abc",
                    "WEEX_API_BASE": "not-a-url",
                    "WEEX_CONTRACT_API_BASE": "https://contract.env.test",
                },
                clear=False,
            ):
                with mock.patch.object(agent_state, "_probe_required_modules", return_value=(True, [])):
                    with mock.patch.object(agent_state, "_load_store_module", return_value=None):
                        payload = agent_state.build_agent_runtime_state(
                            preferred_language="zh",
                            command="contract.place-order",
                        )

        self.assertFalse(payload["env_validation"]["ok"])
        self.assertGreaterEqual(len(payload["env_validation"]["issues"]), 2)
        self.assertTrue(any("WEEX_API_TIMEOUT" in issue for issue in payload["env_validation"]["issues"]))
        self.assertTrue(any("WEEX_API_BASE" in issue for issue in payload["env_validation"]["issues"]))
        self.assertTrue(any("WEEX_CONTRACT_API_BASE" in issue for issue in payload["env_validation"]["issues"]))

    def test_private_runtime_preflight_reports_missing_modules_and_invalid_env(self) -> None:
        with mock.patch.object(agent_state, "_probe_required_modules", return_value=(False, ["cryptography", "requests"])):
            with mock.patch.dict(
                os.environ,
                {
                    "WEEX_API_TIMEOUT": "abc",
                },
                clear=False,
            ):
                with self.assertRaises(agent_state.RuntimePreflightError) as exc_info:
                    agent_state.ensure_private_runtime_ready(command="spot.place-order")

        message = str(exc_info.exception)
        self.assertIn("Private WEEX command preflight failed", message)
        self.assertIn("cryptography", message)
        self.assertIn("requests", message)
        self.assertIn("WEEX_API_TIMEOUT", message)

    def test_private_runtime_preflight_can_auto_run_runtime_setup(self) -> None:
        with mock.patch.object(
            agent_state,
            "_probe_required_modules",
            side_effect=[(False, ["cryptography"]), (True, [])],
        ):
            with mock.patch.object(
                agent_state,
                "validate_runtime_environment",
                return_value={"ok": True, "issues": []},
            ):
                with mock.patch.object(
                    agent_state,
                    "_run_runtime_setup",
                    return_value={"returncode": 0, "payload": {"ok": True}},
                ) as setup_mock:
                    agent_state.ensure_private_runtime_ready(
                        command="contract.place-order",
                        auto_setup=True,
                        language="zh",
                    )

        setup_mock.assert_called_once_with(language="zh")

    def test_private_runtime_preflight_does_not_auto_run_setup_for_invalid_env(self) -> None:
        with mock.patch.object(agent_state, "_probe_required_modules", return_value=(False, ["cryptography"])):
            with mock.patch.object(
                agent_state,
                "validate_runtime_environment",
                return_value={"ok": False, "issues": ["WEEX_API_TIMEOUT must be a positive number of seconds; got 'abc'."]},
            ):
                with mock.patch.object(agent_state, "_run_runtime_setup") as setup_mock:
                    with self.assertRaises(agent_state.RuntimePreflightError):
                        agent_state.ensure_private_runtime_ready(
                            command="spot.place-order",
                            auto_setup=True,
                            language="en",
                        )

        setup_mock.assert_not_called()

    def test_private_runtime_preflight_clears_stale_profile_store_cache_after_auto_setup(self) -> None:
        stale_module = object()
        with mock.patch.object(
            agent_state,
            "_probe_required_modules",
            side_effect=[(False, ["cryptography"]), (True, [])],
        ):
            with mock.patch.object(
                agent_state,
                "validate_runtime_environment",
                return_value={"ok": True, "issues": []},
            ):
                with mock.patch.object(
                    agent_state,
                    "_run_runtime_setup",
                    return_value={"returncode": 0, "payload": {"ok": True}},
                ):
                    with mock.patch.dict(sys.modules, {"weex_profile_store": stale_module}, clear=False):
                        agent_state.ensure_private_runtime_ready(
                            command="contract.place-order",
                            auto_setup=True,
                            language=None,
                        )
                        self.assertNotIn("weex_profile_store", sys.modules)


if __name__ == "__main__":
    unittest.main()
