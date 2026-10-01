# Snapshot Schema

The analysis CLI accepts JSON from a file or stdin.

## Snapshot Input

Use an object with optional top-level balance fields plus a `positions` array:

```json
{
  "equity": 2500,
  "available_balance": 700,
  "positions": [
    {
      "symbol": "BTCUSDT",
      "side": "long",
      "quantity": 0.02,
      "entry_price": 62000,
      "mark_price": 63500,
      "leverage": 8,
      "unrealized_pnl": 30
    }
  ]
}
```

The snapshot analyzer also accepts the account-risk wrapper shape below when balances are nested under `account_snapshot`:

```json
{
  "account_snapshot": {
    "equity": 2500,
    "available_balance": 700
  },
  "positions": [
    {
      "symbol": "BTCUSDT",
      "side": "long",
      "quantity": 0.02,
      "notional": 1270,
      "leverage": 8
    }
  ]
}
```

Accepted aliases include:

- quantity: `quantity`, `qty`, `size`, `position_size`, `positionAmt`
- entry price: `entry_price`, `entryPrice`, `avgEntryPrice`
- mark price: `mark_price`, `markPrice`, `current_price`, `last_price`
- unrealized PnL: `unrealized_pnl`, `unrealizedPnl`, `upl`
- available balance: `available_balance`, `availableBalance`, `free_collateral`

## Fill Input

Use either a bare array or an object with a `fills` array:

```json
{
  "fills": [
    {
      "symbol": "ETHUSDT",
      "side": "sell",
      "quantity": 0.3,
      "price": 3050,
      "realized_pnl": 42,
      "fee": 3.8
    }
  ]
}
```

Accepted aliases include:

- quantity: `quantity`, `qty`, `size`
- realized PnL: `realized_pnl`, `realizedPnl`, `pnl`, `profit`
- fee: `fee`, `fees`, `commission`

## Replay / Profile Input

Use an object emitted by the trader aggregation layer:

```json
{
  "analysis_type": "replay",
  "market": "futures",
  "period": "30d",
  "closed_trade_count": 12,
  "orders": [],
  "fills": [],
  "balances": [],
  "positions": [],
  "bills": [],
  "price_series": [],
  "constraints": [],
  "partial": false,
  "degraded_reasons": []
}
```

Large replay datasets can be prepared first with `scripts/weex_analysis_prepare.py prepare-replay`.
The prepared payload keeps the same top-level replay schema, preserves auxiliary collections when present, and optionally truncates orders or fills for downstream analysis.

Profile payloads reuse the same schema and add:

- `analysis_type = "profile"`
- `selected_period`
- `fallback_applied`
- `metrics`
- `sample_quality`
- replay/profile metrics may include `episode_net_pnl`, `bill_adjustment_total`, and bill-adjusted `net_pnl`
- profile `risk_score` is derived from replay behavior metrics; it intentionally ignores the current `balances` / `positions` snapshot

When `metrics` is absent but replay rows are present, `analyze-profile` derives core profile metrics from the replay payload instead of returning an empty metrics block.

## Order-Risk Input

Use an object with an `order_preview` plus account and market context:

```json
{
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

## Account-Risk Input

Use an object with account context but without an `order_preview`:

```json
{
  "mode": "account_scan",
  "market": "futures",
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
