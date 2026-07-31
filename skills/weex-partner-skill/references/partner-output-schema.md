# WEEX Partner Output Schema

Every query returns one JSON envelope. The skill must preserve these values when explaining the result.

## Completion and identity

- `ok`: whether the current query succeeded.
- `complete`: whether the requested page/time coverage is complete.
- `partial`: whether usable records exist despite an interrupted query.
- `operation`, `profile.resolved_profile_id`, `api_domain`, `environment`, and `capability_mode` identify the source and read-only query capability. `environment` is `partner_production` or `partner_test` after REST execution and is `null` for a local failure before an environment is resolved; the concrete test origin is never part of this envelope.
- `environment` and `capability_mode` are not a standalone fixed prefix. Start normal user-visible output with scope, time, coverage, or results, and mention the environment only when it materially affects interpretation or explains an environment-binding or safety failure.
- When `environment=null` on a local pre-request failure, explain with the error details that the Partner environment was not resolved and no Partner request was sent.
- `query_scope` binds the UID or explicitly confirmed all-referrals scope.

## Time and pagination

- `time_range.source`, `actual_start`, and `actual_end` disclose the effective UTC range.
- `partner_test_upstream_default` means the verified test endpoint received null time values; `actual_start/actual_end` are null and no bounded coverage may be inferred. It requires `expected_environment=partner_test`, which trader verifies against the saved profile before credentials are loaded.
- `pagination.pages_fetched`, `pages_total`, `records_fetched`, and `records_total` disclose coverage.
- `pagination.displayed_count`, `has_more`, `remaining_count`, and `next_page` disclose truncation and the next available page. When the first page fails before total/page metadata is known, `has_more=null` and `remaining_count=null`; this means coverage is unknown, not that more records are known to exist. A boolean `has_more` is published only when totals or page metadata support it.
- `continuation.can_continue` and `stop_reason` explain whether and how to continue.
- Continuation binding fields include `resolved_profile_id`, `environment`, `query_scope`, `operation`, `contract_version`, `filters`, `result_mode`, and `time_range`. A changed or missing environment stops before records from another page or UID batch are merged.
- `continuation.actions[].request_patch` is the exact structured patch for more displayed records, another server page, or an earlier time range.
- `continuation.usage_warnings` is a machine-readable list that the host explains before applying an action. Its ordered contents must exactly match the continuation action types: every executable action requires `continuation_reuse_may_repeat_or_overwrite`, and display/page actions additionally require `offset_pagination_data_may_change`. Missing, extra, duplicated, or reordered warning codes fail local continuation validation.
- `continuation.source_pagination` is a structural snapshot used to derive the only valid display/page action; it is not a server snapshot or a cryptographic integrity token.
- Interrupted offset pagination sets `restart_required=true` and `restart_from_page=1`.

## Results and errors

- `summary` is present only when its source coverage is complete enough for that summary. Partial aggregates are never labeled as totals.
- Cross-call multi-segment queries do not persist trusted prior records or totals. Even the final segment remains incomplete for whole-range aggregation and returns no cross-segment summary. Aggregate mode emits `cross_segment_aggregate_not_combined`; other result modes emit `cross_segment_results_not_combined`.
- Aggregate display continuation re-fetches source pages from page 1, recomputes the aggregate, and applies only the returned display offset. It never resumes aggregation from the last server page.
- `records` contains at most 20 items by default and can contain a complete list only after an explicit request.
- Every item in `warnings` must be explained to the user, including its effect on coverage or interpretation. Warning values remain hidden; unknown-field warnings may report field names only.
- A successful empty result means only that the current query range returned no records. It never proves that the user never traded, never deposited, or never transferred funds.
- `error.category` distinguishes local validation, credentials, authentication, permission, rate limit, upstream, transport, schema, and completeness failures.
- `error.details.schema_issue` is present only when the Trader executor can safely classify a schema failure as `non_json_response` or `invalid_business_code_type`. Error details may also contain allowlisted field names, non-negative counts, hidden-value markers, and safe raw/value type names; they must not contain response values or arbitrary nested details.
- `rate_limit` preserves used and remaining WEEX weight headers. Automatic retry remains disabled.
- Chinese output renders millisecond response fields as `YYYY-MM-DD HH:mm:ss（UTC）` and date-only fields, including the sub-agent `date`, as `YYYY-MM-DD（UTC）`. English output uses `YYYY-MM-DD HH:mm:ss (UTC)` and `YYYY-MM-DD (UTC)`. Source date fields retain day granularity.
- `partner-field-catalog.json` is authoritative for official request/response spellings, types, bilingual descriptions, structural roles, and declared aliases. Chinese output uses `official_description_zh`; English output uses `official_description_en`. Preserve that official wording verbatim and append the original field name. Internal aliases never replace the original API field in an API-definition explanation.
- The official English description for `unimarginTotalUsdt` is `Total contract account equity in USDT`; its Chinese output uses the catalog's corresponding `official_description_zh`. `contractTotalUsdt` remains undocumented in both official response-parameter tables and hidden until they define it.
- Known containers with undocumented nested schemas expose only count/hidden metadata, not unknown nested values.
- A list member must be an object. Documented scalar fields reject list/object values with a schema error; referral-asset `depositList` is the only currently documented hidden container exception.
- Upstream error projection exposes only the stable category/code/message, the two allowlisted `schema_issue` values, safe field names, hidden-value markers, and safe raw type metadata. Arbitrary upstream `details` and values are never copied into the envelope.
