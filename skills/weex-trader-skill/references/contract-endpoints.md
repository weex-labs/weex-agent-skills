# WEEX Contract Endpoints (Compact)

Primary local definitions (inside this skill):
- `references/contract-api-definitions.json`
- `references/contract-api-definitions.md`

Docs root:
- https://www.weex.com/api-doc/contract/changelog

Key transaction endpoint:
- `POST /capi/v3/order`
- https://www.weex.com/api-doc/contract/Transaction_API/PlaceOrder

Catalog groups use endpoint-key prefixes, not a promise that every URL shares one path prefix:
- Market: `market.*`; for example, `GET /capi/v3/market/ticker/24hr`
- Account: `account.*`; for example, `GET /capi/v3/account/balance`
- Transaction: `transaction.*`; for example, `POST /capi/v3/order`
- Simulated futures trading: `sim.*`; for example, `POST /capi/v3/sim/order`

List only the 4 official simulated futures endpoints:

```bash
python3 scripts/weex_contract_api.py list-endpoints --group sim --pretty
```

Use the script for full live list:

```bash
python3 scripts/weex_contract_api.py list-endpoints --pretty
```

Then call any endpoint by key:

```bash
python3 scripts/weex_contract_api.py call --endpoint <key> --query '{}' --body '{}' --pretty
```
