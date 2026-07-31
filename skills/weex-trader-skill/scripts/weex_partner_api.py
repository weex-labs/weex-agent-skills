#!/usr/bin/env python3
"""Strict read-only WEEX Partner REST executor."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence
from urllib import error, parse, request

from weex_agent_state import ensure_private_runtime_ready, refresh_agent_records
from weex_url_policy import open_weex_request


PARTNER_BASE_URL = "https://api-spot.weex.com"
PARTNER_PRODUCTION_ENVIRONMENT = "partner_production"
PARTNER_TEST_ENVIRONMENT = "partner_test"
PARTNER_ENVIRONMENTS = frozenset(
    {PARTNER_PRODUCTION_ENVIRONMENT, PARTNER_TEST_ENVIRONMENT}
)
PARTNER_TEST_HOST_SUFFIX = ".weex.tech"
HOST_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
DEFAULT_TIMEOUT = 30.0
DEFAULT_LOCALE = "en-US"
PARTNER_OVERRIDE_ENV_VARS = (
    "WEEX_PARTNER_API_BASE",
    "WEEX_SPOT_API_BASE",
    "WEEX_API_BASE",
)
AUTHENTICATION_CODES = {
    "-1040",
    "-1041",
    "-1042",
    "-1043",
    "-1044",
    "-1046",
    "-1047",
    "-1049",
}
PERMISSION_CODES = {
    "-1050",
    "-1051",
    "-1052",
    "-1053",
    "-1055",
    "-1056",
    "-1057",
    "-1058",
    "-1190",
}
VALIDATION_CODES = {
    "-1045",
    "-1128",
    "-1135",
    "-1140",
    "-1141",
    "-1142",
    "-1150",
    "-1160",
    "-1170",
    "-1171",
}
UPSTREAM_CODES = {"-1000", "-1054"}
REDACTED_HEADER_NAMES = {
    "ACCESS-KEY",
    "ACCESS-PASSPHRASE",
    "ACCESS-SIGN",
}
STABLE_ERROR_MESSAGES = {
    "authentication": "Partner authentication failed.",
    "permission": "Partner permission validation failed.",
    "rate_limit": "Partner rate limit was reached.",
    "validation": "Partner request validation failed.",
    "transport": "Partner transport failed.",
    "schema": "Partner response schema validation failed.",
    "upstream": "Partner upstream service failed.",
}


class PartnerPolicyError(ValueError):
    """Raised before credentials are loaded when a Partner request is unsafe."""


@dataclass(frozen=True)
class Endpoint:
    key: str
    category: str
    title: str
    method: str
    path: str
    requires_auth: bool
    operation_class: str
    weight: int
    query_fields: tuple[str, ...]
    body_fields: tuple[str, ...]
    doc_url: str


def _definition_path() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "partner-api-definitions.json"


def load_endpoint_map() -> Dict[str, Endpoint]:
    payload = json.loads(_definition_path().read_text(encoding="utf-8"))
    endpoints: Dict[str, Endpoint] = {}
    for item in payload.get("definitions", []):
        endpoint = Endpoint(
            key=str(item["key"]),
            category=str(item.get("category", "partner")),
            title=str(item.get("title", "")),
            method=str(item["method"]).upper(),
            path=str(item["path"]),
            requires_auth=bool(item.get("requires_auth", True)),
            operation_class=str(item.get("operation_class", "")),
            weight=int(item.get("weight", 0)),
            query_fields=tuple(str(value) for value in item.get("query_fields", [])),
            body_fields=tuple(str(value) for value in item.get("body_fields", [])),
            doc_url=str(item.get("doc_url", "")),
        )
        if endpoint.operation_class != "read":
            raise PartnerPolicyError(f"Partner endpoint {endpoint.key!r} is not read-only")
        endpoints[endpoint.key] = endpoint
    if len(endpoints) != 7:
        raise PartnerPolicyError("Partner endpoint allowlist must contain exactly seven entries")
    return endpoints


ENDPOINTS = load_endpoint_map()


def _invalid_partner_origin() -> PartnerPolicyError:
    return PartnerPolicyError(
        "Partner credentials may only be sent to the production origin or a saved "
        "HTTPS subdomain under the approved WEEX test-domain suffix."
    )


def _validate_host_labels(hostname: str) -> None:
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError as exc:
        raise _invalid_partner_origin() from exc
    labels = hostname.split(".")
    if not labels or any(not HOST_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise _invalid_partner_origin()


def classify_partner_origin(raw_url: str) -> tuple[str, str]:
    value = str(raw_url or "")
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or any(ord(character) < 33 or ord(character) == 127 for character in value)
    ):
        raise _invalid_partner_origin()
    try:
        parsed = parse.urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise _invalid_partner_origin() from exc
    hostname = parsed.hostname or ""
    _validate_host_labels(hostname)
    if (
        parsed.scheme != "https"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise _invalid_partner_origin()
    canonical_origin = f"https://{hostname}"
    if value not in {canonical_origin, f"{canonical_origin}/"}:
        raise _invalid_partner_origin()
    if canonical_origin == PARTNER_BASE_URL:
        return canonical_origin, PARTNER_PRODUCTION_ENVIRONMENT
    if hostname != "weex.tech" and hostname.endswith(PARTNER_TEST_HOST_SUFFIX):
        return canonical_origin, PARTNER_TEST_ENVIRONMENT
    raise _invalid_partner_origin()


def validate_partner_base_url(raw_url: str) -> str:
    return classify_partner_origin(raw_url)[0]


def resolve_partner_origin(
    profile_spot_base_url: str = "",
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[str, str]:
    env = os.environ if environ is None else environ
    configured_names = sorted(
        name
        for name in PARTNER_OVERRIDE_ENV_VARS
        if str(env.get(name, "")).strip()
    )
    if configured_names:
        raise PartnerPolicyError(
            "Partner requests do not accept API base URL environment overrides "
            f"({', '.join(configured_names)})."
        )
    profile_origin = str(profile_spot_base_url or "")
    if not profile_origin:
        return PARTNER_BASE_URL, PARTNER_PRODUCTION_ENVIRONMENT
    return classify_partner_origin(profile_origin)


def reject_partner_base_overrides(
    *,
    profile_spot_base_url: str = "",
    environ: Optional[Mapping[str, str]] = None,
) -> None:
    resolve_partner_origin(profile_spot_base_url, environ)


def _partner_preflight_runtime_summary(records: Mapping[str, Any]) -> Dict[str, Any]:
    runtime = records.get("runtime")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    host = runtime.get("host")
    host = host if isinstance(host, Mapping) else {}
    env_validation = runtime.get("env_validation")
    env_validation = env_validation if isinstance(env_validation, Mapping) else {}
    missing_modules = host.get("missing_modules")
    missing_modules = missing_modules if isinstance(missing_modules, list) else []
    issues = env_validation.get("issues")
    issues = issues if isinstance(issues, list) else []
    return {
        "requirements_ready": bool(host.get("requirements_ready")),
        "missing_modules": [str(value) for value in missing_modules],
        "env_validation": {
            "ok": bool(env_validation.get("ok")),
            "issue_count": len(issues),
        },
    }


def _partner_preflight_vault_summary(records: Mapping[str, Any]) -> Dict[str, Any]:
    runtime = records.get("runtime")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    vault = runtime.get("vault")
    vault = vault if isinstance(vault, Mapping) else {}
    return {
        "configured": bool(vault.get("configured")),
        "state": str(vault.get("state") or "unknown"),
        "action_required": vault.get("action_required"),
    }


def _partner_preflight_failure(
    *,
    profile_ref: str,
    runtime: Mapping[str, Any],
    vault: Mapping[str, Any],
    category: str,
    code: str,
    message: str,
    recovery_action: str,
    environment: Optional[str] = None,
    profile: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "request_sent": False,
        "api_domain": "partner",
        "environment": environment,
        "capability_mode": "read_only_query",
        "runtime": dict(runtime),
        "vault": dict(vault),
        "profile": dict(profile or {"requested": profile_ref}),
        "error": {
            "category": category,
            "code": code,
            "message": message,
            "recovery_action": recovery_action,
        },
    }


def build_partner_preflight_envelope(
    records: Mapping[str, Any],
    *,
    profile_ref: str,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Project refreshed trader state into a Partner-safe tool response."""
    requested_profile = str(profile_ref or "").strip()
    runtime = _partner_preflight_runtime_summary(records)
    vault = _partner_preflight_vault_summary(records)
    if not requested_profile:
        return _partner_preflight_failure(
            profile_ref=requested_profile,
            runtime=runtime,
            vault=vault,
            category="local_policy",
            code="profile_required",
            message="Partner preflight requires a saved profile name or ID.",
            recovery_action="Choose one saved profile before running a Partner query.",
        )
    if (
        not runtime["requirements_ready"]
        or runtime["missing_modules"]
        or not runtime["env_validation"]["ok"]
    ):
        return _partner_preflight_failure(
            profile_ref=requested_profile,
            runtime=runtime,
            vault=vault,
            category="runtime_preflight",
            code="private_runtime_not_ready",
            message="Private runtime preflight failed before Partner profile resolution.",
            recovery_action="Repair the reported runtime dependency or environment validation issue, then rerun Partner preflight.",
        )

    init = records.get("init")
    init = init if isinstance(init, Mapping) else {}
    profiles = init.get("profiles")
    profiles = profiles if isinstance(profiles, Mapping) else {}
    summary = profiles.get("summary")
    summary = summary if isinstance(summary, list) else []
    matches = [
        item
        for item in summary
        if isinstance(item, Mapping)
        and requested_profile in {str(item.get("id") or ""), str(item.get("name") or "")}
    ]
    if len(matches) != 1:
        return _partner_preflight_failure(
            profile_ref=requested_profile,
            runtime=runtime,
            vault=vault,
            category="profile_vault",
            code="profile_unavailable",
            message="The requested saved profile could not be resolved uniquely.",
            recovery_action="Choose an existing saved profile by its exact name or stable ID.",
        )

    selected = matches[0]
    safe_profile = {
        "resolved_profile_id": str(selected.get("id") or ""),
        "name": str(selected.get("name") or ""),
    }
    try:
        _origin, environment = resolve_partner_origin(
            str(selected.get("spot_base_url") or ""),
            os.environ if environ is None else environ,
        )
    except PartnerPolicyError:
        return _partner_preflight_failure(
            profile_ref=requested_profile,
            runtime=runtime,
            vault=vault,
            profile=safe_profile,
            category="local_policy",
            code="invalid_partner_origin",
            message="The saved profile does not resolve to an allowed Partner environment.",
            recovery_action="Repair the saved profile Partner origin before querying.",
        )

    if not vault["configured"] or vault["state"] != "unlocked":
        return _partner_preflight_failure(
            profile_ref=requested_profile,
            runtime=runtime,
            vault=vault,
            profile=safe_profile,
            environment=environment,
            category="profile_vault",
            code="vault_unavailable",
            message="The Application Vault is not ready for a private Partner query.",
            recovery_action="Configure or unlock the existing Application Vault, then rerun Partner preflight.",
        )

    return {
        "ok": True,
        "request_sent": False,
        "api_domain": "partner",
        "environment": environment,
        "capability_mode": "read_only_query",
        "runtime": runtime,
        "vault": vault,
        "profile": safe_profile,
        "error": None,
    }


def encode_query(query: Mapping[str, Any]) -> str:
    return parse.urlencode(query, doseq=True)


def compact_json(value: Optional[Mapping[str, Any]]) -> str:
    if not value:
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sign_request(
    *,
    secret: str,
    timestamp_ms: str,
    method: str,
    path: str,
    query_string: str,
    body_string: str,
) -> str:
    message = f"{timestamp_ms}{method.upper()}{path}"
    if query_string:
        message += f"?{query_string}"
    message += body_string
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _validate_endpoint_parameters(
    endpoint: Endpoint,
    query: Mapping[str, Any],
    body: Mapping[str, Any],
) -> None:
    if endpoint.operation_class != "read" or not endpoint.requires_auth:
        raise PartnerPolicyError(f"Partner endpoint {endpoint.key!r} is not an authenticated read")
    unknown_query = sorted(set(query) - set(endpoint.query_fields))
    unknown_body = sorted(set(body) - set(endpoint.body_fields))
    if unknown_query or unknown_body:
        raise PartnerPolicyError(
            f"Unexpected Partner parameters for {endpoint.key}: "
            f"query={unknown_query}, body={unknown_body}"
        )
    if endpoint.method == "GET" and body:
        raise PartnerPolicyError("GET Partner requests do not accept a body")
    if endpoint.method == "POST" and query:
        raise PartnerPolicyError("The read-only Partner POST does not accept query parameters")


def prepare_signed_request(
    *,
    endpoint: Endpoint,
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    timestamp_ms: str,
    query: Optional[Mapping[str, Any]] = None,
    body: Optional[Mapping[str, Any]] = None,
    locale: str = DEFAULT_LOCALE,
    base_url: str = PARTNER_BASE_URL,
) -> Dict[str, Any]:
    canonical_base_url = validate_partner_base_url(base_url)
    query_payload = dict(query or {})
    body_payload = dict(body or {})
    _validate_endpoint_parameters(endpoint, query_payload, body_payload)
    if not api_key or not api_secret or not api_passphrase:
        raise PartnerPolicyError("Saved profile credentials are incomplete")

    query_string = encode_query(query_payload)
    body_string = compact_json(body_payload)
    signature = sign_request(
        secret=api_secret,
        timestamp_ms=timestamp_ms,
        method=endpoint.method,
        path=endpoint.path,
        query_string=query_string,
        body_string=body_string,
    )
    url = f"{canonical_base_url}{endpoint.path}"
    if query_string:
        url += f"?{query_string}"
    return {
        "method": endpoint.method,
        "url": url,
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "locale": locale,
            "User-Agent": "weex-trader-skill-partner/1.0",
            "ACCESS-KEY": api_key,
            "ACCESS-PASSPHRASE": api_passphrase,
            "ACCESS-TIMESTAMP": timestamp_ms,
            "ACCESS-SIGN": signature,
        },
        "data": body_string.encode("utf-8") if endpoint.method == "POST" else None,
        "query": query_payload,
        "body": body_payload,
    }


def _replace_secret_values(value: str, secrets: Sequence[str]) -> str:
    result = value
    for secret in secrets:
        if secret:
            result = re.sub(re.escape(secret), "***", result, flags=re.IGNORECASE)
    return result


def sanitize_for_output(value: Any, secret_values: Iterable[str] = ()) -> Any:
    secrets = tuple(str(secret) for secret in secret_values if str(secret))
    if isinstance(value, Mapping):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = _replace_secret_values(str(key), secrets)
            if key_text.upper() in REDACTED_HEADER_NAMES:
                sanitized[key_text] = "***"
            else:
                sanitized[key_text] = sanitize_for_output(item, secrets)
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_output(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_for_output(item, secrets) for item in value)
    if isinstance(value, str):
        return _replace_secret_values(value, secrets)
    return value


def _partner_origin_secret_values(origin: str) -> tuple[str, ...]:
    hostname = parse.urlsplit(origin).hostname or ""
    values = {
        origin,
        hostname,
        origin.replace("/", "\\/"),
        parse.quote(origin, safe=""),
        parse.quote(hostname, safe=""),
    }
    return tuple(sorted((value for value in values if value), key=len, reverse=True))


def _normalize_partner_leak_text(value: str) -> str:
    normalized = value
    for _ in range(4):
        previous = normalized
        normalized = normalized.replace("\\/", "/")
        normalized = re.sub(r"\\u002f", "/", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\\u002e", ".", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\\u003a", ":", normalized, flags=re.IGNORECASE)
        normalized = parse.unquote(normalized)
        if normalized == previous:
            break
    return normalized.casefold()


def _contains_partner_origin(value: str, origin: str) -> bool:
    hostname = parse.urlsplit(origin).hostname or ""
    return bool(hostname) and _normalize_partner_leak_text(hostname) in _normalize_partner_leak_text(value)


def _redact_partner_origin_content(value: Any, origin: str) -> Any:
    if isinstance(value, Mapping):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            safe_key = "***" if _contains_partner_origin(key_text, origin) else key_text
            redacted[safe_key] = _redact_partner_origin_content(item, origin)
        return redacted
    if isinstance(value, list):
        return [_redact_partner_origin_content(item, origin) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_partner_origin_content(item, origin) for item in value)
    if isinstance(value, str) and _contains_partner_origin(value, origin):
        return "***"
    return value


def _partner_origin_remains(value: Any, origin: str) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_partner_origin(str(key), origin)
            or _partner_origin_remains(item, origin)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_partner_origin_remains(item, origin) for item in value)
    return isinstance(value, str) and _contains_partner_origin(value, origin)


def sanitize_partner_result(
    value: Mapping[str, Any],
    *,
    origin: str,
    environment: str,
    secret_values: Iterable[str] = (),
) -> Dict[str, Any]:
    redacted_values = tuple(secret_values)
    if environment == PARTNER_TEST_ENVIRONMENT:
        redacted_values += _partner_origin_secret_values(origin)
    sanitized = sanitize_for_output(value, redacted_values)
    if environment == PARTNER_TEST_ENVIRONMENT:
        sanitized = _redact_partner_origin_content(sanitized, origin)
        if _partner_origin_remains(sanitized, origin):
            return {
                "ok": False,
                "status": None,
                "rate_limit": {"used": {}, "remaining": {}},
                "retry": {"automatic": False},
                "error": {
                    "category": "schema",
                    "http_status": None,
                    "code": "partner_output_redaction_failed",
                    "message": "Partner output redaction validation failed.",
                    "recovery_action": "Stop using this response and review the Partner output policy.",
                },
                "api_domain": "partner",
                "environment": PARTNER_TEST_ENVIRONMENT,
                "capability_mode": "read_only_query",
            }
    return dict(sanitized)


def _header_value(headers: Mapping[str, Any], name: str) -> Optional[str]:
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return str(value)
    return None


def _rate_limit_metadata(headers: Mapping[str, Any]) -> Dict[str, Dict[str, int]]:
    result: Dict[str, Dict[str, int]] = {"used": {}, "remaining": {}}
    for key, value in headers.items():
        upper = str(key).upper()
        bucket: Optional[str] = None
        group: Optional[str] = None
        if upper.startswith("X-USED-WEIGHT-"):
            group = "used"
            bucket = upper.removeprefix("X-USED-WEIGHT-")
        elif upper.startswith("X-REMAINING-WEIGHT-"):
            group = "remaining"
            bucket = upper.removeprefix("X-REMAINING-WEIGHT-")
        if group and bucket:
            try:
                result[group][bucket] = int(str(value))
            except ValueError:
                continue
    return result


def _business_code(payload: Any) -> Optional[str]:
    if not isinstance(payload, Mapping):
        return None
    for name in ("code", "errorCode"):
        if name in payload and payload[name] is not None:
            value = payload[name]
            if isinstance(value, (Mapping, list, tuple, set)):
                continue
            return str(value)
    return None


def classify_error(status: Optional[int], payload: Any) -> str:
    code = _business_code(payload)
    if isinstance(payload, Mapping) and payload.get("raw_type") == "non_json":
        return "schema"
    if status == 429:
        return "rate_limit"
    if status == 401 or code in AUTHENTICATION_CODES:
        return "authentication"
    if status == 403 or code in PERMISSION_CODES:
        return "permission"
    if status == 400 or code in VALIDATION_CODES:
        return "validation"
    if status is not None and status >= 500 or code in UPSTREAM_CODES:
        return "upstream"
    if status is None:
        return "transport"
    return "upstream"


def _payload_is_business_error(payload: Any) -> bool:
    if isinstance(payload, Mapping) and payload.get("raw_type") == "non_json":
        return True
    code = _business_code(payload)
    return code not in {None, "0", "00000", "200"}


def _safe_error_message(payload: Any) -> str:
    if isinstance(payload, Mapping):
        for field in ("msg", "message"):
            value = payload.get(field)
            if value is not None and not isinstance(value, (Mapping, list, tuple, set)):
                return str(value)
    return "Partner API request failed."


def _safe_error_details(payload: Any) -> Dict[str, Any]:
    details: Dict[str, Any] = {"values_hidden": True}
    if isinstance(payload, Mapping):
        safe_field_names = {
            "code", "errorCode", "msg", "message", "details", "raw_type",
        }
        details["field_names"] = sorted(
            str(key) for key in payload if key in safe_field_names
        )
        unknown_field_count = sum(1 for key in payload if key not in safe_field_names)
        if unknown_field_count:
            details["unknown_field_count"] = unknown_field_count
        if payload.get("raw_type") == "non_json":
            details["raw_type"] = "non_json"
    else:
        details["value_type"] = type(payload).__name__
    return details


def normalize_http_result(
    *,
    status: Optional[int],
    headers: Optional[Mapping[str, Any]],
    payload: Any,
    environment: str = PARTNER_PRODUCTION_ENVIRONMENT,
) -> Dict[str, Any]:
    if environment not in PARTNER_ENVIRONMENTS:
        raise PartnerPolicyError("Partner response environment is invalid.")
    response_headers = dict(headers or {})
    successful_http = status is not None and 200 <= status < 300
    invalid_business_code_type = isinstance(payload, Mapping) and any(
        isinstance(payload.get(field), (Mapping, list, tuple, set))
        for field in ("code", "errorCode")
        if field in payload
    )
    ok = (
        successful_http
        and not invalid_business_code_type
        and not _payload_is_business_error(payload)
    )
    result: Dict[str, Any] = {
        "ok": ok,
        "status": status,
        "rate_limit": _rate_limit_metadata(response_headers),
        "retry": {"automatic": False},
    }
    if ok:
        result["data"] = payload
    else:
        category = (
            "schema"
            if invalid_business_code_type and successful_http
            else classify_error(status, payload)
        )
        safe_details = _safe_error_details(payload)
        if category == "schema":
            if invalid_business_code_type:
                safe_details["schema_issue"] = "invalid_business_code_type"
            elif isinstance(payload, Mapping) and payload.get("raw_type") == "non_json":
                safe_details["schema_issue"] = "non_json_response"
        recovery_actions = {
            "authentication": "Verify the saved API key, passphrase, timestamp, and Vault credentials.",
            "permission": "Verify Partner API permission, IP allowlist, and account status with WEEX.",
            "rate_limit": "Stop requests and wait for WEEX rate-limit weight to recover; do not retry automatically.",
            "validation": "Correct the request parameters and submit a new query.",
            "transport": "Check network connectivity and submit a new query; no automatic retry was attempted.",
            "schema": "Stop using this response and verify the current WEEX Partner response contract.",
            "upstream": "Wait for the WEEX service to recover and submit a new query manually.",
        }
        result["error"] = {
            "category": category,
            "http_status": status,
            "code": _business_code(payload),
            "message": (
                STABLE_ERROR_MESSAGES[category]
                if (
                    environment == PARTNER_TEST_ENVIRONMENT
                    or category in {"transport", "schema"}
                )
                else _safe_error_message(payload)
            ),
            "details": safe_details,
            "recovery_action": recovery_actions[category],
        }
    return result


def _local_failure(
    *,
    endpoint: Endpoint,
    profile_ref: str,
    category: str,
    code: str,
    message: str,
    recovery_action: str,
    environment: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "status": None,
        "rate_limit": {"used": {}, "remaining": {}},
        "retry": {"automatic": False},
        "error": {
            "category": category,
            "http_status": None,
            "code": code,
            "message": message,
            "recovery_action": recovery_action,
        },
        "endpoint": endpoint.key,
        "method": endpoint.method,
        "path": endpoint.path,
        "weight": endpoint.weight,
        "operation_class": endpoint.operation_class,
        "profile": {"requested": profile_ref},
        "api_domain": "partner",
        "environment": environment,
        "capability_mode": "read_only_query",
    }


def _load_profile_dependencies():
    from weex_profile_store import load_profile_credentials, resolve_profile

    return load_profile_credentials, resolve_profile


def _default_preflight(**kwargs: Any) -> None:
    ensure_private_runtime_ready(
        command=str(kwargs.get("command", "partner.execute")),
        auto_setup=True,
        language=kwargs.get("language"),
    )


def _default_open(prepared: Mapping[str, Any], timeout: float):
    req = request.Request(
        url=str(prepared["url"]),
        method=str(prepared["method"]),
        data=prepared.get("data"),
        headers=dict(prepared["headers"]),
    )
    return open_weex_request(req, timeout=timeout, headers=prepared["headers"])


def _read_response(response: Any) -> tuple[int, Dict[str, Any], Any]:
    raw = response.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return int(response.status), dict(response.headers), {
            "code": None,
            "message": "Partner API returned non-JSON data",
            "raw_type": "non_json",
        }
    return int(response.status), dict(response.headers), payload


def execute_partner_request(
    payload: Mapping[str, Any],
    *,
    credential_loader: Optional[Callable[[str], Any]] = None,
    profile_resolver: Optional[Callable[[str], Any]] = None,
    preflight: Optional[Callable[..., None]] = None,
    opener: Optional[Callable[[Mapping[str, Any], float], Any]] = None,
    environ: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    endpoint_key = str(payload.get("endpoint", ""))
    endpoint = ENDPOINTS.get(endpoint_key)
    if endpoint is None or endpoint.operation_class != "read":
        raise PartnerPolicyError(f"Unsupported Partner read endpoint: {endpoint_key!r}")

    profile_ref = str(payload.get("profile", "")).strip()
    if not profile_ref:
        raise PartnerPolicyError("Partner requests require a saved profile name or ID")
    expected_environment = payload.get("expected_environment")
    if expected_environment is not None and (
        not isinstance(expected_environment, str)
        or expected_environment not in PARTNER_ENVIRONMENTS
    ):
        return _local_failure(
            endpoint=endpoint,
            profile_ref=profile_ref,
            category="local_policy",
            code="invalid_expected_environment",
            message="Expected Partner environment is invalid.",
            recovery_action="Use partner_production or partner_test after resolving the saved profile environment.",
        )
    raw_query = payload.get("query")
    raw_body = payload.get("body")
    query = {} if raw_query is None else raw_query
    body = {} if raw_body is None else raw_body
    if not isinstance(query, Mapping) or not isinstance(body, Mapping):
        return _local_failure(
            endpoint=endpoint,
            profile_ref=profile_ref,
            category="local_policy",
            code="invalid_request_container",
            message="Partner query and body must be JSON objects.",
            recovery_action="Submit query and body as JSON objects before resolving the saved profile.",
        )
    _validate_endpoint_parameters(endpoint, query, body)

    env = os.environ if environ is None else environ
    effective_timeout = timeout
    if effective_timeout is None:
        try:
            effective_timeout = float(env.get("WEEX_API_TIMEOUT", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            return _local_failure(
                endpoint=endpoint,
                profile_ref=profile_ref,
                category="local_configuration",
                code="invalid_api_timeout",
                message="WEEX_API_TIMEOUT must be a positive finite number.",
                recovery_action="Unset WEEX_API_TIMEOUT or set it to a positive number of seconds.",
            )
    if not math.isfinite(effective_timeout) or effective_timeout <= 0:
        return _local_failure(
            endpoint=endpoint,
            profile_ref=profile_ref,
            category="local_configuration",
            code="invalid_api_timeout",
            message="WEEX_API_TIMEOUT must be a positive finite number.",
            recovery_action="Unset WEEX_API_TIMEOUT or set it to a positive number of seconds.",
        )
    try:
        (preflight or _default_preflight)(
            command="partner.execute",
            language=payload.get("language"),
        )
    except Exception:
        return _local_failure(
            endpoint=endpoint,
            profile_ref=profile_ref,
            category="runtime_preflight",
            code="private_runtime_not_ready",
            message="Private runtime preflight failed before the Partner request.",
            recovery_action="Run the trader preflight/doctor guidance, repair the reported runtime issue, and retry.",
        )

    try:
        if credential_loader is None or profile_resolver is None:
            default_loader, default_resolver = _load_profile_dependencies()
            credential_loader = credential_loader or default_loader
            profile_resolver = profile_resolver or default_resolver
        profile = profile_resolver(profile_ref)
    except Exception:
        return _local_failure(
            endpoint=endpoint,
            profile_ref=profile_ref,
            category="profile_vault",
            code="profile_unavailable",
            message="The saved profile could not be resolved.",
            recovery_action="Open the trader profile manager and verify that the saved profile exists and is readable.",
        )
    if profile is None:
        return _local_failure(
            endpoint=endpoint,
            profile_ref=profile_ref,
            category="profile_vault",
            code="profile_unavailable",
            message="The saved profile could not be resolved.",
            recovery_action="Open the trader profile manager and create or repair the requested saved profile.",
        )
    try:
        partner_origin, partner_environment = resolve_partner_origin(
            str(getattr(profile, "spot_base_url", "") or ""),
            env,
        )
    except PartnerPolicyError:
        return _local_failure(
            endpoint=endpoint,
            profile_ref=profile_ref,
            category="local_policy",
            code="invalid_partner_origin",
            message=(
                "The saved Partner origin is not an allowed production or test origin."
            ),
            recovery_action="Review the saved spot base URL in the trader profile manager.",
        )
    if expected_environment is not None and expected_environment != partner_environment:
        return _local_failure(
            endpoint=endpoint,
            profile_ref=profile_ref,
            category="local_policy",
            code="expected_partner_environment_mismatch",
            message="Saved profile environment does not match the expected Partner environment.",
            recovery_action="Refresh trader preflight/profile metadata and submit a request for the resolved environment.",
            environment=partner_environment,
        )
    try:
        credentials = credential_loader(str(getattr(profile, "name", profile_ref)))
        secret_values = (
            str(credentials.api_key),
            str(credentials.api_secret),
            str(credentials.api_passphrase),
        )
        if not all(secret_values):
            raise ValueError("incomplete credentials")
    except Exception:
        return _local_failure(
            endpoint=endpoint,
            profile_ref=profile_ref,
            category="profile_vault",
            code="vault_credentials_unavailable",
            message="The saved profile credentials could not be loaded from the Application Vault.",
            recovery_action="Set up or unlock the trader Application Vault, then verify the profile credentials.",
        )
    prepared = prepare_signed_request(
        endpoint=endpoint,
        api_key=secret_values[0],
        api_secret=secret_values[1],
        api_passphrase=secret_values[2],
        timestamp_ms=str(int(time.time() * 1000)),
        query=query,
        body=body,
        locale=str(payload.get("locale") or DEFAULT_LOCALE),
        base_url=partner_origin,
    )

    try:
        response_or_context = (opener or _default_open)(prepared, effective_timeout)
        if hasattr(response_or_context, "__enter__"):
            with response_or_context as response:
                status, response_headers, response_payload = _read_response(response)
        else:
            status, response_headers, response_payload = _read_response(response_or_context)
        result = normalize_http_result(
            status=status,
            headers=response_headers,
            payload=response_payload,
            environment=partner_environment,
        )
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            response_payload = json.loads(raw)
        except json.JSONDecodeError:
            response_payload = {
                "code": None,
                "message": "Partner API returned non-JSON error data",
                "raw_type": "non_json",
            }
        result = normalize_http_result(
            status=exc.code,
            headers=dict(exc.headers or {}),
            payload=response_payload,
            environment=partner_environment,
        )
    except (error.URLError, TimeoutError, OSError) as exc:
        result = normalize_http_result(
            status=None,
            headers={},
            payload={"message": str(exc)},
            environment=partner_environment,
        )

    result.update(
        {
            "endpoint": endpoint.key,
            "method": endpoint.method,
            "path": endpoint.path,
            "weight": endpoint.weight,
            "operation_class": endpoint.operation_class,
            "profile": {
                "resolved_profile_id": str(getattr(profile, "profile_id", "")),
                "name": str(getattr(profile, "name", profile_ref)),
            },
            "api_domain": "partner",
            "environment": partner_environment,
            "capability_mode": "read_only_query",
        }
    )
    return sanitize_partner_result(
        result,
        origin=partner_origin,
        environment=partner_environment,
        secret_values=secret_values,
    )


def _read_stdin_object() -> Dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise PartnerPolicyError(f"Invalid Partner request JSON on stdin: {exc}") from exc
    if not isinstance(payload, dict):
        raise PartnerPolicyError("Partner request JSON must be an object")
    return payload


def _output(payload: Mapping[str, Any], pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strict read-only WEEX Partner REST executor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list-endpoints")
    list_parser.add_argument("--pretty", action="store_true")
    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--profile", required=True)
    preflight_parser.add_argument("--language", choices=("zh", "en"), default="en")
    preflight_parser.add_argument("--pretty", action="store_true")
    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "list-endpoints":
        rows = [
            {
                "key": endpoint.key,
                "method": endpoint.method,
                "path": endpoint.path,
                "operation_class": endpoint.operation_class,
                "weight": endpoint.weight,
            }
            for endpoint in ENDPOINTS.values()
        ]
        _output({"count": len(rows), "endpoints": rows}, args.pretty)
        return 0
    if args.command == "preflight":
        try:
            records = refresh_agent_records(
                preferred_language=args.language,
                command="partner.preflight",
                probe_default_profile_usable=False,
            )
            result = build_partner_preflight_envelope(
                records,
                profile_ref=args.profile,
            )
        except Exception:
            result = _partner_preflight_failure(
                profile_ref=args.profile,
                runtime={
                    "requirements_ready": False,
                    "missing_modules": [],
                    "env_validation": {"ok": False, "issue_count": 0},
                },
                vault={
                    "configured": False,
                    "state": "unknown",
                    "action_required": None,
                },
                category="runtime_preflight",
                code="partner_preflight_failed",
                message="Partner preflight failed before a safe profile summary could be produced.",
                recovery_action="Run the local trader doctor guidance, repair the runtime, and retry Partner preflight.",
            )
        _output(result, args.pretty)
        return 0 if result.get("ok") else 1
    try:
        result = execute_partner_request(_read_stdin_object())
    except PartnerPolicyError as exc:
        _output(
            {
                "ok": False,
                "error": {"category": "local_policy", "message": str(exc)},
                "api_domain": "partner",
                "environment": None,
                "capability_mode": "read_only_query",
            },
            args.pretty,
        )
        return 2
    except Exception:
        _output(
            {
                "ok": False,
                "error": {
                    "category": "internal_error",
                    "code": "partner_executor_failed",
                    "message": "The Partner executor failed before producing a safe response.",
                    "recovery_action": "Run the trader doctor/preflight command and retry after the reported issue is fixed.",
                },
                "api_domain": "partner",
                "environment": None,
                "capability_mode": "read_only_query",
            },
            args.pretty,
        )
        return 2
    _output(result, args.pretty)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
