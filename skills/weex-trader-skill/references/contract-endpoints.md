# WEEX Contract Endpoints (Compact)

Primary local definitions (inside this skill):
- `references/contract-api-definitions.json`
- `references/contract-api-definitions.md`

Docs root:
- https://www.weex.com/api-doc/contract/log/changelog

Key transaction endpoint:
- `POST /capi/v3/order`
- https://www.weex.com/api-doc/contract/Transaction_API/PlaceOrder

Other commonly used groups:
- Market: `/capi/v3/market/*`
- Account: `/capi/v3/account/*`
- Transaction: `/capi/v3/*`
- Simulated futures trading: `sim.*` entries in `references/contract-api-definitions.json`

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
