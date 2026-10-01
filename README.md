# WEEX Agent Skills

[中文版本](README.zh-CN.md)

This repository provides WEEX skill installation paths for Codex, Claude Code, Cursor, GitHub Copilot, and OpenClaw. Local installer dry-runs cover the first four hosts; OpenClaw requires its native installer and has not yet completed an on-machine smoke test in this workspace.

After installing the skills, you can ask your AI tool to check WEEX market data, review account state, collect trading history, preview order risk, create an automated monitor, or analyze WEEX trading records. For normal use, you do not need to run the Python scripts directly. Start from the skill name in chat.

A skill is an add-on instruction package for your AI tool. Mentioning `$weex-trader-skill`, `$weex-analysis-skill`, `$weex-monitor-skill`, or `$weex-partner-skill` tells the AI which WEEX workflow to use.

## Start Here

1. Recommended: ask your AI tool to install the skills for you:

```text
Install all WEEX Agent Skills from https://github.com/weex-labs/weex-trader-skill.
```

If you prefer to install manually, run:

```bash
npx skills add https://github.com/weex-labs/weex-trader-skill --all
```

2. After installation, mention the skill you want to use in chat:

```text
Use $weex-trader-skill to check the latest BTCUSDT spot price.
```

3. For private account or trading tasks, set up a saved WEEX API profile when the skill asks you to. A profile is a local saved credential setup. Use the local profile manager or another local secret-entry method instead of pasting secrets into chat.

4. If you are new to the workflow, start with read-only tasks such as market prices, account review, trading-history replay collection, or risk analysis before trying any live order.

## The Four Skills

### `weex-trader-skill`

Use [`weex-trader-skill`](skills/weex-trader-skill/README.md) when the AI tool needs to connect to WEEX.

Good for:

- checking public spot or futures market data
- checking private account state, balances, orders, positions, and order status
- setting up and using saved API profiles
- collecting normalized trading history for later analysis
- previewing order risk before a live trade
- placing or canceling spot and futures orders after explicit confirmation

Example prompts:

| Scenario | Prompt |
|---|---|
| Check price | `"Use $weex-trader-skill to check the latest BTCUSDT spot price."` |
| Review account | `"Use $weex-trader-skill to show my futures positions and available balance."` |
| Collect history | `"Use $weex-trader-skill to collect my last 30 days of BTCUSDT futures replay data."` |
| Preview risk | `"Use $weex-trader-skill to preview the risk before opening a BTCUSDT long."` |
| Prepare live order | `"Use $weex-trader-skill to preview a 200 USDT BTC market buy before I decide whether to place it."` |

### `weex-analysis-skill`

Use [`weex-analysis-skill`](skills/weex-analysis-skill/README.md) when the AI tool needs to review WEEX data that has already been collected or exported.

This skill is read-only. It does not connect to your live private account and does not place or cancel orders.

Good for:

- reviewing exposure, concentration, leverage, and free collateral
- summarizing filled trades, fees, and realized profit/loss (PnL)
- reviewing replay behavior and trading patterns
- generating a trading profile from normalized history
- reviewing order-risk or account-risk JSON files collected by `weex-trader-skill`

Example prompts:

| Scenario | Prompt |
|---|---|
| Review exposure | `"Use $weex-analysis-skill to analyze this WEEX account snapshot and show my main concentration risk."` |
| Review filled trades | `"Use $weex-analysis-skill to review these filled trades and summarize realized profit/loss after fees."` |
| Review behavior | `"Use $weex-analysis-skill to analyze this replay data and highlight behavior patterns."` |
| Generate profile | `"Use $weex-analysis-skill to generate a trading profile from this replay data."` |
| Review account risk | `"Use $weex-analysis-skill to analyze this account-risk JSON and summarize the main risks."` |

### `weex-monitor-skill`

Use [`weex-monitor-skill`](skills/weex-monitor-skill/SKILL.md) when the AI tool needs to turn a natural-language WEEX monitor request into a confirmed local monitor task.

This skill is an orchestration layer for local position-PnL monitors. It drafts, confirms, stores, evaluates, executes through `weex-trader-skill`, and reports PnL monitor tasks. It does not own API credentials, vault unlock, signing, or direct REST submission. Live PnL-triggered market close still requires explicit authorization to use the real account and submit real close orders. For price-based conditional closes, use WEEX official conditional orders through `weex-trader-skill` instead of `weex-monitor-skill`.

Good for:

- monitoring one futures position by unrealized PnL
- executing a direction-specific market close through `weex-trader-skill` when a PnL threshold is reached and the user authorizes real account execution
- running dry-run monitor checks with local position snapshots
- listing, reviewing, and cancelling local monitor tasks

Example prompts:

| Scenario | Prompt |
|---|---|
| Monitor PnL | `"Use $weex-monitor-skill to monitor my BTCUSDT long; first verify the real position, then if unrealized profit is greater than 50 USDT, close it at market after I authorize real account execution."` |
| Review monitors | `"Use $weex-monitor-skill to list my local monitor tasks and recent events."` |

### `weex-partner-skill`

Use [`weex-partner-skill`](skills/weex-partner-skill/SKILL.md) for read-only WEEX Partner referral, commission, direct-user asset/trade, sub-agent, relationship, asset, and deal-data queries.

It reuses the same saved profile and Application Vault as `weex-trader-skill`; REST access and credentials remain in trader. A Partner-capable profile can still place normal orders through trader's existing preview and explicit confirmation gates.

Partner-only turns use trader's safe Partner preflight, which reports the selected profile identity and `partner_production|partner_test` label without placing the concrete test origin, API-key hint, or unrelated profile routing metadata in the tool transcript.

Example: `"Use $weex-partner-skill to query my commission for the official default time range and explain the actual UTC range."`

## Which Skill Should I Use?

| If you want to... | Use |
|---|---|
| check live market prices | `weex-trader-skill` |
| check live private account, balance, order, or position data | `weex-trader-skill` |
| set up or use a saved WEEX API profile | `weex-trader-skill` |
| preview, place, cancel, or check a live order | `weex-trader-skill` |
| create or review a local automated monitor for PnL conditions | `weex-monitor-skill` |
| create an exchange-native price conditional close | `weex-trader-skill` |
| analyze an existing WEEX JSON file or pasted JSON data | `weex-analysis-skill` |
| analyze live account history | collect data with `weex-trader-skill`, then analyze it with `weex-analysis-skill` |
| query WEEX Partner referrals, commission, assets, or sub-agent data | `weex-partner-skill` |

## Install From A Local Copy (Optional)

If you downloaded or cloned this repository and want to install from that local copy, run:

```bash
python3 tools/install_local_skills.py --all --agent codex
```

Use `--agent claude-code`, `--agent cursor`, or `--agent github-copilot` for those hosts. The local installer validates the agents supported by `gh skill install`.

`weex-monitor-skill` and `weex-partner-skill` depend on `weex-trader-skill`. Installing either one from the local installer automatically includes trader; installing all skills is still recommended.

OpenClaw uses its native installer. From the repository root, install trader first and partner second:

```bash
openclaw skills install ./skills/weex-trader-skill --as weex-trader-skill
openclaw skills install ./skills/weex-partner-skill --as weex-partner-skill
```

Append `--global` to both commands for the shared `~/.openclaw/skills` directory, or append `--agent <id>` to both for one Agent workspace. Then run `openclaw skills list --eligible`, `openclaw skills info weex-partner-skill`, and `openclaw skills check`. Do not use `gh skill install --agent openclaw`.

Most users only need the GitHub install command in [Start Here](#start-here).

## User Safety Notes

- Live order, cancel, or account-changing actions can affect real assets. Check the account, symbol, side, size, price, order type, and risk preview before you confirm any action.
- Do not paste API keys, API secrets, passphrases, vault passwords, or temporary secret files into chat, issue trackers, public logs, or screenshots.
- Prefer saved profiles, the local profile manager, `--prompt-secrets`, environment variables, or `--secrets-stdin-json` for local secret entry.
- Use least-privilege API keys for this workflow. If credentials may have been exposed, revoke or rotate them immediately.
- Partner queries use the production default or a saved HTTPS `https://*.weex.tech` test subdomain. Results disclose only the Partner environment label, not the concrete test origin; API base environment overrides and authenticated redirects remain forbidden.
- `weex-analysis-skill` output is for review and risk reference only. It is not investment or trading advice.
- When in doubt, ask the AI tool to preview or explain before asking it to execute anything.

## More Documentation

- [`weex-trader-skill` README](skills/weex-trader-skill/README.md): live WEEX access, API profiles, order preview, live order flow, and troubleshooting.
- [`weex-analysis-skill` README](skills/weex-analysis-skill/README.md): accepted input data, analysis examples, replay review, and safety notes.
- [`weex-monitor-skill` SKILL.md](skills/weex-monitor-skill/SKILL.md): automated monitor DSL, confirmation flow, dry-run runner, and live execution boundary.
- [`weex-partner-skill` SKILL.md](skills/weex-partner-skill/SKILL.md): Partner routing, full-scope confirmation, time defaults, paging, and read-only boundaries.
- [`weex-trader-skill` script operations](skills/weex-trader-skill/references/script-operations.md): direct script usage for advanced users.
- [`weex-analysis-skill` analysis playbook](skills/weex-analysis-skill/references/analysis-playbook.md): analysis behavior and interpretation details.
