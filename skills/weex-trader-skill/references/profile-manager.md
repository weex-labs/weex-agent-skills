# Profile Manager Guide

Use this reference for the Windows/macOS account manager workflow, the GUI field meanings, and the most common saved-profile operations.

## Contents

- Command context
- When to use the GUI
- Launch the profile manager
- Global vault area
- Profile fields
- Typical GUI tasks
- Before mutating existing profiles
- Notes

## Command Context

Run the shell commands below from the skill root.

If you stay outside the skill root, replace repo-relative paths such as `scripts/weex_profile_manager_en.py` with the full path to that file inside your checkout.

## When To Use The GUI

- Windows or macOS interactive setup
- metadata edits such as description or base URL changes
- secret rotation with local entry fields
- rename, delete, set-default, and clear-default operations
- vault initialize, unlock, or lock actions through the global vault panel

For Linux-first onboarding or terminal-only workflows, use `references/profile-onboarding.md` instead.

## Launch The Profile Manager

```bash
# Windows Chinese users
py -3 scripts/weex_profile_manager_zh.py

# Windows non-Chinese users
py -3 scripts/weex_profile_manager_en.py

# macOS Chinese users
python3 scripts/weex_profile_manager_zh.py

# macOS non-Chinese users
python3 scripts/weex_profile_manager_en.py
```

Compatibility wrapper:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_profile_manager.py
```

Detached launcher for AI/tool-driven shells on macOS or Windows:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_gui_launcher.py profile-manager --language zh --pretty
python3 scripts/weex_gui_launcher.py profile-manager --language en --pretty
```

Vault-only launcher:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_gui_launcher.py vault-manager --language zh --requested-action unlock --pretty
python3 scripts/weex_gui_launcher.py vault-manager --language en --requested-action status --pretty
```

Notes:

- the GUI entrypoints also auto-detach when they are launched from a non-interactive/tool-managed shell, so agent-driven `python3 scripts/weex_profile_manager_zh.py` now survives shell cleanup after it switches to the managed runtime
- prefer the detached launcher instead of `nohup ... &` when an AI/tool host is launching the GUI; on Windows/macOS it verifies and uses the managed runtime before launch
- explicit detached launch aims to avoid extra Terminal/cmd windows: macOS uses a transient `.app` wrapper, while Windows prefers `pythonw.exe` or another hidden background process
- launch records and logs live under `~/.weex-trader-skill/gui-launchers`; the launcher keeps only recent records and trims each `.log` file to 256 KiB, so use the newest log when diagnosing startup problems
- set `WEEX_GUI_FORCE_FOREGROUND=1` only when you explicitly want the GUI to stay attached to the current shell, which can reintroduce a Terminal/cmd window
- vault `--requested-action setup` and `unlock` can immediately open the passphrase flow when the current vault state matches
- vault `--requested-action status` and `lock` only focus the window on that context; they do not mutate vault state by themselves

## Global Vault Area

The profile manager exposes a global vault control area that is separate from the per-profile credential fields.

- initialize the vault before creating the first private profile
- unlock the vault before saving, rotating, or deleting credentials
- lock the vault when the session is finished and you do not want credentials to remain available
- for exact Linux vault mode details, command-line alternatives, and password-file flows, open `references/linux-vault.md`

## Profile Fields

Collect or review the full profile parameter set, not only the minimum credential tuple.

- profile name: required; this is the human-facing `--profile` name used by later commands, while `profile_id` remains the stable internal identity
- `api_key`: required; WEEX API Key
- `api_secret`: required; WEEX Secret Key used to sign private requests
- `api_passphrase`: required; WEEX API Passphrase paired with the key and secret
- description / note: optional metadata for account purpose or permissions
- `contract_base_url`: optional; leave empty to use `https://api-contract.weex.com`; custom values must be full `https://` URLs on `weex.com`, `*.weex.com`, `weex.tech`, or `*.weex.tech`
- `spot_base_url`: optional; leave empty to use `https://api-spot.weex.com`; custom values must be full `https://` URLs on `weex.com`, `*.weex.com`, `weex.tech`, or `*.weex.tech`
- whether to set it as default: optional workflow choice; if enabled, later private commands can omit `--profile`

## Typical GUI Tasks

### Create A Profile

1. Initialize or unlock the global vault area.
2. Enter the profile name.
3. Fill in the API credentials and any optional metadata.
4. Leave base URLs empty unless you intentionally need an override.
5. Choose whether to make the profile the default.
6. Save the profile and confirm the credential status is shown as saved.

### Edit Metadata

1. Select the existing profile.
2. Change description or base URLs.
3. Save without replacing secrets unless you actually mean to rotate them.

### Rotate Secrets

1. Unlock the global vault area.
2. Select the existing profile.
3. Re-enter the full secret set: `api_key`, `api_secret`, and `api_passphrase`.
4. Save and confirm the credential hint updates as expected.

### Rename A Profile

1. Select the profile in the GUI.
2. Change the profile name field.
3. Save the change.

The GUI keeps the existing `profile_id`, so rename is safer here than in ad hoc metadata edits.

### Delete Or Change Default

- delete: select the profile and use the delete action
- set default: select the profile and mark it as default before saving, or use the dedicated default control when available
- clear default: remove the default selection and save, or use the dedicated clear-default control

## Before Mutating Existing Profiles

- inspect the current profile list first
- verify you selected the intended account, especially when names are similar
- confirm the vault is unlocked before any secret-affecting action

## Notes

- private REST commands require a saved profile
- public commands such as `ticker` and `list-endpoints` do not require a valid default profile
- on Windows and macOS, the GUI entrypoints must use an explicitly prepared managed CPython 3.12.13 runtime even when the current interpreter can launch Tk; AI should explain the pinned setup and ask for confirmation before running `scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty`
- on Windows and macOS, the GUI entrypoints can also auto-detach from non-interactive/tool-managed shells so the UI keeps running after the launcher shell exits without needing an extra Terminal/cmd window in the normal detached-launch path; the detached launcher points the child process at the managed runtime
- if the managed GUI bootstrap is disabled or cannot be completed, fall back to `references/profile-onboarding.md` for terminal commands
