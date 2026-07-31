# WEEX Spot API Definitions

Generated from live V3 docs on 2026-04-17.

## Contents

- Summary table
- `spot.account.*` endpoint sections
- `spot.config.*` endpoint sections
- `spot.market.*` endpoint sections
- `spot.order.*` endpoint sections
- `spot.rebate.*` endpoint sections

Use in-page search with the exact endpoint key from the summary table to jump to a specific generated section quickly.

## Summary Table

Total endpoints: **32**

| Key | Method | Path | Auth |
|---|---|---|---|
| `spot.account.get_account_balance` | `GET` | `/api/v3/account/` | `True` |
| `spot.account.get_bill_records` | `POST` | `/api/v3/account/bills` | `True` |
| `spot.account.get_fund_bill_records` | `POST` | `/api/v3/account/fundingBills` | `True` |
| `spot.account.transfer_records` | `GET` | `/api/v3/account/transferRecords` | `True` |
| `spot.config.currency_info` | `GET` | `/api/v3/coins` | `False` |
| `spot.config.get_api_trading_symbols` | `GET` | `/api/v3/apiTradingSymbols` | `False` |
| `spot.config.get_product_info` | `GET` | `/api/v3/exchangeInfo` | `False` |
| `spot.config.get_server_time` | `GET` | `/api/v3/time` | `False` |
| `spot.config.ping` | `GET` | `/api/v3/ping` | `False` |
| `spot.market.get_all_ticker_info` | `GET` | `/api/v3/market/ticker/24hr` | `False` |
| `spot.market.get_book_ticker` | `GET` | `/api/v3/market/ticker/bookTicker` | `False` |
| `spot.market.get_depth_data` | `GET` | `/api/v3/market/depth` | `False` |
| `spot.market.get_k_line_data` | `GET` | `/api/v3/market/klines` | `False` |
| `spot.market.get_ticker_info` | `GET` | `/api/v3/market/ticker/price` | `False` |
| `spot.market.get_trade_data` | `GET` | `/api/v3/market/trades` | `False` |
| `spot.order.bulk_cancel` | `DELETE` | `/api/v3/order/batch` | `True` |
| `spot.order.bulk_order` | `POST` | `/api/v3/order/batch` | `True` |
| `spot.order.cancel_order` | `DELETE` | `/api/v3/order` | `True` |
| `spot.order.cancel_symbol_orders` | `DELETE` | `/api/v3/openOrders` | `True` |
| `spot.order.history_orders` | `GET` | `/api/v3/allOrders` | `True` |
| `spot.order.order_details` | `GET` | `/api/v3/order` | `True` |
| `spot.order.place_order` | `POST` | `/api/v3/order` | `True` |
| `spot.order.transaction_details` | `GET` | `/api/v3/myTrades` | `True` |
| `spot.order.unfinished_orders` | `GET` | `/api/v3/openOrders` | `True` |
| `spot.rebate.get_affiliate_assets` | `GET` | `/api/v3/agency/getAssert` | `True` |
| `spot.rebate.get_affiliate_commission` | `GET` | `/api/v3/rebate/affiliate/getAffiliateCommission` | `True` |
| `spot.rebate.get_affiliate_deal_data` | `GET` | `/api/v3/agency/getDealData` | `True` |
| `spot.rebate.get_affiliate_ui_ds` | `GET` | `/api/v3/rebate/affiliate/getAffiliateUIDs` | `True` |
| `spot.rebate.get_channel_user_trade_and_asset` | `GET` | `/api/v3/rebate/affiliate/getChannelUserTradeAndAsset` | `True` |
| `spot.rebate.internal_withdrawal` | `POST` | `/api/v3/rebate/affiliate/internalWithdrawal` | `True` |
| `spot.rebate.query_sub_channel_transactions` | `POST` | `/api/v3/rebate/affiliate/querySubChannelTransactions` | `True` |
| `spot.rebate.verify_referrals` | `GET` | `/api/v3/agency/verifyReferrals` | `True` |

## Spot Account Endpoint Sections

## spot.account.get_account_balance — Get Account Information (USER_DATA)

- Method: `GET`
- Path: `/api/v3/account/`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 5`
- Source: https://www.weex.com/api-doc/spot/AccountAPI/GetAccountBalance

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `makerCommission` | `Integer` | User-level maker fee rate (basis points). |
| `takerCommission` | `Integer` | User-level taker fee rate (basis points). |
| `commissionRates.maker` | `String` | Maker fee rate as a decimal string. |
| `commissionRates.taker` | `String` | Taker fee rate as a decimal string. |
| `canTrade` | `Boolean` | Whether trading is enabled. |
| `canWithdraw` | `Boolean` | Whether withdrawals are enabled. |
| `canDeposit` | `Boolean` | Whether deposits are enabled. |
| `brokered` | `Boolean` | Whether the account is broker managed. |
| `requireSelfTradePrevention` | `Boolean` | Whether self-trade prevention is enforced. |
| `preventSor` | `Boolean` | Whether smart order routing is disabled. |
| `updateTime` | `Long` | Last account update time (ms). |
| `accountType` | `String` | Account type, e.g. SPOT . |
| `balances` | `Array<Object>` | Asset balances. |
| `-> asset` | `String` | Asset symbol. |
| `-> free` | `String` | Free balance. |
| `-> locked` | `String` | Locked balance. |
| `permissions` | `Array<String>` | Granted permissions (e.g. SPOT_TRADING ). |
| `uid` | `Long` | Account UID. |
| `symbolCommissions` | `Object` | Per-symbol maker/taker commission overrides. |

## spot.account.get_bill_records — Get Spot Account Bills (USER_DATA)

- Method: `POST`
- Path: `/api/v3/account/bills`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 5`
- Source: https://www.weex.com/api-doc/spot/AccountAPI/GetBillRecords

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `coinId` | `Integer` | `No` | Filter by asset ID. |
| `bizType` | `String` | `No` | Business type filter (e.g. deposit , withdraw , trade_out ). |
| `after` | `Long` | `No` | Records created AFTER this timestamp |
| `before` | `Long` | `No` | Records created BEFORE this timestamp |
| `limit` | `Integer` | `No` | Number of records to return (default 10 , maximum 100 ). |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `billId` | `String` | Bill identifier. |
| `coinId` | `Integer` | Asset ID. |
| `coinName` | `String` | Asset symbol. |
| `bizType` | `String` | Business type. |
| `fillSize` | `String` | Filled quantity (if applicable). |
| `fillValue` | `String` | Filled value (if applicable). |
| `deltaAmount` | `String` | Amount change. |
| `afterAmount` | `String` | Balance after the change. |
| `fees` | `String` | Fees charged. |
| `cTime` | `String` | Creation time (ms). |

## spot.account.get_fund_bill_records — Get Funding Account Bills (USER_DATA)

- Method: `POST`
- Path: `/api/v3/account/fundingBills`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 5`
- Source: https://www.weex.com/api-doc/spot/AccountAPI/GetFundBillRecords

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `coinId` | `Integer` | `No` | Asset ID filter. |
| `bizType` | `String` | `No` | Business type filter. |
| `startTime` | `Long` | `No` | Start time (ms). |
| `endTime` | `Long` | `No` | End time (ms). |
| `pageIndex` | `Integer` | `No` | Page number (default 1). |
| `pageSize` | `Integer` | `No` | Page size (default 10, maximum 100). |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `total` | `Long` | Total number of records. |
| `pageSize` | `Integer` | Page size. |
| `pages` | `Integer` | Total number of pages. |
| `page` | `Integer` | Current page number. |
| `hasNextPage` | `Boolean` | Whether another page is available. |
| `items` | `Array<Object>` | List of bill entries (see Get Spot Account Bills for field details). |

## spot.account.transfer_records — Get Transfer Records (USER_DATA)

- Method: `GET`
- Path: `/api/v3/account/transferRecords`
- Category: `account`
- Requires Auth: `True`
- Weight(IP/UID): `3 / 3`
- Source: https://www.weex.com/api-doc/spot/AccountAPI/TransferRecords

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `coinId` | `Integer` | `No` | Filter by asset ID. |
| `fromType` | `String` | `No` | Source account type. |
| `limit` | `Integer` | `No` | Number of records to return (default 100). |
| `after` | `Long` | `No` | Return records after this time (ms). |
| `before` | `Long` | `No` | Return records before this time (ms). |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `coinName` | `String` | Asset symbol. |
| `status` | `String` | Transfer status. |
| `toType` | `String` | Destination account type. |
| `toSymbol` | `String` | Destination symbol (if applicable). |
| `fromType` | `String` | Source account type. |
| `fromSymbol` | `String` | Source symbol (if applicable). |
| `amount` | `String` | Transfer amount. |
| `tradeTime` | `String` | Transfer time (ms). |

## Spot Config Endpoint Sections

## spot.config.currency_info — Get Coin Information

- Method: `GET`
- Path: `/api/v3/coins`
- Category: `config`
- Requires Auth: `False`
- Weight(IP/UID): `5 / -`
- Source: https://www.weex.com/api-doc/spot/ConfigAPI/CurrencyInfo

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `coin` | `String` | Coin symbol, e.g. BTC . |
| `name` | `String` | Coin full name. |
| `depositAllEnable` | `Boolean` | Whether deposits are enabled. |
| `withdrawAllEnable` | `Boolean` | Whether withdrawals are enabled. |
| `networkList` | `Array<Object>` | Supported network information. |
| `-> network` | `String` | Network name (e.g. ERC20 ). |
| `-> isDefault` | `Boolean` | Whether this is the default network. |
| `-> depositEnable` | `Boolean` | Whether deposits via this network are enabled. |
| `-> withdrawEnable` | `Boolean` | Whether withdrawals via this network are enabled. |
| `-> withdrawFee` | `String` | Withdrawal fee. |
| `-> withdrawMin` | `String` | Minimum withdrawal amount. |
| `-> withdrawIntegerMultiple` | `BigDecimal` | Required multiple for withdrawals. |
| `-> minConfirm` | `Integer` | Minimum confirmations required for deposits. |
| `-> withdrawTag` | `Boolean` | Whether a tag/memo is required. |
| `-> depositDust` | `String` | Minimum deposit amount that will be credited. |
| `-> contractAddress` | `String` | Contract address (if applicable). |
| `-> contractAddressUrl` | `String` | Explorer URL for the contract. |
| `-> depositDesc / withdrawDesc` | `String` | Optional status messages when deposits/withdrawals are disabled. |

## spot.config.get_api_trading_symbols — Get Spot API Trading Symbols

- Method: `GET`
- Path: `/api/v3/apiTradingSymbols`
- Category: `config`
- Requires Auth: `False`
- Weight(IP/UID): `5 / -`
- Source: https://www.weex.com/api-doc/spot/ConfigAPI/GetAllProductInfo

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbols[]` | `Array<String>` | Raw response is an array of spot symbols available for API trading. |

## spot.config.get_product_info — Exchange information

- Method: `GET`
- Path: `/api/v3/exchangeInfo`
- Category: `config`
- Requires Auth: `False`
- Weight(IP/UID): `20 / -`
- Source: https://www.weex.com/api-doc/spot/ConfigAPI/GetProductInfo

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Single trading pair. Mutually exclusive with symbols . |
| `symbols` | `Array` | `No` | Multiple trading pairs. Accepts comma-separated values or a JSON array. |
| `symbolStatus` | `String` | `No` | Filter by symbol status. Currently only TRADING is supported. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `timezone` | `String` | Exchange timezone (e.g. UTC ). |
| `serverTime` | `Long` | Current server time in milliseconds. |
| `symbols` | `Array<Object>` | Trading pair definitions. |
| `-> symbol` | `String` | Trading pair code, e.g. BTCUSDT . |
| `-> status` | `String` | Trading status, e.g. TRADING . |
| `-> baseAsset` | `String` | Base asset symbol. |
| `-> baseAssetPrecision` | `Integer` | Base asset precision. |
| `-> quoteAsset` | `String` | Quote asset symbol. |
| `-> quoteAssetPrecision` | `Integer` | Quote asset precision. |
| `-> tickSize` | `String` | Minimum price increment (for quoteCoin) |
| `-> stepSize` | `String` | Minimum quantity increment (for baseCoin) |
| `-> minTradeAmount` | `BigDecimal` | Minimum order quantity. |
| `-> maxTradeAmount` | `BigDecimal` | Maximum order quantity. |
| `-> takerFeeRate` | `BigDecimal` | Taker fee rate. |
| `-> makerFeeRate` | `BigDecimal` | Maker fee rate. |
| `-> buyLimitPriceRatio` | `BigDecimal` | Maximum allowed deviation for buy limit orders. |
| `-> sellLimitPriceRatio` | `BigDecimal` | Maximum allowed deviation for sell limit orders. |
| `-> marketBuyLimitSize` | `BigDecimal` | Per-order size limit for market buys. |
| `-> marketSellLimitSize` | `BigDecimal` | Per-order size limit for market sells. |
| `-> marketFallbackPriceRatio` | `BigDecimal` | Fallback price ratio for market orders. |
| `-> enableTrade` | `Boolean` | Whether trading is enabled. |
| `-> enableDisplay` | `Boolean` | Whether the symbol is displayed. |
| `-> displayDigitMerge` | `String` | Depth merge configuration. |
| `-> displayNew` | `Boolean` | Whether the symbol is marked as ânewâ. |
| `-> displayHot` | `Boolean` | Whether the symbol is marked as âhotâ. |

## spot.config.get_server_time — Get Server Time

- Method: `GET`
- Path: `/api/v3/time`
- Category: `config`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/spot/ConfigAPI/GetServerTime

### Request Parameters

NONE

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `serverTime` | `Long` | Current server time in milliseconds. |

## spot.config.ping — Test Connectivity

- Method: `GET`
- Path: `/api/v3/ping`
- Category: `config`
- Requires Auth: `False`
- Weight(IP/UID): `1 / -`
- Source: https://www.weex.com/api-doc/spot/ConfigAPI/Ping

### Request Parameters

NONE

### Response Parameters

NONE

## Spot Market Endpoint Sections

## spot.market.get_all_ticker_info — Get 24h Ticker Statistics

- Method: `GET`
- Path: `/api/v3/market/ticker/24hr`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `2 / -`
- Source: https://www.weex.com/api-doc/spot/MarketDataAPI/GetAllTickerInfo

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Single trading pair (mutually exclusive with symbols ). |
| `symbols` | `Array` | `No` | Multiple trading pairs. Accepts comma-separated values or a JSON array. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair. |
| `priceChange` | `String` | Absolute price change over the last 24 hours. |
| `priceChangePercent` | `String` | Percentage price change over the last 24 hours. |
| `lastPrice` | `String` | Last traded price. |
| `bidPrice` | `String` | Best bid price. |
| `bidQty` | `String` | Best bid quantity. |
| `askPrice` | `String` | Best ask price. |
| `askQty` | `String` | Best ask quantity. |
| `openPrice` | `String` | Opening price 24 hours ago. |
| `highPrice` | `String` | Highest price in the last 24 hours. |
| `lowPrice` | `String` | Lowest price in the last 24 hours. |
| `volume` | `String` | Base asset volume in the last 24 hours. |
| `quoteVolume` | `String` | Quote asset volume in the last 24 hours. |
| `openTime` | `Long` | First trade timestamp in the 24h window (ms). |
| `closeTime` | `Long` | Last trade timestamp in the 24h window (ms). |
| `count` | `Long` | Number of trades in the 24h window. |

## spot.market.get_book_ticker — Get Best Bid/Ask

- Method: `GET`
- Path: `/api/v3/market/ticker/bookTicker`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `4 / -`
- Source: https://www.weex.com/api-doc/spot/MarketDataAPI/GetBookTicker

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Single trading pair (mutually exclusive with symbols ). |
| `symbols` | `Array` | `No` | Multiple trading pairs. Accepts comma-separated values or a JSON array. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair. |
| `bidPrice` | `String` | Best bid price. |
| `bidQty` | `String` | Best bid quantity. |
| `askPrice` | `String` | Best ask price. |
| `askQty` | `String` | Best ask quantity. |

## spot.market.get_depth_data — Get Order Book Depth

- Method: `GET`
- Path: `/api/v3/market/depth`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `5 / -`
- Source: https://www.weex.com/api-doc/spot/MarketDataAPI/GetDepthData

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair, e.g. BTCUSDT . |
| `limit` | `Integer` | `No` | Number of depth entries. Supported values: 15 , 200 . Default 15 . |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `lastUpdateId` | `Long` | Order book snapshot ID. |
| `bids` | `Array<Array>` | Bid depth entries formatted as [price, quantity] . |
| `asks` | `Array<Array>` | Ask depth entries formatted as [price, quantity] . |

## spot.market.get_k_line_data — Get Kline Data

- Method: `GET`
- Path: `/api/v3/market/klines`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `2 / -`
- Source: https://www.weex.com/api-doc/spot/MarketDataAPI/GetKLineData

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair, e.g. BTCUSDT . |
| `interval` | `String` | `Yes` | Candlestick interval (e.g. [1m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,1w,1M]). |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `` | `` | Open time (ms). |
| `` | `` | Open price. |
| `` | `` | High price. |
| `` | `` | Low price. |
| `` | `` | Close price. |
| `` | `` | Volume (base asset). |
| `` | `` | Close time (ms). |
| `` | `` | Quote asset volume. |
| `` | `` | Number of trades. |
| `` | `` | Taker buy volume (base asset). |
| `` | `` | Taker buy volume (quote asset). |

## spot.market.get_ticker_info — Get Latest Price

- Method: `GET`
- Path: `/api/v3/market/ticker/price`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `4 / -`
- Source: https://www.weex.com/api-doc/spot/MarketDataAPI/GetTickerInfo

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Single trading pair (mutually exclusive with symbols ). |
| `symbols` | `Array` | `No` | Multiple trading pairs. Accepts comma-separated values or a JSON array. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair. |
| `price` | `String` | Latest traded price. |

## spot.market.get_trade_data — Get Recent Trades

- Method: `GET`
- Path: `/api/v3/market/trades`
- Category: `market`
- Requires Auth: `False`
- Weight(IP/UID): `25 / -`
- Source: https://www.weex.com/api-doc/spot/MarketDataAPI/GetTradeData

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair, e.g. BTCUSDT . |
| `limit` | `Integer` | `No` | Number of trades to return. Range 1 â 1000 , default 100 . |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `id` | `String` | Trade identifier. |
| `price` | `String` | Trade price. |
| `qty` | `String` | Executed quantity (base asset). |
| `quoteQty` | `String` | Executed amount (quote asset). |
| `time` | `Long` | Trade time (milliseconds). |
| `isBuyerMaker` | `Boolean` | true if the buyer was the maker side. |
| `isBestMatch` | `Boolean` | true if the trade matched the best price level. |

## Spot Order Endpoint Sections

## spot.order.bulk_cancel — Batch Cancel Orders (TRADE)

- Method: `DELETE`
- Path: `/api/v3/order/batch`
- Category: `order`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 10`
- Source: https://www.weex.com/api-doc/spot/orderApi/BulkCancel

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `orderIds` | `Array` | `Conditional` | Order IDs to cancel. Required when origClientOrderIds is empty. |
| `origClientOrderIds` | `Array` | `Conditional` | Client order IDs to cancel. Required when orderIds is empty. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `orderList` | `Array<Object>` | Per-order cancel result. |
| `-> orderId` | `Long` | Cancelled order ID (if found). |
| `-> status` | `String` | Final status, e.g. CANCELED . |
| `-> errorMsg` | `String` | Error message when cancellation failed. |

## spot.order.bulk_order — Batch Place Orders (TRADE)

- Method: `POST`
- Path: `/api/v3/order/batch`
- Category: `order`
- Requires Auth: `True`
- Weight(IP/UID): `10 / 50`
- Source: https://www.weex.com/api-doc/spot/orderApi/BulkOrder

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair, e.g. BTCUSDT . |
| `orderList` | `Array<Object>` | `Yes` | Up to 10 order definitions. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `orderList` | `Array<Object>` | Per-order result list. |
| `-> symbol` | `String` | Trading pair. |
| `-> orderId` | `Long` | Created order ID (present when successful). |
| `-> clientOrderId` | `String` | Client-defined order ID. |
| `-> transactTime` | `Long` | Order acceptance time (ms). |
| `-> errorCode` | `String` | Error code when the order failed. |
| `-> errorMsg` | `String` | Error message when the order failed. |

## spot.order.cancel_order — Cancel Order (TRADE)

- Method: `DELETE`
- Path: `/api/v3/order`
- Category: `order`
- Requires Auth: `True`
- Weight(IP/UID): `1 / 1`
- Source: https://www.weex.com/api-doc/spot/orderApi/CancelOrder

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `orderId` | `Long` | `Conditional` | Order ID to cancel. Required when origClientOrderId is not supplied. |
| `origClientOrderId` | `String` | `Conditional` | Client order ID to cancel. Required when orderId is not supplied. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `orderId` | `Long` | Cancelled order ID. |
| `status` | `String` | Final status (e.g. CANCELED ). |

## spot.order.cancel_symbol_orders — Cancel All Orders by Symbol (TRADE)

- Method: `DELETE`
- Path: `/api/v3/openOrders`
- Category: `order`
- Requires Auth: `True`
- Weight(IP/UID): `1 / 1`
- Source: https://www.weex.com/api-doc/spot/orderApi/Cancel-Symbol-Orders

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair whose open orders should be cancelled. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `orderId` | `Long` | Cancelled order ID. |
| `status` | `String` | Final order status. |

## spot.order.history_orders — Get All Orders (USER_DATA)

- Method: `GET`
- Path: `/api/v3/allOrders`
- Category: `order`
- Requires Auth: `True`
- Weight(IP/UID): `10 / 10`
- Source: https://www.weex.com/api-doc/spot/orderApi/HistoryOrders

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair to query. |
| `startTime` | `Long` | `No` | Start time in milliseconds. |
| `endTime` | `Long` | `No` | End time in milliseconds. Must be greater than or equal to startTime . |
| `limit` | `Integer` | `No` | Number of records per page (default 100, maximum 1000). |
| `page` | `Integer` | `No` | Page index starting from 1 (default 1). |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair. |
| `orderId` | `Long` | Order ID. |
| `clientOrderId` | `String` | Client-defined order ID. |
| `price` | `String` | Order price. |
| `origQty` | `String` | Original order quantity. |
| `executedQty` | `String` | Filled quantity. |
| `cummulativeQuoteQty` | `String` | Filled amount in quote asset. |
| `status` | `String` | Order status (e.g. NEW , FILLED , CANCELED ). |
| `timeInForce` | `String` | Time-in-force policy. |
| `type` | `String` | Order type. |
| `side` | `String` | BUY or SELL . |
| `time` | `Long` | Creation time (ms). |
| `updateTime` | `Long` | Last update time (ms). |
| `isWorking` | `Boolean` | Whether the order is active. |

## spot.order.order_details — Get Order Details (USER_DATA)

- Method: `GET`
- Path: `/api/v3/order`
- Category: `order`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 2`
- Source: https://www.weex.com/api-doc/spot/orderApi/OrderDetails

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `orderId` | `Long` | `Conditional` | Order ID. Required when origClientOrderId is not supplied. |
| `origClientOrderId` | `String` | `Conditional` | Client order ID. Required when orderId is not supplied. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair. |
| `orderId` | `Long` | Order ID. |
| `clientOrderId` | `String` | Client-defined order ID. |
| `price` | `String` | Order price. |
| `origQty` | `String` | Original order quantity. |
| `executedQty` | `String` | Filled quantity. |
| `cummulativeQuoteQty` | `String` | Filled amount in quote asset. |
| `status` | `String` | Order status (e.g. NEW , FILLED , CANCELED ). |
| `timeInForce` | `String` | Time-in-force policy. |
| `type` | `String` | Order type. |
| `side` | `String` | BUY or SELL . |
| `time` | `Long` | Creation time (ms). |
| `updateTime` | `Long` | Last update time (ms). |
| `isWorking` | `Boolean` | Whether the order is active. |

## spot.order.place_order — Place Order (TRADE)

- Method: `POST`
- Path: `/api/v3/order`
- Category: `order`
- Requires Auth: `True`
- Weight(IP/UID): `2 / 5`
- Source: https://www.weex.com/api-doc/spot/orderApi/PlaceOrder

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair, e.g. BTCUSDT . |
| `side` | `String` | `Yes` | Order side. Supported values: BUY , SELL . |
| `type` | `String` | `Yes` | Order type. Supported values: LIMIT , MARKET . |
| `timeInForce` | `String` | `Conditional` | Time-in-force policy. Required when type = LIMIT . Supported values: GTC , IOC , FOK . |
| `quantity` | `String` | `Yes` | Order quantity. |
| `price` | `String` | `Conditional` | Limit price. Required when type = LIMIT . |
| `newClientOrderId` | `String` | `No` | Client-defined order ID (if omitted, the system assigns one). If an active order already uses the same newClientOrderId , the API returns success but does not create a duplicate order. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair. |
| `orderId` | `Long` | Order ID generated by the system. |
| `clientOrderId` | `String` | Client-defined order ID. |
| `transactTime` | `Long` | Order acceptance timestamp (ms). |

## spot.order.transaction_details — Get Trade History (USER_DATA)

- Method: `GET`
- Path: `/api/v3/myTrades`
- Category: `order`
- Requires Auth: `True`
- Weight(IP/UID): `5 / 5`
- Source: https://www.weex.com/api-doc/spot/orderApi/TransactionDetails

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `Yes` | Trading pair. |
| `orderId` | `Long` | `No` | Filter by order ID. |
| `startTime` | `Long` | `No` | Start time (ms). |
| `endTime` | `Long` | `No` | End time (ms). Must be â¥ startTime . |
| `limit` | `Integer` | `No` | Page size (default 100). |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair. |
| `id` | `Long` | Trade identifier. |
| `orderId` | `Long` | Related order ID. |
| `price` | `String` | Trade price. |
| `qty` | `String` | Filled quantity (base asset). |
| `quoteQty` | `String` | Filled amount (quote asset). |
| `commission` | `String` | Commission amount. |
| `time` | `Long` | Trade time (ms). |
| `isBuyer` | `Boolean` | Whether the user was the buyer. |

## spot.order.unfinished_orders — Get Current Open Orders (USER_DATA)

- Method: `GET`
- Path: `/api/v3/openOrders`
- Category: `order`
- Requires Auth: `True`
- Weight(IP/UID): `3 / 3`
- Source: https://www.weex.com/api-doc/spot/orderApi/UnfinishedOrders

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `symbol` | `String` | `No` | Filter by trading pair. If omitted, returns open orders for all symbols. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `symbol` | `String` | Trading pair. |
| `orderId` | `Long` | Order ID. |
| `clientOrderId` | `String` | Client-defined order ID. |
| `price` | `String` | Order price. |
| `origQty` | `String` | Original order quantity. |
| `executedQty` | `String` | Filled quantity. |
| `cummulativeQuoteQty` | `String` | Filled amount in quote asset. |
| `status` | `String` | Order status (e.g. NEW , PARTIALLY_FILLED ). |
| `timeInForce` | `String` | Time-in-force policy. |
| `type` | `String` | Order type. |
| `side` | `String` | BUY or SELL . |
| `time` | `Long` | Creation time (ms). |
| `updateTime` | `Long` | Last update time (ms). |
| `isWorking` | `Boolean` | Whether the order is currently working. |

## Spot Rebate Endpoint Sections

## spot.rebate.get_affiliate_assets — Get Affiliate Member Assets

- Method: `GET`
- Path: `/api/v3/agency/getAssert`
- Category: `rebate`
- Requires Auth: `True`
- Weight(IP/UID): `20 / 20`
- Source: https://www.weex.com/api-doc/spot/rebate-endpoints/GetAffiliateAssets

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `userId` | `Long` | `Yes` | Direct customer UID |
| `startTime` | `String` | `No` | Optional start time (UTC, format yyyy-MM-dd ) |
| `endTime` | `String` | `No` | Optional end time (UTC, format yyyy-MM-dd , defaults to current if absent) |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `availableBalance` | `String` | Available balance in USDT |
| `fundingTotalUsdt` | `String` | Total funding account equity in USDT |
| `spotProTotalUsdt` | `String` | Total spot account equity in USDT |
| `unimarginTotalUsdt` | `String` | Total contract account equity in USDT |
| `depositTotalAmount` | `String` | Cumulative deposit amount within the time window |
| `depositList` | `Array` | Deposit records |

## spot.rebate.get_affiliate_commission — Get Affiliate Commission

- Method: `GET`
- Path: `/api/v3/rebate/affiliate/getAffiliateCommission`
- Category: `rebate`
- Requires Auth: `True`
- Weight(IP/UID): `20 / 20`
- Source: https://www.weex.com/api-doc/spot/rebate-endpoints/GetAffiliateCommission

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `Long` | `` | `No` | Invited User UID |
| `Long` | `` | `No` | Start timestamp in UTC (milliseconds). Default: 7 days ago,Max range: 3 months |
| `Long` | `` | `No` | End timestamp in UTC (milliseconds). Default: current time,Max range: 3 months |
| `String` | `` | `No` | USDT or BTC |
| `String` | `` | `No` | SPOT or FUTURES (default SPOT) |
| `Integer` | `` | `No` | Page number (starting from 1, default 1) |
| `Integer` | `` | `No` | Page size (default 100) |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `channelCommissionInfoItems` | `Array` | Commission records |
| `uid` | `Long` | Invited User UID |
| `date` | `Long` | Commission timestamp, unit: milliseconds |
| `coin` | `String` | USDT, BTC ... |
| `productType` | `String` | Product Type |
| `fee` | `String` | Net Trading Fee |
| `commission` | `String` | Paid Commission |
| `rate` | `String` | Rebate Rate |
| `symbol` | `String` | Trading Pair |
| `sourceType` | `Integer` | 1: Direct Client, 2: Sub-agent |
| `takerAmount` | `String` | Taker Amount (Trading Volume) |
| `makerAmount` | `String` | Maker Amount (Trading Volume) |
| `pages` | `Integer` | Total pages |
| `pageSize` | `Integer` | Page size |
| `total` | `Long` | Total records |

## spot.rebate.get_affiliate_deal_data — Get Affiliate Deal Data

- Method: `GET`
- Path: `/api/v3/agency/getDealData`
- Category: `rebate`
- Requires Auth: `True`
- Weight(IP/UID): `20 / 20`
- Source: https://www.weex.com/api-doc/spot/rebate-endpoints/GetAffiliateDealData

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `userIds` | `List<Long>` | `No` | Optional repeated query parameter (e.g. userIds=123&userIds=456 ). Defaults to all direct users |
| `startTime` | `String` | `No` | Filter start date (UTC, format yyyy-MM-dd ) |
| `endTime` | `String` | `No` | Filter end date (UTC, format yyyy-MM-dd ) |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `data` | `Array` | Trading statistics returned by the upstream service |
| `userId` | `Long` | UID of the queried customer |
| `spotDealAmountUsdt` | `String` | Spot trading volume (USDT) |
| `futuresProDealAmountUsdt` | `String` | Futures trading volume (USDT) |
| `spotProDealAmountUsdtTemp` | `String` | Spot trading volume (raw value returned by partner system) |
| `startTime` | `String` | Start date applied by the upstream service ( yyyy-MM-dd ) |
| `endTime` | `String` | End date applied by the upstream service ( yyyy-MM-dd ) |

## spot.rebate.get_affiliate_ui_ds — Get Affiliate UIDs

- Method: `GET`
- Path: `/api/v3/rebate/affiliate/getAffiliateUIDs`
- Category: `rebate`
- Requires Auth: `True`
- Weight(IP/UID): `20 / 20`
- Source: https://www.weex.com/api-doc/spot/rebate-endpoints/GetAffiliateUIDs

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `Long` | `` | `No` | Invited User UID |
| `Long` | `` | `No` | Start timestamp in UTC (milliseconds). Must fall within the past 1 year; defaults to 90 days prior to endTime when omitted. |
| `Long` | `` | `No` | End timestamp in UTC (milliseconds). Must be greater than startTime ; total range cannot exceed 90 days. |
| `Integer` | `` | `No` | Page number (starting from 1, default 1) |
| `Integer` | `` | `No` | Page size (default 100) |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `uid` | `String` | Invited User UID |
| `registerTime` | `Long` | Registration timestamp, unit: milliseconds |
| `kycResult` | `Boolean` | KYC status |
| `inviteCode` | `String` | Invitation Code |
| `firstDeposit` | `Long` | First deposit time (milliseconds) |
| `firstTrade` | `Long` | First trade time (milliseconds) |
| `lastDeposit` | `Long` | Latest deposit time (milliseconds) |
| `lastTrade` | `Long` | Latest trade time (milliseconds) |
| `channelUserInfoItemList` | `Array` | Affiliate user items |
| `pages` | `Integer` | Total pages |
| `pageSize` | `Integer` | Page size |
| `total` | `Long` | Total records |

## spot.rebate.get_channel_user_trade_and_asset — Get Affiliate Referral Data

- Method: `GET`
- Path: `/api/v3/rebate/affiliate/getChannelUserTradeAndAsset`
- Category: `rebate`
- Requires Auth: `True`
- Weight(IP/UID): `20 / 20`
- Source: https://www.weex.com/api-doc/spot/rebate-endpoints/GetChannelUserTradeAndAsset

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `Long` | `` | `No` | Invited User UID |
| `Long` | `` | `No` | Start timestamp in UTC (milliseconds) |
| `Long` | `` | `No` | End timestamp in UTC (milliseconds) |
| `Integer` | `` | `No` | Page number (starting from 1, default 1) |
| `Integer` | `` | `No` | Page size (default 100) |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `uid` | `String` | Invited User UID |
| `depositAmount` | `String` | Deposit Amount |
| `withdrawalAmount` | `String` | Withdrawal Amount |
| `spotTradingAmount` | `String` | Spot Trading Volume |
| `futuresTradingAmount` | `String` | Futures Trading Volume |
| `commission` | `String` | Commission |
| `records` | `Array` | Aggregated records |
| `pages` | `Integer` | Total pages |
| `pageSize` | `Integer` | Page size |
| `total` | `Long` | Total records |

## spot.rebate.internal_withdrawal — Internal Withdrawal

- Method: `POST`
- Path: `/api/v3/rebate/affiliate/internalWithdrawal`
- Category: `rebate`
- Requires Auth: `True`
- Weight(IP/UID): `100 / 100`
- Source: https://www.weex.com/api-doc/spot/rebate-endpoints/InternalWithdrawal

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `Long` | `` | `Yes` | Transfer-in user ID |
| `String` | `` | `Yes` | Currency type (USDT, BTC) |
| `String` | `` | `Yes` | Transfer amount (Up to 6 decimal places) |
| `String` | `` | `No` | Type of the originating account (SPOT: spot wallet, FUND: funding wallet, Default: SPOT) |
| `String` | `` | `No` | Type of the target account (SPOT: spot wallet, FUND: funding wallet, Default: SPOT) |

### Response Parameters

NONE

## spot.rebate.query_sub_channel_transactions — Get Subaffiliates Data (affiliate only)

- Method: `POST`
- Path: `/api/v3/rebate/affiliate/querySubChannelTransactions`
- Category: `rebate`
- Requires Auth: `True`
- Weight(IP/UID): `10 / 10`
- Source: https://www.weex.com/api-doc/spot/rebate-endpoints/QuerySubChannelTransactions

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `Long` | `` | `No` | Sub-affiliate's UID |
| `Long` | `` | `No` | Start timestamp (UTC milliseconds) |
| `Long` | `` | `No` | End timestamp (UTC milliseconds) |
| `String` | `` | `Yes` | Product type (SPOT or FUTURES) |
| `Integer` | `` | `No` | Page number (starts from 1, default 1) |
| `Integer` | `` | `No` | Items per page (default 100) |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `records` | `Array` | Sub-affiliate records |
| `subAffiliateUid` | `String` | Sub-affiliate ID |
| `productType` | `String` | Product Type |
| `date` | `String` | Date |
| `tradingVolume` | `String` | Total Trading Volume |
| `netTradingFee` | `String` | Net Trading Fee Collected by Platform |
| `paidCommission` | `String` | Actual Rebate Amount |
| `total` | `Long` | Total records |
| `size` | `Integer` | Page size |
| `current` | `Integer` | Current page |
| `pages` | `Integer` | Total pages |

## spot.rebate.verify_referrals — Verify Referrals

- Method: `GET`
- Path: `/api/v3/agency/verifyReferrals`
- Category: `rebate`
- Requires Auth: `True`
- Weight(IP/UID): `20 / 20`
- Source: https://www.weex.com/api-doc/spot/rebate-endpoints/VerifyReferrals

### Request Parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `userIds` | `String` | `Yes` | Comma-separated UID list (for example 12345,67890 ). Supports up to 100 UIDs per call. |

### Response Parameters

| Name | Type | Description |
|---|---|---|
| `uid` | `Long` | UID that was checked |
| `isRefferal` | `Boolean` | true if the UID belongs to the current affiliate |
