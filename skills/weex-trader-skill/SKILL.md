---
name: weex-trader-skill
description: Use when the user wants WEEX REST automation for contract or spot trading, market or account queries, or secure saved-profile setup and management.
compatibility: Requires Python with requirements.lock installed, network access for WEEX REST calls, and Tk through an explicitly prepared managed GUI runtime for Windows/macOS GUI profile and vault flows.
---

# WEEX Trader Skill

Read `manifest.json` for routing rules. Open `file-index.json` only for file-level guidance.
For every turn that uses this skill, before routing or UI launch, AI must run `scripts/weex_agent_state.py --command skill.preflight --language <zh|en> --pretty` so `agent-init.json` and `agent-runtime.json` stay fresh.
Before any private profile, vault, or trading action, inspect the preflight output and stop if `runtime.host.requirements_ready` is `false`, `runtime.host.missing_modules` is non-empty, or `runtime.env_validation.ok` is `false`.
On Windows and macOS, GUI profile and vault flows must use the managed GUI runtime. AI must not launch GUI entrypoints with the system, miniforge, pyenv, Homebrew, or OS Python even if that interpreter passes Tk or dependency probes. System interpreters may run preflight and the managed-runtime bootstrap only; they are not valid GUI runtimes. Preflight reports whether the managed GUI runtime is ready but must not download or install it implicitly. If `init.host.gui_runtime.action` is `explicit_setup_required`, explain the pinned uv/Python/dependency setup and checksum/hash verification, ask whether AI should install it, and only after clear user approval run `init.host.gui_runtime.setup_command` / `scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty`. Use `scripts/weex_gui_launcher.py` for detached launch after runtime setup is ready.

## Core Entry Points

- `scripts/weex_contract_api.py`: contract/futures REST
- `scripts/weex_spot_api.py`: spot REST
- `scripts/weex_trade_data_aggregator.py`: normalize live/history into replay, profile, order-risk, and account-risk payloads
- `scripts/weex_trade_guard.py`: preview order risk, scan account risk, persist pending intents, and require explicit confirmation before live orders
- `scripts/weex_trade_risk_review.py`: local risk review helpers for standalone trade-guard preview/account-scan flows
- `scripts/weex_order_intent_state.py`: store and validate pending order intents
- `scripts/weex_gui_launcher.py`: detached launcher for GUI profile/vault entrypoints on macOS and Windows; vault launches accept `--requested-action setup|unlock|status|lock`
- `scripts/weex_profile_manager_zh.py` / `scripts/weex_profile_manager_en.py`: Windows/macOS visual profile manager with a global vault control area
- `scripts/weex_profiles_zh.py` / `scripts/weex_profiles_en.py`: terminal profile manager
- `scripts/weex_linux_profile_wizard_zh.sh` / `scripts/weex_linux_profile_wizard_en.sh`: guided Linux onboarding
- `scripts/weex_vault_zh.py` / `scripts/weex_vault_en.py`: cross-platform application vault setup, status, unlock, lock, and mode

Compatibility wrappers:

- `scripts/weex_profile_manager.py`
- `scripts/weex_profiles.py`
- `scripts/weex_linux_profile_wizard.sh`
- `scripts/weex_vault.py`

These auto-detect language from `agent-init.json`.

## Routing

- Contract/futures tasks: use `scripts/weex_contract_api.py`
- Spot tasks: use `scripts/weex_spot_api.py`
- Replay, profile, or order-risk inputs for the analysis skill: collect live data with `scripts/weex_trade_data_aggregator.py`, then pass the normalized JSON into `weex-analysis-skill`
- Order preview, account-risk scan, and confirmation flows: use `scripts/weex_trade_guard.py`
- Windows/macOS setup or editing: prefer the visual profile manager
- Linux interactive setup: prefer the Linux wizard
- Open `README.md` for the broad usage/install summary
- Open `references/profile-manager.md`, `references/profile-onboarding.md`, `references/linux-vault.md`, `references/auth-and-signing.md`, `references/script-operations.md`, `references/trade-data-schema.md`, and `references/troubleshooting.md` as needed

## Runtime Prerequisites

- Profile, vault, other private-account flows, and API-definition regeneration require the hashed dependencies in `requirements.lock`
- Windows uses `py -3`; macOS/Linux uses `python3`
- For one-command local runtime setup, run `scripts/weex_runtime_setup.py --pretty` with the OS-appropriate launcher before private CLI usage
- If `cryptography` or another dependency is missing, install `requirements.lock` with `--require-hashes` using the same interpreter and retry
- Private contract and spot CLIs now auto-attempt `scripts/weex_runtime_setup.py` with the current interpreter when required Python dependencies are missing
- `skill.preflight` also validates `WEEX_API_TIMEOUT` plus any `WEEX_*_API_BASE` overrides; private contract/spot commands now fail fast until those issues are fixed
- Windows/macOS GUI flows ignore system `tkinter` availability and require the managed GUI runtime; if the user declines managed-runtime setup, use the terminal profile manager instead of launching a GUI
- If `agent-init.json` is missing and AI is about to use an auto-language wrapper, refresh `skill.preflight` first instead of guessing

## Profile Policy

- Before private account/trading setup or any task that requires a saved account, check whether any profile already exists
- When asking the user for account setup inputs, introduce the full profile parameter set rather than only the credential tuple
- Complete profile parameter list:
  - profile name: required; this is how later commands refer to the saved account through `--profile`, while `profile_id` stays the stable internal identity
  - `api_key`: required; WEEX API Key
  - `api_secret`: required; WEEX Secret Key for signing private requests
  - `api_passphrase`: required; WEEX API Passphrase paired with the key and secret
  - description / note: optional metadata for account purpose or permissions
  - `contract_base_url`: optional; leave empty for the official contract REST host `https://api-contract.weex.com`; custom values must be full `https://` URLs on `weex.com`, `*.weex.com`, `weex.tech`, or `*.weex.tech`
  - `spot_base_url`: optional; leave empty for the official spot REST host `https://api-spot.weex.com`; custom values must be full `https://` URLs on `weex.com`, `*.weex.com`, `weex.tech`, or `*.weex.tech`
- Do not frame this as only the minimum fields needed to make private endpoints work; explain meaning, requiredness, and blank-value behavior for every field
- For terminal entry, also explain `--prompt-secrets`, `--api-key-env` / `--api-secret-env` / `--api-passphrase-env`, and `--secrets-stdin-json`
- Before edit/delete/default changes, inspect current accounts with `list --pretty`, unless the user explicitly asked to open the GUI first
- Use `show --profile <name-or-id>` when you need to inspect one account before mutating it
- Determine OS first, then language, then choose the matching script variant
- Private REST commands require a saved profile; profile-based storage is the only supported credential path

## OS Guidance

- Windows/macOS: prefer the visual profile manager first
- The profile manager uses the shared application vault on all platforms
- On Windows/macOS, the GUI exposes that shared application vault through a global vault control area separate from per-profile credential fields
- When AI launches a GUI from a non-interactive or tool-managed shell on Windows/macOS, use `scripts/weex_gui_launcher.py` after preflight shows the managed GUI runtime is ready; this path launches the GUI with the managed runtime and avoids an extra Terminal/cmd window
- Windows/macOS vault setup or unlock: AI should launch the vault UI, not terminal prompts
- Linux interactive use: prefer the Linux wizard or terminal profile manager
- Linux headless/server use: prefer the encrypted vault flow first

## Linux Vault Rules

Before running any Linux vault setup command:

- Use `manual_once`
- Explicitly explain the security trade-offs and your recommendation
- Introduce the full vault setup parameter set instead of only the smallest combination needed to initialize the vault
- Complete vault setup parameter list:
  - vault mode: `manual_once`
  - vault password / passphrase: required; user-chosen secret that encrypts the vault
  - `--password-env`: optional secret transport when the secret is already in an environment variable
  - `--password-file`: optional secret transport for one-shot non-interactive flows
  - unlock immediately after setup: operational choice for `manual_once`; setup keeps the vault unlocked for the current session unless `--no-unlock` is used
  - `--force`: destructive reset path; use only when the user explicitly wants to overwrite existing vault config
- Do not introduce vault setup as only the minimum combination needed to run setup; explain what each parameter controls and when it matters

Mode guidance:

- `manual_once`: safer default for interactive/manual usage; re-unlock is required after reboot or session reset
- Recommend `manual_once` for human-driven trading or profile management
- The vault password must be explicitly chosen and provided by the user for this specific setup or rotation flow
- The user must clearly designate which value should be used as the vault password
- Before any `setup` or `change-password` action that sets a new vault password, the user must confirm that same password a second time
- `unlock` only needs one passphrase entry because it verifies an existing vault password instead of setting a new one
- AI must not silently decide, infer, generate, or substitute the vault password
- Never generate a vault passphrase on the user's behalf
- After the user provides the secret and explicitly asks the agent to continue, AI may autonomously execute vault commands such as `setup`, `unlock`, and `change-password`
- The secret may come from the current conversation or another user-authorized source; do not refuse solely because the user chose to provide it to the agent directly
- Unless the user explicitly asks for `lock`, do not autonomously execute `weex_vault ... lock`
- For one-shot non-interactive execution, prefer `--password-file` over interactive PTY prompts when the caller can safely create and delete a temporary secret file
- Do not put vault passwords directly on argv
- The vault CLI supports later maintenance flows such as `change-password`

For exact setup, lock/unlock, and password-change commands, open `references/linux-vault.md`.

## Safety Policy

- Never send mutating requests without `--confirm-live`
- Every natural-language order preview flow must return structured risk output before the order can be confirmed
- For natural-language confirmations, show only the localized `user_confirmation.reply_text` to the user and keep `intent_id` plus `risk_signature` internal to the execution step
- Pending order intents expire after a short TTL and must be regenerated when they are stale
- Confirmation must bind to the latest preview via `intent_id` and `risk_signature`; do not reuse old confirmation tokens
- Default flow is direct live execution; there is no mandatory dry-run phase
- If the instruction is ambiguous or missing fields, ask only for the missing fields
- For server automation, avoid `--api-key`, `--api-secret`, and `--api-passphrase` on argv; prefer `--secrets-stdin-json` or `--api-key-env` / `--api-secret-env` / `--api-passphrase-env`
