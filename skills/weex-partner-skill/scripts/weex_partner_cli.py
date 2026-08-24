#!/usr/bin/env python3
"""Deterministic orchestration for the seven WEEX Partner query operations."""

from __future__ import annotations

import argparse
import calendar
import copy
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


CONTRACT_VERSION = "partner-v3-2026-06-22"
RESULT_MODES = {"summary_with_first_20", "complete_list", "aggregate_all"}
PRODUCT_TYPES = {"SPOT", "FUTURES"}
ACCOUNT_TYPES = {"SPOT", "FUND"}
LANGUAGES = {"zh", "en"}
PARTNER_ENVIRONMENTS = {"partner_production", "partner_test"}
LOCAL_UNRESOLVED_ENVIRONMENT_CATEGORIES = {
    "local_configuration",
    "runtime_preflight",
    "profile_vault",
    "local_policy",
}
MAX_UID = (1 << 63) - 1
REQUEST_FIELDS = {
    "operation", "profile", "language", "scope", "time_range", "filters",
    "result_mode", "continuation", "page", "display_offset", "records_seen",
    "expected_environment",
}
SCOPE_FIELDS = {"mode", "uids", "all_confirmed"}
TIME_RANGE_FIELDS = {"start", "end"}
TIME_STATE_BASE_FIELDS = {
    "original_start", "original_end", "remaining_start", "remaining_end",
}
TIME_SEGMENT_FIELDS = {"segment_days", "segment_months"}
CONTINUATION_FIELDS = {
    "can_continue", "stop_reason", "next_end", "segment_days", "segment_months", "time_state",
    "resolved_profile_id", "environment", "expected_environment", "query_scope",
    "operation", "contract_version", "filters", "result_mode", "time_range",
    "original_time_range", "source_pagination", "actions", "restart_required",
    "restart_from_page", "usage_warnings",
}
CONTINUATION_USAGE_WARNING_CODES = {
    "continuation_reuse_may_repeat_or_overwrite",
    "offset_pagination_data_may_change",
}
SAFE_ERROR_SCHEMA_ISSUES = {
    "non_json_response",
    "invalid_business_code_type",
}
SAFE_ERROR_FIELD_NAMES = {
    "code",
    "errorCode",
    "msg",
    "message",
    "details",
    "raw_type",
}
SAFE_ERROR_VALUE_TYPES = {
    "NoneType",
    "bool",
    "float",
    "int",
    "list",
    "str",
}
SOURCE_PAGINATION_FIELDS = {
    "records_total", "pages_total", "current_page", "page_size",
    "records_seen_before", "records_on_page", "display_offset",
}


class PartnerQueryError(ValueError):
    """A local query error that must be resolved before any REST call."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class OperationPolicy:
    endpoint: str
    time_format: str = "none"
    default_days: Optional[int] = None
    max_days: Optional[int] = None
    history_days: Optional[int] = None
    max_months: Optional[int] = None
    history_months: Optional[int] = None
    time_required: bool = False
    scope_rule: str = "none"
    product_type: str = "none"
    page_field: Optional[str] = None
    page_size_field: Optional[str] = None


OPERATION_POLICIES: Dict[str, OperationPolicy] = {
    "list-referral-uids": OperationPolicy(
        endpoint="partner.get-affiliate-uids",
        time_format="milliseconds",
        default_days=90,
        max_days=90,
        history_days=365,
        scope_rule="optional_single_or_confirmed_all",
        page_field="page",
        page_size_field="pageSize",
    ),
    "get-direct-trade-asset": OperationPolicy(
        endpoint="partner.get-channel-user-trade-and-asset",
        time_format="milliseconds",
        default_days=90,
        max_days=90,
        history_days=365,
        scope_rule="optional_single_or_confirmed_all",
        page_field="page",
        page_size_field="pageSize",
    ),
    "get-commission": OperationPolicy(
        endpoint="partner.get-affiliate-commission",
        time_format="milliseconds",
        default_days=7,
        max_months=3,
        scope_rule="optional_single_or_confirmed_all",
        product_type="default_spot",
        page_field="page",
        page_size_field="pageSize",
    ),
    "get-sub-agent-stats": OperationPolicy(
        endpoint="partner.query-sub-channel-transactions",
        time_format="milliseconds",
        time_required=True,
        scope_rule="optional_single_or_confirmed_all",
        product_type="required",
        page_field="pageNum",
        page_size_field="pageSize",
    ),
    "verify-referrals": OperationPolicy(
        endpoint="partner.verify-referrals",
        scope_rule="one_or_more",
    ),
    "get-referral-assets": OperationPolicy(
        endpoint="partner.get-referral-assets",
        time_format="date",
        time_required=True,
        scope_rule="exactly_one",
    ),
    "get-referral-deal-data": OperationPolicy(
        endpoint="partner.get-referral-deal-data",
        time_format="date",
        time_required=True,
        scope_rule="one_or_more_or_confirmed_all",
    ),
}

OPERATION_FILTER_FIELDS: Dict[str, set[str]] = {
    "list-referral-uids": set(),
    "get-direct-trade-asset": set(),
    "get-commission": {"coin", "product_type"},
    "get-sub-agent-stats": {"product_type"},
    "verify-referrals": set(),
    "get-referral-assets": set(),
    "get-referral-deal-data": set(),
}


FIELD_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "references" / "partner-field-catalog.json"
)
FIELD_ROLES = {"record", "records_container", "pagination", "query_echo"}
FIELD_FORMATS = {"millisecond_timestamp", "date", "hidden_container"}


def _load_partner_field_catalog() -> Dict[str, Any]:
    try:
        payload = json.loads(FIELD_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("The official Partner field catalog is missing or invalid.") from exc
    if payload.get("schema_version") != 2:
        raise RuntimeError("The official Partner field catalog schema version is unsupported.")
    operations = payload.get("operations")
    if not isinstance(operations, Mapping) or set(operations) != set(OPERATION_POLICIES):
        raise RuntimeError("The official Partner field catalog must cover all Partner operations.")
    for operation, definition in operations.items():
        if not isinstance(definition, Mapping):
            raise RuntimeError(f"Invalid field catalog operation: {operation}.")
        if definition.get("endpoint") != OPERATION_POLICIES[operation].endpoint:
            raise RuntimeError(f"Field catalog endpoint mismatch for {operation}.")
        for key in ("official_name_zh", "official_name_en", "doc_url", "doc_url_en"):
            if not isinstance(definition.get(key), str) or not definition[key].strip():
                raise RuntimeError(f"Field catalog {key} must be non-empty for {operation}.")
        for section in ("request_fields", "response_fields"):
            fields = definition.get(section)
            if not isinstance(fields, list) or not all(isinstance(item, Mapping) for item in fields):
                raise RuntimeError(f"Field catalog {section} must be a list for {operation}.")
            wire_names = [item.get("wire_name") for item in fields]
            if any(not isinstance(name, str) or not name for name in wire_names):
                raise RuntimeError(f"Field catalog wire names must be non-empty for {operation}.")
            if len(wire_names) != len(set(wire_names)):
                raise RuntimeError(f"Field catalog wire names must be unique for {operation}.")
            for field in fields:
                for language in ("zh", "en"):
                    description = field.get(f"official_description_{language}")
                    if not isinstance(description, str) or not description.strip():
                        raise RuntimeError(
                            f"Field catalog official {language} description must be non-empty "
                            f"for {operation}/{field['wire_name']}."
                        )
                if "label_zh" in field or "label_en" in field:
                    raise RuntimeError(
                        f"Field catalog normalized labels are not allowed for {operation}."
                    )
        for field in definition["response_fields"]:
            if field.get("role") not in FIELD_ROLES:
                raise RuntimeError(f"Invalid response field role for {operation}.")
            if field.get("format") is not None and field.get("format") not in FIELD_FORMATS:
                raise RuntimeError(f"Invalid response field format for {operation}.")
    return dict(payload)


PARTNER_FIELD_CATALOG = _load_partner_field_catalog()
_CATALOG_OPERATIONS = PARTNER_FIELD_CATALOG["operations"]


def _catalog_record_names(definition: Mapping[str, Any]) -> set[str]:
    return {
        str(field["wire_name"])
        for field in definition["response_fields"]
        if field["role"] == "record"
    }


KNOWN_FIELDS: Dict[str, set[str]] = {
    operation: _catalog_record_names(definition)
    for operation, definition in _CATALOG_OPERATIONS.items()
}
CONTAINER_FIELDS: Dict[str, set[str]] = {
    operation: {
        str(field["wire_name"])
        for field in definition["response_fields"]
        if field.get("format") == "hidden_container"
    }
    for operation, definition in _CATALOG_OPERATIONS.items()
    if any(
        field.get("format") == "hidden_container"
        for field in definition["response_fields"]
    )
}
TIMESTAMP_FIELDS: Dict[str, set[str]] = {
    operation: {
        str(field["wire_name"])
        for field in definition["response_fields"]
        if field.get("format") == "millisecond_timestamp" and field["role"] == "record"
    }
    for operation, definition in _CATALOG_OPERATIONS.items()
    if any(
        field.get("format") == "millisecond_timestamp" and field["role"] == "record"
        for field in definition["response_fields"]
    )
}
DATE_FIELDS: Dict[str, set[str]] = {
    operation: {
        str(field["wire_name"])
        for field in definition["response_fields"]
        if field.get("format") == "date" and field["role"] == "record"
    }
    for operation, definition in _CATALOG_OPERATIONS.items()
    if any(
        field.get("format") == "date" and field["role"] == "record"
        for field in definition["response_fields"]
    )
}
REQUEST_FIELD_ALIASES: Dict[str, Dict[str, str]] = {
    operation: {
        str(field["internal_name"]): str(field["wire_name"])
        for field in definition["request_fields"]
        if field.get("internal_name")
    }
    for operation, definition in _CATALOG_OPERATIONS.items()
}
RESPONSE_FIELD_ALIASES: Dict[str, Dict[str, str]] = {
    operation: {
        str(field["wire_name"]): str(field["output_name"])
        for field in definition["response_fields"]
        if field.get("output_name")
    }
    for operation, definition in _CATALOG_OPERATIONS.items()
}

REQUIRED_RECORD_FIELDS: Dict[str, tuple[set[str], ...]] = {
    "list-referral-uids": ({"uid"},),
    "get-direct-trade-asset": ({"uid"},),
    "get-commission": ({"uid"}, {"commission"}),
    "get-sub-agent-stats": ({"subAffiliateUid"},),
    "verify-referrals": ({"uid"}, {"isRefferal"}),
    "get-referral-assets": (
        {
            "availableBalance", "fundingTotalUsdt", "spotProTotalUsdt",
            "unimarginTotalUsdt", "depositTotalAmount", "depositList",
        },
    ),
    "get-referral-deal-data": ({"userId"},),
}

RECORD_IDENTITY_FIELDS: Dict[str, tuple[str, ...]] = {
    "list-referral-uids": ("uid",),
    "get-direct-trade-asset": ("uid",),
    "get-sub-agent-stats": ("subAffiliateUid", "productType", "date"),
    "get-referral-deal-data": ("userId",),
}


def choose_official_minimum_days(ranges: Iterable[int]) -> int:
    normalized = [int(value) for value in ranges if int(value) > 0]
    if not normalized:
        raise PartnerQueryError(
            "time_range_required",
            "The official contract does not expose a minimum query range.",
        )
    return min(normalized)


def _as_utc_datetime(value: str) -> datetime:
    raw = str(value).strip()
    if not raw:
        raise PartnerQueryError("invalid_time_range", "Time values cannot be empty.")
    if len(raw) == 10:
        parsed = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PartnerQueryError("invalid_time_range", f"Invalid ISO-8601 time: {raw}") from exc
        if parsed.tzinfo is None:
            raise PartnerQueryError("invalid_time_range", "Time values must include a UTC offset.")
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _format_utc(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    normalized = normalized.replace(microsecond=(normalized.microsecond // 1000) * 1000)
    timespec = "milliseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


def _compact_time_range(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    start = value.get("actual_start", value.get("start"))
    end = value.get("actual_end", value.get("end"))
    if start is None or end is None:
        return None
    return {"actual_start": str(start), "actual_end": str(end)}


def _time_continuation_from_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        remaining_start = _as_utc_datetime(str(state["remaining_start"]))
        remaining_end = _as_utc_datetime(str(state["remaining_end"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PartnerQueryError(
            "invalid_continuation",
            "Continuation time_state is invalid.",
        ) from exc
    segment_fields = TIME_SEGMENT_FIELDS.intersection(state)
    if len(segment_fields) != 1:
        raise PartnerQueryError("invalid_continuation", "Continuation time_state is invalid.")
    segment_field = next(iter(segment_fields))
    try:
        segment_value = int(state[segment_field])
    except (TypeError, ValueError) as exc:
        raise PartnerQueryError("invalid_continuation", "Continuation time_state is invalid.") from exc
    if segment_value <= 0 or remaining_start > remaining_end:
        raise PartnerQueryError("invalid_continuation", "Continuation time_state is invalid.")
    normalized_state = {
        "original_start": str(state["original_start"]),
        "original_end": str(state["original_end"]),
        "remaining_start": _format_utc(remaining_start),
        "remaining_end": _format_utc(remaining_end),
        segment_field: segment_value,
    }
    if remaining_start == remaining_end:
        return {
            "can_continue": False,
            "stop_reason": "requested_range_covered",
            "time_state": normalized_state,
        }
    return {
        "can_continue": True,
        "stop_reason": "earlier_official_range_available",
        "next_end": normalized_state["remaining_end"],
        segment_field: segment_value,
        "time_state": normalized_state,
    }


def _subtract_calendar_months(value: datetime, months: int) -> datetime:
    absolute_month = value.year * 12 + (value.month - 1) - months
    year, month_index = divmod(absolute_month, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise PartnerQueryError("invalid_time_range", "Date values must use YYYY-MM-DD.") from exc


def _normalize_uid(value: Any) -> str:
    if isinstance(value, bool) or isinstance(value, (Mapping, list, tuple, set)):
        raise PartnerQueryError("invalid_uid", "UID values must be positive decimal integers.")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str) and value.strip().isdigit():
        normalized = int(value.strip())
    else:
        raise PartnerQueryError("invalid_uid", "UID values must be positive decimal integers.")
    if normalized <= 0 or normalized > MAX_UID:
        raise PartnerQueryError("invalid_uid", "UID values must fit a positive signed 64-bit integer.")
    return str(normalized)


def _normalize_scope(request: Mapping[str, Any], policy: OperationPolicy) -> Dict[str, Any]:
    raw_scope = request.get("scope")
    if raw_scope is None:
        raw_scope = {}
    if not isinstance(raw_scope, Mapping):
        raise PartnerQueryError("invalid_scope", "scope must be an object.")
    unknown_scope_fields = sorted(set(raw_scope) - SCOPE_FIELDS)
    if unknown_scope_fields:
        raise PartnerQueryError(
            "invalid_scope",
            "scope contains unsupported fields: " + ", ".join(unknown_scope_fields) + ".",
        )
    mode = str(raw_scope.get("mode") or "none")
    raw_uids = raw_scope.get("uids", [])
    if not isinstance(raw_uids, list):
        raise PartnerQueryError("invalid_scope", "scope.uids must be a JSON array.")
    uids = [_normalize_uid(value) for value in raw_uids]
    if len(set(uids)) != len(uids):
        raise PartnerQueryError("invalid_scope", "UID values must not be duplicated.")
    raw_all_confirmed = raw_scope.get("all_confirmed", False)
    all_confirmed = raw_all_confirmed if isinstance(raw_all_confirmed, bool) else False

    if mode == "all" and raw_all_confirmed is not True:
        raise PartnerQueryError(
            "scope_confirmation_required",
            "An all-referrals query requires explicit all_confirmed=true.",
        )
    if mode == "all" and policy.scope_rule not in {
        "optional_single_or_confirmed_all",
        "one_or_more_or_confirmed_all",
    }:
        raise PartnerQueryError("invalid_scope", "This operation does not support an all-referrals scope.")
    if policy.scope_rule == "optional_single_or_confirmed_all":
        if mode == "all":
            return {"mode": "all", "uids": [], "all_confirmed": True}
        if mode != "uids" or not uids:
            raise PartnerQueryError(
                "scope_confirmation_required",
                "Provide a UID or explicitly confirm the all-referrals scope.",
            )
        if len(uids) != 1:
            raise PartnerQueryError("invalid_scope", "This operation accepts one UID per query.")
    elif policy.scope_rule == "exactly_one" and (mode != "uids" or len(uids) != 1):
        raise PartnerQueryError("invalid_scope", "This operation requires exactly one UID.")
    elif policy.scope_rule == "one_or_more" and (mode != "uids" or not uids):
        raise PartnerQueryError("invalid_scope", "This operation requires at least one UID.")
    elif policy.scope_rule == "one_or_more_or_confirmed_all":
        if mode != "all" and (mode != "uids" or not uids):
            raise PartnerQueryError(
                "scope_confirmation_required",
                "Provide UID values or explicitly confirm the all-referrals scope.",
            )
    elif policy.scope_rule == "none":
        if mode != "none" or uids or all_confirmed:
            raise PartnerQueryError("invalid_scope", "This operation does not accept a UID scope.")
        return {"mode": "none", "uids": [], "all_confirmed": False}
    return {"mode": mode, "uids": uids, "all_confirmed": all_confirmed}


def _normalize_filters(
    request: Mapping[str, Any],
    operation: str,
    policy: OperationPolicy,
) -> Dict[str, Any]:
    raw_filters = request.get("filters")
    if raw_filters is None:
        raw_filters = {}
    if not isinstance(raw_filters, Mapping):
        raise PartnerQueryError("invalid_filters", "filters must be an object.")
    unknown = sorted(str(key) for key in set(raw_filters) - OPERATION_FILTER_FIELDS[operation])
    if unknown:
        raise PartnerQueryError(
            "invalid_filters",
            f"Unsupported filters for {operation}: {', '.join(unknown)}.",
        )
    filters = {str(key): value for key, value in raw_filters.items() if value is not None}
    product_type = filters.get("product_type")
    if policy.product_type == "default_spot" and not product_type:
        product_type = "SPOT"
    if policy.product_type == "required" and not product_type:
        raise PartnerQueryError("product_type_required", "product_type must be SPOT or FUTURES.")
    if product_type:
        normalized_product = str(product_type).upper()
        if normalized_product not in PRODUCT_TYPES:
            raise PartnerQueryError("invalid_product_type", "product_type must be SPOT or FUTURES.")
        filters["product_type"] = normalized_product
    if "coin" in filters:
        coin = filters["coin"]
        if not isinstance(coin, str) or not coin.strip() or not coin.strip().isalnum():
            raise PartnerQueryError("invalid_coin", "coin must be a non-empty alphanumeric symbol.")
        normalized_coin = coin.strip().upper()
        if operation == "get-commission" and normalized_coin not in {"USDT", "BTC"}:
            raise PartnerQueryError(
                "invalid_coin",
                "coin for get-commission must be USDT or BTC.",
            )
        filters["coin"] = normalized_coin
    if "withdraw_id" in filters:
        withdraw_id = filters["withdraw_id"]
        if isinstance(withdraw_id, bool) or isinstance(withdraw_id, (Mapping, list, tuple, set)):
            raise PartnerQueryError("invalid_withdraw_id", "withdraw_id must be a non-empty scalar value.")
        normalized_withdraw_id = str(withdraw_id).strip()
        if not normalized_withdraw_id:
            raise PartnerQueryError("invalid_withdraw_id", "withdraw_id must be a non-empty scalar value.")
        filters["withdraw_id"] = normalized_withdraw_id
    for field in ("from_account_type", "to_account_type"):
        if field in filters:
            account_type = str(filters[field]).strip().upper()
            if account_type not in ACCOUNT_TYPES:
                raise PartnerQueryError("invalid_account_type", f"{field} must be SPOT or FUND.")
            filters[field] = account_type
    return filters


def _normalize_time_range(
    request: Mapping[str, Any],
    policy: OperationPolicy,
    now: datetime,
    *,
    validated_month_continuation_action: bool = False,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    continuation = {"can_continue": False, "stop_reason": "no_time_continuation"}
    raw_range = request.get("time_range")
    if isinstance(raw_range, Mapping):
        unknown_time_fields = sorted(set(raw_range) - TIME_RANGE_FIELDS)
        if unknown_time_fields:
            raise PartnerQueryError(
                "invalid_time_range",
                "time_range contains unsupported fields: " + ", ".join(unknown_time_fields) + ".",
            )
    if policy.time_format == "none":
        if raw_range:
            raise PartnerQueryError("invalid_time_range", "This operation does not accept a time range.")
        return None, continuation
    if raw_range is None:
        if policy.default_days is None:
            raise PartnerQueryError(
                "time_range_required",
                "The official contract does not expose a safe minimum default; provide start and end.",
            )
        minimum_days = choose_official_minimum_days([policy.default_days])
        end = now.astimezone(timezone.utc).replace(microsecond=0)
        start = end - timedelta(days=minimum_days)
        time_range = {
            "source": "official_minimum_default",
            "minimum_days": minimum_days,
            "requested_start": None,
            "requested_end": None,
            "actual_start": _format_utc(start),
            "actual_end": _format_utc(end),
        }
        if policy.history_days and policy.history_days > minimum_days and policy.max_days:
            continuation = _time_continuation_from_state(
                {
                    "original_start": _format_utc(end - timedelta(days=policy.history_days)),
                    "original_end": _format_utc(end),
                    "remaining_start": _format_utc(end - timedelta(days=policy.history_days)),
                    "remaining_end": _format_utc(start),
                    "segment_days": minimum_days,
                }
            )
        return time_range, continuation
    if not isinstance(raw_range, Mapping) or not raw_range.get("start") or not raw_range.get("end"):
        raise PartnerQueryError("time_range_required", "Both time_range.start and time_range.end are required.")

    if policy.time_format == "date":
        start_date = _parse_date(str(raw_range["start"]))
        end_date = _parse_date(str(raw_range["end"]))
        if start_date > end_date:
            raise PartnerQueryError("invalid_time_range", "Start date must not be after end date.")
        return {
            "source": "user",
            "requested_start": start_date.isoformat(),
            "requested_end": end_date.isoformat(),
            "actual_start": start_date.isoformat(),
            "actual_end": end_date.isoformat(),
        }, continuation

    requested_start = _as_utc_datetime(str(raw_range["start"]))
    requested_end = _as_utc_datetime(str(raw_range["end"]))
    if requested_start >= requested_end:
        raise PartnerQueryError("invalid_time_range", "Start time must be before end time.")
    actual_start = requested_start
    actual_end = requested_end
    duration = requested_end - requested_start
    if policy.history_days:
        earliest = now.astimezone(timezone.utc).replace(microsecond=0) - timedelta(days=policy.history_days)
        if requested_start < earliest:
            raise PartnerQueryError(
                "time_range_out_of_history",
                f"This endpoint only exposes the most recent {policy.history_days} days.",
            )
    if policy.history_months:
        earliest_month = _subtract_calendar_months(
            now.astimezone(timezone.utc).replace(microsecond=0),
            policy.history_months,
        )
        if requested_start < earliest_month:
            raise PartnerQueryError(
                "time_range_out_of_history",
                f"This endpoint only exposes the most recent {policy.history_months} calendar month(s).",
            )
    if policy.max_days and duration > timedelta(days=policy.max_days):
        if policy.history_days and policy.history_days > policy.max_days:
            actual_start = requested_end - timedelta(days=policy.max_days)
            continuation = _time_continuation_from_state(
                {
                    "original_start": _format_utc(requested_start),
                    "original_end": _format_utc(requested_end),
                    "remaining_start": _format_utc(requested_start),
                    "remaining_end": _format_utc(actual_start),
                    "segment_days": policy.max_days,
                }
            )
        else:
            raise PartnerQueryError(
                "time_range_too_large",
                f"This endpoint accepts at most {policy.max_days} days per request.",
            )
    if (
        policy.max_months
        and not validated_month_continuation_action
        and requested_start < _subtract_calendar_months(requested_end, policy.max_months)
    ):
        actual_start = _subtract_calendar_months(requested_end, policy.max_months)
        continuation = _time_continuation_from_state(
            {
                "original_start": _format_utc(requested_start),
                "original_end": _format_utc(requested_end),
                "remaining_start": _format_utc(requested_start),
                "remaining_end": _format_utc(actual_start),
                "segment_months": policy.max_months,
            }
        )
    return {
        "source": "user",
        "requested_start": _format_utc(requested_start),
        "requested_end": _format_utc(requested_end),
        "actual_start": _format_utc(actual_start),
        "actual_end": _format_utc(actual_end),
    }, continuation


def _canonical_action_patch(
    patch: Mapping[str, Any],
    *,
    base_time_range: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    allowed = {"page", "display_offset", "records_seen", "time_range"}
    if set(patch) - allowed:
        raise PartnerQueryError("invalid_continuation", "Continuation action contains unsupported fields.")
    result: Dict[str, Any] = {}
    for field, default in (("page", 1), ("display_offset", 0), ("records_seen", 0)):
        if field in patch:
            try:
                value = int(patch[field])
            except (TypeError, ValueError) as exc:
                raise PartnerQueryError("invalid_continuation", "Continuation action is invalid.") from exc
            if value != default:
                result[field] = value
    if patch.get("time_range") is not None:
        compact = _compact_time_range(patch["time_range"])
        if compact is None:
            raise PartnerQueryError("invalid_continuation", "Continuation action time_range is invalid.")
        if compact != _compact_time_range(base_time_range):
            result["time_range"] = compact
    return result


def _select_continuation_action(
    continuation: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    page: int,
    display_offset: int,
    records_seen: int,
) -> Dict[str, Any]:
    actions = continuation.get("actions")
    if not isinstance(actions, list) or not actions:
        raise PartnerQueryError(
            "continuation_patch_mismatch",
            "Continuation has no available action for this request.",
        )
    request_patch: Dict[str, Any] = {}
    if page != 1:
        request_patch["page"] = page
    if display_offset != 0:
        request_patch["display_offset"] = display_offset
    if records_seen != 0:
        request_patch["records_seen"] = records_seen
    raw_time_range = request.get("time_range")
    if raw_time_range is not None:
        compact = _compact_time_range(raw_time_range)
        if compact is None:
            raise PartnerQueryError("continuation_patch_mismatch", "Continuation time patch is invalid.")
        if compact != _compact_time_range(continuation.get("time_range")):
            request_patch["time_range"] = compact

    matches: List[Dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, Mapping) or not isinstance(action.get("request_patch"), Mapping):
            raise PartnerQueryError("invalid_continuation", "Continuation action is invalid.")
        candidate = _canonical_action_patch(
            action["request_patch"],
            base_time_range=continuation.get("time_range"),
        )
        if candidate == request_patch:
            matches.append(dict(action))
    if len(matches) != 1:
        raise PartnerQueryError(
            "continuation_patch_mismatch",
            "Apply exactly one unchanged continuation action request_patch.",
        )
    return matches[0]


def _advance_time_continuation(
    generated: Mapping[str, Any],
    input_continuation: Optional[Mapping[str, Any]],
    selected_action: Optional[Mapping[str, Any]],
    time_range: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    if not input_continuation or not selected_action:
        return dict(generated)
    previous_state = input_continuation.get("time_state")
    if not isinstance(previous_state, Mapping):
        return dict(generated)
    action_type = str(selected_action.get("type") or "")
    if action_type in {"display_more", "next_page"}:
        return _time_continuation_from_state(previous_state)
    if action_type != "earlier_time_range" or not time_range:
        return dict(generated)
    next_state = dict(previous_state)
    next_state["remaining_end"] = str(time_range["actual_start"])
    return _time_continuation_from_state(next_state)


def plan_query(request: Mapping[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    unknown_request_fields = sorted(set(request) - REQUEST_FIELDS)
    if unknown_request_fields:
        raise PartnerQueryError(
            "invalid_request_fields",
            "Request contains unsupported fields: " + ", ".join(unknown_request_fields) + ".",
        )
    operation = str(request.get("operation") or "")
    policy = OPERATION_POLICIES.get(operation)
    if policy is None:
        raise PartnerQueryError("unsupported_operation", f"Unsupported Partner operation: {operation!r}")
    profile = str(request.get("profile") or "").strip()
    if not profile:
        raise PartnerQueryError("profile_required", "A saved profile name or ID is required.")
    result_mode = str(request.get("result_mode") or "summary_with_first_20")
    if result_mode not in RESULT_MODES:
        raise PartnerQueryError("invalid_result_mode", f"Unsupported result mode: {result_mode!r}")
    raw_continuation = request.get("continuation")
    try:
        page = int(request.get("page", 1))
        display_offset = int(request.get("display_offset", 0))
        records_seen = int(request.get("records_seen", 0))
    except (TypeError, ValueError) as exc:
        raise PartnerQueryError(
            "invalid_pagination",
            "page, display_offset, and records_seen must be integers.",
        ) from exc
    if page < 1 or display_offset < 0 or records_seen < 0:
        raise PartnerQueryError(
            "invalid_pagination",
            "page must be at least 1; display_offset and records_seen cannot be negative.",
        )
    if (
        result_mode == "complete_list"
        and (page != 1 or display_offset != 0)
    ) or (
        result_mode == "aggregate_all"
        and (page != 1 or (display_offset != 0 and raw_continuation is None))
    ):
        raise PartnerQueryError(
            "invalid_pagination",
            "Complete-list and new aggregate queries must restart from page 1 with display_offset 0.",
        )
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    language = str(request.get("language") or "en")
    if language not in LANGUAGES:
        raise PartnerQueryError("invalid_language", "language must be zh or en.")
    expected_environment = request.get("expected_environment")
    if expected_environment is not None and (
        not isinstance(expected_environment, str)
        or expected_environment not in PARTNER_ENVIRONMENTS
    ):
        raise PartnerQueryError(
            "invalid_partner_environment",
            "expected_environment must be partner_production or partner_test.",
        )
    scope = _normalize_scope(request, policy)
    filters = _normalize_filters(request, operation, policy)
    selected_action: Optional[Dict[str, Any]] = None
    if raw_continuation is not None:
        if not isinstance(raw_continuation, Mapping):
            raise PartnerQueryError("invalid_continuation", "continuation must be an object.")
        continuation_environment = raw_continuation.get("environment")
        if (
            not isinstance(continuation_environment, str)
            or continuation_environment not in PARTNER_ENVIRONMENTS
        ):
            raise PartnerQueryError(
                "invalid_continuation",
                "continuation must bind a valid Partner environment.",
            )
        preliminary_binding = {
            "query_scope": scope,
            "operation": operation,
            "contract_version": CONTRACT_VERSION,
            "filters": filters,
            "result_mode": result_mode,
            "expected_environment": expected_environment,
        }
        validate_continuation(
            raw_continuation,
            preliminary_binding,
            fields=tuple(preliminary_binding),
        )
        _validate_continuation_integrity(raw_continuation)
        selected_action = _select_continuation_action(
            raw_continuation,
            request,
            page=page,
            display_offset=display_offset,
            records_seen=records_seen,
        )
    time_request: Mapping[str, Any] = request
    if (
        isinstance(raw_continuation, Mapping)
        and request.get("time_range") is None
        and _compact_time_range(raw_continuation.get("time_range")) is not None
    ):
        continuation_time = _compact_time_range(raw_continuation.get("time_range")) or {}
        time_request = dict(request)
        time_request["time_range"] = {
            "start": continuation_time["actual_start"],
            "end": continuation_time["actual_end"],
        }
    if (
        operation == "get-sub-agent-stats"
        and expected_environment == "partner_test"
        and time_request.get("time_range") is None
    ):
        time_range = {
            "source": "partner_test_upstream_default",
            "requested_start": None,
            "requested_end": None,
            "actual_start": None,
            "actual_end": None,
        }
        time_continuation = {
            "can_continue": False,
            "stop_reason": "partner_test_upstream_default_has_no_continuation",
        }
    else:
        time_range, time_continuation = _normalize_time_range(
            time_request,
            policy,
            current_time,
            validated_month_continuation_action=(
                isinstance(selected_action, Mapping)
                and selected_action.get("type") == "earlier_time_range"
                and policy.max_months is not None
            ),
        )
    time_continuation = _advance_time_continuation(
        time_continuation,
        raw_continuation if isinstance(raw_continuation, Mapping) else None,
        selected_action,
        time_range,
    )
    plan = {
        "operation": operation,
        "endpoint": policy.endpoint,
        "profile": profile,
        "language": language,
        "expected_environment": expected_environment,
        "query_scope": scope,
        "time_range": time_range,
        "filters": filters,
        "result_mode": result_mode,
        "contract_version": CONTRACT_VERSION,
        "continuation": time_continuation,
        "page": page,
        "display_offset": display_offset,
        "records_seen": records_seen,
        "input_continuation": None,
        "selected_action": selected_action,
    }
    if raw_continuation is not None:
        binding = {
            "query_scope": plan["query_scope"],
            "operation": plan["operation"],
            "contract_version": plan["contract_version"],
            "filters": plan["filters"],
            "result_mode": plan["result_mode"],
            "expected_environment": plan["expected_environment"],
            "time_range": _compact_time_range(plan["time_range"]),
        }
        validate_continuation(
            raw_continuation,
            binding,
            fields=tuple(binding),
        )
        plan["input_continuation"] = dict(raw_continuation)
    if page > 1 and (plan["input_continuation"] is None or records_seen <= 0):
        raise PartnerQueryError(
            "invalid_continuation",
            "A server page after page 1 requires its bound continuation and records_seen value.",
        )
    return plan


def _milliseconds(value: str) -> int:
    return int(_as_utc_datetime(value).timestamp() * 1000)


def _base_executor_request(plan: Mapping[str, Any]) -> Dict[str, Any]:
    request_payload = {
        "endpoint": plan["endpoint"],
        "profile": plan["profile"],
        "language": plan.get("language", "en"),
        "query": {},
        "body": {},
    }
    if plan.get("expected_environment") is not None:
        request_payload["expected_environment"] = plan["expected_environment"]
    return request_payload


def _apply_time_and_filters(request: Dict[str, Any], plan: Mapping[str, Any]) -> None:
    operation = str(plan["operation"])
    policy = OPERATION_POLICIES[operation]
    target = request["body"] if request["endpoint"] == "partner.query-sub-channel-transactions" else request["query"]
    time_range = plan.get("time_range")
    if time_range:
        if time_range.get("source") == "partner_test_upstream_default":
            target["startTime"] = None
            target["endTime"] = None
        elif policy.time_format == "milliseconds":
            target["startTime"] = _milliseconds(str(time_range["actual_start"]))
            target["endTime"] = _milliseconds(str(time_range["actual_end"]))
        else:
            target["startTime"] = time_range["actual_start"]
            target["endTime"] = time_range["actual_end"]
    field_map = REQUEST_FIELD_ALIASES.get(operation, {})
    for source, destination in field_map.items():
        if source in plan.get("filters", {}):
            target[destination] = plan["filters"][source]
    if policy.page_field:
        target[policy.page_field] = int(plan.get("page", 1))
    if policy.page_size_field:
        target[policy.page_size_field] = 100


def build_executor_requests(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    operation = str(plan["operation"])
    scope = plan.get("query_scope") or {}
    uids = list(scope.get("uids") or [])
    requests: List[Dict[str, Any]] = []
    if operation == "verify-referrals":
        for index in range(0, len(uids), 100):
            item = _base_executor_request(plan)
            item["query"]["userIds"] = ",".join(uids[index:index + 100])
            requests.append(item)
        return requests

    item = _base_executor_request(plan)
    _apply_time_and_filters(item, plan)
    target = item["body"] if item["endpoint"] == "partner.query-sub-channel-transactions" else item["query"]
    if operation in {"list-referral-uids", "get-direct-trade-asset", "get-commission"} and uids:
        target["uid"] = uids[0]
    elif operation == "get-sub-agent-stats" and uids:
        target["subUid"] = int(uids[0])
    elif operation == "get-referral-assets":
        target["userId"] = uids[0]
    elif operation == "get-referral-deal-data" and uids:
        target["userIds"] = uids
    requests.append(item)
    return requests


def aggregate_decimal_fields(
    records: Iterable[Mapping[str, Any]],
    *,
    group_field: str,
    amount_field: str,
    strict: bool = False,
) -> Dict[str, Decimal]:
    result: Dict[str, Decimal] = {}
    for record in records:
        group = str(record.get(group_field, ""))
        if not group or amount_field not in record:
            if strict:
                raise PartnerQueryError(
                    "missing_aggregate_field",
                    f"Cannot produce a complete aggregate without {group_field} and {amount_field}.",
                )
            continue
        try:
            amount = Decimal(str(record[amount_field]))
        except (InvalidOperation, ValueError) as exc:
            raise PartnerQueryError(
                "invalid_decimal",
                f"Invalid decimal value in {amount_field}.",
            ) from exc
        result[group] = result.get(group, Decimal("0")) + amount
    return result


def aggregate_decimal_totals(
    records: Iterable[Mapping[str, Any]],
    *,
    fields: Sequence[str],
) -> Dict[str, Decimal]:
    totals = {field: Decimal("0") for field in fields}
    for record in records:
        for field in fields:
            if field not in record:
                raise PartnerQueryError(
                    "missing_aggregate_field",
                    f"Cannot produce a complete aggregate without {field}.",
                )
            try:
                totals[field] += Decimal(str(record[field]))
            except (InvalidOperation, ValueError) as exc:
                raise PartnerQueryError(
                    "invalid_decimal",
                    f"Invalid decimal value in {field}.",
                ) from exc
    return totals


def _format_millisecond_utc(value: Any, *, language: str = "en") -> str:
    try:
        milliseconds = int(str(value))
        timestamp = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    except (OverflowError, TypeError, ValueError) as exc:
        raise PartnerQueryError(
            "invalid_timestamp",
            "Partner response contains an invalid millisecond timestamp.",
        ) from exc
    suffix = "（UTC）" if language == "zh" else " (UTC)"
    return timestamp.strftime("%Y-%m-%d %H:%M:%S") + suffix


def _format_date_utc(value: Any, *, language: str = "en") -> str:
    try:
        normalized = datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError) as exc:
        raise PartnerQueryError(
            "invalid_date",
            "Partner response contains an invalid YYYY-MM-DD date.",
        ) from exc
    suffix = "（UTC）" if language == "zh" else " (UTC)"
    return normalized + suffix


def _normalize_referral_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "false"}:
        return normalized == "true"
    raise PartnerQueryError(
        "invalid_boolean",
        "Partner response contains an invalid isRefferal value.",
    )


def project_known_fields(
    payload: Mapping[str, Any],
    *,
    known_fields: set[str],
    timestamp_fields: Optional[set[str]] = None,
    date_fields: Optional[set[str]] = None,
    field_aliases: Optional[Mapping[str, str]] = None,
    language: str = "en",
) -> Dict[str, Any]:
    record: Dict[str, Any] = {}
    unknown_fields = sorted(str(key) for key in payload if key not in known_fields)
    timestamp_names = timestamp_fields or set()
    date_names = date_fields or set()
    for key, value in payload.items():
        if key not in known_fields:
            continue
        if key in timestamp_names and value is not None:
            record[key] = _format_millisecond_utc(value, language=language)
        elif key in date_names and value is not None:
            record[key] = _format_date_utc(value, language=language)
        elif isinstance(value, list):
            record[key] = {"count": len(value), "values_hidden": True}
            if value:
                unknown_fields.append(f"{key}.*")
        elif isinstance(value, Mapping):
            record[key] = {"field_count": len(value), "values_hidden": True}
            if value:
                unknown_fields.append(f"{key}.*")
        else:
            record[key] = value
    aliases = field_aliases or RESPONSE_FIELD_ALIASES.get("verify-referrals", {})
    for source, destination in aliases.items():
        if source not in record:
            continue
        value = record.pop(source)
        record[destination] = (
            _normalize_referral_flag(value) if source == "isRefferal" else value
        )
    return {"record": record, "unknown_fields": sorted(set(unknown_fields))}


def build_result_envelope(
    *,
    operation: str,
    result_mode: str,
    records: Sequence[Mapping[str, Any]],
    pages_fetched: int,
    pages_total: Optional[int],
    records_total: Optional[int],
    next_page: Optional[int],
    profile: Optional[Mapping[str, Any]] = None,
    query_scope: Optional[Mapping[str, Any]] = None,
    time_range: Optional[Mapping[str, Any]] = None,
    summary: Optional[Mapping[str, Any]] = None,
    continuation: Optional[Mapping[str, Any]] = None,
    source_complete: Optional[bool] = None,
    display_offset: int = 0,
    current_page: Optional[int] = None,
    rate_limit: Optional[Mapping[str, Any]] = None,
    continuation_binding: Optional[Mapping[str, Any]] = None,
    records_seen_before: int = 0,
    environment: Optional[str] = None,
    source_partial: bool = False,
) -> Dict[str, Any]:
    if result_mode == "complete_list":
        displayed = list(records)
        effective_offset = 0
    else:
        effective_offset = display_offset
        displayed = list(records[effective_offset:effective_offset + 20])
    total = records_total if records_total is not None else len(records)
    displayed_end = effective_offset + len(displayed)
    covered_records = records_seen_before + displayed_end
    has_more = total > covered_records or bool(next_page)
    complete = (
        bool(source_complete)
        if source_complete is not None
        else not has_more and (pages_total is None or pages_fetched >= pages_total)
    )
    remaining = max(total - covered_records, 0)
    effective_continuation = dict(continuation or {})
    effective_continuation.update(dict(continuation_binding or {}))
    actions: List[Dict[str, Any]] = []
    if result_mode != "complete_list" and displayed_end < len(records):
        display_patch: Dict[str, Any] = {"display_offset": displayed_end}
        if result_mode != "aggregate_all":
            if current_page is not None and current_page != 1:
                display_patch["page"] = current_page
            if records_seen_before:
                display_patch["records_seen"] = records_seen_before
        actions.append(
            {
                "type": "display_more",
                "request_patch": display_patch,
            }
        )
    elif next_page is not None:
        actions.append(
            {
                "type": "next_page",
                "request_patch": {
                    "page": next_page,
                    "display_offset": 0,
                    "records_seen": records_seen_before + len(records),
                },
            }
        )
    if (
        next_page is None
        and continuation
        and continuation.get("next_end")
        and len(TIME_SEGMENT_FIELDS.intersection(continuation)) == 1
    ):
        next_end = _as_utc_datetime(str(continuation["next_end"]))
        if continuation.get("segment_days") is not None:
            next_start = next_end - timedelta(days=int(continuation["segment_days"]))
            action_end = next_end
        else:
            next_start = _subtract_calendar_months(
                next_end,
                int(continuation["segment_months"]),
            )
            action_end = next_end - timedelta(milliseconds=1)
        time_state = continuation.get("time_state")
        if isinstance(time_state, Mapping) and time_state.get("remaining_start"):
            remaining_start = _as_utc_datetime(str(time_state["remaining_start"]))
            if next_start < remaining_start:
                next_start = remaining_start
        actions.append(
            {
                "type": "earlier_time_range",
                "request_patch": {
                    "time_range": {
                        "start": _format_utc(next_start),
                        "end": _format_utc(action_end),
                    },
                    "page": 1,
                    "display_offset": 0,
                    "records_seen": 0,
                },
            }
        )
    effective_continuation["actions"] = actions
    usage_warnings: List[str] = []
    if actions:
        usage_warnings.append("continuation_reuse_may_repeat_or_overwrite")
    if any(action.get("type") in {"display_more", "next_page"} for action in actions):
        usage_warnings.append("offset_pagination_data_may_change")
    effective_continuation["usage_warnings"] = usage_warnings
    if has_more:
        effective_continuation.update(
            {
                "can_continue": True,
                "stop_reason": "next_page_or_records_available",
            }
        )
    else:
        effective_continuation.setdefault("can_continue", False)
        effective_continuation.setdefault("stop_reason", "requested_range_covered")
    return {
        "ok": True,
        "complete": complete,
        "partial": bool(source_partial),
        "operation": operation,
        "profile": dict(profile or {}),
        "api_domain": "partner",
        "environment": environment,
        "capability_mode": "read_only_query",
        "query_scope": dict(query_scope or {}),
        "time_range": dict(time_range or {}) if time_range else None,
        "pagination": {
            "pages_fetched": pages_fetched,
            "pages_total": pages_total,
            "current_page": current_page,
            "records_fetched": len(records),
            "records_total": total,
            "displayed_count": len(displayed),
            "display_offset": effective_offset,
            "records_seen_before": records_seen_before,
            "has_more": has_more,
            "remaining_count": remaining,
            "next_page": next_page,
        },
        "summary": dict(summary) if summary is not None else None,
        "records": displayed,
        "continuation": effective_continuation,
        "rate_limit": dict(rate_limit or {"used": {}, "remaining": {}}),
        "warnings": [],
        "error": None,
    }


def _project_safe_error_details(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    result: Dict[str, Any] = {}
    schema_issue = value.get("schema_issue")
    if isinstance(schema_issue, str) and schema_issue in SAFE_ERROR_SCHEMA_ISSUES:
        result["schema_issue"] = schema_issue
    raw_type = value.get("raw_type")
    if raw_type == "non_json":
        result["raw_type"] = raw_type
    if value.get("values_hidden") is True:
        result["values_hidden"] = True
    field_names = value.get("field_names")
    if (
        isinstance(field_names, list)
        and all(
            isinstance(field, str) and field in SAFE_ERROR_FIELD_NAMES
            for field in field_names
        )
        and len(field_names) == len(set(field_names))
    ):
        result["field_names"] = list(field_names)
    unknown_field_count = value.get("unknown_field_count")
    if (
        isinstance(unknown_field_count, int)
        and not isinstance(unknown_field_count, bool)
        and unknown_field_count >= 0
    ):
        result["unknown_field_count"] = unknown_field_count
    value_type = value.get("value_type")
    if isinstance(value_type, str) and value_type in SAFE_ERROR_VALUE_TYPES:
        result["value_type"] = value_type
    return result or None


def build_partial_error_envelope(
    *,
    operation: str,
    records: Sequence[Mapping[str, Any]],
    pages_fetched: int,
    next_page: Optional[int],
    records_total: Optional[int],
    error: Mapping[str, Any],
    pages_total: Optional[int] = None,
    offset_pagination: bool,
    profile: Optional[Mapping[str, Any]] = None,
    query_scope: Optional[Mapping[str, Any]] = None,
    time_range: Optional[Mapping[str, Any]] = None,
    rate_limit: Optional[Mapping[str, Any]] = None,
    continuation_binding: Optional[Mapping[str, Any]] = None,
    records_seen_before: int = 0,
    environment: Optional[str] = None,
) -> Dict[str, Any]:
    continuation = {
        "can_continue": False,
        "restart_required": bool(offset_pagination),
        "restart_from_page": 1 if offset_pagination else None,
        "stop_reason": (
            "unstable_offset_pagination_interrupted"
            if offset_pagination
            else "query_interrupted"
        ),
        "usage_warnings": (
            ["offset_pagination_data_may_change"] if offset_pagination else []
        ),
    }
    continuation.update(dict(continuation_binding or {}))
    normalized_error = {
        key: error[key]
        for key in ("category", "http_status", "code", "message", "recovery_action")
        if key in error
    }
    normalized_error.setdefault("category", "upstream")
    normalized_error.setdefault(
        "recovery_action",
        "Correct the reported error and restart this query from page 1 when a complete result is required.",
    )
    safe_details = _project_safe_error_details(error.get("details"))
    if safe_details is not None:
        normalized_error["details"] = safe_details
    remaining_count = (
        max(records_total - records_seen_before - len(records), 0)
        if records_total is not None
        else None
    )
    if remaining_count is not None:
        has_more: Optional[bool] = remaining_count > 0
    elif pages_total is not None:
        has_more = (
            next_page <= pages_total
            if next_page is not None
            else pages_fetched < pages_total
        )
    else:
        has_more = None
    return {
        "ok": False,
        "complete": False,
        "partial": bool(records),
        "operation": operation,
        "profile": dict(profile or {}),
        "api_domain": "partner",
        "environment": environment,
        "capability_mode": "read_only_query",
        "query_scope": dict(query_scope or {}),
        "time_range": dict(time_range or {}) if time_range else None,
        "pagination": {
            "pages_fetched": pages_fetched,
            "pages_total": pages_total,
            "next_page": next_page,
            "records_fetched": len(records),
            "records_total": records_total,
            "displayed_count": min(len(records), 20),
            "display_offset": 0,
            "records_seen_before": records_seen_before,
            "has_more": has_more,
            "remaining_count": remaining_count,
        },
        "summary": None,
        "records": list(records[:20]),
        "continuation": continuation,
        "rate_limit": dict(rate_limit or {"used": {}, "remaining": {}}),
        "warnings": [],
        "error": normalized_error,
    }


def build_local_error_envelope(
    *,
    operation: str,
    code: str,
    message: str,
    category: str = "local_validation",
    recovery_action: str = "Correct the request fields described by this error and submit a new query.",
) -> Dict[str, Any]:
    return {
        "ok": False,
        "complete": False,
        "partial": False,
        "operation": operation,
        "profile": {},
        "api_domain": "partner",
        "environment": None,
        "capability_mode": "read_only_query",
        "query_scope": {},
        "time_range": None,
        "pagination": {
            "pages_fetched": 0,
            "pages_total": None,
            "records_fetched": 0,
            "records_total": None,
            "displayed_count": 0,
            "has_more": None,
            "remaining_count": None,
            "next_page": None,
        },
        "summary": None,
        "records": [],
        "continuation": {
            "can_continue": False,
            "stop_reason": "local_request_error",
            "actions": [],
            "usage_warnings": [],
        },
        "rate_limit": {"used": {}, "remaining": {}},
        "warnings": [],
        "error": {
            "category": category,
            "code": code,
            "message": message,
            "recovery_action": recovery_action,
        },
    }


CONTINUATION_BINDING_FIELDS = (
    "resolved_profile_id",
    "environment",
    "expected_environment",
    "query_scope",
    "operation",
    "contract_version",
    "filters",
    "result_mode",
    "time_range",
)


def validate_continuation(
    continuation: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    fields: Sequence[str] = CONTINUATION_BINDING_FIELDS,
) -> None:
    mismatches = [
        field
        for field in fields
        if continuation.get(field) != request.get(field)
        and not (
            field == "time_range"
            and _compact_time_range(request.get(field))
            in [
                _compact_time_range(
                    (action.get("request_patch") or {}).get("time_range")
                )
                for action in continuation.get("actions", [])
                if isinstance(action, Mapping)
            ]
        )
    ]
    if mismatches:
        raise PartnerQueryError(
            "continuation_mismatch",
            "Continuation does not match the current query: " + ", ".join(mismatches),
        )


def _validate_continuation_integrity(continuation: Mapping[str, Any]) -> None:
    if set(continuation) - CONTINUATION_FIELDS:
        raise PartnerQueryError(
            "invalid_continuation",
            "continuation contains unsupported fields.",
        )
    if not str(continuation.get("resolved_profile_id") or "").strip():
        raise PartnerQueryError(
            "invalid_continuation",
            "continuation must bind a resolved_profile_id.",
        )
    usage_warnings = continuation.get("usage_warnings")
    if (
        not isinstance(usage_warnings, list)
        or len(usage_warnings) != len(set(usage_warnings))
        or any(code not in CONTINUATION_USAGE_WARNING_CODES for code in usage_warnings)
    ):
        raise PartnerQueryError("invalid_continuation", "Continuation usage warnings are invalid.")
    source_pagination = continuation.get("source_pagination")
    if source_pagination is not None:
        if (
            not isinstance(source_pagination, Mapping)
            or set(source_pagination) != SOURCE_PAGINATION_FIELDS
        ):
            raise PartnerQueryError("invalid_continuation", "Continuation pagination is invalid.")
        try:
            records_total = int(source_pagination["records_total"])
            pages_total = int(source_pagination["pages_total"])
            current_page = int(source_pagination["current_page"])
            records_seen_before = int(source_pagination["records_seen_before"])
            records_on_page = int(source_pagination["records_on_page"])
            display_offset = int(source_pagination["display_offset"])
            raw_page_size = source_pagination["page_size"]
            page_size = int(raw_page_size) if raw_page_size is not None else None
        except (KeyError, TypeError, ValueError) as exc:
            raise PartnerQueryError("invalid_continuation", "Continuation pagination is invalid.") from exc
        if not (
            records_total >= 0
            and pages_total >= 0
            and current_page >= 1
            and records_seen_before >= 0
            and records_on_page >= 0
            and display_offset >= 0
            and records_seen_before + records_on_page <= records_total
            and (pages_total != 0 or (records_total == 0 and current_page == 1))
            and (page_size is None or page_size > 0)
            and (pages_total <= 1 or page_size is not None)
        ):
            raise PartnerQueryError("invalid_continuation", "Continuation pagination is invalid.")

    actions = continuation.get("actions")
    if not isinstance(actions, list):
        raise PartnerQueryError("invalid_continuation", "Continuation actions are invalid.")
    action_types: List[str] = []
    for action in actions:
        if (
            not isinstance(action, Mapping)
            or set(action) != {"type", "request_patch"}
            or action.get("type") not in {"display_more", "next_page", "earlier_time_range"}
            or not isinstance(action.get("request_patch"), Mapping)
        ):
            raise PartnerQueryError("invalid_continuation", "Continuation action is invalid.")
        _canonical_action_patch(
            action["request_patch"],
            base_time_range=continuation.get("time_range"),
        )
        action_types.append(str(action["type"]))
    if len(action_types) != len(set(action_types)):
        raise PartnerQueryError("invalid_continuation", "Continuation actions are invalid.")
    required_usage_warnings: List[str] = []
    if action_types:
        required_usage_warnings.append("continuation_reuse_may_repeat_or_overwrite")
    if any(action_type in {"display_more", "next_page"} for action_type in action_types):
        required_usage_warnings.append("offset_pagination_data_may_change")
    if usage_warnings != required_usage_warnings:
        raise PartnerQueryError("invalid_continuation", "Continuation usage warnings are invalid.")
    if (
        any(action_type in {"display_more", "next_page"} for action_type in action_types)
        and source_pagination is None
    ):
        raise PartnerQueryError(
            "invalid_continuation",
            "Continuation pagination action requires its source snapshot.",
        )

    if isinstance(source_pagination, Mapping):
        current_page = int(source_pagination["current_page"])
        pages_total = int(source_pagination["pages_total"])
        records_seen_before = int(source_pagination["records_seen_before"])
        records_on_page = int(source_pagination["records_on_page"])
        display_offset = int(source_pagination["display_offset"])
        non_time_actions = [
            action for action in actions if action.get("type") != "earlier_time_range"
        ]
        expected_type: Optional[str] = None
        expected_patch: Optional[Dict[str, Any]] = None
        if (
            continuation.get("result_mode") != "complete_list"
            and display_offset + 20 < records_on_page
        ):
            expected_type = "display_more"
            expected_patch = {"display_offset": display_offset + 20}
            if continuation.get("result_mode") != "aggregate_all":
                if current_page != 1:
                    expected_patch["page"] = current_page
                if records_seen_before:
                    expected_patch["records_seen"] = records_seen_before
        elif current_page < pages_total:
            expected_type = "next_page"
            expected_patch = {
                "page": current_page + 1,
                "records_seen": records_seen_before + records_on_page,
            }
        if expected_type is None:
            if non_time_actions:
                raise PartnerQueryError("invalid_continuation", "Continuation pagination action is invalid.")
        elif len(non_time_actions) != 1:
            raise PartnerQueryError("invalid_continuation", "Continuation pagination action is invalid.")
        else:
            action = non_time_actions[0]
            actual_patch = _canonical_action_patch(
                action["request_patch"],
                base_time_range=continuation.get("time_range"),
            )
            if action.get("type") != expected_type or actual_patch != expected_patch:
                raise PartnerQueryError("invalid_continuation", "Continuation pagination action is invalid.")

    state = continuation.get("time_state")
    segment_fields = TIME_SEGMENT_FIELDS.intersection(continuation)
    if "earlier_time_range" in action_types and (
        state is None
        or "original_time_range" not in continuation
        or "next_end" not in continuation
        or len(segment_fields) != 1
    ):
        raise PartnerQueryError(
            "invalid_continuation",
            "Continuation time action requires its complete time state.",
        )
    if state is None:
        return
    if (
        not isinstance(state, Mapping)
        or set(state) - TIME_STATE_BASE_FIELDS - TIME_SEGMENT_FIELDS
        or not TIME_STATE_BASE_FIELDS.issubset(state)
        or len(TIME_SEGMENT_FIELDS.intersection(state)) != 1
    ):
        raise PartnerQueryError("invalid_continuation", "Continuation time_state is invalid.")
    current_range = _compact_time_range(continuation.get("time_range"))
    if current_range is None:
        raise PartnerQueryError("invalid_continuation", "Continuation time_state is invalid.")
    try:
        original_start = _as_utc_datetime(str(state["original_start"]))
        original_end = _as_utc_datetime(str(state["original_end"]))
        remaining_start = _as_utc_datetime(str(state["remaining_start"]))
        remaining_end = _as_utc_datetime(str(state["remaining_end"]))
        current_start = _as_utc_datetime(str(current_range["actual_start"]))
        current_end = _as_utc_datetime(str(current_range["actual_end"]))
        state_segment_field = next(iter(TIME_SEGMENT_FIELDS.intersection(state)))
        segment_value = int(state[state_segment_field])
    except (KeyError, TypeError, ValueError) as exc:
        raise PartnerQueryError("invalid_continuation", "Continuation time_state is invalid.") from exc
    if not (
        segment_value > 0
        and state_segment_field in segment_fields
        and original_start == remaining_start
        and original_start <= remaining_end == current_start < current_end <= original_end
    ):
        raise PartnerQueryError("invalid_continuation", "Continuation time_state is invalid.")
    if continuation.get("can_continue") and (
        str(continuation.get("next_end")) != _format_utc(remaining_end)
        or continuation.get(state_segment_field) != segment_value
    ):
        raise PartnerQueryError("invalid_continuation", "Continuation time state is invalid.")

    original_time_range = continuation.get("original_time_range")
    if (
        not isinstance(original_time_range, Mapping)
        or set(original_time_range) != {"start", "end"}
        or _compact_time_range(original_time_range)
        != {
            "actual_start": _format_utc(original_start),
            "actual_end": _format_utc(original_end),
        }
    ):
        raise PartnerQueryError("invalid_continuation", "Continuation original time range is invalid.")

    earlier_actions = [
        action
        for action in actions
        if isinstance(action, Mapping) and action.get("type") == "earlier_time_range"
    ]
    pagination_incomplete = (
        isinstance(source_pagination, Mapping)
        and int(source_pagination["current_page"]) < int(source_pagination["pages_total"])
    )
    if pagination_incomplete and earlier_actions:
        raise PartnerQueryError("invalid_continuation", "Continuation time action is invalid.")
    if (
        continuation.get("can_continue")
        and remaining_start < remaining_end
        and not pagination_incomplete
    ):
        if len(earlier_actions) != 1:
            raise PartnerQueryError("invalid_continuation", "Continuation time action is invalid.")
        patch = earlier_actions[0].get("request_patch")
        if not isinstance(patch, Mapping):
            raise PartnerQueryError("invalid_continuation", "Continuation time action is invalid.")
        if state_segment_field == "segment_days":
            segment_start = remaining_end - timedelta(days=segment_value)
            expected_end = remaining_end
        else:
            segment_start = _subtract_calendar_months(remaining_end, segment_value)
            expected_end = remaining_end - timedelta(milliseconds=1)
        expected_start = max(remaining_start, segment_start)
        expected_range = {
            "actual_start": _format_utc(expected_start),
            "actual_end": _format_utc(expected_end),
        }
        if _compact_time_range(patch.get("time_range")) != expected_range:
            raise PartnerQueryError("invalid_continuation", "Continuation time action is invalid.")
    elif (remaining_start >= remaining_end or not continuation.get("can_continue")) and earlier_actions:
        raise PartnerQueryError("invalid_continuation", "Continuation time action is invalid.")


def _trader_script() -> Path:
    return Path(__file__).resolve().parents[2] / "weex-trader-skill" / "scripts" / "weex_partner_api.py"


def invoke_trader(request_payload: Mapping[str, Any]) -> Dict[str, Any]:
    script = _trader_script()
    if not script.exists():
        raise PartnerQueryError(
            "trader_dependency_missing",
            "Install weex-trader-skill before weex-partner-skill.",
        )
    completed = subprocess.run(
        [sys.executable, str(script), "execute"],
        input=json.dumps(request_payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        if completed.returncode != 0:
            raise PartnerQueryError(
                "trader_process_failed",
                "The trader Partner executor failed without a safe JSON response.",
            ) from exc
        raise PartnerQueryError(
            "trader_protocol_error",
            "The trader Partner executor did not return valid JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise PartnerQueryError("trader_protocol_error", "Trader response must be a JSON object.")
    return payload


def _mapping_records(value: Sequence[Any]) -> List[Mapping[str, Any]]:
    if any(not isinstance(item, Mapping) for item in value):
        raise PartnerQueryError(
            "invalid_record_container",
            "Partner response record lists must contain only JSON objects.",
        )
    return [item for item in value if isinstance(item, Mapping)]


def _unwrap_records(response: Mapping[str, Any]) -> tuple[List[Mapping[str, Any]], Dict[str, Any]]:
    payload: Any = response.get("data")
    if isinstance(payload, Mapping) and "data" in payload and (
        "code" in payload or "msg" in payload
    ):
        payload = payload["data"]
    metadata: Dict[str, Any] = {}
    if isinstance(payload, list):
        return _mapping_records(payload), metadata
    if isinstance(payload, Mapping):
        metadata = dict(payload)
        for key in (
            "records",
            "channelUserInfoItemList",
            "channelCommissionInfoItems",
            "items",
            "list",
            "rows",
            "data",
        ):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return _mapping_records(candidate), metadata
        return [payload], metadata
    return [], metadata


def _continuation_binding(
    plan: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    environment: str,
    records_total: Optional[int] = None,
    pages_total: Optional[int] = None,
    current_page: Optional[int] = None,
    page_size: Optional[int] = None,
    records_seen_before: int = 0,
    records_on_page: Optional[int] = None,
    display_offset: int = 0,
) -> Dict[str, Any]:
    binding = {
        "resolved_profile_id": str(profile.get("resolved_profile_id") or ""),
        "environment": environment,
        "expected_environment": plan.get("expected_environment"),
        "query_scope": dict(plan.get("query_scope") or {}),
        "operation": str(plan.get("operation") or ""),
        "contract_version": str(plan.get("contract_version") or ""),
        "filters": dict(plan.get("filters") or {}),
        "result_mode": str(plan.get("result_mode") or ""),
        "time_range": _compact_time_range(plan.get("time_range")),
    }
    time_state = (plan.get("continuation") or {}).get("time_state")
    if isinstance(time_state, Mapping):
        binding["time_state"] = dict(time_state)
        input_original_range = (plan.get("input_continuation") or {}).get(
            "original_time_range"
        )
        binding["original_time_range"] = (
            dict(input_original_range)
            if isinstance(input_original_range, Mapping)
            else {
                "start": str(time_state["original_start"]),
                "end": str(time_state["original_end"]),
            }
        )
    if (
        records_total is not None
        and pages_total is not None
        and current_page is not None
        and records_on_page is not None
    ):
        binding["source_pagination"] = {
            "records_total": records_total,
            "pages_total": pages_total,
            "current_page": current_page,
            "page_size": page_size,
            "records_seen_before": records_seen_before,
            "records_on_page": records_on_page,
            "display_offset": display_offset,
        }
    return binding


def _validate_record_schema(operation: str, records: Sequence[Mapping[str, Any]]) -> None:
    requirements = REQUIRED_RECORD_FIELDS[operation]
    container_fields = CONTAINER_FIELDS.get(operation, set())
    for index, record in enumerate(records):
        missing_groups = [sorted(group) for group in requirements if not group.intersection(record)]
        if missing_groups:
            raise PartnerQueryError(
                "response_schema_mismatch",
                f"Record {index} is missing required Partner response fields: {missing_groups}.",
            )
        invalid_containers = sorted(
            str(field)
            for field in container_fields
            if field in record and not isinstance(record[field], list)
        )
        if invalid_containers:
            raise PartnerQueryError(
                "invalid_record_field_type",
                "Partner response contains non-array values for hidden container fields: "
                f"{invalid_containers}.",
            )
        invalid_fields = sorted(
            str(field)
            for field, value in record.items()
            if field in KNOWN_FIELDS[operation]
            and field not in container_fields
            and isinstance(value, (Mapping, list))
        )
        if invalid_fields:
            raise PartnerQueryError(
                "invalid_record_field_type",
                "Partner response contains nested values for scalar fields: "
                f"{invalid_fields}.",
            )


def _record_identity(operation: str, record: Mapping[str, Any]) -> Optional[tuple[str, ...]]:
    fields = RECORD_IDENTITY_FIELDS.get(operation)
    if not fields or any(field not in record or record[field] is None for field in fields):
        return None
    return tuple(str(record[field]) for field in fields)


def _validate_verify_batch(
    executor_request: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> None:
    requested = [
        value
        for value in str((executor_request.get("query") or {}).get("userIds") or "").split(",")
        if value
    ]
    returned = [str(record.get("uid")) for record in records if record.get("uid") is not None]
    duplicates = sorted({uid for uid in returned if returned.count(uid) > 1})
    missing = sorted(set(requested) - set(returned))
    unexpected = sorted(set(returned) - set(requested))
    if duplicates or missing or unexpected or len(returned) != len(requested):
        raise PartnerQueryError(
            "uid_response_mismatch",
            "Referral verification response does not exactly cover the requested UID batch: "
            f"missing={missing}, duplicate={duplicates}, unexpected={unexpected}.",
        )


def _remaining_weight_insufficient(
    rate_limit: Mapping[str, Any],
    required_weight: Optional[int],
) -> bool:
    if required_weight is None or required_weight <= 0:
        return False
    remaining = rate_limit.get("remaining")
    if not isinstance(remaining, Mapping) or not remaining:
        return False
    values: List[int] = []
    for value in remaining.values():
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            continue
    return bool(values) and min(values) < required_weight


def _record_page_fingerprint(records: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(list(records), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def execute_query(
    request: Mapping[str, Any],
    *,
    executor=invoke_trader,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    plan = plan_query(request, now=now)
    executor_requests = build_executor_requests(plan)
    collected: List[Mapping[str, Any]] = []
    unknown_fields: set[str] = set()
    pages_fetched = 0
    records_total = 0
    pages_total = 0
    next_page: Optional[int] = None
    current_page_result: Optional[int] = None
    resolved_profile: Dict[str, Any] = {}
    resolved_environment: Optional[str] = None
    last_rate_limit: Dict[str, Any] = {"used": {}, "remaining": {}}
    last_weight: Optional[int] = None
    seen_record_identities: set[tuple[str, ...]] = set()
    all_sources_complete = int(plan.get("page", 1)) == 1
    policy = OPERATION_POLICIES[str(plan["operation"])]
    fetch_all_pages = plan["result_mode"] in {"complete_list", "aggregate_all"}

    def partial_result(
        error: Mapping[str, Any],
        *,
        failed_page: Optional[int],
        offset_pagination: Optional[bool] = None,
    ) -> Dict[str, Any]:
        binding = (
            _continuation_binding(
                plan,
                resolved_profile,
                environment=str(resolved_environment),
                records_total=records_total if pages_total else None,
                pages_total=pages_total if pages_total else None,
            )
            if resolved_profile and resolved_environment in PARTNER_ENVIRONMENTS
            else None
        )
        requested_records_total: Optional[int]
        requested_pages_total: Optional[int]
        if plan["operation"] == "verify-referrals":
            requested_records_total = len(plan["query_scope"].get("uids") or [])
            requested_pages_total = len(executor_requests)
        else:
            requested_records_total = records_total if pages_total else None
            requested_pages_total = pages_total if pages_total else None
        return build_partial_error_envelope(
            operation=str(plan["operation"]),
            records=collected,
            pages_fetched=pages_fetched,
            next_page=failed_page,
            records_total=requested_records_total,
            pages_total=requested_pages_total,
            error=error,
            offset_pagination=(
                bool(policy.page_field)
                if offset_pagination is None
                else offset_pagination
            ),
            profile=resolved_profile,
            query_scope=plan["query_scope"],
            time_range=plan["time_range"],
            rate_limit=last_rate_limit,
            continuation_binding=binding,
            records_seen_before=int(plan.get("records_seen", 0)),
            environment=resolved_environment,
        )

    for request_index, initial_request in enumerate(executor_requests):
        executor_request = copy.deepcopy(initial_request)
        first_page_for_request = True
        expected_remote_total: Optional[int] = None
        expected_remote_pages: Optional[int] = None
        expected_remote_page_size: Optional[int] = None
        request_record_count = 0
        seen_pages: set[int] = set()
        seen_fingerprints: set[str] = set()
        target = executor_request["body"] if executor_request["body"] else executor_request["query"]
        request_start_page = int(target.get(policy.page_field, 1)) if policy.page_field else 1
        while True:
            response = executor(executor_request)
            response_environment = response.get("environment")
            previously_resolved_environment = resolved_environment
            response_succeeded = bool(response.get("ok"))
            response_error = response.get("error")
            response_error_category = (
                str(response_error.get("category") or "")
                if isinstance(response_error, Mapping)
                else ""
            )
            allows_unresolved_environment = (
                not response_succeeded
                and response_environment is None
                and resolved_environment is None
                and response_error_category in LOCAL_UNRESOLVED_ENVIRONMENT_CATEGORIES
            )
            environment_error: Optional[Dict[str, Any]] = None
            if response_environment is None and not allows_unresolved_environment:
                environment_error = {
                    "category": "schema",
                    "code": "invalid_partner_environment",
                    "message": "Partner executor response is missing a valid environment.",
                }
            elif response_environment is not None and response_environment not in PARTNER_ENVIRONMENTS:
                environment_error = {
                    "category": "schema",
                    "code": "invalid_partner_environment",
                    "message": "Partner executor response contains an invalid environment.",
                }
            elif response_environment in PARTNER_ENVIRONMENTS and resolved_environment is None:
                resolved_environment = str(response_environment)
            if environment_error is None and (
                plan.get("expected_environment") is not None
                and response_environment != plan.get("expected_environment")
            ):
                environment_error = {
                    "category": "completeness",
                    "code": "expected_partner_environment_mismatch",
                    "message": "Resolved Partner environment does not match the expected environment.",
                }
            elif environment_error is None and (
                previously_resolved_environment is not None
                and response_environment != previously_resolved_environment
            ):
                environment_error = {
                    "category": "completeness",
                    "code": "partner_environment_changed",
                    "message": "Partner environment changed during the query.",
                }
            input_continuation = plan.get("input_continuation")
            if (
                environment_error is None
                and input_continuation
                and not allows_unresolved_environment
                and response_environment != input_continuation.get("environment")
            ):
                environment_error = {
                    "category": "completeness",
                    "code": "continuation_environment_mismatch",
                    "message": "Continuation environment does not match this query.",
                }
            if environment_error is not None:
                return partial_result(
                    environment_error,
                    failed_page=(
                        int(target.get(policy.page_field, 1))
                        if policy.page_field
                        else None
                    ),
                )
            response_profile = response.get("profile")
            if response_succeeded and (
                not isinstance(response_profile, Mapping)
                or not str(response_profile.get("resolved_profile_id") or "").strip()
            ):
                return partial_result(
                    {
                        "category": "schema",
                        "code": "invalid_resolved_profile",
                        "message": "Partner executor response is missing a stable resolved profile ID.",
                    },
                    failed_page=(
                        int(target.get(policy.page_field, 1))
                        if policy.page_field
                        else None
                    ),
                )
            if isinstance(response_profile, Mapping) and response_profile:
                candidate_profile = dict(response_profile)
                candidate_id = str(candidate_profile.get("resolved_profile_id") or "")
                existing_id = str(resolved_profile.get("resolved_profile_id") or "")
                if existing_id and candidate_id and candidate_id != existing_id:
                    return partial_result(
                        {
                            "category": "completeness",
                            "code": "resolved_profile_changed",
                            "message": "Resolved profile changed during the query.",
                        },
                        failed_page=request_start_page,
                    )
                resolved_profile = candidate_profile
            response_rate_limit = response.get("rate_limit")
            if isinstance(response_rate_limit, Mapping):
                last_rate_limit = dict(response_rate_limit)
            try:
                if response.get("weight") is not None:
                    last_weight = int(response["weight"])
            except (TypeError, ValueError):
                last_weight = None
            if not response.get("ok"):
                failed_page = (
                    int(target.get(policy.page_field, pages_fetched + 1))
                    if policy.page_field
                    else None
                )
                return partial_result(
                    response.get("error") or {"category": "upstream"},
                    failed_page=failed_page,
                )
            input_continuation = plan.get("input_continuation")
            if input_continuation:
                expected_profile_id = str(input_continuation.get("resolved_profile_id") or "")
                actual_profile_id = str(resolved_profile.get("resolved_profile_id") or "")
                if actual_profile_id != expected_profile_id:
                    return partial_result(
                        {
                            "category": "completeness",
                            "code": "continuation_profile_mismatch",
                            "message": "Continuation resolved_profile_id does not match this query.",
                        },
                        failed_page=request_start_page,
                    )
            try:
                records, metadata = _unwrap_records(response)
            except PartnerQueryError as exc:
                return partial_result(
                    {
                        "category": "schema",
                        "code": exc.code,
                        "message": str(exc),
                    },
                    failed_page=(
                        int(target.get(policy.page_field, 1))
                        if policy.page_field
                        else None
                    ),
                )
            requested_page = int(target.get(policy.page_field, 1)) if policy.page_field else 1
            if policy.page_field and (
                not any(name in metadata for name in ("pages", "totalPages", "pageCount"))
                or "total" not in metadata
            ):
                return partial_result(
                    {
                        "category": "schema",
                        "code": "missing_pagination_metadata",
                        "message": "Paginated Partner responses must include total and page-count metadata.",
                    },
                    failed_page=requested_page,
                )
            try:
                remote_pages = int(
                    metadata.get(
                        "pages",
                        metadata.get("totalPages", metadata.get("pageCount", 1)),
                    )
                )
                remote_total = int(metadata["total"]) if "total" in metadata else len(records)
                current_page_value = metadata.get(
                    "current",
                    metadata.get("page", metadata.get("pageNum", requested_page)),
                )
                current_page = int(current_page_value)
                page_size_value = metadata.get("pageSize", metadata.get("size"))
                remote_page_size = (
                    int(page_size_value) if page_size_value is not None else None
                )
            except (TypeError, ValueError):
                return partial_result(
                    {
                        "category": "schema",
                        "code": "invalid_pagination_metadata",
                        "message": "Partner pagination metadata must contain integers.",
                    },
                    failed_page=requested_page,
                )
            if remote_page_size is not None and remote_page_size <= 0:
                return partial_result(
                    {
                        "category": "schema",
                        "code": "invalid_pagination_page_size",
                        "message": "Partner pagination page size must be a positive integer.",
                    },
                    failed_page=requested_page,
                )
            if (
                expected_remote_page_size is not None
                and not first_page_for_request
                and remote_page_size is None
            ):
                return partial_result(
                    {
                        "category": "completeness",
                        "code": "pagination_page_size_missing",
                        "message": "Partner pagination page size disappeared during the query.",
                    },
                    failed_page=requested_page,
                )
            if remote_pages > 1 and remote_page_size is None:
                return partial_result(
                    {
                        "category": "schema",
                        "code": "missing_pagination_page_size",
                        "message": "Multi-page Partner responses must include pageSize or size metadata.",
                    },
                    failed_page=requested_page,
                )
            if (
                expected_remote_page_size is not None
                and remote_page_size is not None
                and remote_page_size != expected_remote_page_size
            ):
                return partial_result(
                    {
                        "category": "completeness",
                        "code": "pagination_page_size_changed",
                        "message": "Partner pagination page size changed during the query.",
                    },
                    failed_page=requested_page,
                )
            if plan["operation"] == "get-referral-deal-data":
                response_start = metadata.get("startTime")
                response_end = metadata.get("endTime")
                expected_start = (plan.get("time_range") or {}).get("actual_start")
                expected_end = (plan.get("time_range") or {}).get("actual_end")
                test_exclusive_end_matches = False
                if (
                    resolved_environment == "partner_test"
                    and response_end is not None
                    and expected_end is not None
                ):
                    try:
                        test_exclusive_end_matches = (
                            _parse_date(str(response_end))
                            == _parse_date(str(expected_end)) + timedelta(days=1)
                        )
                    except PartnerQueryError:
                        test_exclusive_end_matches = False
                if (
                    response_start is None
                    or response_end is None
                    or str(response_start) != str(expected_start)
                    or (
                        str(response_end) != str(expected_end)
                        and not test_exclusive_end_matches
                    )
                ):
                    return partial_result(
                        {
                            "category": "completeness",
                            "code": "response_time_range_mismatch",
                            "message": "Partner response date range does not match the requested range.",
                        },
                        failed_page=requested_page,
                        offset_pagination=False,
                    )
            empty_zero_page = (
                remote_pages == 0
                and remote_total == 0
                and not records
                and requested_page == 1
                and current_page in {0, 1}
            )
            if empty_zero_page:
                current_page = requested_page
            if not empty_zero_page and (
                current_page < 1
                or remote_pages < 1
                or current_page > remote_pages
                or remote_total < 0
            ):
                return partial_result(
                    {
                        "category": "completeness",
                        "code": "invalid_pagination_metadata",
                        "message": "Partner pagination metadata is outside its valid range.",
                    },
                    failed_page=requested_page,
                )
            source_pagination = (
                (plan.get("input_continuation") or {}).get("source_pagination")
            )
            if isinstance(source_pagination, Mapping):
                try:
                    continuation_total = int(source_pagination["records_total"])
                    continuation_pages = int(source_pagination["pages_total"])
                except (KeyError, TypeError, ValueError):
                    return partial_result(
                        {
                            "category": "completeness",
                            "code": "invalid_continuation_pagination",
                            "message": "Continuation pagination snapshot is invalid.",
                        },
                        failed_page=requested_page,
                    )
                if remote_total != continuation_total or remote_pages != continuation_pages:
                    return partial_result(
                        {
                            "category": "completeness",
                            "code": "continuation_pagination_changed",
                            "message": "Pagination total or page count changed since the continuation was issued.",
                        },
                        failed_page=requested_page,
                    )
            if current_page != requested_page or current_page in seen_pages:
                return partial_result(
                    {
                        "category": "completeness",
                        "code": "pagination_sequence_mismatch",
                        "message": "Partner response page does not match the requested page sequence.",
                    },
                    failed_page=requested_page,
                )
            if not first_page_for_request and (
                remote_total != expected_remote_total or remote_pages != expected_remote_pages
            ):
                return partial_result(
                    {
                        "category": "completeness",
                        "code": "pagination_metadata_changed",
                        "message": "Pagination total or page count changed during the query.",
                    },
                    failed_page=current_page,
                )
            fingerprint = _record_page_fingerprint(records)
            if records and fingerprint in seen_fingerprints:
                return partial_result(
                    {
                        "category": "completeness",
                        "code": "duplicate_page_payload",
                        "message": "Partner pagination returned a duplicate page payload.",
                    },
                    failed_page=current_page,
                )
            try:
                _validate_record_schema(str(plan["operation"]), records)
                if plan["operation"] == "verify-referrals":
                    _validate_verify_batch(executor_request, records)
            except PartnerQueryError as exc:
                return partial_result(
                    {
                        "category": (
                            "completeness"
                            if exc.code == "uid_response_mismatch"
                            else "schema"
                        ),
                        "code": exc.code,
                        "message": str(exc),
                    },
                    failed_page=current_page,
                    offset_pagination=bool(policy.page_field),
                )
            page_identities: set[tuple[str, ...]] = set()
            for record in records:
                identity = _record_identity(str(plan["operation"]), record)
                if identity is None:
                    continue
                if identity in seen_record_identities or identity in page_identities:
                    return partial_result(
                        {
                            "category": "completeness",
                            "code": "duplicate_record_identity",
                            "message": "Partner pagination returned a duplicate logical record.",
                        },
                        failed_page=current_page,
                        offset_pagination=bool(policy.page_field),
                    )
                page_identities.add(identity)
            seen_pages.add(current_page)
            if records:
                seen_fingerprints.add(fingerprint)
            seen_record_identities.update(page_identities)
            if first_page_for_request:
                pages_total += remote_pages
                records_total += remote_total
                expected_remote_total = remote_total
                expected_remote_pages = remote_pages
                expected_remote_page_size = remote_page_size
                first_page_for_request = False
            pages_fetched += 1
            request_record_count += len(records)
            current_page_result = current_page
            for record in records:
                try:
                    projected = project_known_fields(
                        record,
                        known_fields=KNOWN_FIELDS[str(plan["operation"])],
                        timestamp_fields=TIMESTAMP_FIELDS.get(str(plan["operation"])),
                        date_fields=DATE_FIELDS.get(str(plan["operation"])),
                        field_aliases=RESPONSE_FIELD_ALIASES.get(str(plan["operation"])),
                        language=str(plan.get("language") or "en"),
                    )
                except PartnerQueryError as exc:
                    return partial_result(
                        {
                            "category": "schema",
                            "code": exc.code,
                            "message": str(exc),
                        },
                        failed_page=current_page,
                    )
                collected.append(projected["record"])
                unknown_fields.update(projected["unknown_fields"])

            if current_page < remote_pages and not records:
                return partial_result(
                    {
                        "category": "completeness",
                        "code": "empty_intermediate_page",
                        "message": "Partner pagination returned an empty page before the final page.",
                    },
                    failed_page=current_page,
                )
            if current_page >= remote_pages:
                if (request_start_page == 1 and (fetch_all_pages or remote_pages == 1)) and (
                    request_record_count != remote_total
                ):
                    return partial_result(
                        {
                            "category": "completeness",
                            "code": "pagination_record_count_mismatch",
                            "message": "Fetched Partner record count does not match pagination total.",
                        },
                        failed_page=current_page,
                    )
                if request_start_page > 1 and (
                    int(plan.get("records_seen", 0)) + request_record_count != remote_total
                ):
                    return partial_result(
                        {
                            "category": "completeness",
                            "code": "pagination_record_count_mismatch",
                            "message": "Continuation coverage does not match pagination total.",
                        },
                        failed_page=current_page,
                    )
                break
            next_page = current_page + 1
            if not fetch_all_pages or not policy.page_field:
                all_sources_complete = False
                break
            if _remaining_weight_insufficient(last_rate_limit, last_weight):
                return partial_result(
                    {
                        "category": "rate_limit",
                        "code": "insufficient_remaining_weight",
                        "message": "Remaining WEEX IP weight is insufficient for the next Partner page.",
                        "recovery_action": "Stop now and restart the complete query from page 1 after weight recovers.",
                    },
                    failed_page=next_page,
                )
            target[policy.page_field] = next_page
            next_page = None

        if request_index < len(executor_requests) - 1 and _remaining_weight_insufficient(
            last_rate_limit,
            last_weight,
        ):
            return partial_result(
                {
                    "category": "rate_limit",
                    "code": "insufficient_remaining_weight",
                    "message": "Remaining WEEX IP weight is insufficient for the next UID batch.",
                    "recovery_action": "Stop now and restart all UID batches after weight recovers.",
                },
                failed_page=None,
                offset_pagination=False,
            )

    effective_time_range = plan.get("time_range") or {}
    time_coverage_complete = all(
        effective_time_range.get(requested) in {None, effective_time_range.get(actual)}
        for requested, actual in (
            ("requested_start", "actual_start"),
            ("requested_end", "actual_end"),
        )
    )
    continued_multi_segment_query = (
        isinstance((plan.get("input_continuation") or {}).get("time_state"), Mapping)
        or (plan.get("selected_action") or {}).get("type") == "earlier_time_range"
    )
    if continued_multi_segment_query:
        # Previous segments are not persisted or cryptographically bound to this call.
        # Never publish a cross-segment total or claim full-range completion from only
        # the current segment.
        time_coverage_complete = False
    source_complete = (
        all_sources_complete
        and pages_fetched >= pages_total
        and time_coverage_complete
    )
    summary: Optional[Dict[str, Any]] = None
    if source_complete:
        summary = {"record_count": len(collected)}
        try:
            if plan["operation"] == "get-commission":
                amounts = aggregate_decimal_fields(
                    collected,
                    group_field="coin",
                    amount_field="commission",
                    strict=True,
                )
                summary["commission_by_coin"] = {
                    key: str(value) for key, value in amounts.items()
                }
            elif plan["operation"] == "get-direct-trade-asset" and plan["result_mode"] == "aggregate_all":
                totals = aggregate_decimal_totals(
                    collected,
                    fields=(
                        "depositAmount",
                        "withdrawalAmount",
                        "spotTradingAmount",
                        "futuresTradingAmount",
                        "commission",
                    ),
                )
                summary["direct_trade_asset_totals"] = {
                    key: str(value) for key, value in totals.items()
                }
            elif plan["operation"] == "get-sub-agent-stats" and plan["result_mode"] == "aggregate_all":
                totals = aggregate_decimal_totals(
                    collected,
                    fields=("tradingVolume", "netTradingFee", "paidCommission"),
                )
                summary["sub_agent_totals"] = {
                    key: str(value) for key, value in totals.items()
                }
            elif plan["operation"] == "verify-referrals":
                summary["referral_count"] = sum(
                    1 for record in collected if record.get("is_referral") is True
                )
                summary["non_referral_count"] = sum(
                    1 for record in collected if record.get("is_referral") is False
                )
        except PartnerQueryError as exc:
            return partial_result(
                {
                    "category": "completeness",
                    "code": exc.code,
                    "message": str(exc),
                },
                failed_page=current_page_result,
            )
    binding = _continuation_binding(
        plan,
        resolved_profile,
        environment=str(resolved_environment),
        records_total=records_total,
        pages_total=pages_total if policy.page_field else 1,
        current_page=current_page_result if policy.page_field else 1,
        page_size=expected_remote_page_size if policy.page_field else None,
        records_seen_before=int(plan.get("records_seen", 0)),
        records_on_page=request_record_count if policy.page_field else len(collected),
        display_offset=int(plan.get("display_offset", 0)),
    )
    result = build_result_envelope(
        operation=str(plan["operation"]),
        result_mode=str(plan["result_mode"]),
        records=collected,
        pages_fetched=pages_fetched,
        pages_total=pages_total,
        records_total=records_total,
        next_page=next_page,
        profile=resolved_profile,
        query_scope=plan["query_scope"],
        time_range=plan["time_range"],
        summary=summary,
        continuation=plan["continuation"],
        source_complete=source_complete,
        display_offset=int(plan.get("display_offset", 0)),
        current_page=current_page_result,
        rate_limit=last_rate_limit,
        continuation_binding=binding,
        records_seen_before=int(plan.get("records_seen", 0)),
        environment=resolved_environment,
        source_partial=(not time_coverage_complete or not all_sources_complete) and bool(collected),
    )
    if continued_multi_segment_query:
        aggregate_result = plan.get("result_mode") == "aggregate_all"
        result["warnings"].append(
            {
                "code": (
                    "cross_segment_aggregate_not_combined"
                    if aggregate_result
                    else "cross_segment_results_not_combined"
                ),
                "message": (
                    "Earlier time-segment records are not persisted or combined with this call; "
                    + (
                        "no whole-range aggregate is published."
                        if aggregate_result
                        else "this result covers only the current segment."
                    )
                ),
            }
        )
    if unknown_fields:
        result["warnings"].append(
            {"code": "unknown_response_fields", "field_names": sorted(unknown_fields)}
        )
    return result


def _load_request(path: str) -> Dict[str, Any]:
    if path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PartnerQueryError("invalid_json", f"Invalid request JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PartnerQueryError("invalid_json", "Request JSON must be an object.")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WEEX Partner deterministic query orchestrator")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in OPERATION_POLICIES:
        command = subparsers.add_parser(operation)
        command.add_argument(
            "--request-file",
            default="-",
            help="Structured request JSON file; default '-' reads stdin",
        )
        command.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        request_payload = _load_request(args.request_file)
        declared_operation = request_payload.get("operation")
        if declared_operation not in {None, args.operation}:
            raise PartnerQueryError(
                "operation_mismatch",
                "The subcommand and request operation do not match.",
            )
        request_payload["operation"] = args.operation
        result = execute_query(request_payload)
    except PartnerQueryError as exc:
        result = build_local_error_envelope(
            operation=args.operation,
            code=exc.code,
            message=str(exc),
        )
    except Exception:
        result = build_local_error_envelope(
            operation=args.operation,
            category="internal_error",
            code="partner_orchestrator_failed",
            message="The Partner orchestrator failed before producing a safe query result.",
            recovery_action="Review the local runtime and trader dependency, then submit a new query.",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
