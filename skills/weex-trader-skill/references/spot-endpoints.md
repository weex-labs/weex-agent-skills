# WEEX Spot Endpoints (Compact)

Primary local definitions:
- `references/spot-api-definitions.json`
- `references/spot-api-definitions.md`

Docs roots:
- https://www.weex.com/api-doc/spot/introduction/APIBriefIntroduction
- https://www.weex.com/api-doc/spot/changelog

Base URL:
- `https://api-spot.weex.com`

Catalog groups include:
- `spot.account.*`, `spot.config.*`, `spot.market.*`, and `spot.order.*`
- `spot.tax.*` from the current spot tax pages
- `spot.rebate.*` from the current Partner rebate pages for documentation drift coverage only; `weex_spot_api.py` excludes this group, and the seven supported read-only queries must use the Partner executor

Quick commands:

```bash
python3 scripts/weex_spot_api.py list-endpoints --pretty
python3 scripts/weex_spot_api.py call --endpoint spot.market.get_ticker_info --query '{"symbol":"BTCUSDT"}' --pretty
```

Latest trade endpoint:
- `POST /api/v3/order`
- https://www.weex.com/api-doc/spot/orderApi/PlaceOrder
