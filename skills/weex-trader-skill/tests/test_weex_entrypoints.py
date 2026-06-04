#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import types
import unittest
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class EntryPointTests(unittest.TestCase):
    def write_shell_script(self, path: Path, content: str) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        path.chmod(0o755)

    def run_command(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.setdefault("WEEX_SKIP_DEFAULT_DISCOVERY_SELFTEST", "0")
        return subprocess.run(
            [sys.executable, *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_script_without_cryptography(self, script_name: str, *script_args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.setdefault("WEEX_SKIP_DEFAULT_DISCOVERY_SELFTEST", "0")
        harness = """
import builtins
import os
import runpy
import sys

real_import = builtins.__import__

def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "cryptography" or name.startswith("cryptography."):
        exc = ModuleNotFoundError("No module named 'cryptography'")
        exc.name = "cryptography"
        raise exc
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = fake_import
script_path = sys.argv[1]
sys.path.insert(0, os.path.dirname(script_path))
sys.argv = sys.argv[1:]
runpy.run_path(script_path, run_name="__main__")
"""
        return subprocess.run(
            [sys.executable, "-c", harness, str(SCRIPTS / script_name), *script_args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_authenticated_redirect_is_not_followed(self, client: object) -> None:
        captured_headers: list[dict[str, str]] = []

        class Receiver(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                captured_headers.append(dict(self.headers.items()))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *_args: object) -> None:
                pass

        class Redirector(BaseHTTPRequestHandler):
            target = ""

            def do_GET(self) -> None:
                self.send_response(302)
                self.send_header("Location", self.target)
                self.end_headers()

            def log_message(self, *_args: object) -> None:
                pass

        receiver = HTTPServer(("127.0.0.1", 0), Receiver)
        receiver_thread = Thread(target=receiver.serve_forever, daemon=True)
        receiver_thread.start()
        Redirector.target = f"http://127.0.0.1:{receiver.server_port}/capture"
        redirector = HTTPServer(("127.0.0.1", 0), Redirector)
        redirector_thread = Thread(target=redirector.serve_forever, daemon=True)
        redirector_thread.start()
        try:
            prepared = {
                "url": f"http://127.0.0.1:{redirector.server_port}/start",
                "method": "GET",
                "data": None,
                "headers": {
                    "ACCESS-KEY": "key-secret",
                    "ACCESS-PASSPHRASE": "pass-secret",
                    "ACCESS-SIGN": "sign-secret",
                    "User-Agent": "redirect-regression-test",
                },
            }

            response = client.send(prepared)
        finally:
            redirector.shutdown()
            receiver.shutdown()
            redirector.server_close()
            receiver.server_close()

        self.assertFalse(response["ok"])
        self.assertEqual(response["status"], 302)
        self.assertEqual(captured_headers, [])

    def test_profile_manager_help_works_without_gui_runtime(self) -> None:
        completed = self.run_command(str(SCRIPTS / "weex_profile_manager_en.py"), "--help")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout)
        self.assertIn("Run without arguments", completed.stdout)

    def test_profile_manager_reports_startup_failure_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = os.environ.copy()
            env["WEEX_TRADER_SKILL_HOME"] = tempdir
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "weex_profile_manager_zh.py")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        combined = f"{completed.stdout}\n{completed.stderr}"
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("Traceback", combined)

    def test_vault_manager_reports_startup_failure_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = os.environ.copy()
            env["WEEX_TRADER_SKILL_HOME"] = tempdir
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "weex_vault_manager_app.py"),
                    "--language",
                    "zh",
                    "--requested-action",
                    "status",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        combined = f"{completed.stdout}\n{completed.stderr}"
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("Traceback", combined)

    def test_gui_bootstrap_help_works_without_gui_runtime(self) -> None:
        completed = self.run_command(str(SCRIPTS / "weex_gui_bootstrap.py"), "--help")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout)
        self.assertIn("managed Python runtime", completed.stdout)

    def test_doctor_help_works_without_gui_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = os.environ.copy()
            env["WEEX_TRADER_SKILL_HOME"] = tempdir
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "weex_doctor.py"), "--help"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout)
        self.assertIn("runtime problems", completed.stdout)

    def test_doctor_gui_help_works_without_gui_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = os.environ.copy()
            env["WEEX_TRADER_SKILL_HOME"] = tempdir
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "weex_doctor.py"), "gui", "--help"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Inspect the current Python runtime", completed.stdout)
        self.assertIn("required managed GUI runtime", completed.stdout)
        self.assertIn("--fix", completed.stdout)

    def test_runtime_setup_help_works_without_profile_runtime(self) -> None:
        completed = self.run_command(str(SCRIPTS / "weex_runtime_setup.py"), "--help")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Install WEEX Python dependencies", completed.stdout)
        self.assertIn("--pretty", completed.stdout)

    def test_trade_guard_help_works_without_sibling_analysis_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            skill_root = Path(tempdir) / "weex-trader-skill"
            scripts_dir = skill_root / "scripts"
            scripts_dir.mkdir(parents=True)
            for path in SCRIPTS.glob("*.py"):
                scripts_dir.joinpath(path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(scripts_dir / "weex_trade_guard.py"), "--help"],
                cwd=skill_root,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Preview risk before placing an order", completed.stdout)
        self.assertIn("account-scan", completed.stdout)

    def test_auto_vault_entrypoint_uses_cached_language(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = os.environ.copy()
            env["WEEX_TRADER_SKILL_HOME"] = tempdir
            (Path(tempdir) / "agent-init.json").write_text(
                json.dumps(
                    {
                        "language": {
                            "preferred": "zh",
                            "source": "explicit",
                        }
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "weex_vault.py"), "--help"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("应用保险库", completed.stdout)

    def test_auto_vault_entrypoint_defaults_to_english_when_cache_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = os.environ.copy()
            env["WEEX_TRADER_SKILL_HOME"] = tempdir
            env["WEEX_PROFILE_LANG"] = "zh"

            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "weex_vault.py"), "--help"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Manage the WEEX application vault", completed.stdout)

    def test_linux_profile_wizard_falls_back_to_python_when_python3_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            config_home = temp_path / "config"
            config_home.mkdir()
            (config_home / "agent-init.json").write_text(
                json.dumps({"language": {"preferred": "zh", "source": "explicit"}}),
                encoding="utf-8",
            )

            script_dir = temp_path / "scripts"
            script_dir.mkdir()
            wrapper_path = script_dir / "weex_linux_profile_wizard.sh"
            self.write_shell_script(
                wrapper_path,
                (SCRIPTS / "weex_linux_profile_wizard.sh").read_text(encoding="utf-8"),
            )
            self.write_shell_script(script_dir / "weex_linux_profile_wizard_en.sh", "#!/usr/bin/env bash\nprintf 'en'\n")
            self.write_shell_script(script_dir / "weex_linux_profile_wizard_zh.sh", "#!/usr/bin/env bash\nprintf 'zh'\n")

            fake_bin = temp_path / "fake-bin"
            fake_bin.mkdir()
            self.write_shell_script(fake_bin / "python3", "#!/usr/bin/env bash\nexit 127\n")
            self.write_shell_script(fake_bin / "python", f"#!/usr/bin/env bash\nexec {sys.executable} \"$@\"\n")

            completed = subprocess.run(
                [
                    "bash",
                    "-lc",
                    'export WEEX_TRADER_SKILL_HOME="$PWD/config"; '
                    'export PATH="$PWD/fake-bin:$PATH"; '
                    "./scripts/weex_linux_profile_wizard.sh",
                ],
                cwd=temp_path,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "zh")

    def test_default_unittest_command_discovers_repo_tests(self) -> None:
        if os.getenv("WEEX_SKIP_DEFAULT_DISCOVERY_SELFTEST") == "1":
            self.skipTest("avoid recursive default unittest self-check")

        env = os.environ.copy()
        env["WEEX_SKIP_DEFAULT_DISCOVERY_SELFTEST"] = "1"
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "-q"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        combined = f"{completed.stdout}\n{completed.stderr}"

        self.assertEqual(completed.returncode, 0, combined)
        self.assertNotIn("Ran 0 tests", combined)

    def test_profile_manager_reports_missing_runtime_dependency(self) -> None:
        import weex_profile_manager_app as app

        fake_tk = types.ModuleType("tkinter")
        fake_tk.TclError = RuntimeError
        fake_tk.Tk = object
        fake_font = types.ModuleType("tkinter.font")
        fake_messagebox = types.ModuleType("tkinter.messagebox")
        fake_ttk = types.ModuleType("tkinter.ttk")

        real_import = __import__

        def fake_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
            if name == "weex_profile_store":
                exc = ModuleNotFoundError("No module named 'cryptography'")
                exc.name = "cryptography"
                raise exc
            return real_import(name, globals, locals, fromlist, level)

        previous_store_module = sys.modules.pop("weex_profile_store", None)
        with mock.patch.dict(
            sys.modules,
            {
                "tkinter": fake_tk,
                "tkinter.font": fake_font,
                "tkinter.messagebox": fake_messagebox,
                "tkinter.ttk": fake_ttk,
            },
            clear=False,
        ):
            try:
                with mock.patch("builtins.__import__", side_effect=fake_import):
                    with self.assertRaises(SystemExit) as exc_info:
                        app._load_runtime_dependencies("en")
            finally:
                if previous_store_module is not None:
                    sys.modules["weex_profile_store"] = previous_store_module

        message = str(exc_info.exception)
        self.assertIn("cryptography", message)
        self.assertIn("scripts/weex_profiles.py", message)

    def test_public_spot_cli_still_lists_endpoints_without_profile_runtime(self) -> None:
        completed = self.run_script_without_cryptography("weex_spot_api.py", "list-endpoints", "--pretty")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('"count"', completed.stdout)
        self.assertIn('"endpoints"', completed.stdout)

    def test_public_contract_cli_still_lists_endpoints_without_profile_runtime(self) -> None:
        completed = self.run_script_without_cryptography("weex_contract_api.py", "list-endpoints", "--pretty")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('"count"', completed.stdout)
        self.assertIn('"endpoints"', completed.stdout)

    def test_private_contract_cli_reports_preflight_error_when_cryptography_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            previous_home = os.environ.get("WEEX_TRADER_SKILL_HOME")
            os.environ["WEEX_TRADER_SKILL_HOME"] = tempdir
            try:
                completed = self.run_script_without_cryptography(
                    "weex_contract_api.py",
                    "place-order",
                    "--symbol",
                    "BTCUSDT",
                    "--side",
                    "BUY",
                    "--position-side",
                    "LONG",
                    "--type",
                    "MARKET",
                    "--quantity",
                    "0.001",
                    "--dry-run",
                )
            finally:
                if previous_home is None:
                    os.environ.pop("WEEX_TRADER_SKILL_HOME", None)
                else:
                    os.environ["WEEX_TRADER_SKILL_HOME"] = previous_home

        combined = f"{completed.stdout}\n{completed.stderr}"
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Private WEEX command preflight failed", combined)
        self.assertIn("cryptography", combined)
        self.assertNotIn("Traceback", combined)

    def test_private_contract_cli_reports_invalid_timeout_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = os.environ.copy()
            env["WEEX_TRADER_SKILL_HOME"] = tempdir
            env["WEEX_API_TIMEOUT"] = "abc"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "weex_contract_api.py"),
                    "place-order",
                    "--symbol",
                    "BTCUSDT",
                    "--side",
                    "BUY",
                    "--position-side",
                    "LONG",
                    "--type",
                    "MARKET",
                    "--quantity",
                    "0.001",
                    "--dry-run",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        combined = f"{completed.stdout}\n{completed.stderr}"
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Private WEEX command preflight failed", combined)
        self.assertIn("WEEX_API_TIMEOUT", combined)
        self.assertNotIn("Traceback", combined)

    def test_contract_private_command_runs_runtime_preflight_before_profile_lookup(self) -> None:
        import weex_contract_api as contract

        args = types.SimpleNamespace(command="place-order", profile=None, base_url=None, timeout=None)
        parser = mock.Mock()
        parser.parse_args.return_value = args

        with mock.patch.object(contract, "build_parser", return_value=parser):
            with mock.patch.object(contract, "refresh_agent_records"):
                with mock.patch.object(
                    contract,
                    "ensure_private_runtime_ready",
                    side_effect=contract.RuntimePreflightError("bad runtime"),
                ) as preflight_mock:
                    with mock.patch.object(contract, "resolve_runtime_profile") as resolve_mock:
                        with self.assertRaises(SystemExit) as exc_info:
                            contract.main()

        self.assertEqual(str(exc_info.exception), "bad runtime")
        preflight_mock.assert_called_once_with(command="contract.place-order", auto_setup=True, language=None)
        resolve_mock.assert_not_called()

    def test_spot_private_command_runs_runtime_preflight_before_profile_lookup(self) -> None:
        import weex_spot_api as spot

        args = types.SimpleNamespace(command="place-order", profile=None, base_url=None, timeout=None)
        parser = mock.Mock()
        parser.parse_args.return_value = args

        with mock.patch.object(spot, "build_parser", return_value=parser):
            with mock.patch.object(spot, "refresh_agent_records"):
                with mock.patch.object(
                    spot,
                    "ensure_private_runtime_ready",
                    side_effect=spot.RuntimePreflightError("bad runtime"),
                ) as preflight_mock:
                    with mock.patch.object(spot, "resolve_runtime_profile") as resolve_mock:
                        with self.assertRaises(SystemExit) as exc_info:
                            spot.main()

        self.assertEqual(str(exc_info.exception), "bad runtime")
        preflight_mock.assert_called_once_with(command="spot.place-order", auto_setup=True, language=None)
        resolve_mock.assert_not_called()

    def test_contract_prepare_request_rejects_body_for_get(self) -> None:
        import weex_contract_api as contract

        endpoint = next(ep for ep in contract.ENDPOINTS.values() if ep.method == "GET" and not ep.auth)
        client = contract.WeexContractClient(
            base_url=contract.DEFAULT_BASE_URL,
            timeout=contract.DEFAULT_TIMEOUT,
            locale=contract.DEFAULT_LOCALE,
            api_key=None,
            api_secret=None,
            api_passphrase=None,
        )

        with self.assertRaises(SystemExit) as exc_info:
            client.prepare_request(endpoint, query={}, body={"symbol": "BTCUSDT"})

        self.assertEqual(str(exc_info.exception), contract.GET_BODY_UNSUPPORTED_MESSAGE)

    def test_spot_prepare_request_rejects_body_for_get(self) -> None:
        import weex_spot_api as spot

        endpoint = next(ep for ep in spot.ENDPOINTS.values() if ep.method == "GET" and not ep.requires_auth)
        client = spot.WeexSpotClient(
            base_url=spot.DEFAULT_BASE_URL,
            timeout=spot.DEFAULT_TIMEOUT,
            locale=spot.DEFAULT_LOCALE,
            api_key=None,
            api_secret=None,
            api_passphrase=None,
        )

        with self.assertRaises(SystemExit) as exc_info:
            client.prepare_request(endpoint, query={}, body={"symbol": "BTCUSDT"})

        self.assertEqual(str(exc_info.exception), spot.GET_BODY_UNSUPPORTED_MESSAGE)

    def test_contract_client_rejects_non_weex_base_url(self) -> None:
        import weex_contract_api as contract

        with self.assertRaises(SystemExit) as exc_info:
            contract.WeexContractClient(
                base_url="https://contract.example.test",
                timeout=contract.DEFAULT_TIMEOUT,
                locale=contract.DEFAULT_LOCALE,
                api_key=None,
                api_secret=None,
                api_passphrase=None,
            )

        self.assertIn("must use a weex.com or weex.tech host", str(exc_info.exception))

    def test_spot_client_accepts_weex_tech_base_url(self) -> None:
        import weex_spot_api as spot

        client = spot.WeexSpotClient(
            base_url="https://spot.weex.tech/",
            timeout=spot.DEFAULT_TIMEOUT,
            locale=spot.DEFAULT_LOCALE,
            api_key=None,
            api_secret=None,
            api_passphrase=None,
        )

        self.assertEqual(client.base_url, "https://spot.weex.tech")

    def test_zh_profile_cli_reports_invalid_base_url_in_chinese(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = os.environ.copy()
            env["WEEX_TRADER_SKILL_HOME"] = tempdir
            env["WEEX_GUI_RUNTIME_DISABLE"] = "1"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "weex_profiles_zh.py"),
                    "save",
                    "--profile",
                    "main",
                    "--contract-base-url",
                    "https://contract.example.test",
                    "--api-key",
                    "key-1234",
                    "--api-secret",
                    "secret-1234",
                    "--api-passphrase",
                    "pass-1234",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        combined = f"{completed.stdout}\n{completed.stderr}"
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("合约 Base URL", combined)
        self.assertIn("weex.com", combined)
        self.assertIn("contract.example.test", combined)
        self.assertNotIn("must use", combined)

    def test_profile_cli_validates_base_url_before_prompting_for_secrets(self) -> None:
        import weex_profiles_cli as profiles_cli

        args = types.SimpleNamespace(
            profile="main",
            description=None,
            contract_base_url="https://contract.example.test",
            spot_base_url=None,
            api_key=None,
            api_secret=None,
            api_passphrase=None,
            api_key_env=None,
            api_secret_env=None,
            api_passphrase_env=None,
            secrets_stdin_json=False,
            prompt_secrets=True,
            set_default=False,
            clear_default=False,
            pretty=False,
        )

        with mock.patch.object(profiles_cli, "prompt_secret", side_effect=AssertionError("prompted for secrets")):
            with mock.patch.object(profiles_cli, "upsert_profile") as upsert_mock:
                with self.assertRaises(profiles_cli.ProfileError) as exc_info:
                    profiles_cli.cmd_save(args, "en")

        upsert_mock.assert_not_called()
        self.assertIn("contract.example.test", str(exc_info.exception))

    def test_contract_client_does_not_follow_redirects_with_auth_headers(self) -> None:
        import weex_contract_api as contract

        client = contract.WeexContractClient.__new__(contract.WeexContractClient)
        client.timeout = 5

        self.assert_authenticated_redirect_is_not_followed(client)

    def test_spot_client_does_not_follow_redirects_with_auth_headers(self) -> None:
        import weex_spot_api as spot

        client = spot.WeexSpotClient.__new__(spot.WeexSpotClient)
        client.timeout = 5

        self.assert_authenticated_redirect_is_not_followed(client)

    def test_public_spot_cli_bootstraps_agent_state_without_profile_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = os.environ.copy()
            env.setdefault("WEEX_SKIP_DEFAULT_DISCOVERY_SELFTEST", "0")
            env["WEEX_TRADER_SKILL_HOME"] = tempdir

            harness = """
import builtins
import os
import runpy
import sys

real_import = builtins.__import__

def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "cryptography" or name.startswith("cryptography."):
        exc = ModuleNotFoundError("No module named 'cryptography'")
        exc.name = "cryptography"
        raise exc
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = fake_import
script_path = sys.argv[1]
sys.path.insert(0, os.path.dirname(script_path))
sys.argv = sys.argv[1:]
runpy.run_path(script_path, run_name="__main__")
"""
            completed = subprocess.run(
                [sys.executable, "-c", harness, str(SCRIPTS / "weex_spot_api.py"), "list-endpoints", "--pretty"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((Path(tempdir) / "agent-init.json").exists())
            self.assertTrue((Path(tempdir) / "agent-runtime.json").exists())

    def test_public_spot_runtime_profile_uses_default_profile_when_available(self) -> None:
        import weex_spot_api as spot

        profile = types.SimpleNamespace(name="main", spot_base_url="https://spot.weex.com")
        previous_resolve_profile = spot.resolve_profile
        previous_load_profile_credentials = spot.load_profile_credentials
        previous_profile_error = spot.ProfileError
        try:
            spot.resolve_profile = lambda name=None: profile
            spot.load_profile_credentials = lambda name: None
            spot.ProfileError = RuntimeError

            resolved = spot.resolve_runtime_profile(None, True)
        finally:
            spot.resolve_profile = previous_resolve_profile
            spot.load_profile_credentials = previous_load_profile_credentials
            spot.ProfileError = previous_profile_error

        self.assertIs(resolved, profile)

    def test_public_contract_runtime_profile_uses_default_profile_when_available(self) -> None:
        import weex_contract_api as contract

        profile = types.SimpleNamespace(name="main", contract_base_url="https://contract.weex.tech")
        previous_resolve_profile = contract.resolve_profile
        previous_load_profile_credentials = contract.load_profile_credentials
        previous_profile_error = contract.ProfileError
        try:
            contract.resolve_profile = lambda name=None: profile
            contract.load_profile_credentials = lambda name: None
            contract.ProfileError = RuntimeError

            resolved = contract.resolve_runtime_profile(None, True)
        finally:
            contract.resolve_profile = previous_resolve_profile
            contract.load_profile_credentials = previous_load_profile_credentials
            contract.ProfileError = previous_profile_error

        self.assertIs(resolved, profile)

    def test_contract_cli_uses_env_overrides_for_base_url_and_locale(self) -> None:
        import weex_contract_api as contract

        args = types.SimpleNamespace(command="list-endpoints", profile=None, base_url=None, timeout=None)
        parser = mock.Mock()
        parser.parse_args.return_value = args

        with mock.patch.object(contract, "build_parser", return_value=parser):
            with mock.patch.object(contract, "refresh_agent_records"):
                with mock.patch.object(contract, "resolve_runtime_profile", return_value=None):
                    with mock.patch.object(contract, "WeexContractClient", return_value=object()) as client_mock:
                        with mock.patch.object(contract, "cmd_list_endpoints", return_value=0):
                            with mock.patch.dict(
                                os.environ,
                                {
                                    "WEEX_CONTRACT_API_BASE": "https://contract.weex.tech",
                                    "WEEX_API_BASE": "https://generic.weex.com",
                                    "WEEX_LOCALE": "zh-CN",
                                },
                                clear=False,
                            ):
                                exit_code = contract.main()

        self.assertEqual(exit_code, 0)
        client_mock.assert_called_once_with(
            base_url="https://contract.weex.tech",
            timeout=contract.DEFAULT_TIMEOUT,
            locale="zh-CN",
            api_key=None,
            api_secret=None,
            api_passphrase=None,
            profile_name=None,
        )

    def test_spot_cli_uses_generic_env_base_url_and_locale(self) -> None:
        import weex_spot_api as spot

        args = types.SimpleNamespace(command="list-endpoints", profile=None, base_url=None, timeout=None)
        parser = mock.Mock()
        parser.parse_args.return_value = args

        with mock.patch.object(spot, "build_parser", return_value=parser):
            with mock.patch.object(spot, "refresh_agent_records"):
                with mock.patch.object(spot, "resolve_runtime_profile", return_value=None):
                    with mock.patch.object(spot, "WeexSpotClient", return_value=object()) as client_mock:
                        with mock.patch.object(spot, "cmd_list_endpoints", return_value=0):
                            with mock.patch.dict(
                                os.environ,
                                {
                                    "WEEX_API_BASE": "https://generic.spot.weex.tech",
                                    "WEEX_LOCALE": "zh-CN",
                                },
                                clear=False,
                            ):
                                exit_code = spot.main()

        self.assertEqual(exit_code, 0)
        client_mock.assert_called_once_with(
            base_url="https://generic.spot.weex.tech",
            timeout=spot.DEFAULT_TIMEOUT,
            locale="zh-CN",
            api_key=None,
            api_secret=None,
            api_passphrase=None,
            profile_name=None,
        )

    def test_vault_cli_launches_gui_for_windows_unlock_by_default(self) -> None:
        import weex_vault_cli as vault_cli

        with mock.patch("weex_vault_cli.platform.system", return_value="Windows"):
            with mock.patch("weex_vault_cli.launch_vault_ui", return_value=0) as launch_mock:
                exit_code = vault_cli.main("zh", argv=["unlock"])

        self.assertEqual(exit_code, 0)
        launch_mock.assert_called_once_with("zh", requested_action="unlock")

    def test_vault_ui_path_bootstraps_agent_state_files(self) -> None:
        import weex_vault_cli as vault_cli

        with tempfile.TemporaryDirectory() as tempdir:
            previous_home = os.environ.get("WEEX_TRADER_SKILL_HOME")
            os.environ["WEEX_TRADER_SKILL_HOME"] = tempdir
            try:
                with mock.patch("weex_vault_cli.platform.system", return_value="Windows"):
                    with mock.patch("weex_vault_cli.launch_vault_ui", return_value=0):
                        exit_code = vault_cli.main("zh", argv=["unlock"])
                self.assertEqual(exit_code, 0)
                self.assertTrue((Path(tempdir) / "agent-init.json").exists())
                self.assertTrue((Path(tempdir) / "agent-runtime.json").exists())
            finally:
                if previous_home is None:
                    os.environ.pop("WEEX_TRADER_SKILL_HOME", None)
                else:
                    os.environ["WEEX_TRADER_SKILL_HOME"] = previous_home

    def test_vault_cli_no_args_launches_gui_for_macos(self) -> None:
        import weex_vault_cli as vault_cli

        with mock.patch("weex_vault_cli.platform.system", return_value="Darwin"):
            with mock.patch("weex_vault_cli.launch_vault_ui", return_value=0) as launch_mock:
                exit_code = vault_cli.main("en", argv=[])

        self.assertEqual(exit_code, 0)
        launch_mock.assert_called_once_with("en", requested_action=None)

    def test_vault_cli_status_stays_terminal_on_macos_by_default(self) -> None:
        import weex_vault_cli as vault_cli

        fake_args = types.SimpleNamespace(command="status", pretty=False, cli=False)
        with mock.patch("weex_vault_cli.platform.system", return_value="Darwin"):
            with mock.patch("weex_vault_cli.launch_vault_ui") as launch_mock:
                with mock.patch("weex_vault_cli._load_runtime_dependencies") as load_mock:
                    with mock.patch("weex_vault_cli.build_parser") as parser_mock:
                        parser_mock.return_value.parse_args.return_value = fake_args
                        with mock.patch("weex_vault_cli.cmd_status", return_value=0) as status_mock:
                            exit_code = vault_cli.main("en", argv=["status"])

        self.assertEqual(exit_code, 0)
        launch_mock.assert_not_called()
        load_mock.assert_called_once()
        status_mock.assert_called_once_with(fake_args)

    def test_vault_cli_lock_stays_terminal_on_windows_by_default(self) -> None:
        import weex_vault_cli as vault_cli

        fake_args = types.SimpleNamespace(command="lock", pretty=False, cli=False)
        with mock.patch("weex_vault_cli.platform.system", return_value="Windows"):
            with mock.patch("weex_vault_cli.launch_vault_ui") as launch_mock:
                with mock.patch("weex_vault_cli._load_runtime_dependencies") as load_mock:
                    with mock.patch("weex_vault_cli.build_parser") as parser_mock:
                        parser_mock.return_value.parse_args.return_value = fake_args
                        with mock.patch("weex_vault_cli.cmd_lock", return_value=0) as lock_mock:
                            exit_code = vault_cli.main("en", argv=["lock"])

        self.assertEqual(exit_code, 0)
        launch_mock.assert_not_called()
        load_mock.assert_called_once()
        lock_mock.assert_called_once_with(fake_args)

    def test_vault_cli_cli_flag_keeps_terminal_flow_on_windows(self) -> None:
        import weex_vault_cli as vault_cli

        fake_args = types.SimpleNamespace(command="status", pretty=False, cli=True)
        with mock.patch("weex_vault_cli.platform.system", return_value="Windows"):
            with mock.patch("weex_vault_cli._load_runtime_dependencies") as load_mock:
                with mock.patch("weex_vault_cli.build_parser") as parser_mock:
                    parser_mock.return_value.parse_args.return_value = fake_args
                    with mock.patch("weex_vault_cli.cmd_status", return_value=0) as status_mock:
                        exit_code = vault_cli.main("en", argv=["--cli", "status"])

        self.assertEqual(exit_code, 0)
        load_mock.assert_called_once()
        status_mock.assert_called_once_with(fake_args)

    def test_vault_cli_unlock_with_cli_flags_stays_terminal_on_windows(self) -> None:
        import weex_vault_cli as vault_cli

        fake_args = types.SimpleNamespace(command="unlock", password_env=None, password_file=None, pretty=True, cli=False)
        with mock.patch("weex_vault_cli.platform.system", return_value="Windows"):
            with mock.patch("weex_vault_cli.launch_vault_ui") as launch_mock:
                with mock.patch("weex_vault_cli._load_runtime_dependencies") as load_mock:
                    with mock.patch("weex_vault_cli.build_parser") as parser_mock:
                        parser_mock.return_value.parse_args.return_value = fake_args
                        with mock.patch("weex_vault_cli.cmd_unlock", return_value=0) as unlock_mock:
                            exit_code = vault_cli.main("en", argv=["unlock", "--pretty"])

        self.assertEqual(exit_code, 0)
        launch_mock.assert_not_called()
        load_mock.assert_called_once()
        unlock_mock.assert_called_once_with(fake_args, "en")

    def test_vault_cli_localizations_keep_key_sets_in_sync(self) -> None:
        import weex_vault_cli as vault_cli

        self.assertEqual(set(vault_cli.TEXTS["en"]), set(vault_cli.TEXTS["zh"]))

    def test_vault_cli_zh_help_describes_cross_platform_application_vault(self) -> None:
        completed = self.run_command(str(SCRIPTS / "weex_vault_zh.py"), "--help")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Windows", completed.stdout)
        self.assertIn("macOS", completed.stdout)
        self.assertIn("Linux", completed.stdout)
        self.assertIn("应用保险库", completed.stdout)

    def test_vault_cli_change_password_help_is_localized_in_zh(self) -> None:
        completed = self.run_command(str(SCRIPTS / "weex_vault_zh.py"), "change-password", "--help")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("使用新密码重新加密保险库", completed.stdout)
        self.assertIn("从这个环境变量读取当前保险库密码", completed.stdout)
        self.assertIn("从这个文件读取新保险库密码", completed.stdout)
        self.assertNotIn("Change the vault passphrase", completed.stdout)

    def test_vault_cli_unlock_prompts_once(self) -> None:
        import weex_vault_cli as vault_cli

        prompts: list[str] = []

        def fake_getpass(prompt: str) -> str:
            prompts.append(prompt)
            return "vault-pass"

        args = types.SimpleNamespace(password_env=None, password_file=None, pretty=False)
        with mock.patch.object(vault_cli.getpass, "getpass", side_effect=fake_getpass):
            with mock.patch.object(vault_cli, "unlock_linux_vault", return_value={"ok": True}) as unlock_mock:
                with mock.patch.object(vault_cli, "output_json"):
                    exit_code = vault_cli.cmd_unlock(args, "en")

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            prompts,
            [
                vault_cli.TEXTS["en"]["prompt_passphrase"],
            ],
        )
        unlock_mock.assert_called_once_with("vault-pass")


if __name__ == "__main__":
    unittest.main()
