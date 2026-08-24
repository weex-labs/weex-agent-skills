# Trade Data Schema

The trader aggregation layer emits normalized JSON for replay, profile, order-risk, and account-risk payloads.

## Replay Payload

```json
{
  "analysis_type": "replay",
  "trading_mode": "live",
  "environment": {
    "trading_mode": "live",
    "label": "live",
    "market": "futures",
    "uses_real_funds": true,
    "notice": "This operation targets real WEEX futures trading."
  },
  "market": "futures",
  "period": "30d",
  "symbol": "BTCUSDT",
  "focus": "losses",
  "time_range": {
    "start_ms": 1710000000000,
    "end_ms": 1712592000000
  },
  "closed_trade_count": 12,
  "orders": [],
  "fills": [],
  "positions": [],
  "balances": [],
  "bills": [],
  "price_series": [],
  "constraints": [],
  "partial": false,
  "degraded_reasons": []
}
```

Notes:

- `market`: `futures`, `spot`, or `all`
- `trading_mode`: `live` for real WEEX trading, or `demo` for WEEX futures demo mode
- `environment`: user-facing environment metadata for private-account payloads; callers should display it instead of inferring environment from profile names or base URLs
- `period`: replay accepts `7d`, `30d`, `90d`; profile fallback windows may also use `180d` or `360d`
- `constraints`: explicit limits such as `spot_symbol_required`
- `partial`: whether the aggregation layer could not prove the dataset is complete for the requested window
- `degraded_reasons`: machine-readable reasons such as `spot_kline_window_unbounded`, `futures_fills_limit_hit`, `spot_tp_sl_state_unavailable`, `spot_history_skipped_without_symbol`, or `demo_futures_fills_unavailable`
- Futures bill pagination reads the next request cursor from `nextKey.nextKeyId` and `nextKey.nextKeyTime`. A repeated cursor produces `futures_bills_cursor_stalled`; an unusable cursor that cannot be recovered by splitting the time window produces `futures_bills_window_truncated`. Either condition sets `partial=true` so callers do not present incomplete bills as complete coverage.
- Futures historical pending orders use the official time-range request only; the aggregator does not send an undocumented `page` parameter. When the endpoint reaches its result limit, the requested range is recursively split. If the limit is still reached at the minimum split window, the collector returns the available records with `partial=true` and `futures_historical_pending_orders_window_truncated` in `degraded_reasons` instead of claiming complete coverage.
- Spot bill collection treats the requested `start_ms` / `end_ms` as an inclusive range. Because the official endpoint defines `after` and `before` as strict bounds, each internal window `[start, end]` is sent as `after=start-1` (omitted when `start=0`) and `before=end+1`; recursively split child windows therefore have no boundary gaps.
- `balances[*].account_scope`, `positions[*].account_scope`, `orders[*].account_scope`, `fills[*].account_scope`, and `bills[*].account_scope` default to `personal_futures` or `personal_spot`; simulated futures rows use `sim_futures`
- `positions[*].margin_type` is normalized from upstream `marginType` when available
- `positions[*].position_mode` is normalized from `positionMode` / `separatedMode`; `ONE_WAY` maps to `COMBINED`, `HEDGE` maps to `SEPARATED`
- Futures `positions[*].open_value` preserves official `openValue` (value at position opening), and `entry_price` is derived as `open_value / abs(quantity)` when possible. `open_value` is never used directly as current `notional`.
- Futures `positions[*].notional` and `mark_price` represent current position value and current price. They come from explicit current fields, a fresh market price, or—when side and unrealized PnL are available—the linear position relation `open_value + unrealized_pnl` for long positions and `open_value - unrealized_pnl` for short positions. If current value cannot be established, these fields remain `null` and risk analysis degrades instead of substituting the entry price.
- Futures `positions[*].margin_size` and `positions[*].liquidation_price` preserve official `marginSize` and `liquidatePrice`; liquidation price `0` remains a returned value and means the exchange currently reports no liquidation price because risk is low.
- `orders[*].margin_type`, `orders[*].position_mode`, `fills[*].margin_type`, and `fills[*].position_mode` are best-effort fields in Phase 1: they are preserved when upstream context already includes them, otherwise they remain `null`
- `market=all` without `symbol` runs in degraded mode in Phase 1: futures history is still collected, while spot symbol-specific history is skipped, `partial=true`, and the skip is surfaced through `constraints` plus `spot_history_skipped_without_symbol`

## Normalized Row Examples

### Position Row

```json
{
  "account_scope": "personal_futures",
  "market": "futures",
  "symbol": "BTCUSDT",
  "side": "long",
  "margin_type": "CROSSED",
  "position_mode": "COMBINED",
  "quantity": 0.01,
  "open_value": 620.0,
  "entry_price": 62000.0,
  "mark_price": 65000.0,
  "notional": 650.0,
  "unrealized_pnl": 30.0,
  "margin_size": 62.0,
  "liquidation_price": 54000.0,
  "leverage": 10.0,
  "created_time": 1710000000000,
  "updated_time": 1710003600000
}
```

### Order / Fill Context Fields

- `account_scope` is always present on normalized orders and fills
- `margin_type` and `position_mode` are nullable on historical orders/fills in the current minimal implementation because the WEEX history endpoints do not consistently expose that context on every row

## Profile Payload

`collect-profile` reuses the replay schema and adds:

- `analysis_type = "profile"`
- `selected_period`
- `fallback_applied`
- `sample_quality`

Notes:

- the trader skill does not embed downstream profile metrics in the payload
- profile collection starts from `30d` and only falls back through `90d`, `180d`, and `360d` when the selected replay payload has `closed_trade_count < 10`
- downstream analysis can derive profile metrics, risk scoring, and persona labels from the replay rows when needed
- profile payloads preserve `trading_mode` and `environment` from the replay payload

## Order-Risk Payload

```json
{
  "trading_mode": "demo",
  "environment": {
    "trading_mode": "demo",
    "label": "demo",
    "market": "futures",
    "uses_real_funds": false,
    "notice": "This operation targets WEEX futures demo mode."
  },
  "order_preview": {
    "market": "futures",
    "symbol": "BTCUSDT",
    "side": "BUY",
    "position_side": "LONG",
    "order_type": "LIMIT",
    "quantity": 0.01,
    "price": 65000
  },
  "tp_sl": {
    "has_take_profit": false,
    "has_stop_loss": false
  },
  "account_snapshot": {
    "equity": 1000,
    "available_balance": 250
  },
  "positions": [],
  "recent_orders": [],
  "open_orders": [],
  "conditional_orders": [],
  "market_snapshot": {
    "current_price": 64850
  },
  "partial": false,
  "degraded_reasons": []
}
```

Notes:

- `order_preview` mirrors the order that will later be submitted if the user confirms
- the pending order intent and risk signature bind `trading_mode`, `environment`, profile, market, order preview, and alerts so a demo preview cannot be confirmed as live and a live preview cannot be confirmed as demo
- `recent_orders` is used to detect short-window overtrading
- `market_snapshot.current_price` is the price anchor for limit-price distance checks
- futures order-risk payloads can include `conditional_orders` so `missing_tp_sl` is based on live protection state instead of only the order preview
- demo futures order-risk payloads use official `sim.*` balance, all-position, and order-history endpoints; missing demo equivalents for open orders, conditional orders, and TP/SL state are reported as degraded data

## Account-Risk Payload

```json
{
  "mode": "account_scan",
  "trading_mode": "demo",
  "environment": {
    "trading_mode": "demo",
    "label": "demo",
    "market": "futures",
    "uses_real_funds": false,
    "notice": "This operation targets WEEX futures demo mode."
  },
  "market": "futures",
  "symbol": "BTCUSDT",
  "account_snapshot": {
    "equity": 1000,
    "available_balance": 120
  },
  "positions": [],
  "recent_orders": [],
  "open_orders": [],
  "conditional_orders": [],
  "market_snapshot": {
    "current_price": 64850
  },
  "partial": false,
  "degraded_reasons": [],
  "constraints": []
}
```

Notes:

- `mode = account_scan` is intentionally separate from pre-order risk payloads
- account-risk payloads do not require `order_preview`
- demo futures account-risk scans are read-only and do not require a confirmation flag, but the result still carries `trading_mode` and `environment`

## Simulated Futures Scope

`trading_mode=demo` is supported only with `market=futures`. The trader skill routes demo balance, all-position, order-history, and order-placement calls to the official `sim.*` contract endpoints. It does not use live futures endpoints to fill missing demo data.

Convenience order and guard flows accept normal contract symbols such as `BTCUSDT` and convert them to the official simulated-order symbol shape before submitting `sim.transaction.place_order`. Normalized rows convert simulated endpoint symbols back to the normal contract symbol shape so `positions[*].symbol`, `orders[*].symbol`, monitor matching, and downstream analysis consistently use symbols such as `BTCUSDT`.

For `sim.transaction.get_order_history`, a normal display symbol such as `BTCUSDT` is not sent as an upstream filter because the demo history API can reject it. The aggregator omits that upstream filter, paginates the returned demo history, then filters locally after normalizing official simulated symbols such as `BTCSUSDT` back to `BTCUSDT`. If the caller explicitly supplies an official simulated symbol, it remains eligible for upstream filtering.

Expected demo degraded reasons include:

- `demo_futures_fills_unavailable`
- `demo_futures_bills_unavailable`
- `demo_futures_open_orders_unavailable`
- `demo_futures_conditional_orders_unavailable`
- `demo_futures_tp_sl_state_unavailable`
