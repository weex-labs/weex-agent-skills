---
compatibility: Requires Python for the bundled JSON analysis scripts; network access is optional and only needed when paired with trader-skill live collection workflows.
description: Use when the user wants read-only WEEX snapshot, fill, replay, profile, order-risk, or account-risk analysis from normalized JSON payloads.
metadata:
    local-path: /var/folders/25/vzbzcnfx6jx58c7c00nckb8r0000gn/T/weex-local-skills-qhr5kqcc/repo/skills/weex-analysis-skill
name: weex-analysis-skill
---
# WEEX Analysis Skill

Read `manifest.json` for routing rules. Open `file-index.json` only when you need file-level guidance.

This skill is read-only. It analyzes normalized replay, profile, order-risk, snapshot, and fill JSON payloads, but it never places orders, updates profiles, or changes vault state.
When trader-collected payloads include `trading_mode`, `environment`, or `account_scope`, preserve those fields and show the trading mode in user-facing analysis output. Use localized trading-mode labels, not environment labels and not account labels, for user-facing copy. For Chinese, use `模拟盘` and `真实盘`; for English, use `demo trading` and `real trading`. Keep raw `live` or `demo` only as preserved internal payload values.

## Core Entry Points

- `scripts/weex_analysis_cli.py`: analyze normalized account snapshots and fills
- `scripts/weex_analysis_prepare.py`: prepare large replay payloads before analysis

## Routing

- Exposure, leverage, concentration, and free-collateral review: use `analyze-snapshot`
- Execution recap, fee burden, realized PnL, and fill mix review: use `analyze-fills`
- Large replay payload simplification before behavioral review: use `scripts/weex_analysis_prepare.py prepare-replay`
- Behavioral replay review from normalized order/fill payloads: use `analyze-replay`
- Trade-by-trade episode review with highlights and pattern snapshot: use `review-trades`
- User trading profile from normalized replay/profile payloads: use `analyze-profile`
- Pre-order reminder generation from normalized order-risk payloads: use `analyze-order-risk`
- Current account-level risk review from normalized account-risk payloads: use `analyze-account-risk`
- Open `README.md` for overview and examples
- Open `references/snapshot-schema.md` and `references/analysis-playbook.md` when you need the accepted input shapes or review checklist

## Input Policy

- Prefer normalized JSON payloads over natural-language summaries
- If the user already has live data from `weex-trader-skill`, convert it into the accepted normalized JSON shape for the target analysis command before analysis
- Preserve and display `trading_mode`, `environment`, and `account_scope` from trader output; do not infer hidden live/demo state from profile names or symbols. If the payload lacks environment context and the user needs account-scope interpretation, ask for the source environment instead of guessing.
- `analyze-snapshot` can also read `account_snapshot.equity` and `account_snapshot.available_balance` when the payload comes from account-risk collection
- If a replay payload is too large to review comfortably, prepare it first with `scripts/weex_analysis_prepare.py prepare-replay`
- If the input shape is incomplete, ask only for the missing fields that materially affect the analysis
- Missing prices, leverage, or balance fields are acceptable; the script will emit partial analysis instead of guessing

## Safety Policy

- Do not use this skill to submit, amend, or cancel orders
- Do not infer hidden account state that is absent from the snapshot
- State clearly when the analysis is partial because required fields are missing
- Every user-facing analysis result must include the exact standard disclaimer below. Do not paraphrase it, shorten it, or omit it.

`Disclaimer: This result is generated solely from the current input data and is for reference only. It does not constitute any investment or trading advice. Please make your own independent judgment based on real-time data, official rules, and your own risk tolerance. Responsibility for related decisions and execution rests solely with the user.`
