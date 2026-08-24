# WEEX Agent Skills

[中文版本](README.zh-CN.md)

This repository provides WEEX skill installation paths for Codex, Claude Code, Cursor, GitHub Copilot, and OpenClaw. The local installer covers the first four hosts. OpenClaw uses a fixed local Git checkout with skill-directory symlinks, managed by the repository's update script.

After installing the skills, you can ask your AI tool to check WEEX market data, review account state, collect trading history, preview order risk, create an automated monitor, or analyze WEEX trading records. For normal use, you do not need to run the Python scripts directly. Start from the skill name in chat.

A skill is an add-on instruction package for your AI tool. Mentioning `$weex-trader-skill`, `$weex-analysis-skill`, `$weex-monitor-skill`, or `$weex-partner-skill` tells the AI which WEEX workflow to use.

## Start Here

1. Recommended: ask your AI tool to install the skills for you:

```text
Install all WEEX Agent Skills from https://github.com/weex-labs/weex-agent-skills.
```

If you prefer to install manually, run:

```bash
npx skills add https://github.com/weex-labs/weex-agent-skills --all
```

OpenClaw users should use the dedicated [Git and symlink workflow](#install-or-update-openclaw) below instead of this `npx` command.

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
- registering a saved-profile automated strategy with a finite, revocable authorization and durable per-order audit trail

Example prompts:

| Scenario | Prompt |
|---|---|
| Check price | `"Use $weex-trader-skill to check the latest BTCUSDT spot price."` |
| Review account | `"Use $weex-trader-skill to show my futures positions and available balance."` |
| Collect history | `"Use $weex-trader-skill to collect my last 30 days of BTCUSDT futures replay data."` |
| Preview risk | `"Use $weex-trader-skill to preview the risk before opening a BTCUSDT long."` |
| Prepare live order | `"Use $weex-trader-skill to preview a 200 USDT BTC market buy before I decide whether to place it."` |
| Authorize an automated strategy | `"Use $weex-trader-skill to register my strategy and request a 24-hour Spot and Futures authorization with 200 U per order and 2,000 U total."` |

### Automated strategy authorization

The formal Trader implementation supports explicit integration by user-maintained Python or quantitative strategies. It does not discover or wrap arbitrary scripts, and automated authorization is available only for saved-profile real trading, not demo trading. Each strategy registers a stable identity and requests its own authorization before its first order. Restarts and renames reuse that identity; a copied strategy receives a new identity and an independent authorization and quota. The authorization has five scope dimensions only: Spot/Futures modules, selected symbols or all symbols, the per-leg conservative U maximum, the cumulative U maximum during the validity period, and an explicit validity. It does not add separate restrictions for side, order type, minimum order amount, or order count. Natural-language requests that omit validity must be clarified; the JSON CLI requires `valid_hours`, which must be greater than zero and cannot exceed 720 hours (30 days). Never derive `max_total_amount` from frequency, validity, target amount, max_single_amount, balance, or expected order count. A cumulative quota omitted from a natural-language request requires a separate user choice; the exact value must appear in the final confirmation. Granting requires the exact request and `--confirm-live`. A pending request grants no trading authority and expires after 15 minutes; the authorization validity starts when the grant succeeds. Changing any scope dimension requires a new request and explicit grant, which replaces the strategy's previous active authorization.

A real-trading automation may become `ACTIVE` only in the same atomic update that includes an `UNTIL` no later than the authorization expiry; never activate an unbounded recurrence, even temporarily. If the runtime cannot update status and expiry atomically, keep the automation `PAUSED`.

What approval means:

- Granting changes local authorization state only; it does not submit an order to WEEX.
- While the authorization is active, eligible in-scope orders may be submitted without confirmation for each order. Every order still has to pass the official-data, risk, product, balance, scope, and quota checks.
- The per-leg and cumulative limits apply to conservative U estimates, not requested or actual fill amounts. An accepted estimate remains consumed for the authorization period and is not refunded by later reconciliation.
- Expiry or revocation blocks future automatic reservations. Retiring a strategy also permanently blocks new authorizations for that strategy identity. These actions do not cancel, retry, or amend orders already submitted to WEEX.

Only official Spot/Futures operations with fresh, complete WEEX facts can enter the automatic path. Batch legs are quota-checked and reserved atomically, then audited with strategy, authorization, usage, group, client order ID, and WEEX order ID. Accepted estimates are not refunded by later reconciliation; explicit rejections release their reservation; uncertain results or mappings return to manual review without retry. Full-position TP/SL, unproven reduce-only behavior, stale/degraded data, missing conversion/depth/leverage/fee facts, scope or quota violations, revoked/expired authorizations, state conflicts, and unknown operations never submit automatically.

Use the saved-profile JSON facade in `skills/weex-trader-skill/scripts/weex_auto_trade.py` for lifecycle, `submit-auto`, recovery, event, and read-only reconciliation operations. Strategies call that CLI as a subprocess; direct state imports, injected production collaborators, raw credentials, and direct database writes are unsupported. Local owner-only permissions and integrity checks are misuse/corruption controls, not identity authentication or tamper-proofing against an attacker controlling the same OS user or Agent. Ordinary accepted notifications may be aggregated for 60 seconds; exception notifications are immediate and attempted once. See [Script operations](skills/weex-trader-skill/references/script-operations.md) for the exact JSON commands.

The same facade provides explicit owner-only local snapshots with a default retention count of 10 (range 1-100) and restore by Trader-generated snapshot ID. Every syntactically valid restore attempt engages the persistent kill switch before index lookup, so an unknown ID or invalid snapshot also leaves automatic trading disabled. Restore preserves the current database, leaves unresolved usage for manual reconciliation, and requires post-switch authorization plus explicit reconciliation and enablement. Snapshots are not password-encrypted and are never uploaded or synchronized automatically. Automated-authorization state currently fails closed on Windows until owner-only DACL creation and verification are supported; other Trader Windows workflows are unaffected.

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

### Install Or Update OpenClaw

OpenClaw should keep one fixed Git checkout and expose each skill through a symbolic link. The default layout is:

- repository: `~/.openclaw/skill-repos/weex-agent-skills`
- `~/.openclaw/skills/weex-trader-skill`
- `~/.openclaw/skills/weex-analysis-skill`
- `~/.openclaw/skills/weex-monitor-skill`
- `~/.openclaw/skills/weex-partner-skill`

From a checkout containing this repository version, run:

```bash
bash skills/weex-trader-skill/scripts/update_openclaw_skills.sh
```

The script clones the official repository at the pinned approved release commit `e10c2089550159afb7247271d1041d9b415145cd`; it never follows a moving branch in production. It stages and validates the checkout (Git object integrity, expected skills, and no submodules), switches the four links only after validation, runs the OpenClaw checks, and keeps a stable updater copy at `~/.openclaw/update-weex-openclaw-skills.sh` so a fetched checkout cannot replace the updater itself. If any check fails, the previous checkout and links are restored.

```bash
openclaw skills list --eligible
openclaw skills info weex-trader-skill
openclaw skills check
```

Future updates only need:

```bash
~/bin/update-weex-openclaw-skills.sh
```

The script never replaces a real file or directory at a link destination. Resolve that conflict manually and rerun it. Non-official repositories or branches are for explicit development use only:

```bash
WEEX_OPENCLAW_REPO_URL=/path/to/checkout \
WEEX_OPENCLAW_BRANCH=feature/my-branch \
bash skills/weex-trader-skill/scripts/update_openclaw_skills.sh --dev
```

`WEEX_OPENCLAW_REPO_DIR`, `WEEX_OPENCLAW_SKILLS_DIR`, and `WEEX_OPENCLAW_BIN_LINK` may still select the local installation paths.

To roll back, restore the previous checkout and rerun the updater with the approved release policy; failed validation already leaves the previous checkout and links untouched:

```bash
bash ~/.openclaw/update-weex-openclaw-skills.sh
```

Finally, start a new OpenClaw task and run this read-only smoke test:

```text
Use $weex-trader-skill to check the latest BTCUSDT spot price.
```

Do not use `gh skill install --agent openclaw`; the root Python installer remains for the other supported hosts.

Users of Codex, Claude Code, Cursor, or GitHub Copilot usually only need the GitHub install command in [Start Here](#start-here); OpenClaw users should keep using the workflow above.

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
