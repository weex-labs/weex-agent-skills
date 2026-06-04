---
name: weex-monitor-skill
description: Use when the user wants a WEEX automated monitor for position PnL that drafts, confirms, stores, evaluates, runs, and reports monitor tasks while delegating live execution to weex-trader-skill.
compatibility: Requires Python for the bundled monitor task scripts. Live WEEX REST access, profile storage, vault access, signing, and order submission belong to weex-trader-skill.
---

# WEEX Monitor Skill

Read `manifest.json` for routing rules. Open `file-index.json` only when you need file-level guidance.

This skill owns WEEX position-PnL automated monitor orchestration. It converts a constrained PnL monitor intent into a task DSL, renders localized monitor confirmation text with a confirmation token, requires explicit monitor confirmation, stores local task state in SQLite, appends audit events, evaluates local PnL triggers, can activate and start a bounded live PnL loop from one combined user confirmation, can run live PnL checks or a bounded live loop through `weex-trader-skill`, and reports results to the current thread.

This skill does not own API credentials, profile storage, vault unlock, REST signing, or direct REST submission. Those capabilities stay in `weex-trader-skill`. Live monitor commands are orchestration wrappers around `weex-trader-skill` guard flows and require internal live-execution flags. Do not use internal flag names in user-facing wording; tell the user plainly that they are authorizing real account access and, if triggered, a real close order. A single user confirmation may authorize monitor activation and live execution only when the confirmation text includes the matched live position, close action, final task details, and real-account/order authority; PnL live loops must also include a finite live duration. User-facing monitor confirmation must ask for one simple localized reply word: `确认` for Chinese copy or `confirm` for English copy; do not ask for longer localized phrases such as "confirm start monitoring."

## AI natural-language parsing

When the user gives a natural-language monitor request, the AI layer of this skill must convert the user's monitor instruction into the Task DSL before calling the deterministic script. The script intentionally accepts JSON only; do not pass raw chat text to the script and treat a missing DSL field as a clarification need, not as permission to infer a risky value.

Required extraction fields:

- monitor object: `profile`, `symbol`, and `position_side`; profile is always required before calling the CLI because the monitor task must bind to a saved WEEX profile name
- monitor frequency: `frequency_seconds` for PnL monitors, defaulting to `5` when omitted
- trigger condition: metric must be `unrealized_pnl`, with an operator and numeric threshold
- execution action: only `market_close` targeting the same `position_side`; `action.quantity` is optional and, when omitted, live execution uses the matched position size from the fresh account snapshot
- live run scope for combined create-and-start PnL flows: finite `duration_seconds` or an absolute expiry time that the AI converts to `duration_seconds`; do not ask users for iteration counts
- live position confirmation for combined create-and-start PnL flows: before asking the user to confirm, collect the real account position through `weex-trader-skill`, find the exact `symbol` + `position_side` match, and show its side, size, current unrealized PnL, and detailed live position snapshot fields such as entry price, current/mark price, leverage, margin mode, closable quantity, liquidation price, position update time, account available balance, and confirmation snapshot time; if a field is not returned, show the missing-value placeholder localized to the confirmation language, such as `not returned` in English
- Agent status reporting for live PnL flows: default reporting interval is `60` seconds; if the user asks for a different interval, convert it to `reporting_interval_seconds` as a whole-minute interval no lower than `60` seconds
- callback: only `current_thread`

If any required field is absent or ambiguous, ask for the missing field and keep the task as a draft. Do not create a monitor for price-threshold conditions, open, add, reverse, leverage, transfer, arbitrary script, or spot actions. For price-based conditional closes, direct the user to WEEX official conditional orders via `weex-trader-skill`; do not create a local monitor task. Do not submit orders by bypassing the trader guard; live execution must be delegated to `weex-trader-skill` and requires explicit user authorization for real account access and real order execution.

Examples:

- User: `Close the BTCUSDT long position automatically when unrealized PnL is above 50; check every 5 seconds.`
  - DSL: `position_pnl_monitor`, `profile=<saved-profile>`, `symbol=BTCUSDT`, `position_side=LONG`, `condition.metric=unrealized_pnl`, `condition.operator=>`, `condition.threshold=50`, `action.target=LONG`, `frequency_seconds=5`.
- User: `Create and start a BTCUSDT long-position PnL monitor; close the long position when unrealized PnL is above 50; run for 1 hour.`
  - DSL: same `position_pnl_monitor` as above plus live run scope `duration_seconds=3600`; first call `confirm-text-live` so the user sees the matched live position, then after the user replies with the localized simple confirmation word from the summary, use `confirm-and-run-loop` with the internal confirmation token and live-execution flag.

## Supported Scenarios

- `position_pnl_monitor`: monitor one futures position by `symbol` and `position_side`, compare `unrealized_pnl` against a threshold, then use `weex-trader-skill` to preview and confirm a direction-specific market-close order when an authorized live runner is used.

Do not expand this skill to price-threshold monitors, open positions, add margin, change leverage, reverse positions, spot trading, grid trading, trailing stops, multi-account tasks, or arbitrary script execution unless the skill policy and tests are updated first.

## Core Entry Point

- `scripts/weex_monitor_cli.py`: normalize PnL monitor tasks, render monitor confirmation text and a confirmation token, render live-position confirmation text with `confirm-text-live`, localize confirmation copy and the simple reply word with `--language zh|en`, require `--confirm-monitor` plus a matching `--confirmation-token`, persist draft/active/executing/completed/cancelled/review_required tasks, append events, evaluate PnL snapshots, run dry-run checks and dry-run loops, run live PnL checks and bounded live loops through `weex-trader-skill`, activate and run a bounded PnL live loop with `confirm-and-run-loop`, return `agent_reporting` metadata for Codex heartbeat, Claude Code `/loop`, and OpenClaw cron status reporting, list tasks/events, and cancel local tasks.

## Safety Policy

- Never send mutating requests directly from this skill's REST code path; live execution must go through `weex-trader-skill` guard commands.
- Never send mutating requests without explicit user confirmation that they authorize real account access and real order execution.
- Use this skill to create, confirm, store, evaluate, and run monitor tasks; use `weex-trader-skill` for any live REST call, profile lookup, vault operation, signing, or order submission.
- Do not import trader internals such as `weex_contract_api`, `weex_trade_guard`, or `weex_profile_store` into this skill's deterministic monitor script.
- Do not default to WEEX `closePositions(symbol)` for "close long" or "close short"; that endpoint cannot express `positionSide`.
- Directional close is mandatory. If a directional close cannot be represented or verified, report the trigger and ask for manual handling instead of submitting a live order.
- The first callback channel is `current_thread` only.

## Task DSL

```json
{
  "task_type": "position_pnl_monitor",
  "profile": "demo",
  "market": "futures",
  "symbol": "BTCUSDT",
  "position_side": "LONG",
  "frequency_seconds": 5,
  "condition": {
    "metric": "unrealized_pnl",
    "operator": ">",
    "threshold": "50"
  },
  "action": {
    "type": "market_close",
    "target": "LONG"
  },
  "callback": {
    "type": "current_thread"
  }
}
```

## Operating Rules

- A task starts as `draft`; `confirm-text` renders localized monitor confirmation text, including the required simple reply word (`确认` or `confirm`), writes the draft locally, and returns a `confirmation_token`.
- Always pass `--language zh` for Chinese user copy. Always pass `--language en` for English user copy when calling `confirm-text` or `confirm-text-live`; choose from the current user's language context, and do not rely on the script default language for user-facing confirmation copy.
- A task becomes `active` only after the caller passes both `--confirm-monitor` and the matching `--confirmation-token`; do not activate a task that has not first gone through `confirm-text`.
- PnL monitors default to `5` seconds and reject values below `3` seconds.
- Agent status reporting defaults to `60` seconds and rejects lower or non-whole-minute intervals. Pass `--reporting-interval-seconds` to `confirm-text-live` when the user asks for a different reporting cadence.
- Do not create local price-threshold monitors. WEEX official conditional orders should be used for price-based conditional closes.
- One task should trigger at most once. Persist the final task state and report the outcome.
- Treat missing fields, missing positions, zero size, degraded input, or ambiguous direction as non-executable.
- Local task state is stored in `monitor-tasks.sqlite3`; legacy `monitor-tasks.json` may only be read as a fallback.
- dry-run commands still write local SQLite task state and events so trigger handling, one-shot behavior, and reporting can be audited.
- `run-once` requires `--dry-run` and must never submit a live order.
- `run-loop --dry-run` consumes caller-supplied position snapshots and must never submit a live order.
- Live `run-loop` runs a bounded live loop for active PnL monitors. It uses the smallest active task `frequency_seconds` as the default sleep interval unless `--sleep-seconds` is provided, delegates every live account read and order action to `weex-trader-skill`, and requires explicit user authorization for real account access and real order execution.
- If the user asks to create and start a PnL monitor in one flow, prefer one combined confirmation summary over separate chat confirmations. The summary must include the exact matched live position, detailed live position snapshot fields, task DSL details, finite `duration_seconds` or absolute expiry time, real-account/order authority, and one-shot close behavior, and it must ask the user to reply with the localized simple confirmation word (`确认` for Chinese, `confirm` for English). Use `confirm-text-live` to collect the live position and render that summary; after the user replies with that word, call `confirm-and-run-loop` with the internal confirmation token, live-execution flag, and duration.
- After starting the live loop and verifying the task is still `active` or `executing`, create a runtime-native status reporting loop from returned `agent_reporting` metadata. In Codex, use `agent_reporting.runtimes.codex` or the backward-compatible `codex_reporting` / `reporting` metadata with `automation_update` to create a thread heartbeat. In Claude Code, use `agent_reporting.runtimes.claude_code` with the native `/loop` scheduled task capability and ask Claude Code what scheduled tasks exist before creating a duplicate. In OpenClaw, use `agent_reporting.runtimes.openclaw` with `openclaw cron add` for exact interval reporting; use `HEARTBEAT.md` only when its configured heartbeat cadence can satisfy the requested interval. Runtime-native reporting must report task status, condition, latest current value, threshold, trigger state, reason, close order, exchange response, or error.
- Do not output HTML entities such as `&lt;`, `&gt;`, or `&amp;` in runtime-native status reports. Render comparison operators as readable words in the response language, such as `less than` for `<`, so users do not see escaped symbols in automated monitor updates.
- If a runtime status reporter sees a terminal task state such as `completed`, `cancelled`, or `review_required`, it should report the final state and stop, cancel, pause, or remove its own recurring job when the host runtime allows it. If the current runtime has no Codex `automation_update`, Claude Code `/loop`, OpenClaw cron, or equivalent scheduled-task tool, treat `agent_reporting` / `codex_reporting` / `reporting` as advisory metadata and continue the monitor without automated status reporting. Do not emulate runtime automation with live WEEX actions, ad hoc shell loops, or unconfirmed order operations.
- `confirm-and-run-loop` is only for `position_pnl_monitor`, requires a matching monitor confirmation token from `confirm-text-live`, requires internal live-execution authorization, requires finite `--duration-seconds > 0`, converts duration to internal iterations from `frequency_seconds`, activates the task, then runs only that task through the bounded live loop.
- Triggered dry-runs may produce a live delegate plan, but that plan is only a summary for `weex-trader-skill` and is not an execution request.
- `run-live-once` requires internal live-execution authorization; it only runs active PnL tasks that still match a consumed monitor confirmation token, collects live account risk data through `weex-trader-skill`, evaluates active PnL monitors, re-collects and revalidates the target position before submission, atomically claims the task as `executing`, then executes the market close through `weex-trader-skill preview-order` and `confirm-order`.
- Live PnL execution writes `completed` and exchange response details only after trader guard returns a successful response; failed delegated commands write `review_required` plus a `live_order_failed` event instead of silently retrying.
- Execution claim is local SQLite active-to-executing compare-and-set. If another runner has already claimed the task, report `execution_already_claimed` and do not submit a duplicate order.
