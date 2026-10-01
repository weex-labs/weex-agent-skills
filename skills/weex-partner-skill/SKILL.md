---
name: weex-partner-skill
description: Use when the user wants WEEX Partner referral, commission, referral asset, direct-user trade, sub-agent, or referral relationship queries.
---

# WEEX Partner Skill

Use this skill as the natural-language entry point for the seven read-only WEEX Partner query capabilities. REST access, profile resolution, Vault credentials, signing, HTTPS, exact-host enforcement, and transport remain owned by `weex-trader-skill` through `scripts/weex_partner_cli.py`.

## Route First

- Partner data query: use this skill and one of the seven CLI operations below.
- Normal order, cancel, or conditional-order request: route to `weex-trader-skill`. A profile that can call Partner APIs can still trade normally. Preserve trader risk preview and the existing `--confirm-live` gate for live mutation.
- PnL, fills, exposure, or account-risk analysis: route to `weex-analysis-skill` after trader collects normalized live data.
- PnL monitoring: route to `weex-monitor-skill`, which delegates any live action to trader.
- Partner transfer creation or any other Partner write: explain that it is unsupported. Never translate it into the read-only Partner POST.
- UID-to-phone/email account identity checks are unsupported; do not downgrade them to `verify-referrals`.
- Cross-Partner queries are unsupported. Every result must remain within the authority of the Partner associated with the current saved profile.

Do not create a separate account category for this skill. Reuse the user's existing saved profile and Application Vault. If setup is missing, follow `weex-trader-skill` profile/Vault guidance; never ask for secrets in chat or argv.

Before a Partner query, use the trader-owned safe preflight command: `weex_partner_api.py preflight --profile <saved-profile> --language <zh|en> --pretty` (with the repository script path and OS-appropriate Python launcher). It refreshes the normal trader state files but returns only Partner-safe runtime, Vault, selected-profile identity, and `partner_production|partner_test` fields. Do not expose the general trader preflight payload in Partner-only tool transcripts because it contains saved profile routing metadata that is not needed for the query.

When the user explicitly identifies a saved profile in the current conversation, reuse that profile for later Partner turns in the same conversation. Do not ask for it again unless the user changes it or the reference is ambiguous. A profile is missing only when no unique saved-profile reference exists in the current conversation, not merely because the latest sentence omits it. Apply the same conversation-aware check before asking for UID scope, product type, time, or other required fields, and ask only for fields still missing after current-turn and prior-turn context are combined.

If no unique saved-profile reference exists and trader finds multiple usable profiles, use trader's numbered profile-choice rule before Partner preflight: show only the profile display names as a numbered list and ask the user to reply with the number or exact profile name. Make it a standalone account-selection question rather than combining it with UID, scope, product, or time questions. Do not choose a profile on the user's behalf. After a valid selection, reuse it under the conversation rule above.

## Operations and Chinese intent mapping

- `list-referral-uids`: referral UID list.
- `get-direct-trade-asset`: direct-user trading and funding statistics, including deposits, withdrawals, spot/futures volume, and commission.
- `get-commission`: commission records.
- `get-sub-agent-stats`: sub-agent volume, fees, and commission.
- `verify-referrals`: direct-referral relationship verification; determine only whether a UID is a direct referral of the current Partner.
- `get-referral-assets`: direct-user asset snapshots.
- `get-referral-deal-data`: direct-user deal statistics for subordinate spot/futures trading only; exclude deposits, withdrawals, and commission.

When an ambiguous Chinese direct-user trading-statistics phrase does not distinguish funding from deal volume, use the requested fields to disambiguate: funding/deposit/withdrawal/commission intent maps to `get-direct-trade-asset`; deal-volume-only intent maps to `get-referral-deal-data`. Ask one focused question if those fields are not clear.

Open `references/partner-query-policy.md` before constructing a request. Pass structured JSON by stdin or a non-secret request file to `scripts/weex_partner_cli.py <operation>`. Do not call Partner REST directly from this skill.

Use `references/natural-language-regression.json` as the executable host-dialogue contract when adding or changing intent, clarification, or rejection wording. Route outcomes are limited to the seven operations above. Clarification and rejection fixtures must not send a Partner REST request.

## Required Gates

- Require a saved profile reference from the current conversation. Let trader load credentials from Vault.
- Missing UID must never silently mean all referrals. Require `scope.mode=all` and `all_confirmed=true` for an all-referrals query.
- Ask only for missing required fields, such as `product_type`, UID, or explicit start/end.
- When the official contract supplies multiple legal ranges and time is omitted, use its smallest range. State the default basis and actual UTC start/end. Explicit valid user time takes priority.
- If only a maximum/history limit is known and no minimum is published, require explicit start/end.
- A bounded explicit `get-commission` range longer than three calendar months is the PRD exception: query only its latest three-calendar-month segment, then offer the exact returned earlier-time action until the user's original start is reached. Commission continuation actions are closed millisecond ranges; the earlier segment ends one millisecond before the current segment starts. Do not infer an unbounded history limit; an upstream rejection stops fail-closed.
- Test-only exception: after trader preflight or a prior response has verified the same profile as `partner_test`, pass `expected_environment=partner_test`. For `get-sub-agent-stats` only, omitted time then uses `source=partner_test_upstream_default` and sends documented null time values. Never use this exception for production or claim that it enforces a bounded time range.
- Treat `all_confirmed` as the literal JSON boolean `true`; strings and numbers do not authorize an all-referrals query. Normalize every UID as a positive signed 64-bit decimal integer and reject comma-containing or nested values before REST execution.
- Accept only the documented filters for the selected operation. Reject unknown filter/top-level fields and invalid language, coin, product, or scope combinations instead of silently dropping them.
- Default to `summary_with_first_20`. An all-referrals scope does not imply `complete_list` or `aggregate_all`: the Chinese all/every/list wording cataloged in `references/natural-language-regression.json` authorizes only `scope.mode=all`. Fetch all server pages only when the user explicitly asks for a complete list, all details, a total, or an aggregate; otherwise fetch the current server page, show at most the first 20 records, and use the returned continuation for more displayed records or the next server page.
- Treat `complete`, `partial`, and pagination fields as authoritative. Never convert a partial aggregate into a total.
- Reuse the returned continuation object and one exact `continuation.actions[].request_patch`; it binds the resolved profile, scope, operation, filters, result mode, contract version, and actual time range. Never reconstruct or edit those bindings from memory.
- A repeated continuation is still read-only, but explicitly warn that it may repeat or overwrite coverage for the same page or time segment.
- Before applying any continuation action, explain every `continuation.usage_warnings` code. The warning set is part of continuation integrity and must match the returned action types exactly. `continuation_reuse_may_repeat_or_overwrite` means the same action can repeat/overwrite perceived coverage; `offset_pagination_data_may_change` means records or totals can change between pages or after a page-1 restart.
- Stop before another page or UID batch when any returned remaining weight bucket is lower than the endpoint weight. Preserve the rate-limit metadata and never retry automatically.

The trader executor allows only seven `operation_class=read` entries. It uses the exact production default or a strictly validated saved `https://*.weex.tech` test subdomain, while rejecting the bare suffix, API base environment overrides, authenticated redirects, all Partner write endpoints, unknown endpoints, and credential disclosure. Partner query output exposes only `partner_production` or `partner_test`, never the concrete test origin. The query-only POST is not a mutation.

## Explain Results

Open `references/partner-output-schema.md` and `references/partner-field-catalog.json`. The catalog is the field-name and field-meaning authority for all seven operations. For every known business value, use `official_description_zh` for Chinese output or `official_description_en` for English output, preserving the official wording verbatim, and retain the original field name. Format this as `official description (original field name)`. Never infer, shorten, or freely translate a field meaning from its spelling.

Keep official wire fields separate from declared internal aliases. `isRefferal` is the official response spelling; `is_referral` is only the normalized output name. Do not present an internal alias as an official API field.

The official English table defines `unimarginTotalUsdt` as `Total contract account equity in USDT`; use the corresponding `official_description_zh` for Chinese output and never call it unified-account total assets. `contractTotalUsdt` is currently undocumented in both official response-parameter tables, so keep it under `unknown_response_fields` and hide its value. In the user's language, start with the query scope and actual UTC range, then present:

`environment` and `capability_mode` remain structured identity and safety metadata. They are not a standalone fixed prefix. Mention the returned Partner environment only when it materially affects interpretation, such as `partner_test_upstream_default`, or when explaining an environment-binding or safety failure. Never display the concrete test origin or claim that the API key itself is read-only.

If `environment=null` because a local failure happened before resolution, explain with the error details that the Partner environment was not resolved and no Partner request was sent.

1. Profile identity without credentials, query scope, and actual UTC range.
2. Complete or partial coverage.
3. Summary and up to the first 20 records by default.
4. If the display is incomplete: `has_more`, remaining count or next page when available, and the exact way to continue.
5. Every returned warning and its user impact.
6. Rate-limit/error category and a safe recovery action.
7. The returned Partner environment (`partner_production` or `partner_test`) only when it materially changes interpretation or explains a safety failure.

Explain every returned warning. In particular, `cross_segment_aggregate_not_combined` means no whole-range aggregate was produced, while `cross_segment_results_not_combined` means the response covers only the current segment; never silently omit either warning.

For a successful empty result, say only that the current query range returned no records. Never infer that the user never traded, never deposited, or never transferred funds.

For Chinese output, convert returned millisecond timestamps to `YYYY-MM-DD HH:mm:ss（UTC）` and date-only source fields to `YYYY-MM-DD（UTC）`. For English output, use `YYYY-MM-DD HH:mm:ss (UTC)` and `YYYY-MM-DD (UTC)`. Never invent a time of day. For relationship checks, `is_referral=false` means only that the UID is not a direct referral of the current Partner; never infer that the account does not exist or identify another Partner.

Unknown response field values, including nested values under otherwise known containers, stay hidden. A nested value in a documented scalar field is a schema failure; only documented container fields such as referral-asset `depositList` may be summarized as hidden count metadata. A 429 or insufficient remaining weight stops immediately with no automatic retry. Interrupted offset pagination must restart from page 1 for any complete result, and the user-facing result must warn that data may change while pages are being fetched or after a restart.

The Skill does not persist Partner business responses itself. The host chat or tool transcript can still retain query inputs and outputs; do not promise automatic retention, access, or deletion behavior on behalf of Codex, Claude Code, Cursor, GitHub Copilot, or OpenClaw.
