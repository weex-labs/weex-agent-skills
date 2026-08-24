---
description: Use when the user wants WEEX REST automation for contract or spot trading, market or account queries, or secure saved-profile setup and management.
name: weex-trader-skill
---
# WEEX Trader Skill

Compatibility requires Python with `requirements.lock` installed, network access for WEEX REST calls, and Tk through an explicitly prepared managed GUI runtime for Windows/macOS GUI profile and vault flows.

Read `manifest.json` for routing rules. Open `file-index.json` only for file-level guidance.
For every turn that uses this skill, before routing or UI launch, AI must refresh preflight state. For Partner-only turns with a selected saved profile, run `scripts/weex_partner_api.py preflight --profile <saved-profile> --language <zh|en> --pretty`; it refreshes `agent-init.json` and `agent-runtime.json` while projecting only Partner-safe fields to the tool transcript. For other turns, run `scripts/weex_agent_state.py --command skill.preflight --language <zh|en> --pretty`.
Before any private profile, vault, or trading action, inspect the preflight output and stop if `runtime.host.requirements_ready` is `false`, `runtime.host.missing_modules` is non-empty, or `runtime.env_validation.ok` is `false`.
On Windows and macOS, GUI profile and vault flows must use the managed GUI runtime. AI must not launch GUI entrypoints with the system, miniforge, pyenv, Homebrew, or OS Python even if that interpreter passes Tk or dependency probes. System interpreters may run preflight and the managed-runtime bootstrap only; they are not valid GUI runtimes. Preflight reports whether the managed GUI runtime is ready but must not download or install it implicitly. If `init.host.gui_runtime.action` is `explicit_setup_required`, explain the pinned uv/Python/dependency setup and checksum/hash verification, ask whether AI should install it, and only after clear user approval run `init.host.gui_runtime.setup_command` / `scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty`. Use `scripts/weex_gui_launcher.py` for detached launch after runtime setup is ready.

## Core Entry Points

- `scripts/weex_contract_api.py`: contract/futures REST
- `scripts/weex_spot_api.py`: spot REST
- `scripts/weex_partner_api.py`: strict read-only Partner REST executor for the seven allowlisted Partner endpoints
- `scripts/weex_trade_data_aggregator.py`: normalize live/history into replay, profile, order-risk, and account-risk payloads
- `scripts/weex_trade_guard.py`: preview order risk, preview TP/SL conditional order risk, scan account risk, persist pending intents, and require explicit confirmation before live orders
- `scripts/weex_auto_trade.py`: saved-profile-only JSON facade for strategy registration, authorization requests/grants/revocation, order reconciliation, and event inspection
- `scripts/weex_auto_trade_state.py`: owner-only six-table SQLite authorization, quota, usage, order, event, and migration state kernel
- `scripts/weex_auto_trade_amount.py`: deterministic conservative official-fact valuation for Spot and Futures authorization quota checks
- `scripts/weex_auto_trade_notify.py`: one-shot local notification adapter for claimed authorization events; notification failure never changes trade state
- `scripts/weex_trade_risk_review.py`: local risk review helpers for standalone trade-guard preview/account-scan flows
- `scripts/weex_order_intent_state.py`: store and validate pending order intents
- `scripts/weex_gui_launcher.py`: detached launcher for GUI profile/vault entrypoints on macOS and Windows; vault launches accept `--requested-action setup|unlock|status|lock`
- `scripts/weex_profile_manager_zh.py` / `scripts/weex_profile_manager_en.py`: Windows/macOS visual profile manager with a global vault control area
- `scripts/weex_profiles_zh.py` / `scripts/weex_profiles_en.py`: terminal profile manager
- `scripts/weex_linux_profile_wizard_zh.sh` / `scripts/weex_linux_profile_wizard_en.sh`: guided Linux onboarding
- `scripts/weex_vault_zh.py` / `scripts/weex_vault_en.py`: cross-platform application vault setup, status, unlock, lock, and mode
- `scripts/update_openclaw_skills.sh`: maintain the official OpenClaw Git checkout at an approved pinned commit, expose all four WEEX skills through `~/.openclaw/skills` symlinks, and run OpenClaw eligibility checks with rollback on failure

Compatibility wrappers:

- `scripts/weex_profile_manager.py`
- `scripts/weex_profiles.py`
- `scripts/weex_linux_profile_wizard.sh`
- `scripts/weex_vault.py`

These auto-detect language from `agent-init.json`.

## Routing

- Contract/futures tasks: use `scripts/weex_contract_api.py`; simulated futures trading uses explicit `--trading-mode demo` and the 4 official `sim.*` endpoints documented in `references/contract-api-definitions.json`
- Spot tasks: use `scripts/weex_spot_api.py`
- Partner queries: accept only structured requests from `weex-partner-skill` and execute them with `scripts/weex_partner_api.py`; profile resolution, Vault credentials, signing, exact-host enforcement, and HTTPS remain owned by this skill
- Replay, profile, or order-risk inputs for the analysis skill: collect live data with `scripts/weex_trade_data_aggregator.py`, then pass the normalized JSON into `weex-analysis-skill`
- Order preview, TP/SL preview, account-risk scan, and confirmation flows: use `scripts/weex_trade_guard.py`
- Automatic strategy authorization and authorized-order guard flows: use `scripts/weex_auto_trade.py` for the stable JSON facade, including `submit-auto`, recovery, events, snapshots, and reconciliation; strategies call the CLI as a subprocess and never import `scripts/weex_auto_trade_state.py` or inject production collaborators
- Windows/macOS setup or editing: prefer the visual profile manager
- Linux interactive setup: prefer the Linux wizard
- OpenClaw installation or update: use `scripts/update_openclaw_skills.sh`; do not use the obsolete native installer flags
- Open `README.md` for the broad usage/install summary
- Open `references/profile-manager.md`, `references/profile-onboarding.md`, `references/linux-vault.md`, `references/auth-and-signing.md`, `references/script-operations.md`, `references/trade-data-schema.md`, `references/contract-api-definitions.md`, and `references/troubleshooting.md` as needed

## Runtime Prerequisites

- Profile, vault, other private-account flows, and API-definition regeneration require the hashed dependencies in `requirements.lock`
- Windows uses `py -3`; macOS/Linux uses `python3`
- For one-command local runtime setup, run `scripts/weex_runtime_setup.py --pretty` with the OS-appropriate launcher before private CLI usage
- If `cryptography` or another dependency is missing, install `requirements.lock` with `--require-hashes` using the same interpreter and retry
- Private contract and spot CLIs now auto-attempt `scripts/weex_runtime_setup.py` with the current interpreter when required Python dependencies are missing
- `skill.preflight` also validates `WEEX_API_TIMEOUT` plus any `WEEX_*_API_BASE` overrides; private contract/spot commands now fail fast until those issues are fixed
- Partner REST requests default to a 30-second timeout because Partner queries can legitimately exceed the 15-second contract/spot default; a valid positive `WEEX_API_TIMEOUT` still overrides it. Timeout failures remain fail-closed and are never retried automatically.
- Windows/macOS GUI flows ignore system `tkinter` availability and require the managed GUI runtime; if the user declines managed-runtime setup, use the terminal profile manager instead of launching a GUI
- If `agent-init.json` is missing and AI is about to use an auto-language wrapper, refresh `skill.preflight` first instead of guessing

## Profile Policy

- Before private account/trading setup or any task that requires a saved account, check whether any profile already exists
- Resolve the saved profile from an explicit current-turn choice, an unambiguous choice already made in the current conversation, or the configured default profile. If these sources cannot resolve one unique saved profile and multiple usable profiles exist, inspect them with the localized profile `list --pretty` command and ask the user to choose. Do not guess from list order, notes, IDs, or name similarity.
- Present ambiguous profile choices as a numbered list containing profile display names only; do not expose profile IDs, credential hints, base URLs, or raw profile records. Ask this as a standalone question so the user can reply with either the number or the exact profile name. Do not combine it with trading-mode or other missing-field questions.
- Use this localized response shape:

  ```text
  Choose an account. Reply with either the number or the exact profile name:
  1. main
  2. quant-bot
  3. backup
  ```

- Accept a bare number only against the most recent numbered list in the current conversation. Reject an out-of-range or stale number and show the current choices again. After a valid choice, reuse that saved profile for later turns in the same conversation unless the user changes it or the reference becomes ambiguous.
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

## Automated Strategy Authorization

- This V1 path is for the official Trader distribution, saved profiles, and real trading only. It does not authorize demo trading and does not auto-detect external scripts.
- Each strategy must register a stable `strategy_id`, then call the authorization facade at startup before its first order. A copied strategy registers a new ID; a renamed or restarted strategy reuses its existing ID. Each strategy has an independent authorization.
- Require every new scope to include `trade_types`, symbols/all-symbols, maximum conservative U amount per leg, maximum cumulative U amount during the validity period, and `valid_hours`. `trade_types` may contain `SPOT`, `FUTURES`, or both. When a natural-language request omits validity, ask the user instead of choosing a default. The JSON CLI requires `valid_hours`; it must be greater than zero and cannot exceed 720 hours (30 days).
- A natural-language request that does not state a cumulative quota must be paused for a separate user choice. Never derive `max_total_amount` from frequency, validity, target amount, max_single_amount, balance, or expected order count. The exact user-provided value must appear in the final confirmation and match the authorization request.
- Show the complete saved-profile name, masked strategy ID, Spot/Futures modules, symbols, maximum conservative per-leg amount, cumulative quota, projected validity, per-order-confirmation effect, revoke action, and local trust boundary before grant. Grant requires the exact request and scope signature plus `--confirm-live`.
- A real-trading automation may become `ACTIVE` only in the same atomic update that includes an `UNTIL` no later than the authorization expiry; never activate an unbounded recurrence, even temporarily. If the runtime cannot update status and expiry atomically, keep the automation `PAUSED`.
- Authorization scope follows the official Spot/Futures modules, not REST paths. Only the explicit official operation catalog may enter the automatic path; new or unknown endpoints do not inherit authorization.
- Quota is calculated by deterministic code from fresh official WEEX facts. The per-leg maximum constrains that conservative estimate, not the target or actual fill amount. When the Futures quantity unit cannot be proven uniquely, use the conservative notional `quantity * price * max(1, contractVal)` before leverage and fee bounds; this is an upper-bound estimate, not exact exchange margin. AI text never changes balances or authorization state. Missing, stale, degraded, incomplete-depth, unconvertible, or unproven reduce-only facts require the existing manual preview-and-confirm flow.
- Before Spot quota reservation, validate quantity with Decimal against official `stepSize`, `minTradeAmount`, and `maxTradeAmount`. A Spot BUY requires the matching quote asset's available U value to cover the conservative estimate; a Spot SELL requires the matching base asset's available quantity to cover the order quantity. Missing or mismatched product/asset facts fail closed.
- Batch submissions reserve every leg atomically before any WEEX write. Each leg keeps its strategy, authorization, usage, submission group, opaque client order ID, and WEEX order ID mapping. Accepted legs consume estimated quota permanently; explicit rejections release it and preserve sanitized, bounded `error_code`/`error_message` in the leg result and `USAGE_RELEASED` event; unknown results or mappings remain `REVIEW_REQUIRED`. Error text never changes the state-classification rules. Never split, retry, or positionally guess a batch result.
- Full-position TP/SL (`quantity` is `0` or omitted) always returns to manual confirmation because the automatic path has no deterministic quantity. Partial reduce-only orders require official proof and use a conservative fee upper bound.
- Normal risk advisories are recorded but do not block an in-scope automatic order. Hard constraints, incomplete risk data, missing authorization, scope/quota violations, revoked/expired authorization, state conflicts, and unsupported operations block every WEEX write and return to manual confirmation. Authorizations that contain the removed expanded-scope fields remain auditable but return `AUTHORIZATION_SCOPE_REAUTHORIZATION_REQUIRED` until the user grants a new V1.1 scope.
- Raw API keys, secrets, passphrases, passwords, arbitrary direct database access, and direct production calls to internal guard collaborators are forbidden at this boundary. Use a selected saved profile; all production state and automatic-order actions go through `scripts/weex_auto_trade.py`.
- Use `event-list` for the durable per-order audit trail. Public `submit-auto` legs and event payloads keep the leg's conservative value in `estimated_amount_u` and group authorization totals under `authorization_quota`; they do not expose cumulative `accepted_amount_u` beside a leg amount. Ordinary accepted events are claimed as one owner-scoped UTC 60-second summary; a credential-free detached one-shot worker waits for the final window to close even when no later facade command runs, then exits. Exceptions are immediate. Notification adapters run once, do not retry, and never change trade or quota state.
- Reconciliation records exchange status, fills, quote amount, and fees separately. For archived Spot orders, current-detail error `-2200` falls back to official history orders plus trades and requires the saved order ID and client order ID to match; incomplete fill or fee evidence stays `PARTIAL`. The per-order conservative value is `estimated_amount_u`; current authorization totals are scoped under `authorization_quota` and are not exposed as a top-level per-order `accepted_amount_u`. Reconciliation never changes the accepted conservative authorization amount. Six-table activity and terminal history are retained; there is no purge/TTL path.
- `resolve-auto-usage` accepts only `profile`, `strategy_id`, and `usage_id`. The facade performs a read-only WEEX query and internally binds usage, strategy, authorization, submission group, leg, client order, and WEEX order identities. Caller-supplied outcomes/evidence/order IDs, query timeouts, ambiguity, mismatches, and Futures submissions without a queryable order ID cannot release quota and remain `REVIEW_REQUIRED`.
- `snapshot-state` is an explicit local SQLite backup operation. It stores only validated owner-only regular snapshots in the Trader-managed `snapshots/` directory, returns immutable IDs and the registered list, defaults to retaining 10, and accepts only an integer count from 1 through 100. It publishes before rotating and never rotates unknown, temporary, failed, preserved pre-restore, or active database files.
- `restore-state` accepts only a registered snapshot ID. Every syntactically valid attempt enables the persistent kill switch before index lookup, including unknown IDs and invalid indexes. It validates and, only through a complete registered path, migrates a temporary database; revokes restored ACTIVE authorizations; rejects restored pending requests; preserves RESERVED/REVIEW_REQUIRED records; backs up the current database as non-rotated evidence; then atomically switches. It never merges ledgers or acts on WEEX orders. A fresh ACTIVE authorization must dynamically return resolve-plus-enable while unresolved usage remains, enable-only while the restore latch remains, and `SUBMIT_ALLOWED` only after explicit enable; a submission-uncertain latch returns manual inspection/reconciliation instead. Post-switch detailed authorization, verified `resolve-auto-usage` handling for every unresolved record, and explicit `enable-auto-trading-after-restore --confirm-live` are required; pre-switch or expired ACTIVE rows cannot unlock the latch.
- V1 snapshot protection is owner-only filesystem access, not password encryption. Do not accept snapshot paths, passwords, keys, encryption options, cloud upload, cross-machine synchronization, or claims that same-OS-user compromise is prevented.
- Automated-authorization state is unavailable on Windows until owner-only DACL creation and verification are implemented; fail closed instead of treating POSIX mode bits as a Windows access-control proof. This restriction is specific to automated-authorization state.
- Owner-only local permissions, foreign keys, integrity checks, migrations, and business invariants are misuse/corruption controls, not identity authentication or tamper-proofing against an attacker controlling the same OS user, Agent, Vault session, or API key.

## Safety Policy

- Never send live mutating requests without `--confirm-live`; never send demo mutating requests without `--trading-mode demo --confirm-demo`
- Partner execution is limited to the seven `operation_class=read` entries in `references/partner-api-definitions.json`. The query-only POST is read-only by contract; the internal-withdrawal write endpoint and every unknown path are forbidden.
- Partner credentials may only be sent to the exact production origin `https://api-spot.weex.com` or a saved, strictly validated HTTPS `https://*.weex.tech` test subdomain. Partner execution labels results as `partner_production` or `partner_test`, rejects the bare test-domain suffix, malformed URL variants, API base environment overrides, and authenticated redirects, and never includes the concrete test origin in Partner query output.
- Demo futures orders are not local dry-runs; they are mutating requests to WEEX futures demo mode
- Keep `live` and `demo` as internal CLI/API values only. In user-facing dialogue, risk previews, order confirmations, and account queries, use localized trading-mode labels, not environment labels and not account labels. For Chinese, use `模拟盘` and `真实盘`; for English, use `demo trading` and `real trading`. Never present raw `live` or `demo` as the trading-mode label for the user.
- In natural-language private account queries and direct non-preview trading actions, if the user did not clearly choose `模拟盘` or `真实盘` in Chinese, or `demo trading` or `real trading` in English, ask them to choose before calling private account or order commands.
- In natural-language order preview flows where a saved profile and order details are present but trading mode is missing, do not ask a standalone trading-mode question. Generate the preview with the most likely initial preview mode: explicit user wording wins first; profile names or notes can only be weak preview-default signals; if no useful signal exists, use `live` because the default flow is direct live execution. This is a preview-only default, and the same saved profile can target either trading mode.
- For every natural-language summary that uses private WEEX data or mentions a private order action, start with `user_environment_prefix` when it is returned. This includes account balances, positions, account risk, order previews, submitted order results, order cancel results, TP/SL order results, open-order queries, order status queries, and order-history queries. If a private command returns `environment` but not `user_environment_prefix`, derive the first line from that environment before summarizing anything else.
- The environment prefix must be the first user-visible line, using localized labels such as `模拟盘` or `Current trading mode: real trading`. Keep this prefix informational; it is not an order confirmation gate.
- Every natural-language order preview flow must return structured risk output before the order can be confirmed
- For `transaction.place_tp_sl_order`, `quantity` is optional: `0` or omitted means TP/SL for the full position. When the user requests full-position TP/SL, AI must not ask for quantity merely because it is absent. The preview and confirmation must explicitly state that the TP/SL order applies to the full position; a non-zero quantity remains a partial-position request.
- For natural-language order preview confirmations, show the returned `user_confirmation.reply_instruction` verbatim as the user-facing confirmation block. Do not summarize, truncate, or reconstruct it manually. The confirmation block must put the mode and funds warning first, then include the risk preview status, order summary, highest-priority warning plus any additional risk alerts, the exact confirmation reply, and include the switch prompt from `user_confirmation.switch_reply_text` when present. For ordinary `preview-order`, preserve the final localized automated-trading authorization paragraph after any mode-switch line.
- For natural-language confirmations, the only text the user should reply with to execute is `user_confirmation.reply_text`; keep `intent_id` plus `risk_signature` internal to the execution step. The reply text is intentionally simple and localized — a single word such as `confirm` for English.
- Pending order intents expire after a short TTL and must be regenerated when they are stale
- Confirmation must bind to the latest preview via `intent_id` and `risk_signature`; do not reuse old confirmation tokens
- Default flow is direct live execution; there is no mandatory dry-run phase
- If the instruction is ambiguous or missing fields, ask only for the missing fields
- For server automation, avoid `--api-key`, `--api-secret`, and `--api-passphrase` on argv; prefer `--secrets-stdin-json` or `--api-key-env` / `--api-secret-env` / `--api-passphrase-env`
