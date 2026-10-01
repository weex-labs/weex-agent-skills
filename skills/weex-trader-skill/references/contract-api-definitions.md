# WEEX Contract API Definitions

Generated from live V3 docs on 2026-04-17.

Contract simulated futures endpoints are maintained in this generated catalog from the official WEEX contract demo API docs.
Demo is not a local dry-run; demo mutating endpoints send requests to WEEX futures demo mode.

## Contents

- Summary table
- `account.*` endpoint sections
- `market.*` endpoint sections
- `sim.*` endpoint sections
- `transaction.*` endpoint sections

Use in-page search with the exact endpoint key from the summary table to jump to a specific generated section quickly.

## Summary Table

Total endpoints: **47**

| Key | Method | Path | Auth |
|---|---|---|---|
| `account.adjust_position_margin_trade` | `POST` | `/capi/v3/account/positionMargin` | `True` |
| `account.change_margin_mode_trade` | `POST` | `/capi/v3/account/marginType` | `True` |
| `account.get_account_balance` | `GET` | `/capi/v3/account/balance` | `True` |
| `account.get_account_config` | `GET` | `/capi/v3/account/accountConfig` | `True` |
| `account.get_all_positions` | `GET` | `/capi/v3/account/position/allPosition` | `True` |
| `account.get_commission_rate` | `GET` | `/capi/v3/account/commissionRate` | `True` |
| `account.get_contract_bills` | `POST` | `/capi/v3/account/income` | `True` |
| `account.get_single_position` | `GET` | `/capi/v3/account/position/singlePosition` | `True` |
| `account.get_symbol_config` | `GET` | `/capi/v3/account/symbolConfig` | `True` |
| `account.modify_auto_append_margin_trade` | `POST` | `/capi/v3/account/modifyAutoAppendMargin` | `True` |
| `account.update_leverage_trade` | `POST` | `/capi/v3/account/leverage` | `True` |
| `market.get_api_trading_symbols` | `GET` | `/capi/v3/market/apiTradingSymbols` | `False` |
| `market.get_book_ticker` | `GET` | `/capi/v3/market/ticker/bookTicker` | `False` |
| `market.get_contract_info` | `GET` | `/capi/v3/market/exchangeInfo` | `False` |
| `market.get_current_funding_rate` | `GET` | `/capi/v3/market/premiumIndex` | `False` |
| `market.get_depth_data` | `GET` | `/capi/v3/market/depth` | `False` |
| `market.get_funding_rate_history` | `GET` | `/capi/v3/market/fundingRate` | `False` |
| `market.get_history_klines` | `GET` | `/capi/v3/market/historyKlines` | `False` |
| `market.get_index_price_klines` | `GET` | `/capi/v3/market/indexPriceKlines` | `False` |
| `market.get_klines` | `GET` | `/capi/v3/market/klines` | `False` |
| `market.get_mark_price_klines` | `GET` | `/capi/v3/market/markPriceKlines` | `False` |
| `market.get_open_interest` | `GET` | `/capi/v3/market/openInterest` | `False` |
| `market.get_recent_trades` | `GET` | `/capi/v3/market/trades` | `False` |
| `market.get_server_time` | `GET` | `/capi/v3/market/time` | `False` |
| `market.get_symbol_price` | `GET` | `/capi/v3/market/symbolPrice` | `False` |
| `market.get_ticker24h` | `GET` | `/capi/v3/market/ticker/24hr` | `False` |
| `sim.account.get_account_balance` | `GET` | `/capi/v3/sim/balance` | `True` |
| `sim.account.get_all_positions` | `GET` | `/capi/v3/sim/position/allPosition` | `True` |
| `sim.transaction.get_order_history` | `GET` | `/capi/v3/sim/order/history` | `True` |
| `sim.transaction.place_order` | `POST` | `/capi/v3/sim/order` | `True` |
| `transaction.cancel_all_orders` | `DELETE` | `/capi/v3/allOpenOrders` | `True` |
| `transaction.cancel_all_pending_orders` | `DELETE` | `/capi/v3/algoOpenOrders` | `True` |
| `transaction.cancel_order` | `DELETE` | `/capi/v3/order` | `True` |
| `transaction.cancel_orders_batch` | `DELETE` | `/capi/v3/batchOrders` | `True` |
| `transaction.cancel_pending_order` | `DELETE` | `/capi/v3/algoOrder` | `True` |
| `transaction.close_positions` | `POST` | `/capi/v3/closePositions` | `True` |
| `transaction.get_current_order_status` | `GET` | `/capi/v3/openOrders` | `True` |
| `transaction.get_current_pending_orders` | `GET` | `/capi/v3/openAlgoOrders` | `True` |
| `transaction.get_historical_pending_orders` | `GET` | `/capi/v3/allAlgoOrders` | `True` |
| `transaction.get_order_history` | `GET` | `/capi/v3/order/history` | `True` |
| `transaction.get_single_order_info` | `GET` | `/capi/v3/order` | `True` |
| `transaction.get_trade_details` | `GET` | `/capi/v3/userTrades` | `True` |
| `transaction.modify_tp_sl_order` | `POST` | `/capi/v3/modifyTpSlOrder` | `True` |
| `transaction.place_order` | `POST` | `/capi/v3/order` | `True` |
| `transaction.place_orders_batch` | `POST` | `/capi/v3/batchOrders` | `True` |
| `transaction.place_pending_order` | `POST` | `/capi/v3/algoOrder` | `True` |
| `transaction.place_tp_sl_order` | `POST` | `/capi/v3/placeTpSlOrder` | `True` |

## Account Endpoint Sections

## account.adjust_position_margin_trade — Adjust Isolated Margin (TRADE)

- Method: `POST`
- Path: `/capi/v3/account/positionMargin`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `15 / 30`
- Source: https://www.weex.com/api-doc/contract/Account_API/AdjustPositionMarginTRADE

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `isolatedPositionId` | `Long` | `Yes` | Isolated position ID. Obtain via Get Single Position . |
| `amount` | `String` | `Yes` | Margin amount to adjust. Must be greater than 0. |
| `type` | `Integer` | `Yes` | Adjustment direction. 1 = increase isolated margin; 2 = decrease isolated margin. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `code` | `String` | Response code |
| `msg` | `String` | Response message |
| `requestTime` | `Long` | Timestamp Unix millisecond timestamp |

## account.change_margin_mode_trade — Change Margin Mode (TRADE)

- Method: `POST`
- Path: `/capi/v3/account/marginType`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `20 / 50`
- Source: https://www.weex.com/api-doc/contract/Account_API/ChangeMarginModeTRADE

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |
| `marginType` | `String` | `Yes` | Margin mode. Supported values: CROSSED (cross margin), ISOLATED (isolated margin). |
| `separatedType` | `String` | `No` | Position mode. COMBINED keeps both long and short in one position. SEPARATED splits positions. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `code` | `String` | Response code |
| `msg` | `String` | Response message |
| `requestTime` | `Long` | Timestamp Unix millisecond timestamp |

## account.get_account_balance — Get Account Balance (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/account/balance`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/contract/Account_API/GetAccountBalance

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `asset` | `String` | Asset name |
| `balance` | `String` | Total balance |
| `availableBalance` | `String` | Available balance |
| `frozen` | `String` | Frozen amount |
| `unrealizePnl` | `String` | Unrealized Profit and Loss |

## account.get_account_config — Get Account Configuration (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/account/accountConfig`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/contract/Account_API/GetAccountConfig

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `canTrade` | `Boolean` | Whether trading is enabled |
| `canDeposit` | `Boolean` | Whether deposits are enabled |
| `canWithdraw` | `Boolean` | Whether withdrawals are enabled |
| `dualSidePosition` | `Boolean` | Whether dual-side position mode is enabled true: Can hold both long and short positions simultaneously false: One-way position mode |
| `updateTime` | `Long` | Update time Unix millisecond timestamp |

## account.get_all_positions — Get All Positions (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/account/position/allPosition`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `10 / 15`
- Source: https://www.weex.com/api-doc/contract/Account_API/GetAllPositions

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `id` | `Long` | Position ID |
| `asset` | `String` | Associated collateral asset |
| `symbol` | `String` | Trading pair |
| `side` | `String` | Position direction such as LONG or SHORT |
| `marginType` | `String` | Margin mode of current position CROSSED: Cross Mode ISOLATED: Isolated Mode |
| `separatedMode` | `String` | Current position's separated mode COMBINED: Combined mode SEPARATED: Separated mode |
| `separatedOpenOrderId` | `Long` | Opening order ID of separated position |
| `leverage` | `String` | Position leverage |
| `size` | `String` | Current position size |
| `openValue` | `String` | Initial value at position opening |
| `openFee` | `String` | Opening fee |
| `fundingFee` | `String` | Funding fee |
| `marginSize` | `String` | Margin amount (margin coin) |
| `isolatedMargin` | `String` | Isolated margin |
| `isAutoAppendIsolatedMargin` | `Boolean` | Whether the auto-adding of funds for the isolated margin is enabled (only for isolated mode) |
| `cumOpenSize` | `String` | Accumulated opened positions |
| `cumOpenValue` | `String` | Accumulated value of opened positions |
| `cumOpenFee` | `String` | Accumulated fees paid for opened positions |
| `cumCloseSize` | `String` | Accumulated closed positions |
| `cumCloseValue` | `String` | Accumulated value of closed positions |
| `cumCloseFee` | `String` | Accumulated fees paid for closing positions |
| `cumFundingFee` | `String` | Accumulated settled funding fees |
| `cumLiquidateFee` | `String` | Accumulated liquidation fees |
| `createdMatchSequenceId` | `Long` | Matching engine sequence ID at creation |
| `updatedMatchSequenceId` | `Long` | Matching engine sequence ID at last update |
| `createdTime` | `Long` | Creation time Unix millisecond timestamp |
| `updatedTime` | `Long` | Update time Unix millisecond timestamp |
| `unrealizePnl` | `String` | Unrealized PnL |
| `liquidatePrice` | `String` | Estimated liquidation price If the value = 0, it means the position is at low risk and there is no liquidation price at this time |

## account.get_commission_rate — Get Commission Rate (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/account/commissionRate`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/contract/Account_API/GetCommissionRate

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair |
| `makerCommissionRate` | `String` | Maker fee rate e.g. 0.0002 means 0.02% |
| `takerCommissionRate` | `String` | Taker fee rate e.g. 0.0004 means 0.04% |

## account.get_contract_bills — Get Account Income (USER_DATA)

- Method: `POST`
- Path: `/capi/v3/account/income`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 5`
- Source: https://www.weex.com/api-doc/contract/Account_API/GetContractBills

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `asset` | `String` | `No` | Asset name |
| `symbol` | `String` | `No` | Trading pair |
| `incomeType` | `String` | `No` | Business type deposit : Deposit withdraw : Withdrawal transfer_in : Transfer between different accounts (in) transfer_out : Transfer between different accounts (out) margin_move_in : Collateral transferred within the same account due to opening/closing positions, manual/auto addition margin_move_out : Collateral transferred out within the same account due to opening/closing positions, manual/auto addition position_open_long : Collateral change from opening long positions (buying decreases collateral) position_open_short : Collateral change from opening short positions (selling increases collateral) position_close_long : Collateral change from closing long positions (selling increases collateral) position_close_short : Collateral change from closing short positions (buying decreases collateral) position_funding : Collateral change from position funding fee settlement order_fill_fee_income : Order fill fee income (specific to fee account) order_liquidate_fee_income : Order liquidation fee income (specific to fee account) start_liquidate : Start liquidation finish_liquidate : Finish liquidation order_fix_margin_amount : Compensation for liquidation loss tracking_follow_pay : Copy trading payment, pre-deducted from followers after position closing if profitable tracking_system_pre_receive : Pre-received commission, commission system account receives pre-deducted amount from followers tracking_follow_back : Copy trading commission refund tracking_trader_income : Lead trader income tracking_third_party_share : Profit sharing (shared by lead trader with others) |
| `startTime` | `Long` | `No` | Start timestamp Unit: milliseconds. If only endTime is provided, the system defaults startTime to 30 days before endTime (not earlier than current time). |
| `endTime` | `Long` | `No` | End timestamp Unit: milliseconds. If only startTime is provided, endTime defaults to the current time. When both are provided, the range must not exceed 100 days. |
| `limit` | `Integer` | `No` | Return record limit, default: 20 Minimum: 1 Maximum: 100 |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `hasNextPage` | `Boolean` | Whether there is a next page |
| `items` | `Array` | Data list |
| `> billId` | `Long` | Bill ID |
| `> asset` | `String` | Asset name |
| `> symbol` | `String` | Trading pair |
| `> income` | `String` | Amount |
| `> incomeType` | `String` | Income type |
| `> balance` | `String` | Balance |
| `> fillFee` | `String` | Transaction fee |
| `> time` | `Long` | Creation time Unix millisecond timestamp |
| `> transferReason` | `String` | Transfer Reason UNKNOWN_TRANSFER_REASON: Unknown transfer reason USER_TRANSFER: User manual transfer INCREASE_CONTRACT_CASH_GIFT: Increase contract cash gift REDUCE_CONTRACT_CASH_GIFT: Reduce contract cash gift REFUND_WXB_DISCOUNT_FEE: Refund WXB discount fee |

## account.get_single_position — Get Single Position (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/account/position/singlePosition`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 3`
- Source: https://www.weex.com/api-doc/contract/Account_API/GetSinglePosition

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |

### Response Parameters

NONE

## account.get_symbol_config — Get Symbol Configuration (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/account/symbolConfig`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/contract/Account_API/GetSymbolConfig

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair If not provided, all will be returned by default |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair |
| `marginType` | `String` | Margin mode CROSSED: Cross Mode ISOLATED: Isolated Mode |
| `separatedType` | `String` | Position segregation mode COMBINED: Combined mode SEPARATED: Separated mode |
| `crossLeverage` | `String` | Cross margin leverage |
| `isolatedLongLeverage` | `String` | Isolated long position leverage |
| `isolatedShortLeverage` | `String` | Isolated short position leverage |

## account.modify_auto_append_margin_trade — Modify Auto-Append Margin (TRADE)

- Method: `POST`
- Path: `/capi/v3/account/modifyAutoAppendMargin`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `15 / 30`
- Source: https://www.weex.com/api-doc/contract/Account_API/ModifyAutoAppendMarginTRADE

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `positionId` | `Long` | `Yes` | Isolated position ID |
| `autoAppendMargin` | `Boolean` | `Yes` | Whether to enable automatic isolated margin top-up |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `code` | `String` | Response code |
| `msg` | `String` | Response message |
| `requestTime` | `Long` | Timestamp Unix millisecond timestamp |

## account.update_leverage_trade — Update Leverage Settings (TRADE)

- Method: `POST`
- Path: `/capi/v3/account/leverage`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `10 / 20`
- Source: https://www.weex.com/api-doc/contract/Account_API/UpdateLeverageTRADE

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |
| `marginType` | `String` | `No` | Target margin mode. Supported values: CROSSED (cross margin), ISOLATED (isolated margin). |
| `crossLeverage` | `String` | `No` | Cross leverage to apply when marginType is CROSSED. |
| `isolatedLongLeverage` | `String` | `No` | Isolated long leverage. Required when updating the isolated long position. |
| `isolatedShortLeverage` | `String` | `No` | Isolated short leverage. Required when updating the isolated short position. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair |
| `marginType` | `String` | Applied margin mode CROSSED or ISOLATED |
| `crossLeverage` | `String` | Resulting cross leverage |
| `isolatedLongLeverage` | `String` | Resulting isolated long leverage |
| `isolatedShortLeverage` | `String` | Resulting isolated short leverage |

## Market Endpoint Sections

## market.get_api_trading_symbols — Get API Trading Symbols

- Method: `GET`
- Path: `/capi/v3/market/apiTradingSymbols`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `5 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetApiTradingSymbols

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbols[]` | `Array<String>` | Raw response is an array of futures symbols available for API trading. |

## market.get_book_ticker — Get Best Bid/Ask

- Method: `GET`
- Path: `/capi/v3/market/ticker/bookTicker`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetBookTicker

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair. Leave empty to return all trading pairs. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair |
| `bidPrice` | `String` | Best bid price |
| `bidQty` | `String` | Quantity available at the best bid price |
| `askPrice` | `String` | Best ask price |
| `askQty` | `String` | Quantity available at the best ask price |
| `time` | `Long` | Matching engine timestamp |

## market.get_contract_info — Get Exchange Information

- Method: `GET`
- Path: `/capi/v3/market/exchangeInfo`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetContractInfo

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair. Leave empty to return all supported contracts and assets. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `assets[]` | `Array<Object>` | Collateral assets list. |
| `assets[].asset` | `String` | Collateral asset symbol. |
| `assets[].marginAvailable` | `Boolean` | Whether the asset can be used as margin. |
| `symbols[]` | `Array<Object>` | Contract symbol configuration list. |
| `symbols[].symbol` | `String` | Contract trading pair symbol. |
| `symbols[].baseAsset` | `String` | Base asset symbol. |
| `symbols[].quoteAsset` | `String` | Quote asset symbol. |
| `symbols[].marginAsset` | `String` | Margin asset symbol. |
| `symbols[].pricePrecision` | `Integer` | Price precision. |
| `symbols[].quantityPrecision` | `Integer` | Quantity precision. |
| `symbols[].contractVal` | `Number` | Contract value. |
| `symbols[].minLeverage` | `Integer` | Minimum leverage. |
| `symbols[].maxLeverage` | `Integer` | Maximum leverage. |
| `symbols[].buyLimitPriceRatio` | `Number` | Maximum allowed buy-side limit price deviation ratio. |
| `symbols[].sellLimitPriceRatio` | `Number` | Maximum allowed sell-side limit price deviation ratio. |
| `symbols[].makerFeeRate` | `Number` | Maker fee rate. |
| `symbols[].takerFeeRate` | `Number` | Taker fee rate. |
| `symbols[].minOrderSize` | `Number` | Minimum order size. |
| `symbols[].maxOrderSize` | `Number` | Maximum order size. |
| `symbols[].maxPositionSize` | `Number` | Maximum position size. |
| `symbols[].marketOpenLimitSize` | `Number` | Maximum market-open order size. |

## market.get_current_funding_rate — Get Current Funding Rate

- Method: `GET`
- Path: `/capi/v3/market/premiumIndex`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetCurrentFundingRate

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair. Leave empty to return all trading pairs. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair |
| `markPrice` | `String` | Latest mark price |
| `indexPrice` | `String` | Latest index price |
| `lastFundingRate` | `String` | Most recent funding rate |
| `forecastFundingRate` | `String` | Forecasted funding rate |
| `interestRate` | `String` | Underlying benchmark interest rate |
| `nextFundingTime` | `Long` | Next funding time Unix millisecond timestamp |
| `time` | `Long` | Data timestamp Unix millisecond timestamp |
| `collectCycle` | `Long` | Funding interval in minutes |

## market.get_depth_data — Get Order Book Depth

- Method: `GET`
- Path: `/capi/v3/market/depth`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetDepthData

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |
| `limit` | `Integer` | `No` | Depth size. Supported values: 15 , 200 . Default: 15 . |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `bids` | `Array` | Bid side depth. Each item is [price, size] . |
| `asks` | `Array` | Ask side depth. Each item is [price, size] . |
| `lastUpdateId` | `Long` | Last processed order book update ID |

## market.get_funding_rate_history — Get Funding Rate History

- Method: `GET`
- Path: `/capi/v3/market/fundingRate`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `5 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetFundingRateHistory

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |
| `startTime` | `Long` | `No` | Start time (inclusive). Unix millisecond timestamp. |
| `endTime` | `Long` | `No` | End time (inclusive). Unix millisecond timestamp. Must be â¥ startTime. |
| `limit` | `Integer` | `No` | Number of records. Range: 1-1000. Default: 100. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair |
| `fundingRate` | `String` | Historical funding rate |
| `fundingTime` | `Long` | Funding time Unix millisecond timestamp |
| `markPrice` | `String` | Mark price at the funding time |

## market.get_history_klines — Get Historical Klines

- Method: `GET`
- Path: `/capi/v3/market/historyKlines`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `5 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetHistoryKlines

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |
| `interval` | `String` | `Yes` | Kline interval. Allowed values: 1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d, 1w. |
| `startTime` | `Long` | `No` | Start time (inclusive). Unix millisecond timestamp. Must not be in the future. |
| `endTime` | `Long` | `No` | End time (inclusive). Unix millisecond timestamp. Must not be in the future and must be â¥ startTime. |
| `limit` | `Integer` | `No` | Number of klines to return. Range: 1-100. Default: 100. |
| `priceType` | `String` | `No` | Price type. Supported values: LAST (last trade), INDEX (index price), MARK (mark price). Default: LAST. |

### Response Parameters

NONE

## market.get_index_price_klines — Get Index Price Klines

- Method: `GET`
- Path: `/capi/v3/market/indexPriceKlines`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetIndexPriceKlines

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |
| `interval` | `String` | `Yes` | Kline interval. Allowed values: 1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d, 1w. |
| `limit` | `Integer` | `No` | Number of klines to return. Range: 1-1000. Default: 100. |

### Response Parameters

NONE

## market.get_klines — Get Kline Data

- Method: `GET`
- Path: `/capi/v3/market/klines`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetKlines

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |
| `interval` | `String` | `Yes` | Kline interval. Allowed values: 1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d, 1w. |
| `limit` | `Integer` | `No` | Number of klines to return. Range: 1-1000. Default: 100. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `` | `Long` | Open time Unix millisecond timestamp |
| `` | `String` | Open price |
| `` | `String` | High price |
| `` | `String` | Low price |
| `` | `String` | Close price |
| `` | `String` | Volume (base asset) |
| `` | `Long` | Close time Unix millisecond timestamp |
| `` | `String` | Quote volume (quote asset) |
| `` | `Long` | Number of trades |
| `` | `String` | Taker buy volume (base asset) |
| `` | `String` | Taker buy volume (quote asset) |

## market.get_mark_price_klines — Get Mark Price Klines

- Method: `GET`
- Path: `/capi/v3/market/markPriceKlines`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetMarkPriceKlines

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |
| `interval` | `String` | `Yes` | Kline interval. Allowed values: 1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d, 1w. |
| `limit` | `Integer` | `No` | Number of klines to return. Range: 1-1000. Default: 100. |

### Response Parameters

NONE

## market.get_open_interest — Get Open Interest

- Method: `GET`
- Path: `/capi/v3/market/openInterest`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `2 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetOpenInterest

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair |
| `openInterest` | `String` | Current open interest (contracts) |
| `time` | `Long` | Matching engine timestamp Unix millisecond timestamp |

## market.get_recent_trades — Get Recent Trades

- Method: `GET`
- Path: `/capi/v3/market/trades`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `5 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetRecentTrades

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |
| `limit` | `Integer` | `No` | Number of trades to return. Range: 1-1000. Default: 100. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `id` | `String` | Trade ID |
| `time` | `Long` | Trade time Unix millisecond timestamp |
| `price` | `String` | Trade price |
| `qty` | `String` | Filled quantity (base asset) |
| `quoteQty` | `String` | Trade value (quote asset) |
| `isBestMatch` | `Boolean` | Whether the trade was the best match |
| `isBuyerMaker` | `Boolean` | Whether the buyer was the maker |

## market.get_server_time — Get Server Time

- Method: `GET`
- Path: `/capi/v3/market/time`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetServerTime

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `serverTime` | `Long` | Server time Unix millisecond timestamp |

## market.get_symbol_price — Get Symbol Price

- Method: `GET`
- Path: `/capi/v3/market/symbolPrice`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetSymbolPrice

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair |
| `priceType` | `String` | `No` | Price type. Supported values: INDEX, MARK. Default: INDEX. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair |
| `price` | `String` | Requested price |
| `time` | `Long` | System timestamp Unix millisecond timestamp |

## market.get_ticker24h — Get 24hr Ticker Statistics

- Method: `GET`
- Path: `/capi/v3/market/ticker/24hr`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `40 / -`
- Source: https://www.weex.com/api-doc/contract/Market_API/GetTicker24h

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair. Leave empty to return all trading pairs. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair |
| `priceChange` | `String` | Absolute price change over the last 24 hours |
| `priceChangePercent` | `String` | Percentage price change over the last 24 hours |
| `lastPrice` | `String` | Last traded price |
| `openPrice` | `String` | Open price 24 hours ago |
| `highPrice` | `String` | Highest price in the last 24 hours |
| `lowPrice` | `String` | Lowest price in the last 24 hours |
| `volume` | `String` | 24-hour trading volume (base asset) |
| `quoteVolume` | `String` | 24-hour trading volume (quote asset) |
| `markPrice` | `String` | Last mark price |
| `indexPrice` | `String` | Last index price |
| `openTime` | `Long` | Timestamp of the first trade in the 24-hour window |
| `closeTime` | `Long` | Timestamp of the last trade in the 24-hour window |

## Sim Endpoint Sections

## sim.account.get_account_balance — Get Demo Account Balance (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/sim/balance`
- Category: `sim`
- Requires Auth: `True`
- Permission: `USER_DATA`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/zh-CN/contract/intro

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `asset` | `String` | Asset name. |
| `balance` | `String` | Total balance. |
| `availableBalance` | `String` | Available balance. |
| `frozen` | `String` | Frozen amount. |
| `unrealizePnl` | `String` | Unrealized profit and loss. |

## sim.account.get_all_positions — Get Demo All Positions (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/sim/position/allPosition`
- Category: `sim`
- Requires Auth: `True`
- Permission: `USER_DATA`
- Weight(IP/UID): `10 / 15`
- Source: https://www.weex.com/api-doc/zh-CN/contract/intro

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `id` | `Long` | Position ID. |
| `asset` | `String` | Associated collateral asset. |
| `symbol` | `String` | Trading pair. |
| `side` | `String` | Position direction such as LONG or SHORT. |
| `marginType` | `String` | Margin mode: CROSSED or ISOLATED. |
| `separatedMode` | `String` | Position separation mode: COMBINED or SEPARATED. |
| `separatedOpenOrderId` | `Long` | Separated-position open order ID. |
| `leverage` | `String` | Position leverage. |
| `size` | `String` | Current position size. |
| `openValue` | `String` | Open position value. |
| `openFee` | `String` | Open fee. |
| `fundingFee` | `String` | Funding fee. |
| `marginSize` | `String` | Margin amount in the collateral asset. |
| `isolatedMargin` | `String` | Isolated margin amount. |
| `isAutoAppendIsolatedMargin` | `Boolean` | Whether automatic isolated-margin append is enabled. |
| `cumOpenSize` | `String` | Cumulative open size. |
| `cumOpenValue` | `String` | Cumulative open value. |
| `cumOpenFee` | `String` | Cumulative open fee. |
| `cumCloseSize` | `String` | Cumulative close size. |
| `cumCloseValue` | `String` | Cumulative close value. |
| `cumCloseFee` | `String` | Cumulative close fee. |
| `cumFundingFee` | `String` | Cumulative settled funding fee. |
| `cumLiquidateFee` | `String` | Cumulative liquidation fee. |
| `createdMatchSequenceId` | `Long` | Match engine sequence ID at creation. |
| `updatedMatchSequenceId` | `Long` | Latest match engine sequence ID. |
| `createdTime` | `Long` | Creation time in Unix milliseconds. |
| `updatedTime` | `Long` | Update time in Unix milliseconds. |
| `unrealizePnl` | `String` | Unrealized profit and loss. |
| `liquidatePrice` | `String` | Estimated liquidation price; 0 means no current liquidation risk. |

## sim.transaction.get_order_history — Get Demo Order History (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/sim/order/history`
- Category: `sim`
- Requires Auth: `True`
- Permission: `USER_DATA`
- Weight(IP/UID): `10 / 10`
- Source: https://www.weex.com/api-doc/zh-CN/contract/intro

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Optional trading pair filter. Omit by default for demo history because normal contract symbols such as `BTCUSDT` may be rejected unless the API accepts the exact simulated symbol filter. |
| `limit` | `Integer` | `No` | Number of records per page, 1-1000. Default 500. |
| `startTime` | `Long` | `No` | Start time in Unix milliseconds. Must be less than or equal to endTime. |
| `endTime` | `Long` | `No` | End time in Unix milliseconds. Must be within 90 days of startTime. |
| `page` | `Integer` | `No` | Page index starting from 0. Default 0. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `avgPrice` | `String` | Average fill price. |
| `clientOrderId` | `String` | Client-defined order ID. |
| `cumQuote` | `String` | Cumulative filled amount in the quote asset. |
| `executedQty` | `String` | Filled quantity in the base asset. |
| `orderId` | `Long` | System order ID. |
| `origQty` | `String` | Original order quantity. |
| `price` | `String` | Order price. |
| `reduceOnly` | `Boolean` | Whether the order is reduce-only. |
| `side` | `String` | Order side. |
| `positionSide` | `String` | Position side. |
| `status` | `String` | Order status. |
| `stopPrice` | `String` | Trigger or stop price when applicable. |
| `symbol` | `String` | Trading pair. |
| `time` | `Long` | Order time in Unix milliseconds. |
| `timeInForce` | `String` | Time-in-force policy. |
| `type` | `String` | Order type. |
| `updateTime` | `Long` | Last update time in Unix milliseconds. |
| `workingType` | `String` | Trigger price source. |

## sim.transaction.place_order — Place Demo Order (TRADE)

- Method: `POST`
- Path: `/capi/v3/sim/order`
- Category: `sim`
- Requires Auth: `True`
- Permission: `TRADE`
- Weight(IP/UID): `2 / 5`
- Source: https://www.weex.com/api-doc/zh-CN/contract/intro

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair, for example BTCSUSDT. |
| `side` | `String` | `Yes` | Order side. Supported values: BUY, SELL. |
| `positionSide` | `String` | `Yes` | Position side. Supported values: LONG, SHORT. |
| `type` | `String` | `Yes` | Order type. Demo order supports LIMIT and MARKET. |
| `timeInForce` | `String` | `Conditional` | Required when type = LIMIT. Supported values: GTC, IOC, FOK, POST_ONLY. |
| `quantity` | `String` | `Yes` | Order quantity. Must be greater than 0. |
| `price` | `String` | `Conditional` | Limit price. Required when type = LIMIT. |
| `newClientOrderId` | `String` | `Yes` | Client-defined order ID, 1-36 chars matching ^[.A-Z:/a-z0-9_-]{1,36}$. |
| `tpTriggerPrice` | `String` | `No` | Optional take-profit trigger price. |
| `slTriggerPrice` | `String` | `No` | Optional stop-loss trigger price. |
| `TpWorkingType` | `String` | `No` | Take-profit trigger price source. Preserve this official field casing. |
| `SlWorkingType` | `String` | `No` | Stop-loss trigger price source. Preserve this official field casing. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `orderId` | `String` | Order ID assigned by the system. |
| `clientOrderId` | `String` | Echo of newClientOrderId. |
| `success` | `Boolean` | Whether the order request was accepted. |
| `errorCode` | `String` | Error code when success = false; otherwise empty. |
| `errorMessage` | `String` | Error message when success = false; otherwise empty. |

## Transaction Endpoint Sections

## transaction.cancel_all_orders — Cancel All Open Orders (TRADE)

- Method: `DELETE`
- Path: `/capi/v3/allOpenOrders`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/CancelAllOrders

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair to filter. Omit to cancel all open orders across symbols. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `orderId` | `Long` | ID of the cancelled order. |
| `success` | `Boolean` | Whether this order was successfully cancelled. |
| `errorCode` | `String` | Error code when success = false . |
| `errorMessage` | `String` | Error message when success = false . |

## transaction.cancel_all_pending_orders — Cancel All Conditional Orders (TRADE)

- Method: `DELETE`
- Path: `/capi/v3/algoOpenOrders`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/CancelAllPendingOrders

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair filter. Omit to cancel all conditional orders. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `orderId` | `Long` | Conditional order ID. |
| `success` | `Boolean` | Whether the cancel succeeded. |
| `errorCode` | `String` | Error code when success = false . |
| `errorMessage` | `String` | Error description when success = false . |

## transaction.cancel_order — Cancel Order (TRADE)

- Method: `DELETE`
- Path: `/capi/v3/order`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 3`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/CancelOrder

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `orderId` | `Long` | `Conditional` | Target order ID. Required when origClientOrderId is not provided. |
| `origClientOrderId` | `String` | `Conditional` | Client order ID, 1-36 characters. Required when orderId is not provided. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `orderId` | `String` | Cancelled order ID. |
| `origClientOrderId` | `String` | Client order ID (if provided). |
| `success` | `Boolean` | Whether the cancel request succeeded. |
| `errorCode` | `String` | Error code when success = false . |
| `errorMessage` | `String` | Error description when success = false . |

## transaction.cancel_orders_batch — Cancel Orders Batch (TRADE)

- Method: `DELETE`
- Path: `/capi/v3/batchOrders`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/CancelOrdersBatch

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `orderIdList` | `Array<Long>` | `Conditional` | Up to 10 order IDs to cancel. Required when origClientOrderIdList is empty. |
| `origClientOrderIdList` | `Array<String>` | `Conditional` | Up to 10 client order IDs. Each must match ^[\\.A-Z\:/a-z0-9_-]{1,36}$ . Required when orderIdList is empty. |

### Response Parameters

NONE

## transaction.cancel_pending_order — Cancel Conditional Order (TRADE)

- Method: `DELETE`
- Path: `/capi/v3/algoOrder`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 3`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/CancelPendingOrder

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `orderId` | `Long` | `Yes` | Conditional order ID to cancel. |

### Response Parameters

NONE

## transaction.close_positions — Close Positions (TRADE)

- Method: `POST`
- Path: `/capi/v3/closePositions`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `40 / 50`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/ClosePositions

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair to close. Omit to close all open positions. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `positionId` | `Long` | Position identifier. |
| `success` | `Boolean` | Whether the close action succeeded. |
| `successOrderId` | `Long` | Order ID created to close the position (when successful). |
| `errorMessage` | `String` | Failure reason when success = false . |

## transaction.get_current_order_status — Get Current Orders (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/openOrders`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 3`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/GetCurrentOrderStatus

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Filter by trading pair. |
| `orderId` | `String` | `No` | Only return orders with ID greater than the specified value. |
| `startTime` | `Long` | `No` | Filter orders created after this timestamp (ms). |
| `endTime` | `Long` | `No` | Filter orders created before this timestamp (ms). |
| `limit` | `Integer` | `No` | Page size, 1-100. Default 100. |
| `page` | `Integer` | `No` | Page index starting from 0. Default 0. |

### Response Parameters

NONE

## transaction.get_current_pending_orders — Get Current Conditional Orders (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/openAlgoOrders`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `3 / 3`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/GetCurrentPendingOrders

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair filter. |
| `startTime` | `Long` | `No` | Start time (ms). |
| `endTime` | `Long` | `No` | End time (ms). Must be â¥ startTime . |
| `page` | `Integer` | `No` | Page number starting from 1. Default 1. |
| `limit` | `Integer` | `No` | Page size, 1-100. Default 100. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `algoId` | `Long` | Conditional order ID. |
| `clientAlgoId` | `String` | Client-defined ID. |
| `algoType` | `String` | Conditional order category. Currently only CONDITIONAL is returned. |
| `orderType` | `String` | Triggered order type. Values: STOP , TAKE_PROFIT , STOP_MARKET , TAKE_PROFIT_MARKET , TRAILING_STOP_MARKET . |
| `symbol` | `String` | Trading pair. |
| `side` | `String` | Order side. Values: BUY , SELL . |
| `positionSide` | `String` | Position side. Values: LONG , SHORT . |
| `timeInForce` | `String` | Time-in-force for the triggered order. Values: GTC , IOC , FOK , POST_ONLY . |
| `quantity` | `String` | Requested quantity. |
| `algoStatus` | `String` | Conditional order status. Values: NEW , PENDING , UNTRIGGERED , FILLED , CANCELED , CANCELING . |
| `actualOrderId` | `Long` | ID of the triggered active order (if any). |
| `actualPrice` | `String` | Execution price (if triggered). |
| `triggerPrice` | `String` | Trigger price. |
| `price` | `String` | Execution price configured for the triggered order. |
| `tpTriggerPrice` | `String` | Linked take-profit trigger price (if configured). |
| `tpPrice` | `String` | Linked take-profit execution price (if configured). |
| `slTriggerPrice` | `String` | Linked stop-loss trigger price (if configured). |
| `slPrice` | `String` | Linked stop-loss execution price (if configured). |
| `tpOrderType` | `String` | Take-profit trigger price source. Values: CONTRACT_PRICE , MARK_PRICE . |
| `workingType` | `String` | Trigger price source. Values: CONTRACT_PRICE , MARK_PRICE . |
| `closePosition` | `Boolean` | Whether the triggered order will close the entire position. |
| `reduceOnly` | `Boolean` | Whether the triggered order is reduce-only. |
| `createTime` | `Long` | Creation time (ms). |
| `updateTime` | `Long` | Last update time (ms). |
| `triggerTime` | `Long` | Trigger time (ms). Returns 0 if the order has not been triggered. |

## transaction.get_historical_pending_orders — Get Conditional Order History (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/allAlgoOrders`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/GetHistoricalPendingOrders

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair filter. |
| `startTime` | `Long` | `No` | Start time (ms). |
| `endTime` | `Long` | `No` | End time (ms). Must be within 90 days of startTime . |
| `limit` | `Integer` | `No` | Page size, 1-1000. Default 500. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `orders` | `Array<PlanOrder>` | Current page of conditional orders. Each element follows the schema described in Get Current Conditional Orders . |
| `hasMore` | `Boolean` | true if more data is available. |

## transaction.get_order_history — Get Order History (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/order/history`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `10 / 10`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/GetOrderHistory

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Filter by trading pair. |
| `limit` | `Integer` | `No` | Number of records per page, 1-1000. Default 500. |
| `startTime` | `Long` | `No` | Start time (ms). Must be less than or equal to endTime . |
| `endTime` | `Long` | `No` | End time (ms). Must be within 90 days of startTime . |
| `page` | `Integer` | `No` | Page index starting from 0. Default 0. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `avgPrice` | `String` | Average fill price. |
| `clientOrderId` | `String` | Client-defined order ID. |
| `cumQuote` | `String` | Cumulative filled amount in the quote asset. |
| `executedQty` | `String` | Filled quantity in the base asset. |
| `orderId` | `Long` | System order ID. |
| `origQty` | `String` | Original order quantity. |
| `price` | `String` | Order price. |
| `reduceOnly` | `Boolean` | Whether the order can only reduce positions. |
| `side` | `String` | Order side. See Order Side for possible values. |
| `positionSide` | `String` | Position side. See Position Mode . |
| `status` | `String` | Order status. See Order Status . |
| `stopPrice` | `String` | Stop price / trigger price (if applicable). |
| `symbol` | `String` | Trading pair. |
| `time` | `Long` | Order creation time (ms). |
| `timeInForce` | `String` | Time-in-force policy. See Time in Force . |
| `type` | `String` | Order type. See Order Type . |
| `updateTime` | `Long` | Last update time (ms). |
| `workingType` | `String` | Trigger price type. See Trigger Price Type . |

## transaction.get_single_order_info — Get Order Info (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/order`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 3`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/GetSingleOrderInfo

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `orderId` | `Long` | `Yes` | Order ID to query. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `avgPrice` | `String` | Average fill price. |
| `clientOrderId` | `String` | Client-defined order ID. |
| `cumQuote` | `String` | Cumulative filled amount in the quote asset. |
| `executedQty` | `String` | Filled quantity in the base asset. |
| `orderId` | `Long` | System order ID. |
| `origQty` | `String` | Original order quantity. |
| `price` | `String` | Order price. |
| `reduceOnly` | `Boolean` | Whether the order can only reduce positions. |
| `side` | `String` | Order side. See Order Side for possible values. |
| `positionSide` | `String` | Position side. See Position Mode . |
| `status` | `String` | Order status. See Order Status . |
| `stopPrice` | `String` | Stop price / trigger price (if applicable). |
| `symbol` | `String` | Trading pair. |
| `time` | `Long` | Order creation time (ms). |
| `timeInForce` | `String` | Time-in-force policy. See Time in Force . |
| `type` | `String` | Order type. See Order Type . |
| `updateTime` | `Long` | Last update time (ms). |
| `workingType` | `String` | Trigger price type. See Trigger Price Type . |

## transaction.get_trade_details — Get Trade Details (USER_DATA)

- Method: `GET`
- Path: `/capi/v3/userTrades`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 5`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/GetTradeDetails

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Trading pair filter. |
| `orderId` | `Long` | `No` | Only return trades associated with this order. |
| `startTime` | `Long` | `No` | Start time (ms). |
| `endTime` | `Long` | `No` | End time (ms). Must be â¥ startTime . |
| `limit` | `Integer` | `No` | Number of records (1-100). Default 100. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `id` | `Long` | Trade ID. |
| `orderId` | `Long` | Associated order ID. |
| `symbol` | `String` | Trading pair. |
| `buyer` | `Boolean` | Whether the user was the buyer. |
| `commission` | `String` | Commission amount. |
| `commissionAsset` | `String` | Asset used to pay commission. |
| `maker` | `Boolean` | true if maker, false if taker. |
| `price` | `String` | Trade price. |
| `qty` | `String` | Filled quantity (base asset). |
| `quoteQty` | `String` | Filled amount (quote asset). |
| `realizedPnl` | `String` | Realised PnL for this fill. |
| `side` | `String` | Order side, BUY or SELL . |
| `positionSide` | `String` | Position side, LONG or SHORT . |
| `time` | `Long` | Trade time (ms). |

## transaction.modify_tp_sl_order — Modify TP/SL Conditional Order (TRADE)

- Method: `POST`
- Path: `/capi/v3/modifyTpSlOrder`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 5`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/ModifyTpSlOrder

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `orderId` | `Long` | `Yes` | Conditional order ID to modify. |
| `triggerPrice` | `String` | `Yes` | New trigger price (> 0). |
| `executePrice` | `String` | `Conditional` | New execution price. Set to 0 or omit to switch to market execution. |
| `triggerPriceType` | `String` | `No` | Trigger price source. CONTRACT_PRICE or MARK_PRICE . Default CONTRACT_PRICE . |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `success` | `Boolean` | Whether the modification was accepted. |

## transaction.place_order — Place Order (TRADE)

- Method: `POST`
- Path: `/capi/v3/order`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 5`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/PlaceOrder

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair, for example BTCUSDT . |
| `side` | `String` | `Yes` | Order side. Supported values: BUY , SELL . |
| `positionSide` | `String` | `Yes` | Position side. Supported values: LONG , SHORT . |
| `type` | `String` | `Yes` | Order type. Supported values: LIMIT , MARKET . |
| `timeInForce` | `String` | `Conditional` | Time-in-force policy. Required when type = LIMIT . Supported values: GTC , IOC , FOK . |
| `quantity` | `String` | `Yes` | Order quantity. Must be greater than 0. |
| `price` | `String` | `Conditional` | Limit price. Required when type = LIMIT . |
| `newClientOrderId` | `String` | `Yes` | Client order identifier (1-36 characters, pattern ^[\\.A-Z\:/a-z0-9_-]{1,36}$ ). |
| `tpTriggerPrice` | `String` | `No` | Optional take-profit trigger price. |
| `slTriggerPrice` | `String` | `No` | Optional stop-loss trigger price. |
| `TpWorkingType` | `String` | `No` | Take-profit trigger price source. Supported values: CONTRACT_PRICE , MARK_PRICE . Default CONTRACT_PRICE . |
| `SlWorkingType` | `String` | `No` | Stop-loss trigger price source. Supported values: CONTRACT_PRICE , MARK_PRICE . Default CONTRACT_PRICE . |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `orderId` | `String` | Order ID assigned by the system. |
| `clientOrderId` | `String` | Echo of newClientOrderId . |
| `success` | `Boolean` | Whether the order request was accepted. |
| `errorCode` | `String` | Error code when success = false ; otherwise empty. |
| `errorMessage` | `String` | Error message when success = false ; otherwise empty. |

## transaction.place_orders_batch — Place Orders Batch (TRADE)

- Method: `POST`
- Path: `/capi/v3/batchOrders`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/PlaceOrdersBatch

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `batchOrders` | `Array<PlaceOrder>` | `Yes` | Up to 10 orders per request. Each element uses the same fields as Place Order (TRADE) . |

### Response Parameters

NONE

## transaction.place_pending_order — Place Conditional Order (TRADE)

- Method: `POST`
- Path: `/capi/v3/algoOrder`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 5`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/PlacePendingOrder

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair, e.g. BTCUSDT . |
| `side` | `String` | `Yes` | Order side. Values: BUY , SELL . |
| `positionSide` | `String` | `Yes` | Position side. Values: LONG , SHORT . |
| `type` | `String` | `Yes` | Conditional order type. Values: STOP , TAKE_PROFIT , STOP_MARKET , TAKE_PROFIT_MARKET . |
| `quantity` | `String` | `Yes` | Order quantity. Must be > 0. |
| `price` | `String` | `Conditional` | Execution price. Required when type is STOP or TAKE_PROFIT . |
| `triggerPrice` | `String` | `Yes` | Trigger price. Must be > 0. |
| `clientAlgoId` | `String` | `Yes` | Client-defined identifier (1-36 characters, pattern ^[\\.A-Z\:/a-z0-9_-]{1,36}$ ). |
| `presetTakeProfitPrice` | `String` | `No` | Optional take-profit trigger price. |
| `presetStopLossPrice` | `String` | `No` | Optional stop-loss trigger price. |
| `TpWorkingType` | `String` | `No` | Take-profit trigger type: CONTRACT_PRICE or MARK_PRICE . Default CONTRACT_PRICE . |
| `SlWorkingType` | `String` | `No` | Stop-loss trigger type: CONTRACT_PRICE or MARK_PRICE . Default CONTRACT_PRICE . |

### Response Parameters

NONE

## transaction.place_tp_sl_order — Place TP/SL Conditional Orders (TRADE)

- Method: `POST`
- Path: `/capi/v3/placeTpSlOrder`
- Category: `transaction`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 5`
- Source: https://www.weex.com/api-doc/contract/Transaction_API/PlaceTpSlOrder

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair. |
| `clientAlgoId` | `String` | `Yes` | Client-defined identifier (1-36 characters, pattern ^[\\.A-Z\:/a-z0-9_-]{1,36}$ ). |
| `planType` | `String` | `Yes` | Plan type. Values: TAKE_PROFIT , STOP_LOSS . |
| `triggerPrice` | `String` | `Yes` | Trigger price (> 0). |
| `executePrice` | `String` | `Conditional` | Execution price. Set to 0 or omit for market execution. |
| `quantity` | `String` | `Yes` | Quantity to execute. |
| `positionSide` | `String` | `Yes` | Position side ( LONG , SHORT ). |
| `triggerPriceType` | `String` | `No` | Trigger source. CONTRACT_PRICE or MARK_PRICE . Default CONTRACT_PRICE . |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `success` | `Boolean` | Whether the plan order was accepted. |
| `orderId` | `Long` | Plan order ID when successful. |
| `errorCode` | `String` | Error code when success = false . |
| `errorMessage` | `String` | Error description when success = false . |
