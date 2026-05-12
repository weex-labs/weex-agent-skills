# Auth and Signing (Compact)

REST base URLs:
- contract: `https://api-contract.weex.com`
- spot: `https://api-spot.weex.com`
- custom base URLs must use `https://` and a host under `weex.com` or `weex.tech`

Private headers:
- `ACCESS-KEY`
- `ACCESS-PASSPHRASE`
- `ACCESS-TIMESTAMP` (ms)
- `ACCESS-SIGN`

Signing message:
- no query: `timestamp + METHOD + requestPath + body`
- with query: `timestamp + METHOD + requestPath + "?" + queryString + body`

Signature:
- `Base64(HMAC_SHA256(secret, message))`

Recommended credential source:
- profile metadata in `~/.weex-trader-skill/profiles.meta.json`
- secrets in the Application Vault
  - Windows/macOS: application vault with UI-first unlock/setup flows
  - Linux: application vault with terminal/manual_once flows

Optional environment overrides still supported:
- `WEEX_TRADER_SKILL_HOME`: override the runtime state directory for profiles, vault files, and agent cache
- `WEEX_API_TIMEOUT`: override HTTP timeout in seconds for API calls

Credential source policy:
- prefer saved profiles
- private commands require a saved profile
- if private credentials are missing, fail fast and ask user to configure or fix a profile
- for server automation, save/rotate profile secrets with `--secrets-stdin-json` or `--api-key-env` / `--api-secret-env` / `--api-passphrase-env` instead of raw argv secrets
- public endpoints and endpoint-listing commands do not require a valid default profile
- an explicitly requested `--profile` must still resolve successfully
- on Linux `manual_once`, `list/show` may return `has_credentials: null` with `credentials_status: "unknown_locked"` until the vault is unlocked

Main reference:
- https://www.weex.com/api-doc/spot/QuickStart/Signature
