# weex-trader-skill

Use this skill in Codex / Openclaw / Claude Code to automate WEEX futures and spot workflows with natural language.

It supports:

- public market data
- private account and position queries
- spot and futures order placement
- normalized replay, profile, order-risk, and account-risk payload collection for downstream read-only analysis
- preview-before-submit order risk checks with a pending confirmation intent
- current account-risk scans without order parameters
- raw endpoint access through local endpoint catalogs
- secure saved-profile management across Windows, macOS, and Linux

## Contents

- Get API credentials
- Critical secret warning
- Install in Codex
- How to use this skill in Codex / Openclaw / Claude Code
- Module quick-reference
- Companion skill boundary
- Recommended live order flow
- Saved profile setup
- Security notes
- Troubleshooting
- References

## Get API Credentials

Access to private endpoints such as account and trading APIs requires a WEEX API key. Public market data endpoints are available without authentication.

Create an API key from [API Management](https://www.weex.com/account/newapi/).

Keep these values secure:

- API Key
- Secret Key
- Passphrase

Never share your Secret Key or Passphrase. Anyone with those credentials can control the account.

## Critical Secret Warning

This skill allows AI-assisted handling of API keys, API secrets, passphrases, and vault passwords, but doing so creates exposure risk. If you choose to paste secrets into AI chat or let the AI operate on them directly, assume they may be retained or leaked later. This includes any secret entered through the profile manager or vault manager.

Openclaw Telegram conversations can leave server-side chat logs and relay history. Treat anything sent through that path as potentially retained, reviewed, or recovered later.

Prefer local secret-entry flows such as the GUI profile manager, the vault UI, `--prompt-secrets`, or a trusted local stdin pipe instead of typing secrets into a chatbot window.

## Install In Codex

Install from the checkout you plan to use, or from the published GitHub repo URL `https://github.com/weex-labs/weex-trader-skill`. In that repo layout, this skill should be read from `skills/weex-trader-skill/`, including `skills/weex-trader-skill/README.md`, `skills/weex-trader-skill/SKILL.md`, `skills/weex-trader-skill/manifest.json`, `skills/weex-trader-skill/file-index.json`, `skills/weex-trader-skill/scripts/`, `skills/weex-trader-skill/references/`, `skills/weex-trader-skill/requirements.txt`, and `skills/weex-trader-skill/requirements.lock`.

If you install from the source repository, prefer the clean-export wrapper instead of installing directly from the working tree:

```bash
python3 tools/install_local_skills.py --skill weex-trader-skill --agent codex
```

That wrapper exports only the selected skill directory plus small repo metadata files before running `gh skill install`, so generated noise such as `.DS_Store` and `__pycache__` does not leak into the installed skill and the flow no longer depends on manually preparing a clean local checkout. If you intentionally need local untracked skill files during development, add `--include-untracked`. If you need to scrub the working tree itself before packaging checks, run `python3 tools/clean_local_skill_checkout.py`. If you are not installing from the source repository, prefer that published GitHub repo URL instead.

Saved profiles and vault files are runtime state under the local WEEX config directory. Do not ship, version, or share that state as part of the skill checkout.
AI helper cache files `agent-init.json` and `agent-runtime.json` may also appear there. They help route later AI actions faster, but they are not secret storage.
AI agents using this skill should run `py -3 scripts/weex_agent_state.py --command skill.preflight --language <zh|en> --pretty` on Windows or `python3 scripts/weex_agent_state.py --command skill.preflight --language <zh|en> --pretty` on macOS/Linux before each routed task so the cache is always present and fresh.
On Windows and macOS, GUI profile and vault flows always require the managed GUI runtime. The preflight step reports whether that runtime is ready, but it does not download or install one implicitly; AI should ask for confirmation before running the reported setup command.
When a GUI must be launched from an AI/tool-managed shell, the detached launcher tries to show only the WEEX window: macOS uses a transient `.app` wrapper, while Windows prefers `pythonw.exe` or another hidden background process instead of a visible console window.
If `agent-init.json` is missing and the AI is about to use an auto-language wrapper such as `scripts/weex_vault.py`, the AI should refresh the cache first instead of guessing.

For repo-local dependency setup, one-command runtime installation, direct script invocation, or maintenance commands, use [Script operations](references/script-operations.md) instead of this overview page.
Private contract and spot CLIs can now auto-attempt that runtime setup helper when the current interpreter is missing required Python dependencies.

Example prompts:

```text
Help me install this skill from https://github.com/weex-labs/weex-trader-skill
```

```text
Check whether $weex-trader-skill is installed.
```

## How to Use This Skill in Codex / Openclaw / Claude Code

Mention `$weex-trader-skill`, then describe the task in plain language.

| Scenario | Natural-language example |
|---|---|
| Check market price | `"What's the latest BTCUSDT spot price?"` |
| Review account or positions | `"Show me my current futures positions and available balance."` |
| Replay recent futures trading | `"Replay my last 30 days of BTCUSDT futures trades and summarize the biggest mistakes."` |
| Generate a trading profile | `"Build a trading profile from my recent futures history."` |
| Preview order risk before submit | `"Preview the risk on this BTCUSDT long before placing it."` |
| Ask for current account risk | `"What are my main futures account risks right now?"` |
| Place a spot market order | `"Buy 200 USDT worth of BTC at market."` |
| Place a futures limit order | `"Open a small ETHUSDT short with a limit order at 2500."` |
| Cancel open orders | `"Cancel my open ETHUSDT futures orders."` |
| Check order status | `"Did my BTCUSDT order fill yet?"` |

## Module quick-reference

| Module | What it covers | Auth |
|---|---|---|
| `Spot` | Spot market data, balances, orders, trade history, and related endpoints. | Public + private |
| `Futures` | Futures market data, account state, orders, positions, and leverage/margin endpoints. | Public + private |
| `Aggregation` | Replay, profile, order-risk, and account-risk payload collection for downstream analysis. | Public + private |
| `Trade Guard` | Preview-before-submit order risk, account-risk scan, pending confirmation intent handling, and trader-local risk review for standalone installs. | Private |

If you need the underlying Python/runtime setup, shell command context, or direct CLI examples, open [Script operations](references/script-operations.md).

## Companion Skill Boundary

This skill may collect normalized replay, profile, order-risk, and account-risk payloads for downstream review, but it does not own the read-only analysis layer.

If the user wants interpretation of those payloads rather than data collection or live trading actions, hand the normalized JSON to `weex-analysis-skill`.
Keep the detailed analysis workflow, analysis commands, and result semantics in the analysis skill documentation instead of duplicating them here.

## Recommended Live Order Flow

Use this safety order for live trading tasks:

- run `skill.preflight` first so profile, runtime, env, and GUI-routing facts are fresh before private actions
- use a saved profile for private REST access instead of pasting credentials into ad hoc commands
- preview the order risk first and review the returned alerts plus `user_confirmation`
- in natural-language flows, ask the user to reply with exactly `user_confirmation.reply_text`; do not ask them to copy `intent_id`, `risk_signature`, or longer phrases such as "confirm order"
- keep `intent_id` and `risk_signature` internal for the execution step
- confirm only with the latest preview output plus `--confirm-live`; stale or mismatched confirmation tokens must be regenerated
- use account-risk scan when the user wants current exposure review without an order payload

## Saved Profile Setup

Private account and trading operations require a saved profile.

Choose the setup guide that matches how you want to work:

- Windows/macOS account manager workflow: [Profile manager guide](references/profile-manager.md)
- Full OS matrix and terminal-based profile commands: [Profile onboarding](references/profile-onboarding.md)
- Linux vault modes, password handling, and lock/unlock flows: [Linux vault](references/linux-vault.md)

Use the profile manager guide when you want the GUI flow. Use the onboarding guide when you need exact terminal commands or server automation patterns. On Linux, use the `manual_once` vault flow and handle vault passwords, env vars, and temporary secret files carefully. `unlock` only needs one passphrase entry for the existing vault password, while `setup` and `change-password` still confirm the new passphrase twice.
On Windows and macOS, GUI entrypoints must run under the pinned managed CPython 3.12.13 GUI runtime even when the current interpreter can initialize Tk. An AI assistant should explain the pinned setup and ask for confirmation before running `python3 scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty` on the user's behalf.
If an AI or automation host launches the GUI, prefer `scripts/weex_gui_launcher.py ...`; launcher records and logs are written under `~/.weex-trader-skill/gui-launchers`, keep only the most recent launches, and trim each log to a bounded size.

## Security Notes

- AI-assisted secret handling is supported by this skill, but it increases leakage risk. Openclaw Telegram is especially sensitive because it can leave server-side chat logs.
- Never share or commit API credentials.
- Use least-privilege API keys for this workflow.
- If credentials are exposed, revoke or rotate them immediately.
- Prefer saved profiles over ad hoc secret-passing shell commands.
- For server automation, avoid `--api-key`, `--api-secret`, and `--api-passphrase` on argv; prefer environment variables or `--secrets-stdin-json`.
- Raw argv secrets and literal vault passwords can leak through shell history, the process list, terminal scrollback, audit logs, and crash reports.
- temporary password files and secret JSON files can leak through backups, sync folders, editors' recent-file lists, and filesystem forensics. Delete them immediately and keep them outside the repo.
- `profiles.meta.json` is not the encrypted vault. It can still reveal account names, descriptions, default-profile choices, and custom base URLs.
- `manual_once` is the supported Linux vault mode. Lock it again after sensitive work when appropriate.
- `--confirm-live` sends real order or cancel requests to the real account. Start with least-privilege keys and a small or non-critical account whenever possible.
- `preview-order` returns a localized `user_confirmation.reply_text` for the human reply and still returns `intent_id` plus `risk_signature` for internal execution binding.
- `confirm-order` expects the `intent_id` and `risk_signature` returned by `preview-order`; if either is missing or mismatched, regenerate the preview instead of forcing the old confirmation through.

## Troubleshooting

For common operator issues and recovery paths, open [Troubleshooting](references/troubleshooting.md).
If a Windows or macOS GUI entrypoint fails before opening, `python3 scripts/weex_doctor.py gui` provides a concise diagnosis plus the explicit managed-runtime repair path.
For detached-launch failures, inspect the newest file under `~/.weex-trader-skill/gui-launchers/*.log`; those logs are capped so they stay useful without growing forever.

## References

- [Script operations](references/script-operations.md)
- [Trade data schema](references/trade-data-schema.md)
- [Profile manager guide](references/profile-manager.md)
- [Profile onboarding](references/profile-onboarding.md)
- [Linux vault](references/linux-vault.md)
- [Troubleshooting](references/troubleshooting.md)
- [Auth and signing](references/auth-and-signing.md)
- [Spot endpoints](references/spot-endpoints.md)
- [Contract endpoints](references/contract-endpoints.md)
- [Spot API definitions](references/spot-api-definitions.md)
- [Contract API definitions](references/contract-api-definitions.md)
- [WebSocket notes](references/websocket.md)
