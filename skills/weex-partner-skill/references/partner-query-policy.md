# WEEX Partner Query Policy

This reference defines deterministic orchestration above the trader-owned REST executor.

## Operations

- `list-referral-uids`: one UID or explicitly confirmed all-referrals scope; 90-day official minimum/default segment, 365-day history boundary.
- `get-direct-trade-asset`: one UID or explicitly confirmed all-referrals scope; 90-day official minimum/default segment, 365-day history boundary.
- `get-commission`: one UID or explicitly confirmed all-referrals scope; official minimum/default is 7 days; `product_type` defaults to `SPOT`. A bounded explicit range longer than three calendar months starts with its latest three-calendar-month segment and continues only within that original range.
- `get-sub-agent-stats`: one sub-UID or explicitly confirmed all scope; `product_type` is required. Time is required for production. A profile already verified as `partner_test` may pass `expected_environment=partner_test` and omit time, producing `partner_test_upstream_default` with explicit null time values and no bounded-range claim.
- `verify-referrals`: one or more UIDs; groups larger than 100 are split without overlap.
- `get-referral-assets`: exactly one UID and explicit `YYYY-MM-DD` start/end dates.
- `get-referral-deal-data`: one or more repeated `userIds`, or explicitly confirmed all scope, plus explicit dates.

## Time rules

When official documentation lists legal ranges such as 30, 90, and 365 days and the user omits time, select the official minimum: 30 days in that example. Always return the actual UTC start/end and `source=official_minimum_default`. Explicit valid user time wins. If only a maximum or history limit is known, require explicit start/end instead of treating the maximum as a default.

Only endpoints with complete segment and history boundaries may offer an unbounded/default-history continuation. The next segment cannot overlap or leave a gap. A bounded explicit commission request is the PRD exception: its user-supplied start is the finite stop boundary, each segment is at most three calendar months, and any upstream history rejection stops fail-closed. Commission actions expose closed millisecond ranges: an earlier segment ends exactly one millisecond before the current segment starts. This preserves complete, non-overlapping coverage at API millisecond precision across month-end and leap-day boundaries. An unchanged earlier action that already passed continuation integrity is executed as the exact returned range rather than being segmented again as new user input. Sub-agent, asset, and deal-data queries do not auto-segment under the current contract.

## Scope and paging

An omitted singular UID never silently becomes all referrals. `scope.mode=all` requires the literal JSON boolean `all_confirmed=true`; strings and numbers are rejected. UIDs must be positive signed 64-bit decimal integers, with no comma encoding or nested values. Request objects and operation filters use strict allowlists; unknown fields, invalid language/product/coin values, and scope-less operation UID scopes fail before REST execution. All-referrals scope and result mode are orthogonal: “all/every/my referral list” authorizes the scope but does not imply a complete list or aggregate. Default output is a summary plus the first 20 records from the current server page. The continuation first exposes undisplayed records from that page and then the next server page when available. Cross-page fetching is reserved for an explicit complete-list or aggregate request. Offset pagination interrupted by an error restarts from page 1; old and new pages are never joined into a complete result.

Every continuation binds the resolved profile ID, Partner environment, expected environment, scope, operation, filters, result mode, contract version, original/actual time range, and a source-pagination snapshot. The snapshot binds current page, page size, total pages/records, records already covered, records on the current page, and display offset; the next action must be uniquely derivable from it. Unknown continuation fields or edited actions fail before REST. The executor environment must be `partner_production` or `partner_test`; a missing, invalid, or changed environment stops before records from another page or UID batch are merged and requires a page-1 restart. Apply exactly one returned `actions[].request_patch` while passing the continuation object back unchanged. Display continuation preserves the current server page and prior record coverage; complete-list and aggregate requests always restart at page 1.

A repeated continuation may query the same page or segment again. Keep the request read-only and warn that repeated continuation can duplicate or overwrite the caller's perceived coverage. Offset pagination has no stable snapshot: always disclose that data may change between pages and after a page-1 restart.

Every executable continuation carries `usage_warnings`. `continuation_reuse_may_repeat_or_overwrite` is present whenever an action can be applied. `offset_pagination_data_may_change` is additionally present for display/page actions and interrupted offset pagination. The warning list must exactly match the action-derived required set; a missing, extra, duplicated, or reordered code is rejected before REST. These warnings provide deterministic presentation input; they do not claim that the stateless CLI knows whether an action was already executed.

Before another page or UID batch, compare every available remaining-weight bucket with the endpoint weight. Stop fail-closed when any bucket is insufficient. Validate requested/current page sequence, duplicate page payloads and logical identities, stable `pages/total/pageSize`, final fetched record count, returned date coverage where the endpoint echoes dates, and exact UID coverage for relationship batches. Multi-page responses require `pageSize/size` from the first page. A strict `pages=0,total=0,records=[]` empty result is valid. A partial UID batch reports totals against the original request and the exact remaining UID count. Cross-call time continuation never carries trusted business totals, so a later segment must not be labeled as the whole-range aggregate.

If the first page fails before `records_total` and `pages_total` are known, publish `has_more=null` and `remaining_count=null`. Do not infer that records exist from `next_page=1` or from a restart instruction; `continuation.can_continue=false` remains authoritative until the user submits a fresh page-1 query.

All money aggregation uses decimal arithmetic and is published only when source coverage is complete. Unknown response field names may be reported for schema diagnosis, but their values, including nested values, remain hidden until classified.

## Natural-language regression matrix

Use these rows as behavior fixtures, not as an exhaustive phrase list. Reuse a profile selected in an earlier turn before treating the latest message as missing a profile. An all-referrals scope does not imply a complete list; only explicit complete-list, detail, total, or aggregate wording changes `result_mode`.

| Fixture ID | Expected routing or reason |
| --- | --- |
| `route_referral_uids` | `list-referral-uids` |
| `route_direct_trade_asset` | `get-direct-trade-asset` |
| `route_commission` | `get-commission` |
| `route_sub_agent_stats` | `get-sub-agent-stats` |
| `route_verify_referral` | `verify-referrals` |
| `route_referral_assets` | `get-referral-assets` |
| `route_referral_deal_data` | `get-referral-deal-data` |
| `clarify_ambiguous_trade_stats` | `funding_or_deal_volume_intent_required` |
| `clarify_missing_uid` | `uid_or_explicit_all_scope_required` |
| `clarify_sub_agent_product` | `product_type_required` |
| `reject_internal_transfer` | `partner_write_unsupported` |
| `reject_uid_contact_match` | `account_identity_check_unsupported` |
| `reject_cross_partner` | `cross_partner_query_unsupported` |
| `delegate_order_to_trader` | `normal_order_uses_trader_confirmation_gate` |
| `reject_fund_transfer` | `partner_skill_does_not_transfer_funds` |
| `reject_withdrawal` | `partner_skill_does_not_withdraw` |
