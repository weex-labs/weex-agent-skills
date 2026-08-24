# Script Operations

Use this reference only when direct local script execution, dependency setup, or repo maintenance is needed instead of the normal natural-language skill flow.

## Python Prerequisites

Profile, vault, private-trading, and API-definition regeneration commands require the hashed dependencies in [requirements.lock](../requirements.lock).

```bash
# Windows
py -3 -m pip install --require-hashes -r requirements.lock

# macOS / Linux
python3 -m pip install --require-hashes -r requirements.lock
```

Before private contract or spot commands, run `scripts/weex_agent_state.py --command skill.preflight ...` and inspect `runtime.host.requirements_ready`, `runtime.host.missing_modules`, and `runtime.env_validation`. The private REST CLIs now stop immediately when those checks fail instead of waiting until profile or order execution.

One-command runtime setup:

```bash
# Windows
py -3 scripts/weex_runtime_setup.py --pretty

# macOS / Linux
python3 scripts/weex_runtime_setup.py --pretty
```

This helper installs `requirements.lock` with hash verification into the current interpreter, attempts `ensurepip` first if `pip` is missing, refreshes `agent-init.json` / `agent-runtime.json`, and reports whether the interpreter is actually ready for private WEEX CLI flows.

Private contract and spot CLIs also auto-attempt this helper when the current interpreter is missing required Python dependencies. Invalid runtime overrides such as a bad `WEEX_API_TIMEOUT` value still stop immediately because the helper does not modify environment variables for you.

Command launcher policy:

- Windows: use `py -3`
- macOS / Linux: use `python3`
- GUI profile management also needs `tkinter`
- On macOS and Windows tool-managed shells, use `scripts/weex_gui_launcher.py` for detached GUI launch after the managed GUI runtime is ready; the launcher verifies the managed runtime and uses it for the child process

## Managed GUI Runtime

On Windows and macOS, the GUI entrypoints must use an explicitly prepared managed Python runtime even when the current interpreter can initialize Tk and has GUI-side dependencies. They do not download or install that runtime implicitly. If an AI assistant sees `explicit_setup_required`, it should explain the pinned uv/Python setup plus checksum/hash verification, ask whether it should install the runtime, and run the `ensure --accept-managed-runtime --pretty` command only after clear confirmation.

Manual repair commands:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_gui_bootstrap.py probe --pretty
python3 scripts/weex_gui_bootstrap.py ensure --accept-managed-runtime --pretty
python3 scripts/weex_doctor.py gui
```

Notes:

- the bootstrap stores a user-local runtime under the WEEX config directory such as `~/.weex-trader-skill/gui-runtime`
- explicit `ensure --accept-managed-runtime` downloads a pinned uv installer, verifies its SHA256, provisions a managed CPython 3.12.13 virtual environment, and installs `requirements.lock` with hash verification
- user-facing AI flows should offer to perform this command after confirmation instead of requiring non-technical users to copy and run it themselves
- the profile and vault GUI entrypoints will automatically re-launch themselves inside that managed runtime when they are started directly from a non-managed interpreter
- this managed bootstrap is for the Windows/macOS GUI flows; terminal/private REST commands still run on the interpreter you launched and therefore still need their own preflight/runtime checks
- the profile and vault GUI entrypoints also auto-detach when they are started from a non-interactive/tool-managed shell on macOS or Windows
- explicit detached launch uses a transient `.app` wrapper on macOS and prefers `pythonw.exe` or another hidden background process on Windows
- detached-launch records and logs are stored under `~/.weex-trader-skill/gui-launchers`; the launcher keeps only recent records and trims each `.log` file to 256 KiB
- `scripts/weex_agent_state.py --command skill.preflight ...` only reports when explicit managed-runtime setup is required; it does not download or install runtime files
- use `WEEX_GUI_RUNTIME_DISABLE=1` only when you explicitly want to suppress the bootstrap path
- use `WEEX_GUI_FORCE_FOREGROUND=1` only when you explicitly want the GUI to stay attached to the current shell, which can reintroduce a Terminal/cmd window

Detached GUI launch examples:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_gui_launcher.py profile-manager --language zh --pretty
python3 scripts/weex_gui_launcher.py vault-manager --language zh --requested-action setup --pretty
```

Vault `--requested-action` values:

- `setup`: open the vault UI focused on initialization; if the vault is currently uninitialized, the window immediately starts the passphrase flow
- `unlock`: open the vault UI focused on unlocking; if the vault is currently locked, the window immediately starts the passphrase flow
- `status`: open the vault UI focused on reviewing the current state only; it does not unlock or lock by itself
- `lock`: open the vault UI focused on the lock workflow; it does not lock by itself until the user presses the button in the window

Windows/macOS vault command routing:

- `python3 scripts/weex_vault.py` with no subcommand opens the vault UI
- bare `setup` and bare `unlock` also open the vault UI by default
- `status`, `lock`, `mode`, `change-password`, and any command that includes extra CLI flags stay in the terminal unless you explicitly use `scripts/weex_gui_launcher.py vault-manager ...`
- use `--cli` when you explicitly want the terminal flow for `setup` or `unlock` on Windows/macOS

## Command Context

Run the shell commands below from the skill root.

If you stay outside the skill root, prefix repo-relative paths with the full skill path. For example:

```text
py -3 E:\path\to\weex-trader-skill\scripts\weex_spot_api.py --help
python3 /path/to/weex-trader-skill/scripts/weex_spot_api.py --help
```

The examples below are written as single-line commands so they can be pasted into PowerShell, bash, or zsh without changing the line continuation style.

## Quick Start

Public market data works without any API credentials:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_contract_api.py ticker --symbol BTCUSDT --pretty
python3 scripts/weex_spot_api.py ticker --symbol BTCUSDT --pretty
```

List bundled endpoints:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_contract_api.py list-endpoints --pretty
python3 scripts/weex_spot_api.py list-endpoints --pretty
```

List only the official simulated futures endpoints:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_contract_api.py list-endpoints --group sim --pretty
```

## Trading Commands

Representative futures order:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_contract_api.py --profile main place-order --symbol ETHUSDT --side SELL --position-side SHORT --type LIMIT --quantity 0.001 --price 10000 --time-in-force GTC --confirm-live --pretty
```

Current contract constraints from the official endpoint pages:

- LIMIT orders support `GTC`, `IOC`, `FOK`, and `POST_ONLY` as `timeInForce` values.
- `transaction.place_orders_batch` accepts at most 5 orders in one request.
- `account.update_leverage_trade` requires at least one of `crossLeverage`, `isolatedLongLeverage`, or `isolatedShortLeverage`.
- `market.get_funding_rate_history` accepts a maximum start/end span of 7 days.
- `transaction.get_trade_details` defaults to the latest 7 days when both timestamps are omitted, accepts at most 7 days per request, and supports only trades from the past 365 days.
- For `transaction.place_tp_sl_order`, `quantity` set to `0` or omitted means TP/SL for the full position. Do not treat an omitted quantity as missing input for an explicit full-position request, and state the full-position effect in the confirmation.

Full-position TP/SL preview using the explicit `0` form:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_trade_guard.py preview-tp-sl --profile main --trading-mode live --tp-sl-json '{"symbol":"BTCUSDT","clientAlgoId":"tp-full-1","planType":"TAKE_PROFIT","triggerPrice":"70500","executePrice":"0","quantity":"0","positionSide":"LONG","triggerPriceType":"MARK_PRICE"}' --language en --pretty
```

Only after the user gives the exact confirmation returned by that preview, submit the bound intent with `confirm-tp-sl --trading-mode live --intent-id <intent_id> --risk-signature <risk_signature> --confirm-live`.

Representative simulated futures order:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_contract_api.py --profile main place-order --symbol ETHUSDT --side SELL --position-side SHORT --type LIMIT --quantity 0.001 --price 10000 --time-in-force GTC --trading-mode demo --confirm-demo --pretty
```

The convenience wrapper accepts normal contract symbols such as `BTCUSDT` or `ETHUSDT` and maps them to the official simulated-order symbol shape before sending `sim.transaction.place_order`. Raw `call --endpoint sim.transaction.place_order` expects you to provide the exact official request body yourself.

Simulated futures balance, position, and order-history reads use the `sim.*` endpoints and do not require a confirmation flag:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_contract_api.py --profile main call --endpoint sim.account.get_account_balance --trading-mode demo --pretty
python3 scripts/weex_contract_api.py --profile main call --endpoint sim.account.get_all_positions --trading-mode demo --pretty
python3 scripts/weex_contract_api.py --profile main call --endpoint sim.transaction.get_order_history --trading-mode demo --query '{"limit":50}' --pretty
```

For raw contract calls, `--profile is a global argument`; place it before `call`, then use `--endpoint <key>` for the official endpoint key. The client uses each endpoint's generated `request_transport` and rejects query fields sent in a documented JSON body or body fields sent in a documented query. `USER_DATA` POST endpoints are read-only queries and do not require a trade confirmation flag; `TRADE` endpoints still require the matching live or demo confirmation flag.

For simulated futures order history, omit `symbol` unless you are using an officially accepted simulated symbol filter for that endpoint. Querying without `symbol` avoids normal-symbol filters such as `BTCUSDT` being rejected by the demo history API.

Use `--dry-run` when you need to inspect the signed request without sending a mutating request:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_contract_api.py --profile main place-order --symbol BTCUSDT --side BUY --position-side LONG --type MARKET --quantity 0.001 --trading-mode demo --confirm-demo --dry-run --pretty
```

Representative spot order:

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_spot_api.py --profile main place-order --symbol ETHUSDT --side BUY --order-type LIMIT --quantity 0.001 --price 999 --time-in-force GTC --confirm-live --pretty
```

Current convenience wrappers:

- Spot: `ticker`, `place-order`
- Futures: `ticker`, `poll-ticker`, `place-order`, `cancel-order`

For broader spot or futures cancel/query/history flows, use the generic `call` command with the bundled endpoint catalogs.

## Aggregation And Trade Guard

Private normalized payloads accept `--trading-mode live|demo`. Demo mode is futures-only and uses the official `sim.*` balance, all-position, and historical-order endpoints. Missing simulated futures equivalents for fills, bills, open orders, conditional orders, and TP/SL state are reported through `partial=true` and `degraded_reasons`; the aggregator does not call live endpoints to fill those gaps.
When WEEX simulated endpoints return symbols in the simulated-order shape, normalized payloads map them back to the normal contract symbol shape, for example `BTCUSDT`, so downstream analysis and monitor matching stay consistent.

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_trade_data_aggregator.py collect-account-risk --profile main --market futures --trading-mode demo --symbol BTCUSDT --pretty
python3 scripts/weex_trade_data_aggregator.py collect-order-risk --profile main --market futures --trading-mode demo --order-json '{"symbol":"BTCUSDT","side":"BUY","position_side":"LONG","order_type":"MARKET","quantity":"0.001"}' --pretty
```

`weex_trade_guard.py` binds `trading_mode`, `environment`, profile, market, order preview, and alerts into the pending intent risk signature. Preview first, then confirm with the matching environment flag from the latest preview output.
Private account, order, cancel, TP/SL, and order-query outputs include `user_environment_prefix` whenever the command has environment context. Natural-language summaries must put that prefix on the first line before describing balances, positions, submitted orders, order status, or order history.

```bash
# Windows users: replace python3 with py -3
python3 scripts/weex_trade_guard.py preview-order --profile main --market futures --trading-mode demo --order-json '{"symbol":"BTCUSDT","side":"BUY","position_side":"LONG","order_type":"MARKET","quantity":"0.001"}' --language en --pretty
python3 scripts/weex_trade_guard.py confirm-order --trading-mode demo --intent-id <intent_id> --risk-signature <risk_signature> --confirm-demo --pretty
```

For live order confirmation, use `--trading-mode live --confirm-live`. A live intent cannot be confirmed with `--confirm-demo`, and a demo intent cannot be confirmed with `--confirm-live`.

## Automated Strategy Authorization

This is a saved-profile, real-trading-only JSON facade. It does not accept raw API credentials, demo trading, arbitrary state paths, direct database fields, or caller-supplied trusted risk/amount/exchange facts. Put one request object in a local JSON file or send it on stdin with `--input -`. Input is limited to 1 MiB, and duplicate JSON keys and unknown fields are rejected. `grant-authorization`, `submit-auto`, `resolve-auto-usage`, and `enable-auto-trading-after-restore` require `--confirm-live` for their live state transition.

Register a stable strategy identity. Reuse the returned `strategy_id` after a rename or restart; do not reuse it for a copied strategy:

```json
{"profile":"main","strategy_name":"btc-grid"}
```

```bash
python3 scripts/weex_auto_trade.py register-strategy --input @register-strategy.json --pretty
```

Request the complete V1.1 strategy scope. `trade_types` may contain `SPOT`, `FUTURES`, or both. `max_single_amount` constrains the conservative U estimate per leg, not the requested or actual fill amount. `max_total_amount` caps cumulative conservative investment during the validity period. `valid_hours` is required, has no default, and must be greater than zero and no more than `720`; all amount fields are Decimal strings:

```json
{"profile":"main","strategy_id":"<strategy_id>","trade_types":["SPOT"],"symbols":["BTCUSDT"],"all_symbols":false,"max_single_amount":"200","max_total_amount":"2000","valid_hours":"720"}
```

```bash
python3 scripts/weex_auto_trade.py ensure-authorization --input @ensure-authorization.json --pretty
```

Inspect the exact pending request before grant. The response shows the saved profile, masked strategy ID, modules, symbols, conservative per-leg maximum, cumulative quota, projected expiry, confirmation effect, revoke action, and local trust boundary:

```json
{"profile":"main","strategy_id":"<strategy_id>","request_id":"<request_id>"}
```

```bash
python3 scripts/weex_auto_trade.py show-authorization-request --input @show-authorization-request.json --pretty
```

After the user explicitly accepts that complete confirmation, bind the returned request and scope signature exactly:

```json
{"profile":"main","strategy_id":"<strategy_id>","request_id":"<request_id>","scope_signature":"<scope_signature>"}
```

```bash
python3 scripts/weex_auto_trade.py grant-authorization --input @grant-authorization.json --confirm-live --pretty
```

Granting is a local authorization mutation; it does not itself call WEEX. A strategy integrates by invoking `submit-auto` through this JSON CLI, passing the exact `strategy_id`, `authorization_id`, unique idempotency key, official operation key, and complete order legs. The facade constructs saved-profile/Vault clients and official risk, valuation, submission, and reconciliation boundaries internally. Caller-supplied trusted amounts, leverage, authorization status, risk results, exchange facts, and injected submitters are not accepted.

```json
{"profile":"main","strategy_id":"<strategy_id>","authorization_id":"<authorization_id>","idempotency_key":"strategy-order-0001","operation_key":"spot.order.place_order","orders":[{"symbol":"BTCUSDT","side":"BUY","type":"MARKET","quantity":"0.001"}]}
```

```bash
python3 scripts/weex_auto_trade.py submit-auto --input @submit-auto.json --confirm-live --pretty
```

The guard injects opaque client order IDs after all legs pass preflight and quota reservation. Spot quantity must be an exact Decimal multiple of official `stepSize` and remain within `minTradeAmount`/`maxTradeAmount`. Spot BUY checks the matching quote asset's available U value; Spot SELL checks the matching base asset's available quantity. Futures valuation uses `quantity * price * max(1, contractVal)` when the official quantity unit cannot be proven uniquely, then applies leverage and fee bounds; the result is a conservative upper-bound estimate, not exact exchange margin. The strategy must not write SQLite, calculate trusted quota with AI output, split a submitted group, retry a submitted request, or bypass manual fallback. Pre-submit hard failures produce a bound local `auto_fallback` intent and exception event without calling WEEX. A result of `REVIEW_REQUIRED` means a request may have reached WEEX; it never produces a manual retry intent.

The automatic operation catalog is limited to `spot.order.place_order`, `spot.order.bulk_order`, `transaction.place_order`, `transaction.place_orders_batch`, `transaction.place_pending_order`, and `transaction.place_tp_sl_order`. Authorization follows the official `SPOT`/`FUTURES` module. Full-position TP/SL (`quantity` `0` or omitted), unproven reduce-only semantics, incomplete risk or valuation facts, product/asset-rule failures, scope/quota failures, state conflicts, and unknown operations return to the normal preview/confirmation path before any WEEX write. Authorizations containing the removed expanded-scope fields are retained for audit after migration but fail with `AUTHORIZATION_SCOPE_REAUTHORIZATION_REQUIRED`; create and explicitly grant a new V1.1 scope instead of editing the database. A leg that is explicitly rejected and proven not created returns `RELEASED` with sanitized `error_code` (maximum 128 characters) and `error_message` (control whitespace normalized, maximum 512 characters); the same fields are stored in its `USAGE_RELEASED` event without the raw response. Error text is display evidence only and does not decide whether release is safe.

Operational inspection and stop commands:

```json
{"profile":"main","strategy_id":"<strategy_id>"}
```

```bash
python3 scripts/weex_auto_trade.py event-list --input @strategy.json --pretty
```

```json
{"profile":"main","strategy_id":"<strategy_id>","authorization_id":"<authorization_id>"}
```

```bash
python3 scripts/weex_auto_trade.py revoke-authorization --input @revoke-authorization.json --pretty
```

`revoke-authorization` blocks new reservations for that strategy and retains all six-table history. `retire-strategy` also revokes its active authorization and prevents new authorization requests for that strategy identity. Neither command cancels, retries, or amends an exchange order.

Accepted conservative quota and exchange fill facts are separate ledgers. `reconcile-auto-order` accepts only the local order identity; the facade resolves its saved profile, module, symbol, client ID, and WEEX order ID, then calls the bundled official read-only order/trade endpoints. It does not accept caller-supplied exchange status, fill, fee, or source fields.

```json
{"profile":"main","strategy_id":"<strategy_id>","auto_trade_order_id":"<auto_trade_order_id>"}
```

```bash
python3 scripts/weex_auto_trade.py reconcile-auto-order --input @reconcile-auto-order.json --pretty
```

Reconciliation records `COMPLETE`, `PARTIAL`, or `UNAVAILABLE` facts but never releases or increases accepted quota. If an archived Spot order returns `-2200` from current order detail, the read-only runtime searches official history orders and requires both the saved WEEX order ID and client order ID to match before reading `myTrades`; incomplete fill coverage or fee-asset evidence remains `PARTIAL`, and any non-unique match remains `UNAVAILABLE` at the facade. No reconciliation fallback calls a trading endpoint.

Public `submit-auto` legs, `event-list` usage payloads, and reconciliation responses keep the per-order conservative amount in `estimated_amount_u`. Authorization-level totals are grouped under `authorization_quota` as `consumed_amount_u`, `reserved_amount_u`, and `remaining_amount_u`; those public surfaces do not expose cumulative `accepted_amount_u` beside a per-order amount. Historical usage events are projected into the same public shape without rewriting their stored audit payload.

Use `event-list` as the durable per-order display source. Each facade call performs an immediate post-commit notification pass by default. A newly accepted submission also launches a detached, credential-free one-shot worker with only the summary notification key, owner-only state path, and UTC window end; it waits for that owner-scoped 60-second window to close, attempts the exact key once, and exits even when no later facade command runs. Exception events remain immediate. Set `WEEX_AUTO_TRADE_NOTIFICATION_MODE=disabled` only for controlled testing or an operator-approved no-notification environment. Worker launch or notification failure never changes the committed order or quota state; a claimed notification is recorded once and never retried.

## Regenerate Definitions

To rebuild local spot and futures REST definitions from the current WEEX V3 docs, including spot tax pages, current Partner rebate raw pages, and contract demo pages:

```bash
# Windows users: replace python3 with py -3
python3 scripts/generate_weex_api_definitions.py --product all
```

## Automatic Authorization Database Snapshot And Restore

Status: implemented V1 local operation through the saved-profile JSON facade. Do not manually copy, merge, or replace the database or edit the managed index as a substitute.

Automated-authorization state is currently supported on POSIX hosts only. On Windows, this feature fails closed because V1 does not yet create and verify an owner-only DACL; POSIX mode bits are not accepted as a Windows access-control proof. Other Trader profile, Vault, and REST workflows keep their documented Windows support.

### Snapshot

- The user must trigger each snapshot explicitly.
- The Trader state facade must use SQLite's transaction-consistent backup mechanism.
- The snapshot must be stored only in the Trader-managed owner-only `<config_dir>/snapshots/` directory. Callers select snapshots by Trader-generated `snapshot_id`; V1 must not accept an arbitrary destination path.
- Do not copy only the main SQLite file while WAL is active.
- Validate the supported schema/`user_version`, `integrity_check`, `foreign_key_check`, and business invariants before reporting success.
- The snapshot must not contain API keys, secrets, passphrases, Vault passwords, or raw private-account responses.
- Do not upload snapshots to cloud storage or synchronize them across machines automatically.
- V1 does not provide extra snapshot password encryption, a snapshot password, key storage, password/key rotation, password recovery, or cross-device key recovery. Count-based rotation of managed regular snapshots is defined separately below. Owner-only permissions are access controls, not encryption; do not describe the snapshot as encrypted or unreadable to an attacker who controls the same OS user or local files.

Create a snapshot with the default retention count of 10:

```json
{"profile":"main"}
```

```bash
python3 scripts/weex_auto_trade.py snapshot-state --input @snapshot-state.json --pretty
```

Choose a count from 1 through 100 by adding `"retention_count":3` to the request. The response contains the new immutable `snapshot_id`, its UTC creation time and managed relative path, `retention_status`, actual `retained_count`, warnings, and the complete current list of registered regular snapshots. Keep a returned `snapshot_id` when it may be used for restore; do not infer IDs from filenames or file mtimes.

### Snapshot Retention

- `snapshot-state` accepts an optional JSON `retention_count`. Omitting it uses `10`; only integers from `1` through `100` are valid. Invalid values are rejected without truncating, creating, or deleting files.
- Create the snapshot in a temporary file, complete the SQLite backup and all permission/schema/integrity/foreign-key/business-invariant checks, then atomically publish it. Never overwrite an older snapshot in place.
- Each Trader-managed index record contains exactly six fields: immutable `snapshot_id`, UTC `created_at`, managed `relative_path`, `database_schema_version`, `size_bytes`, and lowercase SHA-256 `sha256`. Size, digest, and the database's actual `user_version` must match before restore. Serialize snapshot creation and order managed regular snapshots by `created_at`, then `snapshot_id`; do not use file mtime or a caller-supplied filename.
- Delete the oldest managed regular snapshots only after the new snapshot is successfully published, until the number of managed regular snapshots is no greater than the requested retention count. A creation, validation, index, disk, or publish failure must delete zero old snapshots; do not delete an old snapshot first to free space.
- If deletion fails after publication, keep the new snapshot and every undeleted older snapshot, return `retention_status=INCOMPLETE` with the actual retained count and warning, and do not roll back the new snapshot, automatically retry deletion, or skip the failed oldest item to delete newer items.
- The active authorization database, its six-table history, preserved pre-restore database evidence, temporary/failed files, and unknown files do not count toward retention and must never be deleted by snapshot rotation.

### Restore

Restore accepts only an immutable ID returned by the facade, never a caller path:

```json
{"profile":"main","snapshot_id":"<snapshot_id>"}
```

```bash
python3 scripts/weex_auto_trade.py restore-state --input @restore-state.json --pretty
```

1. For every syntactically valid restore attempt, enable the kill switch before reading the index or resolving the snapshot ID. Unknown IDs, malformed indexes, unsupported versions, checksum failures, and later restore failures therefore leave automatic trading disabled.
2. Import the selected snapshot into a new temporary database in the active database directory. Do not overwrite the active database in place.
3. Validate owner-only permissions and the supported schema/`user_version`.
4. Run `integrity_check`, `foreign_key_check`, and all authorization business-invariant checks.
5. Prepare the restored database so snapshot ACTIVE authorizations cannot resume automatically. They require fresh user authorization.
6. Keep RESERVED and REVIEW_REQUIRED records for manual reconciliation. Do not release or consume them automatically.
7. Preserve the current database as isolated rollback and audit evidence. Never merge the two authorization or quota ledgers.
8. Atomically switch the active path only after every validation and restore-safety step succeeds.
9. Keep automatic trading disabled after the switch. A user must create fresh authorizations after the kill-switch timestamp for every strategy that should resume, manually resolve every restored RESERVED/REVIEW_REQUIRED record from verified evidence, and explicitly enable automatic trading before any automatic order can resume. Logically expired or pre-switch ACTIVE rows never satisfy this gate.
10. Never query, retry, cancel, amend, recreate, or otherwise act on exchange orders automatically as part of restore.

A successful response uses `status=STATE_RESTORED_DISABLED`, reports how many ACTIVE authorizations were revoked, how many pending requests were rejected, how many RESERVED/REVIEW_REQUIRED records need manual reconciliation, and the owner-only pre-restore evidence path. The persistent `auto-trade/automatic-trading.disabled` file blocks both single-leg and submission-group reservations. Do not delete or edit it manually. A newly created authorization does not clear this latch by itself. Before explicit enable, grant/ensure/list still show the truthful authorization `status=ACTIVE`, but `next_action` is `RESOLVE_AUTO_USAGE_AND_ENABLE_AUTO_TRADING_AFTER_RESTORE` while unresolved usage remains and `ENABLE_AUTO_TRADING_AFTER_RESTORE` after it is cleared. Only successful explicit enable changes it to `SUBMIT_ALLOWED`. A `SUBMISSION_STATE_UNCERTAIN` latch instead returns `INSPECT_AND_RECONCILE_MANUALLY`.

For each unresolved usage, run `resolve-auto-usage` with only the usage identity below. The facade performs the official read-only WEEX query itself; it rejects caller-supplied outcome, evidence source, and order ID fields. Only a complete, unique result bound to the local usage and order mapping can resolve the record. A timeout, disconnect, ambiguous result, mismatched client/order ID, or unsupported Futures client-ID lookup remains `REVIEW_REQUIRED`.

```json
{"profile":"main","strategy_id":"<strategy_id>","usage_id":"<usage_id>"}
```

```bash
python3 scripts/weex_auto_trade.py resolve-auto-usage --input @resolve-auto-usage.json --confirm-live --pretty
```

The official provider chooses `ACCEPTED` only when the matching WEEX order is proven and chooses `RELEASED` only when the matching order is definitively absent; those fields are never accepted from JSON. After all unresolved records are handled, at least one post-switch authorization is ACTIVE, and no pre-switch or logically expired authorization remains ACTIVE, explicitly clear the restore latch:

```json
{"profile":"main"}
```

```bash
python3 scripts/weex_auto_trade.py enable-auto-trading-after-restore --input @enable-auto-trading.json --confirm-live --pretty
```

### Restore Failure

- Keep the current active database unchanged or roll back to the preserved current database.
- Return `STATE_CONFLICT` with the failed validation or replacement stage.
- Keep the kill switch enabled, including when the index is malformed or the requested snapshot ID is unknown.
- Do not delete history, the source snapshot, or failure evidence automatically. The only automatic deletion allowed by this V1 design is the post-publication count-based rotation of managed regular snapshots described above.
- Do not fall back to stateless automatic trading.
- Require the user to correct the file or version problem and explicitly retry the restore.
