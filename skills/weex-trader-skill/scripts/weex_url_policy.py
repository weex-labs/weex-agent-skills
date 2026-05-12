#!/usr/bin/env python3
"""Shared URL policy for WEEX REST base URLs."""

from __future__ import annotations

from typing import Mapping
from urllib import request
from urllib.parse import urlparse


ALLOWED_WEEX_BASE_DOMAINS = ("weex.com", "weex.tech")
WEEX_AUTH_HEADER_NAMES = {
    "ACCESS-KEY",
    "ACCESS-PASSPHRASE",
    "ACCESS-TIMESTAMP",
    "ACCESS-SIGN",
}


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


_NO_REDIRECT_OPENER = request.build_opener(_NoRedirectHandler)


class BaseUrlPolicyError(ValueError):
    """Raised when a configured WEEX base URL violates the local safety policy."""

    def __init__(self, reason_key: str, *, label: str, host: str | None = None) -> None:
        self.reason_key = reason_key
        self.label = label
        self.host = host
        super().__init__(self.localized_message("en"))

    def localized_message(self, language: str) -> str:
        resolved_language = "zh" if language == "zh" else "en"
        messages = {
            "en": {
                "empty": "{label} cannot be empty.",
                "shape": "{label} must be a full https URL.",
                "userinfo": "{label} must not include username or password components.",
                "query_fragment": "{label} must not include query or fragment components.",
                "host": "{label} must use a weex.com or weex.tech host; got {host!r}.",
            },
            "zh": {
                "empty": "{label} 不能为空。",
                "shape": "{label} 必须是完整的 https:// URL。",
                "userinfo": "{label} 不能包含用户名或密码。",
                "query_fragment": "{label} 不能包含查询参数或 fragment。",
                "host": "{label} 必须使用 weex.com 或 weex.tech 及其子域名；当前域名为 {host!r}。",
            },
        }
        template = messages[resolved_language][self.reason_key]
        return template.format(label=self.label, host=self.host)


def _canonical_hostname(hostname: str) -> str:
    value = hostname.strip().rstrip(".").lower()
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError:
        return value


def is_allowed_weex_hostname(hostname: str) -> bool:
    canonical = _canonical_hostname(hostname)
    return any(canonical == domain or canonical.endswith(f".{domain}") for domain in ALLOWED_WEEX_BASE_DOMAINS)


def validate_weex_base_url(raw_url: str, *, label: str = "base URL") -> str:
    value = str(raw_url or "").strip()
    if not value:
        raise BaseUrlPolicyError("empty", label=label)

    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        raise BaseUrlPolicyError("shape", label=label)
    if parsed.username or parsed.password:
        raise BaseUrlPolicyError("userinfo", label=label)
    if parsed.query or parsed.fragment:
        raise BaseUrlPolicyError("query_fragment", label=label)
    if not is_allowed_weex_hostname(parsed.hostname):
        raise BaseUrlPolicyError("host", label=label, host=parsed.hostname)

    return value.rstrip("/")


def contains_weex_auth_headers(headers: Mapping[str, object]) -> bool:
    names = {str(name).upper() for name in headers}
    return bool(names & WEEX_AUTH_HEADER_NAMES)


def open_weex_request(
    req: request.Request,
    *,
    timeout: float,
    headers: Mapping[str, object],
):
    if contains_weex_auth_headers(headers):
        return _NO_REDIRECT_OPENER.open(req, timeout=timeout)
    return request.urlopen(req, timeout=timeout)
