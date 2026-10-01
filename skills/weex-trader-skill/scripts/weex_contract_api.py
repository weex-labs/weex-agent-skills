#!/usr/bin/env python3
"""WEEX Contract REST API helper.

- Endpoint definitions loaded from references/contract-api-definitions.json
- Private auth from a secure saved profile
- Supports generic endpoint calls and deterministic convenience commands
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error, parse, request

from weex_agent_state import RuntimePreflightError, ensure_private_runtime_ready, refresh_agent_records
from weex_profile_language import resolve_language
from weex_url_policy import BaseUrlPolicyError, open_weex_request, validate_weex_base_url

ProfileError = RuntimeError
load_profile_credentials = None
resolve_profile = None


DEFAULT_BASE_URL = "https://api-contract.weex.com"
DEFAULT_LOCALE = "en-US"
DEFAULT_TIMEOUT = 15.0
DEFAULT_TRADING_MODE = "live"
TRADING_MODES = ("live", "demo")
GET_BODY_UNSUPPORTED_MESSAGE = (
    "GET requests do not accept --body. Pass request fields with --query instead."
)
PRIVATE_PROFILE_REQUIRED_MESSAGE = (
    "Private commands require a saved profile. Configure a default profile with "
    "scripts/weex_profile_manager.py or scripts/weex_profiles.py, or pass --profile <name>."
)
PROFILE_RUNTIME_DEPENDENCY_MISSING = (
    "Unable to enable saved-profile support for the WEEX Contract REST API helper "
    "because Python dependency '{module_name}' is missing. Run scripts/weex_runtime_setup.py --pretty "
    "or install requirements.lock with --require-hashes using this interpreter and retry."
)
PROFILE_RUNTIME_UNAVAILABLE = (
    "Unable to enable saved-profile support for the WEEX Contract REST API helper "
    "because its runtime dependencies are unavailable."
)


@dataclass(frozen=True)
class Endpoint:
    key: str
    group: str
    title: str
    method: str
    path: str
    auth: bool
    mutating: bool
    doc_url: str
    permission: str = ""


def load_endpoint_map() -> Dict[str, Endpoint]:
    refs = Path(__file__).resolve().parent.parent / "references" / "contract-api-definitions.json"
    obj = json.loads(refs.read_text(encoding="utf-8"))
    endpoint_map: Dict[str, Endpoint] = {}
    for d in obj.get("definitions", []):
        method = d.get("method", "GET").upper()
        auth = bool(d.get("requires_auth", False))
        ep = Endpoint(
            key=d["key"],
            group=d.get("category", ""),
            title=d.get("title", ""),
            method=method,
            path=d.get("path", ""),
            auth=auth,
            mutating=auth and method in {"POST", "PUT", "DELETE"},
            doc_url=d.get("doc_url", ""),
            permission=d.get("permission", ""),
        )
        endpoint_map[ep.key] = ep
    return endpoint_map


ENDPOINTS = load_endpoint_map()


def _load_profile_runtime_dependencies() -> None:
    global ProfileError, load_profile_credentials, resolve_profile

    if load_profile_credentials is not None and resolve_profile is not None:
        return

    try:
        from weex_profile_store import (
            ProfileError as profile_error_type,
            load_profile_credentials as load_profile_credentials_fn,
            resolve_profile as resolve_profile_fn,
        )
    except ModuleNotFoundError as exc:
        module_name = exc.name or "unknown"
        raise SystemExit(PROFILE_RUNTIME_DEPENDENCY_MISSING.format(module_name=module_name)) from exc
    except ImportError as exc:
        raise SystemExit(PROFILE_RUNTIME_UNAVAILABLE) from exc

    ProfileError = profile_error_type
    load_profile_credentials = load_profile_credentials_fn
    resolve_profile = resolve_profile_fn


def parse_json_arg(raw: str, arg_name: str) -> Dict[str, Any]:
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("@"):
        raise SystemExit(
            f"{arg_name} no longer accepts @file input. Pass a JSON object string directly."
        )
    payload = raw
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON for {arg_name}: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise SystemExit(f"{arg_name} must be a JSON object")
    return parsed


def compact_json(value: Optional[Dict[str, Any]]) -> str:
    if not value:
        return ""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class WeexContractClient:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        locale: str,
        api_key: Optional[str],
        api_secret: Optional[str],
        api_passphrase: Optional[str],
        profile_name: Optional[str] = None,
        user_agent: str = "weex-trader-skill-contract/1.0",
    ) -> None:
        try:
            self.base_url = validate_weex_base_url(base_url, label="contract base URL")
        except BaseUrlPolicyError as exc:
            raise SystemExit(str(exc)) from exc
        self.timeout = timeout
        self.locale = locale
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.profile_name = profile_name
        self.user_agent = user_agent

    def _require_auth(self) -> None:
        _load_profile_runtime_dependencies()
        if self.profile_name and (not self.api_key or not self.api_secret or not self.api_passphrase):
            try:
                creds = load_profile_credentials(self.profile_name)
            except ProfileError as exc:
                raise SystemExit(str(exc)) from exc
            self.api_key = creds.api_key
            self.api_secret = creds.api_secret
            self.api_passphrase = creds.api_passphrase
        missing = []
        if not self.api_key:
            missing.append("API Key")
        if not self.api_secret:
            missing.append("Secret Key")
        if not self.api_passphrase:
            missing.append("Passphrase")
        if missing:
            if self.profile_name:
                raise SystemExit(
                    f"Missing private API credentials in profile '{self.profile_name}'. "
                    "Update the saved profile with scripts/weex_profile_manager.py "
                    "or scripts/weex_profiles.py and retry: "
                    + ", ".join(missing)
                )
            raise SystemExit(PRIVATE_PROFILE_REQUIRED_MESSAGE)

    def _sign(self, timestamp_ms: str, method: str, path: str, query_string: str, body_str: str) -> str:
        # Per WEEX docs, message = timestamp + method + requestPath + (?queryString) + body
        message = f"{timestamp_ms}{method}{path}"
        if query_string:
            message += f"?{query_string}"
        message += body_str
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def prepare_request(
        self,
        endpoint: Endpoint,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        method = endpoint.method.upper()
        q = query or {}
        b = body or {}
        if method == "GET" and b:
            raise SystemExit(GET_BODY_UNSUPPORTED_MESSAGE)
        query_string = parse.urlencode(q, doseq=True)
        body_str = compact_json(b)

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "locale": self.locale,
            "User-Agent": self.user_agent,
        }

        if endpoint.auth:
            self._require_auth()
            timestamp_ms = str(int(time.time() * 1000))
            sign = self._sign(timestamp_ms, method, endpoint.path, query_string, body_str)
            headers.update(
                {
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-PASSPHRASE": self.api_passphrase,
                    "ACCESS-TIMESTAMP": timestamp_ms,
                    "ACCESS-SIGN": sign,
                }
            )

        url = f"{self.base_url}{endpoint.path}"
        if query_string:
            url = f"{url}?{query_string}"

        data = body_str.encode("utf-8") if body_str and method != "GET" else None

        return {
            "method": method,
            "url": url,
            "headers": headers,
            "data": data,
            "query": q,
            "body": b,
        }

    def send(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        req = request.Request(
            url=prepared["url"],
            method=prepared["method"],
            data=prepared["data"],
            headers=prepared["headers"],
        )
        try:
            with open_weex_request(req, timeout=self.timeout, headers=prepared["headers"]) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"raw": raw}
                return {"ok": True, "status": resp.status, "data": payload}
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return {
                "ok": False,
                "status": exc.code,
                "error": payload,
            }
        except error.URLError as exc:
            return {
                "ok": False,
                "status": None,
                "error": {"message": str(exc)},
            }


def sanitize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    result = dict(headers)
    if "ACCESS-KEY" in result:
        result["ACCESS-KEY"] = "***"
    if "ACCESS-PASSPHRASE" in result:
        result["ACCESS-PASSPHRASE"] = "***"
    if "ACCESS-SIGN" in result:
        result["ACCESS-SIGN"] = "***"
    return result


def output_json(payload: Dict[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    else:
        print(json.dumps(payload, ensure_ascii=False))


def normalize_trading_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    if mode not in TRADING_MODES:
        raise SystemExit(f"invalid_trading_mode: expected one of {', '.join(TRADING_MODES)}")
    return mode


def endpoint_is_demo(endpoint: Endpoint) -> bool:
    return endpoint.key.startswith("sim.") or endpoint.path.startswith("/capi/v3/sim/")


def environment_for_mode(trading_mode: str) -> Dict[str, Any]:
    mode = normalize_trading_mode(trading_mode)
    if mode == "demo":
        return {
            "trading_mode": "demo",
            "label": "demo",
            "market": "futures",
            "uses_real_funds": False,
            "notice": "This operation targets WEEX futures demo mode.",
        }
    return {
        "trading_mode": "live",
        "label": "live",
        "market": "futures",
        "uses_real_funds": True,
        "notice": "This operation targets real WEEX futures trading.",
    }


def user_environment_prefix(environment: Dict[str, Any], language: Optional[str] = None) -> str:
    resolved_language = resolve_language(language)
    mode = normalize_trading_mode(str(environment.get("trading_mode") or DEFAULT_TRADING_MODE))
    if resolved_language == "zh":
        label = "模拟盘" if mode == "demo" else "真实盘"
        return f"当前交易环境：{label}"
    label = "demo trading" if mode == "demo" else "real trading"
    return f"Current trading mode: {label}"


def add_environment_context(payload: Dict[str, Any], environment: Dict[str, Any]) -> None:
    payload["environment"] = environment
    payload["user_environment_prefix"] = user_environment_prefix(environment)


def validate_endpoint_trading_mode(endpoint: Endpoint, trading_mode: str) -> str:
    mode = normalize_trading_mode(trading_mode)
    is_demo = endpoint_is_demo(endpoint)
    if is_demo and mode != "demo":
        raise SystemExit(
            f"demo_endpoint_requires_demo_mode: endpoint {endpoint.key} requires --trading-mode demo"
        )
    if mode == "demo" and endpoint.auth and not is_demo:
        raise SystemExit(
            f"demo_endpoint_unsupported: endpoint {endpoint.key} is not a simulated futures endpoint"
        )
    return mode


def validate_confirm_flags(
    endpoint: Endpoint,
    trading_mode: str,
    dry_run: bool,
    confirm_live: bool,
    confirm_demo: bool,
) -> None:
    if confirm_live and confirm_demo:
        raise SystemExit("confirm_flag_mode_mismatch: pass only one of --confirm-live or --confirm-demo")
    if not endpoint.mutating or dry_run:
        return
    if trading_mode == "demo":
        if not confirm_demo or confirm_live:
            raise SystemExit(
                f"confirm_flag_mode_mismatch: demo mutating request for {endpoint.key} requires --confirm-demo"
            )
        return
    if not confirm_live or confirm_demo:
        raise SystemExit(
            f"confirm_flag_mode_mismatch: live mutating request for {endpoint.key} requires --confirm-live"
        )


def _upper_body_value(body: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = body.get(key)
        if value is not None:
            return str(value).strip().upper()
    return ""


def _is_directional_close_like_order(body: Dict[str, Any]) -> bool:
    side = _upper_body_value(body, "side")
    position_side = _upper_body_value(body, "positionSide", "position_side")
    return (position_side == "LONG" and side == "SELL") or (
        position_side == "SHORT" and side == "BUY"
    )


def validate_pending_order_routing(endpoint: Endpoint, body: Dict[str, Any]) -> None:
    if endpoint.key != "transaction.place_pending_order":
        return
    if _is_directional_close_like_order(body):
        raise SystemExit(
            "pending_close_requires_tp_sl: price-threshold close requests must use "
            "preview-tp-sl/confirm-tp-sl (placeTpSlOrder) because generic "
            "place_pending_order is not guaranteed reduce-only"
        )


def execute_endpoint(
    client: WeexContractClient,
    endpoint_key: str,
    query: Dict[str, Any],
    body: Dict[str, Any],
    dry_run: bool,
    confirm_live: bool,
    confirm_demo: bool,
    trading_mode: str,
    pretty: bool,
) -> int:
    endpoint = ENDPOINTS[endpoint_key]
    mode = validate_endpoint_trading_mode(endpoint, trading_mode)
    validate_confirm_flags(endpoint, mode, dry_run, confirm_live, confirm_demo)
    validate_pending_order_routing(endpoint, body)

    prepared = client.prepare_request(
        endpoint,
        query=query,
        body=body,
    )
    environment = environment_for_mode(mode) if endpoint.auth else None
    if dry_run:
        preview = {
            "dry_run": True,
            "endpoint": endpoint.key,
            "method": prepared["method"],
            "url": prepared["url"],
            "headers": sanitize_headers(prepared["headers"]),
            "query": query,
            "body": body,
        }
        if environment is not None:
            add_environment_context(preview, environment)
        output_json(preview, pretty)
        return 0

    response = client.send(prepared)
    payload = {
        "endpoint": endpoint.key,
        "method": endpoint.method,
        "path": endpoint.path,
        "status": response.get("status"),
        "ok": response.get("ok"),
        "result": response.get("data") if response.get("ok") else response.get("error"),
    }
    if environment is not None:
        add_environment_context(payload, environment)
    output_json(payload, pretty)
    return 0 if response.get("ok") else 1


def generate_client_oid() -> str:
    return f"codex-{int(time.time() * 1000)}-{secrets.token_hex(3)}"


def find_endpoint_key_by_doc_suffix(doc_suffix: str) -> str:
    target = f"/{doc_suffix}"
    for endpoint in ENDPOINTS.values():
        if endpoint.doc_url.endswith(target):
            return endpoint.key
    raise SystemExit(f"Unable to find endpoint with doc suffix {doc_suffix}")


def normalize_contract_trade_symbol(symbol: str) -> str:
    s = symbol.strip().upper().replace("-", "").replace("/", "").replace(" ", "").replace("_", "")
    if s.startswith("CMT") and s.endswith("USDT"):
        s = s[3:]
    if s.endswith("USDT") and len(s) > 4:
        return s
    raise SystemExit(f"Unsupported symbol format: {symbol}. Expected like ETHUSDT.")


def normalize_contract_demo_trade_symbol(symbol: str) -> str:
    normalized = normalize_contract_trade_symbol(symbol)
    if normalized.endswith("SUSDT"):
        return normalized
    if normalized.endswith("USDT"):
        return f"{normalized[:-4]}SUSDT"
    return normalized


def normalize_contract_symbol(symbol: str) -> str:
    return normalize_contract_trade_symbol(symbol)


def command_requires_auth(args: argparse.Namespace) -> bool:
    if args.command == "call":
        return ENDPOINTS[args.endpoint].auth
    return args.command in {"place-order", "cancel-order"}


def resolve_runtime_profile(
    requested_profile: Optional[str],
    allow_invalid_default: bool,
) -> Optional[Any]:
    try:
        _load_profile_runtime_dependencies()
    except SystemExit:
        if requested_profile is None and allow_invalid_default:
            return None
        raise

    if requested_profile:
        try:
            return resolve_profile(requested_profile)
        except ProfileError as exc:
            raise SystemExit(str(exc)) from exc
    try:
        return resolve_profile(None)
    except ProfileError as exc:
        if allow_invalid_default:
            return None
        raise SystemExit(str(exc)) from exc


def require_private_profile(profile: Optional[Any]) -> None:
    if profile is None:
        raise SystemExit(PRIVATE_PROFILE_REQUIRED_MESSAGE)


def cmd_list_endpoints(args: argparse.Namespace) -> int:
    rows = []
    for endpoint in sorted(ENDPOINTS.values(), key=lambda e: (e.group, e.key)):
        if args.group and endpoint.group != args.group:
            continue
        rows.append(
            {
                "key": endpoint.key,
                "group": endpoint.group,
                "method": endpoint.method,
                "path": endpoint.path,
                "auth": endpoint.auth,
                "mutating": endpoint.mutating,
                "permission": endpoint.permission,
                "doc_url": endpoint.doc_url,
            }
        )
    output_json({"count": len(rows), "endpoints": rows}, args.pretty)
    return 0


def cmd_call(args: argparse.Namespace, client: WeexContractClient) -> int:
    query = parse_json_arg(args.query, "--query")
    body = parse_json_arg(args.body, "--body")
    return execute_endpoint(
        client=client,
        endpoint_key=args.endpoint,
        query=query,
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        confirm_demo=args.confirm_demo,
        trading_mode=args.trading_mode,
        pretty=args.pretty,
    )


def cmd_place_order(args: argparse.Namespace, client: WeexContractClient) -> int:
    mode = normalize_trading_mode(args.trading_mode)
    body_symbol = (
        normalize_contract_demo_trade_symbol(args.symbol)
        if mode == "demo"
        else normalize_contract_trade_symbol(args.symbol)
    )
    body: Dict[str, Any] = {
        "symbol": body_symbol,
        "side": args.side.upper(),
        "positionSide": args.position_side.upper(),
        "type": args.order_type.upper(),
        "quantity": args.quantity,
        "newClientOrderId": args.new_client_order_id or generate_client_oid(),
    }
    if args.price is not None:
        body["price"] = args.price
    if args.time_in_force is not None:
        body["timeInForce"] = args.time_in_force.upper()
    if args.tp_trigger_price is not None:
        body["tpTriggerPrice"] = args.tp_trigger_price
    if args.sl_trigger_price is not None:
        body["slTriggerPrice"] = args.sl_trigger_price
    if args.tp_working_type is not None:
        body["TpWorkingType"] = args.tp_working_type.upper()
    if args.sl_working_type is not None:
        body["SlWorkingType"] = args.sl_working_type.upper()

    if body["type"] == "LIMIT":
        if "price" not in body:
            raise SystemExit("price is required when type=LIMIT")
        if "timeInForce" not in body:
            raise SystemExit("time-in-force is required when type=LIMIT")
    else:
        if "price" in body:
            raise SystemExit("price must be omitted when type=MARKET")
        if "timeInForce" in body:
            raise SystemExit("time-in-force must be omitted when type=MARKET")

    endpoint_key = (
        "sim.transaction.place_order"
        if mode == "demo"
        else find_endpoint_key_by_doc_suffix("PlaceOrder")
    )

    return execute_endpoint(
        client=client,
        endpoint_key=endpoint_key,
        query={},
        body=body,
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        confirm_demo=args.confirm_demo,
        trading_mode=args.trading_mode,
        pretty=args.pretty,
    )


def cmd_cancel_order(args: argparse.Namespace, client: WeexContractClient) -> int:
    query: Dict[str, Any] = {}
    if args.order_id:
        query["orderId"] = args.order_id
    if args.client_oid:
        query["origClientOrderId"] = args.client_oid
    if not query:
        raise SystemExit("Provide at least one of --order-id or --client-oid")

    return execute_endpoint(
        client=client,
        endpoint_key=find_endpoint_key_by_doc_suffix("CancelOrder"),
        query=query,
        body={},
        dry_run=args.dry_run,
        confirm_live=args.confirm_live,
        confirm_demo=False,
        trading_mode=DEFAULT_TRADING_MODE,
        pretty=args.pretty,
    )


def cmd_ticker(args: argparse.Namespace, client: WeexContractClient) -> int:
    return execute_endpoint(
        client=client,
        endpoint_key=find_endpoint_key_by_doc_suffix("GetSymbolPrice"),
        query={"symbol": normalize_contract_symbol(args.symbol)},
        body={},
        dry_run=False,
        confirm_live=False,
        confirm_demo=False,
        trading_mode=DEFAULT_TRADING_MODE,
        pretty=args.pretty,
    )


def cmd_poll_ticker(args: argparse.Namespace, client: WeexContractClient) -> int:
    run_count = 0
    while True:
        run_count += 1
        code = execute_endpoint(
            client=client,
            endpoint_key=find_endpoint_key_by_doc_suffix("GetSymbolPrice"),
            query={"symbol": normalize_contract_symbol(args.symbol)},
            body={},
            dry_run=False,
            confirm_live=False,
            confirm_demo=False,
            trading_mode=DEFAULT_TRADING_MODE,
            pretty=args.pretty,
        )
        if code != 0:
            return code
        if args.count > 0 and run_count >= args.count:
            return 0
        time.sleep(args.interval)


def add_trading_mode_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--trading-mode",
        choices=TRADING_MODES,
        default=DEFAULT_TRADING_MODE,
        help="Trading mode for private contract endpoints",
    )


def add_confirm_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirm-live", action="store_true", help="Allow live mutating requests")
    parser.add_argument("--confirm-demo", action="store_true", help="Allow demo mutating requests")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WEEX Contract REST API helper",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Saved profile name; omit it to use the configured default profile",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Optional contract API base URL override; leave empty to use the saved profile value or the built-in official default",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="HTTP timeout in seconds; leave empty to use WEEX_API_TIMEOUT or the built-in default",
    )
    groups = sorted({endpoint.group for endpoint in ENDPOINTS.values() if endpoint.group})

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser(
        "list-endpoints",
        help="List all supported contract REST endpoints",
        description="List the contract endpoint definitions bundled with this skill.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_list.add_argument("--group", choices=groups, default=None, help="Filter endpoints by contract endpoint group")
    p_list.add_argument("--pretty", action="store_true", help="Pretty-print JSON output for easier reading")

    p_call = sub.add_parser(
        "call",
        help="Call an endpoint by key with JSON query/body",
        description="Call a specific contract REST endpoint using raw JSON query and body payloads.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_call.add_argument("--endpoint", required=True, choices=sorted(ENDPOINTS.keys()), help="Exact endpoint key from list-endpoints")
    p_call.add_argument("--query", default="{}", help="JSON object string")
    p_call.add_argument("--body", default="{}", help="JSON object string")
    p_call.add_argument("--dry-run", action="store_true", help="Preview signed request without sending")
    add_trading_mode_argument(p_call)
    add_confirm_arguments(p_call)
    p_call.add_argument("--pretty", action="store_true", help="Pretty-print JSON output for easier reading")

    p_place = sub.add_parser(
        "place-order",
        help="Convenience wrapper for the live contract PlaceOrder doc",
        description="Place one contract order using the documented V3 fields exposed by this wrapper.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_place.add_argument("--symbol", required=True, help="Trading pair symbol, for example BTCUSDT or ETHUSDT")
    p_place.add_argument("--side", required=True, choices=["BUY", "SELL", "buy", "sell"], help="Order side: BUY opens/adds long exposure, SELL opens/adds short exposure depending on position side")
    p_place.add_argument("--position-side", required=True, choices=["LONG", "SHORT", "long", "short"], help="Position direction for the contract order")
    p_place.add_argument("--type", dest="order_type", required=True, choices=["LIMIT", "MARKET", "limit", "market"], help="Order type: LIMIT requires a price, MARKET sends immediately at market price")
    p_place.add_argument("--quantity", required=True, help="Order quantity as expected by WEEX for this contract")
    p_place.add_argument("--price", default=None, help="Limit price; usually required for LIMIT orders and omitted for MARKET orders")
    p_place.add_argument("--time-in-force", default=None, choices=["GTC", "IOC", "FOK", "gtc", "ioc", "fok"], help="Execution policy for LIMIT orders: GTC, IOC, or FOK")
    p_place.add_argument("--new-client-order-id", default=None, help="Optional client-defined order identifier; auto-generated when omitted")
    p_place.add_argument("--tp-trigger-price", default=None, help="Optional take-profit trigger price")
    p_place.add_argument("--sl-trigger-price", default=None, help="Optional stop-loss trigger price")
    p_place.add_argument("--tp-working-type", default=None, choices=["CONTRACT_PRICE", "MARK_PRICE", "contract_price", "mark_price"], help="Price source used to evaluate the take-profit trigger")
    p_place.add_argument("--sl-working-type", default=None, choices=["CONTRACT_PRICE", "MARK_PRICE", "contract_price", "mark_price"], help="Price source used to evaluate the stop-loss trigger")
    p_place.add_argument("--dry-run", action="store_true", help="Build and sign the request without sending it")
    add_trading_mode_argument(p_place)
    add_confirm_arguments(p_place)
    p_place.add_argument("--pretty", action="store_true", help="Pretty-print JSON output for easier reading")

    p_cancel = sub.add_parser(
        "cancel-order",
        help="Convenience wrapper for the live contract CancelOrder doc",
        description="Cancel one contract order by WEEX order id or client order id.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_cancel.add_argument("--order-id", default=None, help="WEEX order id to cancel")
    p_cancel.add_argument("--client-oid", default=None, help="Client order id to cancel when you do not have the WEEX order id")
    p_cancel.add_argument("--dry-run", action="store_true", help="Build and sign the cancel request without sending it")
    p_cancel.add_argument("--confirm-live", action="store_true", help="Allow the live cancel request")
    p_cancel.add_argument("--pretty", action="store_true", help="Pretty-print JSON output for easier reading")

    p_ticker = sub.add_parser(
        "ticker",
        help="Get ticker for one symbol",
        description="Fetch the current contract ticker for a single symbol.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_ticker.add_argument("--symbol", required=True, help="Trading pair symbol, for example BTCUSDT")
    p_ticker.add_argument("--pretty", action="store_true", help="Pretty-print JSON output for easier reading")

    p_poll = sub.add_parser(
        "poll-ticker",
        help="Continuously poll ticker",
        description="Repeatedly fetch the contract ticker for one symbol at a fixed interval.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_poll.add_argument("--symbol", required=True, help="Trading pair symbol, for example BTCUSDT")
    p_poll.add_argument("--interval", type=float, default=2.0, help="Seconds to wait between requests")
    p_poll.add_argument("--count", type=int, default=0, help="0 means infinite")
    p_poll.add_argument("--pretty", action="store_true", help="Pretty-print JSON output for easier reading")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    command_name = f"contract.{args.command}"
    try:
        refresh_agent_records(command=command_name)
    except Exception:
        pass

    requires_auth = command_requires_auth(args)
    if requires_auth:
        try:
            ensure_private_runtime_ready(command=command_name, auto_setup=True, language=None)
        except RuntimePreflightError as exc:
            raise SystemExit(str(exc)) from exc
    profile = resolve_runtime_profile(
        requested_profile=args.profile,
        allow_invalid_default=not requires_auth,
    )
    if requires_auth:
        require_private_profile(profile)

    env_base_url = os.getenv("WEEX_CONTRACT_API_BASE") or os.getenv("WEEX_API_BASE")
    base_url = (
        args.base_url
        or (profile.contract_base_url if profile else "")
        or env_base_url
        or DEFAULT_BASE_URL
    )
    locale = os.getenv("WEEX_LOCALE") or DEFAULT_LOCALE
    timeout = args.timeout if args.timeout is not None else float(os.getenv("WEEX_API_TIMEOUT", DEFAULT_TIMEOUT))

    client = WeexContractClient(
        base_url=base_url,
        timeout=timeout,
        locale=locale,
        api_key=None,
        api_secret=None,
        api_passphrase=None,
        profile_name=profile.name if profile else None,
    )

    if args.command == "list-endpoints":
        return cmd_list_endpoints(args)
    if args.command == "call":
        return cmd_call(args, client)
    if args.command == "place-order":
        return cmd_place_order(args, client)
    if args.command == "cancel-order":
        return cmd_cancel_order(args, client)
    if args.command == "ticker":
        return cmd_ticker(args, client)
    if args.command == "poll-ticker":
        return cmd_poll_ticker(args, client)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
