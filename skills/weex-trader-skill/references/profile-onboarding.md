# Profile Onboarding

Use this reference for exact saved-profile setup commands, OS-specific onboarding paths, and profile-management command patterns.

For the Windows/macOS GUI workflow itself, open `references/profile-manager.md`.

## Contents

- Choose a setup path
- Command context
- Profile parameters
- Windows and macOS
- Linux desktop or interactive shell
- Headless or server automation
- Profile management routes
- Notes

## Choose a Setup Path

- Windows or macOS: prefer the visual profile manager.
- Linux desktop or interactive shell: prefer the Linux wizard.
- Linux headless/server automation: use the encrypted vault flow first, then add profiles with the terminal manager.
- Terminal-only fallback: use the localized `weex_profiles_zh.py` / `weex_profiles_en.py` scripts.

## Command Context

Run the shell commands below from the skill root.

If you stay outside the skill root, replace repo-relative paths such as `scripts/weex_profiles_en.py` with the full path to that file inside your checkout.

## Profile Parameters

When asking a user for account setup inputs, introduce the full profile parameter set instead of only the minimum credential combination.

Complete profile parameter list

- profile name: required. This is how later commands refer to the saved account through `--profile`, even though `profile_id` remains the stable internal identity.
- `api_key`: required. WEEX API Key.
- `api_secret`: required. WEEX Secret Key used to sign private requests.
- `api_passphrase`: required. WEEX API Passphrase.
- description / note: optional metadata for the account purpose, such as main, test, read-only, or bot account.
- `contract_base_url`: optional. Leaving it empty uses the official contract REST host `https://api-contract.weex.com`. Custom values must be full `https://` URLs on `weex.com`, `*.weex.com`, `weex.tech`, or `*.weex.tech`.
- `spot_base_url`: optional. Leaving it empty uses the official spot REST host `https://api-spot.weex.com`. Custom values must be full `https://` URLs on `weex.com`, `*.weex.com`, `weex.tech`, or `*.weex.tech`.
- whether to set it as default: optional workflow choice. If enabled, future private commands can omit `--profile` when they should use this account automatically.

Do not frame this as only the minimum fields needed to make private endpoints work. Explain what each field means, whether it is required, what happens if it is omitted, and when metadata or host overrides are intentionally useful.

For terminal flows, also explain the credential input transports:

- `--prompt-secrets`: interactive entry
- `--api-key-env` / `--api-secret-env` / `--api-passphrase-env`: read credentials from environment variables
- `--secrets-stdin-json`: pipe credentials in JSON for automation

## Windows And macOS

Default onboarding path:

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

Notes:

- Use the GUI first for interactive setup, metadata edits, and rename flows.
- If the user is on Windows or macOS and asks the agent to add an account, directly launch the matching GUI instead of only naming the command.
- The GUI now fronts the shared application vault on both Windows and macOS through a global vault control area separate from per-profile credential fields.
- Use the vault action in that global vault control area to initialize, unlock, or lock the vault around private profile work.
- Windows/macOS vault setup or unlock: AI should use the UI and launch the vault UI so the user completes unlock graphically instead of through terminal prompts.
- On Windows and macOS, GUI profile and vault entrypoints must use the pinned managed CPython 3.12.13 runtime even when the current Python runtime can initialize Tk. AI should explain the pinned setup and ask for confirmation before running `python3 scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty`; GUI entrypoints can then relaunch themselves there.

Terminal fallback:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_profiles_zh.py save --profile main --description "Main account" --prompt-secrets --set-default

python3 scripts/weex_profiles_en.py save --profile main --description "Main account" --prompt-secrets --set-default
```

## Linux Desktop Or Interactive Shell

Preferred onboarding path:

```bash
bash scripts/weex_linux_profile_wizard_zh.sh

# For non-Chinese users
bash scripts/weex_linux_profile_wizard_en.sh
```

Terminal fallback:

```bash
python3 scripts/weex_profiles_zh.py save --profile main --description "Main account" --prompt-secrets --set-default

python3 scripts/weex_profiles_en.py save --profile main --description "Main account" --prompt-secrets --set-default
```

For headless/server Linux vault setup, open `references/linux-vault.md`.

## Headless Or Server Automation

After the Linux vault is configured and available, add profiles without placing secrets on argv:

```bash
export PROFILE_API_KEY="your-api-key"
export PROFILE_API_SECRET="your-secret-key"
export PROFILE_API_PASSPHRASE="your-passphrase"

python3 scripts/weex_profiles_zh.py save --profile main --description "Main account" --api-key-env PROFILE_API_KEY --api-secret-env PROFILE_API_SECRET --api-passphrase-env PROFILE_API_PASSPHRASE --set-default

python3 scripts/weex_profiles_en.py save --profile main --description "Main account" --api-key-env PROFILE_API_KEY --api-secret-env PROFILE_API_SECRET --api-passphrase-env PROFILE_API_PASSPHRASE --set-default
```

Stdin JSON is the other preferred automation path:

```bash
printf '%s' '{"api_key":"your-api-key","api_secret":"your-secret-key","api_passphrase":"your-passphrase"}' | python3 scripts/weex_profiles_en.py save --profile main --secrets-stdin-json --set-default
```

Important:

- `PROFILE_API_KEY`, `PROFILE_API_SECRET`, and `PROFILE_API_PASSPHRASE` here are example variable names only.
- Use any variable names you want, as long as `--api-key-env`, `--api-secret-env`, and `--api-passphrase-env` point to the same names.
- Private trading commands do not read those env vars directly at runtime.
- Save credentials into a profile first, then run private commands with the default or explicit `--profile`.

## Profile Management Routes

Use `profile_id` when the target may be ambiguous. Treat it as the stable account identity.

- `list/show`: use `scripts/weex_profiles_zh.py list --pretty` / `show --profile <name-or-id> --pretty`, or the English variants.
- `edit description` or `edit base URLs`: on Windows/macOS prefer the GUI for interactive editing; otherwise use `save --profile <existing-name> ...`.
- `rotate secrets`: use `save --profile <existing-name> --prompt-secrets` for interactive terminals, or `--secrets-stdin-json` / `--api-key-env` / `--api-secret-env` / `--api-passphrase-env` for automation.
- `rename account`: prefer the GUI on Windows/macOS because it preserves the existing `profile_id`.
- `delete account`: use `delete --profile <name-or-id>`.
- `set default`: use `set-default --profile <name-or-id>`.
- `clear default`: use `clear-default`.

Before edit/delete/default changes, inspect the current accounts first with `list --pretty` unless the user explicitly asked to open the GUI first.

## Notes

- Private REST commands require a saved profile.
- If a command fails with a missing dependency such as `ModuleNotFoundError: cryptography`, install `requirements.lock` with `--require-hashes` using the same interpreter before retrying.
- Public commands such as `ticker` and `list-endpoints` do not require a valid default profile.
