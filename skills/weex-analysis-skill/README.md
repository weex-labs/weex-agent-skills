# weex-analysis-skill

Use this skill in Codex / Openclaw / Claude Code to review WEEX snapshots, fills, replay data, profiles, and risk payloads from structured JSON input.

It supports:

- snapshot-based exposure analysis
- leverage and concentration review
- free-collateral checks
- fill and fee recap
- realized PnL aggregation
- large replay payload preparation before analysis
- replay analysis with behavior tags, evidence, and advice
- trade review with reconstructed episodes and highlights
- profile analysis with persona labels, strengths, and weaknesses
- bill-adjusted replay/profile net PnL alongside episode-only PnL totals
- pre-order risk analysis for preview-before-submit reminder flows
- current account-risk analysis without an order preview
- trading-mode display for trader-collected real-trading or demo futures payloads

## Contents

- How to use this skill in Codex / Openclaw / Claude Code
- Input expectations
- Example prompts
- CLI examples
- Working with `weex-trader-skill`
- Live replay verification
- Safety notes
- References

## How to Use This Skill in Codex / Openclaw / Claude Code

Mention `$weex-analysis-skill`, then provide the normalized JSON payload or tell the agent which JSON file to analyze.

| Scenario | Natural-language example |
|---|---|
| Review exposure and concentration | `"Analyze this WEEX snapshot and tell me where my main concentration risk is."` |
| Review fills and fees | `"Review these fills and summarize realized PnL after fees."` |
| Review replay behavior | `"Replay these normalized trades and tell me what behavior tags stand out."` |
| Review reconstructed trade episodes | `"Review these normalized trades and highlight the most important trade episodes."` |
| Generate a profile | `"Generate a trading profile from this normalized replay payload."` |
| Review pre-order reminders | `"Analyze this order-risk payload and list the alerts before I place the order."` |
| Review current account risk | `"Analyze this account-risk payload and tell me what my main account risks are right now."` |
| Shrink a large replay first | `"Prepare this large replay payload so the analysis step only keeps the rows it needs."` |

If you only have live WEEX account data, collect or normalize it with `weex-trader-skill` first. This skill does not fetch live private data by itself.

## Input Expectations

- Prefer normalized JSON payloads over prose summaries.
- Use the schema that matches the target command: snapshot, fills, replay, profile, order-risk, or account-risk.
- Preserve `trading_mode`, `environment`, and `account_scope` when they come from `weex-trader-skill`; analysis is read-only and does not infer live or demo state from profile names.
- If the replay payload is too large to review comfortably, run `prepare-replay` first.
- Missing prices, leverage, or balance fields produce partial analysis instead of invented values.
- Open [Snapshot schema](references/snapshot-schema.md) when you need the accepted input shapes or aliases.

## Example Prompts

```text
Analyze this WEEX positions snapshot and tell me where my concentration risk is.
```

```text
Review these fills and summarize realized PnL after fees.
```

```text
Replay these normalized trades and tell me what behavior tags stand out.
```

```text
Review these normalized trades and highlight the most important trade episodes.
```

```text
Prepare this large replay payload so the analysis step only keeps the rows it needs.
```

```text
Generate a trading profile from this normalized replay payload.
```

```text
Analyze this order-risk payload and list the alerts before I place the order.
```

```text
Analyze this account-risk payload and tell me what my main account risks are right now.
```

## CLI Examples

```bash
python3 scripts/weex_analysis_cli.py analyze-snapshot --input snapshot.json --pretty
```

```bash
python3 scripts/weex_analysis_cli.py analyze-fills --input fills.json --format text
```

```bash
python3 scripts/weex_analysis_cli.py analyze-replay --input replay.json --pretty
```

```bash
python3 scripts/weex_analysis_cli.py review-trades --input replay.json --format text
```

```bash
python3 scripts/weex_analysis_prepare.py prepare-replay --input replay.json --symbol BTCUSDT --account-scope sim_futures --start-time-ms 1717200000000 --end-time-ms 1719791999000 --max-orders 2000 --max-fills 2000 --pretty
```

```bash
python3 scripts/weex_analysis_cli.py analyze-profile --input profile.json --pretty
```

```bash
python3 scripts/weex_analysis_cli.py analyze-order-risk --input order-risk.json --pretty
```

```bash
python3 scripts/weex_analysis_cli.py analyze-account-risk --input account-risk.json --pretty
```

## Working with `weex-trader-skill`

When analysis needs live data, collect it first with `weex-trader-skill`, then pass the normalized JSON into the matching command here.

| Goal | Collect with trader skill | Analyze with this skill |
|---|---|---|
| Replay behavior review | `collect-replay` | `prepare-replay`, `analyze-replay`, or `review-trades` |
| Trading profile | `collect-profile` or a replay payload | `analyze-profile` |
| Pre-order reminder flow | `collect-order-risk` | `analyze-order-risk` |
| Current account-risk scan | `collect-account-risk` | `analyze-account-risk` |

Do not coerce every payload into one generic snapshot wrapper. Pass each normalized payload to the command that matches that payload type.

## Live Replay Verification

This check requires both this skill and `weex-trader-skill`. Run the collection step from a local `weex-trader-skill` checkout or installation, then run the preparation and analysis steps from this skill's directory.

From a local `weex-trader-skill` checkout or installation:

```bash
python3 scripts/weex_agent_state.py --command skill.preflight --language zh --pretty
python3 scripts/weex_trade_data_aggregator.py collect-replay --profile main --market futures --period 30d --pretty > /tmp/weex-live-replay.json
```

From this skill's directory:

```bash
python3 scripts/weex_analysis_prepare.py prepare-replay --input /tmp/weex-live-replay.json --symbol BTCUSDT --account-scope futures --start-time-ms 1717200000000 --end-time-ms 1719791999000 --max-orders 2000 --max-fills 2000 --pretty > /tmp/weex-live-replay-prepared.json
python3 scripts/weex_analysis_cli.py analyze-replay --input /tmp/weex-live-replay-prepared.json --format text
```

Notes:

- Keep `--profile` explicit unless you have already configured a default saved profile.
- This flow is read-only. It collects replay data, prepares a smaller replay payload, and analyzes it. It does not place, amend, or cancel orders.
- `prepare-replay` can filter by `--symbol`, `--account-scope`, `--start-time-ms`, and `--end-time-ms`, then trim with `--max-orders` and `--max-fills`.
- If you want to force a truncation check, lower `--max-orders` and `--max-fills` until the prepared payload returns `partial=true` with `analysis_orders_truncated` or `analysis_fills_truncated`.
- Replay/profile `risk_score` reflects replay behavior only; it no longer uses the current account snapshot as a stand-in for historical trading style.

## Safety Notes

- This skill is read-only. It does not place, amend, or cancel orders.
- State clearly when the analysis is partial because fields are missing or the replay payload was truncated.
- Order-risk and account-risk output here are advisory summaries. Real live-order confirmation still belongs to `weex-trader-skill`.
- Every user-facing analysis result must include the exact standard disclaimer below in both JSON and text output. Do not paraphrase it, shorten it, or omit it.

`Disclaimer: This result is generated solely from the current input data and is for reference only. It does not constitute any investment or trading advice. Please make your own independent judgment based on real-time data, official rules, and your own risk tolerance. Responsibility for related decisions and execution rests solely with the user.`

## References

- [Snapshot schema](references/snapshot-schema.md)
- [Analysis playbook](references/analysis-playbook.md)
