#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import weex_profile_manager_app as app  # noqa: E402


class FakeVar:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class FakeBoolVar(FakeVar):
    def __init__(self, value: bool = False) -> None:
        super().__init__(value)
        self.callbacks: list[object] = []

    def trace_add(self, _mode: str, callback: object) -> str:
        self.callbacks.append(callback)
        return f"trace-{len(self.callbacks)}"

    def set(self, value: object) -> None:
        super().set(value)
        for callback in list(self.callbacks):
            callback()


class FakeTextValue:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self, *_args: object, **_kwargs: object) -> str:
        return self.value


class FakeEditableText(FakeTextValue):
    def delete(self, *_args: object, **_kwargs: object) -> None:
        self.value = ""

    def insert(self, _index: object, value: str) -> None:
        self.value = value


class FakeListboxSelection:
    def __init__(self, selection: tuple[int, ...]) -> None:
        self.selection = selection

    def curselection(self) -> tuple[int, ...]:
        return self.selection


class ProfileManagerLayoutTests(unittest.TestCase):
    def test_main_bootstraps_agent_state_before_launching_ui(self) -> None:
        fake_root = types.SimpleNamespace(mainloop=mock.Mock())

        with tempfile.TemporaryDirectory() as tempdir:
            previous_home = os.environ.get("WEEX_TRADER_SKILL_HOME")
            os.environ["WEEX_TRADER_SKILL_HOME"] = tempdir
            try:
                with mock.patch.object(app, "_load_runtime_dependencies"):
                    with mock.patch.object(app, "ProfileManagerApp"):
                        with mock.patch.object(app, "maybe_detach_gui_entrypoint"):
                            with mock.patch.object(app, "maybe_reexec_under_managed_gui_runtime"):
                                with mock.patch.object(
                                    app,
                                    "tk",
                                    types.SimpleNamespace(Tk=mock.Mock(return_value=fake_root), TclError=RuntimeError),
                                ):
                                    exit_code = app.main("en", argv=[])
                self.assertEqual(exit_code, 0)
                self.assertTrue((Path(tempdir) / "agent-init.json").exists())
                self.assertTrue((Path(tempdir) / "agent-runtime.json").exists())
            finally:
                if previous_home is None:
                    os.environ.pop("WEEX_TRADER_SKILL_HOME", None)
                else:
                    os.environ["WEEX_TRADER_SKILL_HOME"] = previous_home

    def test_main_checks_managed_gui_runtime_before_loading_tk(self) -> None:
        fake_root = types.SimpleNamespace(mainloop=mock.Mock())

        with mock.patch.object(app, "_load_runtime_dependencies"):
            with mock.patch.object(app, "ProfileManagerApp"):
                with mock.patch.object(app, "maybe_detach_gui_entrypoint"):
                    with mock.patch.object(app, "maybe_reexec_under_managed_gui_runtime") as bootstrap_mock:
                        with mock.patch.object(
                            app,
                            "tk",
                            types.SimpleNamespace(Tk=mock.Mock(return_value=fake_root), TclError=RuntimeError),
                        ):
                            exit_code = app.main("en", argv=[])

        self.assertEqual(exit_code, 0)
        bootstrap_mock.assert_called_once()
        bootstrap_kwargs = bootstrap_mock.call_args.kwargs
        self.assertEqual(bootstrap_kwargs["argv"], ["--language", "en"])
        self.assertTrue(str(bootstrap_kwargs["entrypoint_path"]).endswith("weex_profile_manager_app.py"))

    def test_prompt_vault_passphrase_uses_set_copy_during_initial_setup(self) -> None:
        prompts: list[tuple[str, str]] = []

        def fake_askstring(title: str, prompt: str, **kwargs) -> str:
            prompts.append((title, prompt))
            return "vault-passphrase"

        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.language = "en"
        profile_app.root = object()

        with mock.patch.object(app, "simpledialog", types.SimpleNamespace(askstring=fake_askstring)):
            with mock.patch.object(app, "messagebox", types.SimpleNamespace(showwarning=mock.Mock(), showerror=mock.Mock())):
                result = profile_app._prompt_vault_passphrase(confirm=True, setup_flow=True)

        self.assertEqual(result, "vault-passphrase")
        self.assertEqual(
            prompts,
            [
                ("Set Vault Passphrase", "Set the vault passphrase."),
                ("Confirm Vault Passphrase", "Re-enter the vault passphrase."),
            ],
        )

    def test_manage_vault_prompts_once_during_unlock(self) -> None:
        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.language = "en"
        profile_app.root = object()
        profile_app.current_profile_id = None
        profile_app.local_text = lambda en_text, _zh_text: en_text
        profile_app.refresh_vault_status = mock.Mock()
        profile_app.refresh_profiles = mock.Mock()

        with mock.patch.object(app, "vault_status", return_value={"state": "locked", "mode": "manual_once"}):
            with mock.patch.object(profile_app, "_prompt_vault_passphrase", return_value="vault-passphrase") as prompt_mock:
                with mock.patch.object(app, "unlock_linux_vault") as unlock_mock:
                    with mock.patch.object(app, "get_profile_by_id", return_value=None):
                        profile_app.manage_vault()

        prompt_mock.assert_called_once_with(confirm=False)
        unlock_mock.assert_called_once_with("vault-passphrase")

    def test_refresh_vault_status_shows_reset_button_only_when_locked(self) -> None:
        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.language = "en"
        profile_app.local_text = lambda en_text, zh_text: en_text
        profile_app.backend_var = FakeVar()
        profile_app.vault_state_var = FakeVar()
        profile_app.vault_guidance_var = FakeVar()
        profile_app.profile_actions_enabled = False
        profile_app.profile_action_hint_var = FakeVar()
        profile_app.header_var = FakeVar()
        profile_app.vault_action_button = types.SimpleNamespace(configure=mock.Mock())
        profile_app.vault_reset_button = types.SimpleNamespace(pack=mock.Mock(), pack_forget=mock.Mock())
        profile_app._sync_profile_action_controls = mock.Mock()
        profile_app._sync_account_surface_lock = mock.Mock()

        with mock.patch.object(app, "vault_status", return_value={"state": "locked", "mode": "manual_once"}):
            with mock.patch.object(app, "secure_store_backend_name", return_value="Application Vault (manual_once)"):
                profile_app.refresh_vault_status()

        self.assertTrue(profile_app.account_surface_locked)
        profile_app._sync_account_surface_lock.assert_called_once()
        profile_app.vault_reset_button.pack.assert_called_once()
        profile_app.vault_reset_button.pack_forget.assert_not_called()

    def test_refresh_vault_status_hides_overlay_when_unlocked(self) -> None:
        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.language = "en"
        profile_app.local_text = lambda en_text, zh_text: en_text
        profile_app.backend_var = FakeVar()
        profile_app.vault_state_var = FakeVar()
        profile_app.vault_guidance_var = FakeVar()
        profile_app.profile_actions_enabled = False
        profile_app.profile_action_hint_var = FakeVar()
        profile_app.header_var = FakeVar()
        profile_app.vault_action_button = types.SimpleNamespace(configure=mock.Mock())
        profile_app.vault_reset_button = types.SimpleNamespace(pack=mock.Mock(), pack_forget=mock.Mock())
        profile_app._sync_profile_action_controls = mock.Mock()
        profile_app._sync_account_surface_lock = mock.Mock()

        with mock.patch.object(app, "vault_status", return_value={"state": "unlocked", "mode": "manual_once"}):
            with mock.patch.object(app, "secure_store_backend_name", return_value="Application Vault (manual_once)"):
                profile_app.refresh_vault_status()

        self.assertFalse(profile_app.account_surface_locked)
        profile_app._sync_account_surface_lock.assert_called_once()
        profile_app.vault_reset_button.pack_forget.assert_called_once()

    def test_reset_vault_executes_destructive_reset_flow(self) -> None:
        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.language = "en"
        profile_app.root = object()
        profile_app.current_profile_id = "profile-main"
        profile_app.refresh_vault_status = mock.Mock()
        profile_app.refresh_profiles = mock.Mock()
        profile_app.reset_form = mock.Mock()
        profile_app._refresh_agent_cache = mock.Mock()
        profile_app.local_text = lambda en_text, zh_text: en_text
        profile_app._confirm_vault_reset = mock.Mock(return_value=True)

        fake_messagebox = types.SimpleNamespace(showinfo=mock.Mock(), showerror=mock.Mock())

        with mock.patch.object(app, "messagebox", fake_messagebox):
            with mock.patch.object(app, "reset_linux_vault", return_value={"state": "uninitialized"}) as reset_mock:
                profile_app.reset_vault()

        reset_mock.assert_called_once_with()
        profile_app.reset_form.assert_called_once()
        profile_app.refresh_vault_status.assert_called_once()
        profile_app.refresh_profiles.assert_called_once()
        profile_app._refresh_agent_cache.assert_called_once()

    def test_action_button_supports_keyboard_activation_and_invoke(self) -> None:
        class FakeWidget:
            def __init__(self, master=None, **kwargs):
                self.master = master
                self.kwargs = dict(kwargs)
                self.bindings: dict[str, object] = {}

            def bind(self, sequence: str, callback: object) -> None:
                self.bindings[sequence] = callback

            def configure(self, **kwargs) -> None:
                self.kwargs.update(kwargs)

            config = configure

            def cget(self, key: str) -> object:
                return self.kwargs.get(key)

            def pack(self, *args, **kwargs) -> None:
                return None

            def focus_set(self) -> None:
                self.kwargs["focused"] = True

        fake_tk = types.SimpleNamespace(
            Frame=FakeWidget,
            Label=FakeWidget,
            NORMAL="normal",
            DISABLED="disabled",
            BOTH="both",
        )
        previous_tk = app.tk
        app.tk = fake_tk
        try:
            calls: list[str] = []
            button = app.ActionButton(
                object(),
                text="Save",
                command=lambda: calls.append("called") or "ok",
                kind="primary",
                font=object(),
            )

            self.assertEqual(button.invoke(), "ok")
            self.assertEqual(button.widget.cget("highlightthickness"), 2)
            self.assertEqual(button.widget.cget("highlightbackground"), app.PALETTE["button_primary_bg"])
            self.assertEqual(button.widget.cget("highlightcolor"), app.PALETTE["button_primary_bg"])
            self.assertIn("<FocusIn>", button.widget.bindings)
            self.assertIn("<FocusOut>", button.widget.bindings)
            self.assertIn("<KeyPress-space>", button.widget.bindings)
            self.assertIn("<KeyRelease-space>", button.widget.bindings)
            self.assertIn("<Return>", button.widget.bindings)

            button.widget.bindings["<FocusIn>"](object())
            self.assertEqual(button.widget.cget("highlightbackground"), app.PALETTE["accent"])
            self.assertEqual(button.widget.cget("highlightcolor"), app.PALETTE["accent"])
            button.widget.bindings["<FocusOut>"](object())
            self.assertEqual(button.widget.cget("highlightbackground"), app.PALETTE["button_primary_bg"])
            self.assertEqual(button.widget.cget("highlightcolor"), app.PALETTE["button_primary_bg"])

            self.assertEqual(button.widget.bindings["<Return>"](object()), "break")
            self.assertEqual(button.widget.bindings["<KeyPress-space>"](object()), "break")
            self.assertEqual(button.widget.bindings["<KeyRelease-space>"](object()), "break")
            self.assertEqual(calls, ["called", "called", "called"])

            button.configure(state=fake_tk.DISABLED)
            button.widget.bindings["<FocusIn>"](object())
            self.assertIsNone(button.invoke())
            self.assertEqual(button.widget.cget("highlightbackground"), app.PALETTE["button_disabled_bg"])
            self.assertEqual(button.widget.cget("highlightcolor"), app.PALETTE["button_disabled_bg"])
            self.assertEqual(button.widget.bindings["<Return>"](object()), "break")
            self.assertEqual(button.widget.bindings["<KeyPress-space>"](object()), "break")
            self.assertEqual(button.widget.bindings["<KeyRelease-space>"](object()), "break")
            self.assertEqual(calls, ["called", "called", "called"])
        finally:
            app.tk = previous_tk

    def test_action_checkbox_supports_keyboard_activation_and_external_updates(self) -> None:
        class FakeWidget:
            def __init__(self, master=None, **kwargs):
                self.master = master
                self.kwargs = dict(kwargs)
                self.bindings: dict[str, object] = {}

            def bind(self, sequence: str, callback: object) -> None:
                self.bindings[sequence] = callback

            def configure(self, **kwargs) -> None:
                self.kwargs.update(kwargs)

            config = configure

            def cget(self, key: str) -> object:
                return self.kwargs.get(key)

            def pack(self, *args, **kwargs) -> None:
                return None

            def focus_set(self) -> None:
                self.kwargs["focused"] = True

        fake_tk = types.SimpleNamespace(
            Frame=FakeWidget,
            Label=FakeWidget,
            NORMAL="normal",
            DISABLED="disabled",
            FLAT="flat",
            LEFT="left",
        )
        previous_tk = app.tk
        app.tk = fake_tk
        try:
            variable = FakeBoolVar(False)
            checkbox = app.ActionCheckbox(
                FakeWidget(bg="white"),
                text="Default profile",
                variable=variable,
                font=object(),
            )

            self.assertEqual(checkbox._box.cget("highlightbackground"), app.PALETTE["checkbox_border"])
            self.assertEqual(checkbox._box.cget("text"), "")
            self.assertEqual(checkbox.invoke(), None)
            self.assertTrue(variable.get())
            self.assertEqual(checkbox._box.cget("text"), "✓")
            self.assertEqual(checkbox._box.cget("bg"), app.PALETTE["checkbox_checked_bg"])
            self.assertIn("<Return>", checkbox.widget.bindings)
            self.assertIn("<KeyPress-space>", checkbox.widget.bindings)
            self.assertIn("<KeyRelease-space>", checkbox.widget.bindings)

            variable.set(False)
            self.assertEqual(checkbox._box.cget("text"), "")
            self.assertEqual(checkbox._box.cget("bg"), app.PALETTE["checkbox_bg"])

            self.assertEqual(checkbox.widget.bindings["<Return>"](object()), "break")
            self.assertTrue(variable.get())
            self.assertEqual(checkbox.widget.bindings["<KeyPress-space>"](object()), "break")
            self.assertEqual(checkbox.widget.bindings["<KeyRelease-space>"](object()), "break")
            self.assertFalse(variable.get())

            checkbox.configure(state=fake_tk.DISABLED)
            self.assertEqual(checkbox._box.cget("bg"), app.PALETTE["checkbox_disabled_bg"])
            self.assertEqual(checkbox._label.cget("fg"), app.PALETTE["checkbox_disabled_text"])
            self.assertEqual(checkbox.widget.bindings["<Return>"](object()), "break")
            self.assertFalse(variable.get())
        finally:
            app.tk = previous_tk

    def test_form_inputs_no_longer_bind_page_mousewheel_handler(self) -> None:
        class FakeWidget:
            def __init__(self, master=None, **kwargs):
                self.master = master
                self.kwargs = kwargs
                self.bindings: dict[str, object] = {}

            def bind(self, sequence: str, callback: object) -> None:
                self.bindings[sequence] = callback

            def grid(self, *args, **kwargs) -> None:
                return None

            def pack(self, *args, **kwargs) -> None:
                return None

            def cget(self, key: str) -> object:
                return self.kwargs[key]

        class FakeEntry(FakeWidget):
            pass

        class FakeText(FakeWidget):
            pass

        class FakeLabel(FakeWidget):
            pass

        fake_tk = types.SimpleNamespace(
            Frame=FakeWidget,
            Entry=FakeEntry,
            Text=FakeText,
            Label=FakeLabel,
            FLAT="flat",
            EW="ew",
            LEFT="left",
            W="w",
            NW="nw",
        )
        previous_tk = app.tk
        app.tk = fake_tk
        try:
            profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
            profile_app.fonts = {"body": object(), "label": object(), "small": object()}
            profile_app._layout_metrics = app.compute_layout_metrics(viewport_width=1360, viewport_height=880)
            profile_app.section_wraplength = profile_app._layout_metrics["section_wraplength"]
            entry = profile_app._create_entry(FakeWidget(bg="white"), variable=object())
            self.assertNotIn("<MouseWheel>", entry.bindings)

            profile_app._add_text_row(FakeWidget(bg="white"), 0, "Description", "Help text")
            self.assertNotIn("<MouseWheel>", profile_app.description_text.bindings)
        finally:
            app.tk = previous_tk

    def test_compute_layout_metrics_scales_for_desktop_window(self) -> None:
        layout = app.compute_layout_metrics(
            viewport_width=1480,
            viewport_height=940,
        )

        self.assertGreater(layout["scale"], 1.0)
        self.assertEqual(layout["form_columns"], 2)
        self.assertGreaterEqual(layout["sidebar_width"], 300)
        self.assertIn("status_wraplength", layout)
        self.assertIn("workspace_gap", layout)

    def test_compute_layout_metrics_keeps_desktop_grid_at_minimum_window(self) -> None:
        layout = app.compute_layout_metrics(
            viewport_width=1280,
            viewport_height=820,
        )

        self.assertLess(layout["scale"], 1.0)
        self.assertEqual(layout["form_columns"], 2)
        self.assertLess(layout["page_pad_x"], 24)
        self.assertLess(layout["card_pad_x"], 16)
        self.assertGreaterEqual(layout["workspace_min_row_height"], 180)

    def test_compute_window_geometry_caps_requested_size_to_screen(self) -> None:
        geometry = app.compute_window_geometry(
            screen_width=1512,
            screen_height=982,
            requested_width=1480,
            requested_height=940,
        )

        self.assertEqual(geometry["width"], 1416)
        self.assertEqual(geometry["height"], 886)
        self.assertEqual(geometry["x"], 48)
        self.assertEqual(geometry["y"], 48)

    def test_manage_vault_shows_error_when_status_lookup_fails(self) -> None:
        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.root = object()
        profile_app.local_text = lambda en_text, _zh_text: en_text

        fake_messagebox = types.SimpleNamespace(
            showwarning=mock.Mock(),
            showerror=mock.Mock(),
            showinfo=mock.Mock(),
        )

        with mock.patch.object(app, "messagebox", fake_messagebox):
            with mock.patch.object(app, "vault_status", side_effect=app.ProfileError("vault broke")):
                profile_app.manage_vault()

        fake_messagebox.showerror.assert_called_once_with(
            "Vault Error",
            "vault broke",
            parent=profile_app.root,
        )

    def test_save_profile_refreshes_agent_cache_after_success(self) -> None:
        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.profile_actions_enabled = True
        profile_app.root = object()
        profile_app.language = "en"
        profile_app.current_profile_id = None
        profile_app.name_var = FakeVar("main")
        profile_app.description_text = FakeTextValue("Main account")
        profile_app.contract_base_url_var = FakeVar("")
        profile_app.spot_base_url_var = FakeVar("")
        profile_app.api_key_var = FakeVar("key-1234")
        profile_app.api_secret_var = FakeVar("secret-1234")
        profile_app.api_passphrase_var = FakeVar("pass-1234")
        profile_app.default_var = FakeVar(True)
        profile_app.editor_context_var = FakeVar("")
        profile_app.local_text = lambda en_text, _zh_text: en_text
        profile_app.t = lambda key, **kwargs: key
        profile_app.refresh_profiles = mock.Mock()
        profile_app._set_mode_badge = mock.Mock()
        profile_app._update_profile_credential_status = mock.Mock()

        profile = types.SimpleNamespace(profile_id="profile-main", name="main", api_key_hint="***1234")

        with mock.patch.object(app, "tk", types.SimpleNamespace(END="end")):
            with mock.patch.object(app, "upsert_profile", return_value=profile):
                with mock.patch.object(app, "set_default_profile") as set_default_mock:
                    with mock.patch.object(app, "messagebox", types.SimpleNamespace(showwarning=mock.Mock(), showerror=mock.Mock(), showinfo=mock.Mock())):
                        with mock.patch.object(app, "refresh_agent_records") as refresh_agent_records_mock:
                            profile_app.save_profile()

        set_default_mock.assert_called_once_with("profile-main")
        refresh_agent_records_mock.assert_called_once()

    def test_save_profile_shows_zh_error_for_invalid_base_url_without_saving(self) -> None:
        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.profile_actions_enabled = True
        profile_app.root = object()
        profile_app.language = "zh"
        profile_app.texts = app.TEXTS["zh"]
        profile_app.current_profile_id = None
        profile_app.name_var = FakeVar("main")
        profile_app.description_text = FakeTextValue("主账号")
        profile_app.contract_base_url_var = FakeVar("https://contract.example.test")
        profile_app.spot_base_url_var = FakeVar("")
        profile_app.api_key_var = FakeVar("key-1234")
        profile_app.api_secret_var = FakeVar("secret-1234")
        profile_app.api_passphrase_var = FakeVar("pass-1234")
        profile_app.default_var = FakeVar(True)
        profile_app.t = app.ProfileManagerApp.t.__get__(profile_app, app.ProfileManagerApp)
        profile_app.local_text = app.ProfileManagerApp.local_text.__get__(profile_app, app.ProfileManagerApp)

        fake_messagebox = types.SimpleNamespace(
            showwarning=mock.Mock(),
            showerror=mock.Mock(),
            showinfo=mock.Mock(),
        )

        with mock.patch.object(app, "tk", types.SimpleNamespace(END="end")):
            with mock.patch.object(app, "messagebox", fake_messagebox):
                with mock.patch.object(app, "upsert_profile") as upsert_mock:
                    profile_app.save_profile()

        upsert_mock.assert_not_called()
        fake_messagebox.showerror.assert_called_once()
        title, message = fake_messagebox.showerror.call_args.args
        self.assertEqual(title, "保存失败")
        self.assertIn("合约 Base URL", message)
        self.assertIn("weex.com", message)
        self.assertIn("contract.example.test", message)
        self.assertNotIn("must use", message)
        self.assertEqual(profile_app.name_var.get(), "main")
        self.assertEqual(profile_app.description_text.get(), "主账号")
        self.assertEqual(profile_app.contract_base_url_var.get(), "https://contract.example.test")
        self.assertEqual(profile_app.spot_base_url_var.get(), "")
        self.assertEqual(profile_app.api_key_var.get(), "key-1234")
        self.assertEqual(profile_app.api_secret_var.get(), "secret-1234")
        self.assertEqual(profile_app.api_passphrase_var.get(), "pass-1234")

    def test_save_profile_preserves_form_values_when_store_rejects_save(self) -> None:
        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.profile_actions_enabled = True
        profile_app.root = object()
        profile_app.language = "en"
        profile_app.texts = app.TEXTS["en"]
        profile_app.current_profile_id = None
        profile_app.name_var = FakeVar("main")
        profile_app.description_text = FakeTextValue("Main account")
        profile_app.contract_base_url_var = FakeVar("https://contract.weex.tech")
        profile_app.spot_base_url_var = FakeVar("https://spot.weex.com")
        profile_app.api_key_var = FakeVar("key-1234")
        profile_app.api_secret_var = FakeVar("secret-1234")
        profile_app.api_passphrase_var = FakeVar("pass-1234")
        profile_app.default_var = FakeVar(True)
        profile_app.t = app.ProfileManagerApp.t.__get__(profile_app, app.ProfileManagerApp)
        profile_app.local_text = app.ProfileManagerApp.local_text.__get__(profile_app, app.ProfileManagerApp)
        profile_app.refresh_profiles = mock.Mock()
        profile_app._set_mode_badge = mock.Mock()
        profile_app._update_profile_credential_status = mock.Mock()

        fake_messagebox = types.SimpleNamespace(
            showwarning=mock.Mock(),
            showerror=mock.Mock(),
            showinfo=mock.Mock(),
        )

        with mock.patch.object(app, "tk", types.SimpleNamespace(END="end")):
            with mock.patch.object(app, "messagebox", fake_messagebox):
                with mock.patch.object(app, "upsert_profile", side_effect=app.ProfileError("storage failed")):
                    profile_app.save_profile()

        fake_messagebox.showerror.assert_called_once_with("Save failed", "storage failed", parent=profile_app.root)
        profile_app.refresh_profiles.assert_not_called()
        self.assertEqual(profile_app.name_var.get(), "main")
        self.assertEqual(profile_app.description_text.get(), "Main account")
        self.assertEqual(profile_app.contract_base_url_var.get(), "https://contract.weex.tech")
        self.assertEqual(profile_app.spot_base_url_var.get(), "https://spot.weex.com")
        self.assertEqual(profile_app.api_key_var.get(), "key-1234")
        self.assertEqual(profile_app.api_secret_var.get(), "secret-1234")
        self.assertEqual(profile_app.api_passphrase_var.get(), "pass-1234")

    def test_existing_profile_secret_placeholders_are_not_saved_as_credentials(self) -> None:
        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.profile_actions_enabled = True
        profile_app.root = object()
        profile_app.language = "en"
        profile_app.texts = app.TEXTS["en"]
        profile_app.profile_listbox = FakeListboxSelection((0,))
        profile_app.profile_rows = {0: "profile-main"}
        profile_app.current_profile_id = None
        profile_app.name_var = FakeVar("")
        profile_app.description_text = FakeEditableText("")
        profile_app.contract_base_url_var = FakeVar("")
        profile_app.spot_base_url_var = FakeVar("")
        profile_app.api_key_var = FakeVar("")
        profile_app.api_secret_var = FakeVar("")
        profile_app.api_passphrase_var = FakeVar("")
        profile_app.default_var = FakeVar(False)
        profile_app.editor_context_var = FakeVar("")
        profile_app.credential_status_var = FakeVar("")
        profile_app.t = app.ProfileManagerApp.t.__get__(profile_app, app.ProfileManagerApp)
        profile_app.local_text = app.ProfileManagerApp.local_text.__get__(profile_app, app.ProfileManagerApp)
        profile_app._set_mode_badge = mock.Mock()
        profile_app._set_credential_badge = mock.Mock()
        profile_app.refresh_profiles = mock.Mock()
        profile_app._refresh_agent_cache = mock.Mock()

        profile = types.SimpleNamespace(
            profile_id="profile-main",
            name="main",
            description="Main account",
            contract_base_url="",
            spot_base_url="",
            api_key_hint="***2f4f",
        )

        fake_messagebox = types.SimpleNamespace(
            showwarning=mock.Mock(),
            showerror=mock.Mock(),
            showinfo=mock.Mock(),
        )

        with mock.patch.object(app, "tk", types.SimpleNamespace(END="end")):
            with mock.patch.object(app, "get_profile_by_id", return_value=profile):
                with mock.patch.object(app, "get_default_profile_id", return_value=None):
                    with mock.patch.object(app, "profile_has_credentials_by_id", return_value=True):
                        with mock.patch.object(app, "messagebox", fake_messagebox):
                            profile_app.on_select_profile(object())

                            self.assertEqual(profile_app.api_key_var.get(), "Saved (***2f4f, leave blank to keep)")
                            self.assertEqual(profile_app.api_secret_var.get(), "Saved (leave blank to keep)")
                            self.assertEqual(profile_app.api_passphrase_var.get(), "Saved (leave blank to keep)")

                            with mock.patch.object(app, "upsert_profile", return_value=profile) as upsert_mock:
                                with mock.patch.object(app, "set_default_profile") as set_default_mock:
                                    profile_app.save_profile()

        upsert_kwargs = upsert_mock.call_args.kwargs
        self.assertIsNone(upsert_kwargs["api_key"])
        self.assertIsNone(upsert_kwargs["api_secret"])
        self.assertIsNone(upsert_kwargs["api_passphrase"])
        set_default_mock.assert_not_called()

    def test_delete_profile_refreshes_agent_cache_after_success(self) -> None:
        profile_app = app.ProfileManagerApp.__new__(app.ProfileManagerApp)
        profile_app.profile_actions_enabled = True
        profile_app.root = object()
        profile_app.language = "en"
        profile_app.current_profile_id = "profile-main"
        profile_app.name_var = FakeVar("")
        profile_app.local_text = lambda en_text, _zh_text: en_text
        profile_app.t = lambda key, **kwargs: key
        profile_app.refresh_profiles = mock.Mock()
        profile_app.reset_form = mock.Mock()

        profile = types.SimpleNamespace(profile_id="profile-main", name="main")

        with mock.patch.object(app, "get_profile_by_id", return_value=profile):
            with mock.patch.object(app, "delete_profile_by_id", return_value=True):
                with mock.patch.object(app, "messagebox", types.SimpleNamespace(showwarning=mock.Mock(), showerror=mock.Mock(), showinfo=mock.Mock(), askyesno=mock.Mock(return_value=True))):
                    with mock.patch.object(app, "refresh_agent_records") as refresh_agent_records_mock:
                        profile_app.delete_current_profile()

        refresh_agent_records_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
