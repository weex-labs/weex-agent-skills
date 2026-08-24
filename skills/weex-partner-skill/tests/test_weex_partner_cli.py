#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
MODULE_PATH = SCRIPTS / "weex_partner_cli.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_partner_cli_module():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("weex_partner_cli", MODULE_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WeexPartnerCliPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.partner = load_partner_cli_module()

    def require_partner(self):
        self.assertIsNotNone(
            self.partner,
            "Partner orchestration CLI is not implemented yet; expected T1 RED.",
        )
        return self.partner

    def test_runtime_field_projection_metadata_is_derived_from_official_catalog(self) -> None:
        partner = self.require_partner()
        self.assertTrue(
            hasattr(partner, "PARTNER_FIELD_CATALOG"),
            "runtime field catalog is not loaded",
        )
        catalog_operations = partner.PARTNER_FIELD_CATALOG["operations"]
        expected_known_fields = {
            operation: {
                item["wire_name"]
                for item in definition["response_fields"]
                if item["role"] == "record"
            }
            for operation, definition in catalog_operations.items()
        }

        self.assertEqual(partner.KNOWN_FIELDS, expected_known_fields)
        self.assertEqual(
            partner.CONTAINER_FIELDS,
            {
                operation: {
                    item["wire_name"]
                    for item in definition["response_fields"]
                    if item.get("format") == "hidden_container"
                }
                for operation, definition in catalog_operations.items()
                if any(
                    item.get("format") == "hidden_container"
                    for item in definition["response_fields"]
                )
            },
        )
        self.assertNotIn("get-internal-withdrawals", catalog_operations)
        self.assertNotIn("get-internal-withdrawals", partner.REQUEST_FIELD_ALIASES)
        self.assertEqual(partner.RESPONSE_FIELD_ALIASES["verify-referrals"]["isRefferal"], "is_referral")


    def test_official_minimum_selector_uses_smallest_documented_range(self) -> None:
        partner = self.require_partner()
        self.assertEqual(partner.choose_official_minimum_days([30, 90, 365]), 30)

    def test_missing_time_uses_endpoint_official_minimum_and_reports_utc(self) -> None:
        partner = self.require_partner()
        now = datetime(2026, 7, 15, 0, 0, 0, tzinfo=timezone.utc)
        request = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        plan = partner.plan_query(request, now=now)

        self.assertEqual(plan["time_range"]["source"], "official_minimum_default")
        self.assertEqual(plan["time_range"]["minimum_days"], 90)
        self.assertEqual(plan["time_range"]["actual_end"], "2026-07-15T00:00:00Z")
        self.assertEqual(plan["time_range"]["actual_start"], "2026-04-16T00:00:00Z")
        self.assertTrue(plan["continuation"]["can_continue"])

    def test_explicit_time_range_takes_priority_over_endpoint_default(self) -> None:
        partner = self.require_partner()
        request = {
            "operation": "get-commission",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["10001"], "all_confirmed": False},
            "time_range": {
                "start": "2026-07-01T00:00:00Z",
                "end": "2026-07-10T00:00:00Z",
            },
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        plan = partner.plan_query(request, now=datetime(2026, 7, 15, tzinfo=timezone.utc))

        self.assertEqual(plan["time_range"]["source"], "user")
        self.assertEqual(plan["time_range"]["actual_start"], request["time_range"]["start"])
        self.assertEqual(plan["time_range"]["actual_end"], request["time_range"]["end"])

    def test_explicit_year_commission_range_starts_with_latest_three_calendar_months(self) -> None:
        partner = self.require_partner()
        request = {
            "operation": "get-commission",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": {
                "start": "2025-01-01T00:00:00Z",
                "end": "2026-01-01T00:00:00Z",
            },
            "filters": {"product_type": "SPOT"},
            "result_mode": "summary_with_first_20",
        }

        try:
            plan = partner.plan_query(
                request,
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        except partner.PartnerQueryError as exc:
            self.fail(f"bounded commission history should be segmented instead of rejected: {exc}")

        self.assertEqual(plan["time_range"]["actual_start"], "2025-10-01T00:00:00Z")
        self.assertEqual(plan["time_range"]["actual_end"], "2026-01-01T00:00:00Z")
        self.assertTrue(plan["continuation"]["can_continue"])
        self.assertEqual(plan["continuation"]["segment_months"], 3)
        earlier = next(
            action
            for action in partner.build_result_envelope(
                operation="get-commission",
                result_mode="summary_with_first_20",
                records=[],
                pages_fetched=1,
                pages_total=1,
                records_total=0,
                next_page=None,
                continuation=plan["continuation"],
                source_complete=False,
            )["continuation"]["actions"]
            if action["type"] == "earlier_time_range"
        )
        self.assertEqual(
            earlier["request_patch"]["time_range"],
            {
                "start": "2025-07-01T00:00:00Z",
                "end": "2025-09-30T23:59:59.999Z",
            },
        )

    def test_commission_calendar_month_continuation_walks_bounded_range_without_gaps(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "get-commission",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": {
                "start": "2025-01-01T00:00:00Z",
                "end": "2026-01-01T00:00:00Z",
            },
            "filters": {"product_type": "SPOT"},
            "result_mode": "summary_with_first_20",
        }
        expected_ranges = [
            ("2025-10-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            ("2025-07-01T00:00:00Z", "2025-09-30T23:59:59.999Z"),
            ("2025-04-01T00:00:00Z", "2025-06-30T23:59:59.999Z"),
            ("2025-01-01T00:00:00Z", "2025-03-31T23:59:59.999Z"),
        ]
        request = base

        for index, expected_range in enumerate(expected_ranges):
            result = partner.execute_query(
                request,
                executor=lambda _request: {
                    "ok": True,
                    "environment": "partner_production",
                    "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                    "data": {
                        "channelCommissionInfoItems": [],
                        "page": 1,
                        "pages": 1,
                        "pageSize": 100,
                        "total": 0,
                    },
                },
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(
                (
                    result["time_range"]["actual_start"],
                    result["time_range"]["actual_end"],
                ),
                expected_range,
            )
            self.assertFalse(result["complete"])
            earlier_actions = [
                action
                for action in result["continuation"]["actions"]
                if action["type"] == "earlier_time_range"
            ]
            if index < len(expected_ranges) - 1:
                self.assertEqual(len(earlier_actions), 1)
                request = dict(
                    base,
                    continuation=result["continuation"],
                    **earlier_actions[0]["request_patch"],
                )
            else:
                self.assertEqual(earlier_actions, [])
                self.assertFalse(result["continuation"]["can_continue"])

    def test_commission_month_end_leap_year_segments_are_closed_without_overlap_or_gap(self) -> None:
        partner = self.require_partner()
        plan = partner.plan_query(
            {
                "operation": "get-commission",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": {
                    "start": "2023-11-30T00:00:00Z",
                    "end": "2024-05-31T00:00:00Z",
                },
                "filters": {"product_type": "SPOT"},
                "result_mode": "summary_with_first_20",
            },
            now=datetime(2024, 5, 31, tzinfo=timezone.utc),
        )
        result = partner.build_result_envelope(
            operation="get-commission",
            result_mode="summary_with_first_20",
            records=[],
            pages_fetched=1,
            pages_total=1,
            records_total=0,
            next_page=None,
            continuation=plan["continuation"],
            source_complete=False,
        )
        earlier = next(
            action
            for action in result["continuation"]["actions"]
            if action["type"] == "earlier_time_range"
        )

        self.assertEqual(plan["time_range"]["actual_start"], "2024-02-29T00:00:00Z")
        self.assertEqual(
            earlier["request_patch"]["time_range"],
            {
                "start": "2023-11-30T00:00:00Z",
                "end": "2024-02-28T23:59:59.999Z",
            },
        )
        earlier_end = partner._as_utc_datetime(
            earlier["request_patch"]["time_range"]["end"]
        )
        current_start = partner._as_utc_datetime(plan["time_range"]["actual_start"])
        self.assertEqual(earlier_end + timedelta(milliseconds=1), current_start)

    def test_commission_month_end_closed_action_is_executable_without_resegmentation(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "get-commission",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": {
                "start": "2023-02-28T00:00:00Z",
                "end": "2023-08-31T00:00:00Z",
            },
            "filters": {"product_type": "SPOT"},
            "result_mode": "summary_with_first_20",
        }

        def executor(_request):
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "channelCommissionInfoItems": [],
                    "page": 1,
                    "pages": 1,
                    "pageSize": 100,
                    "total": 0,
                },
            }

        first = partner.execute_query(
            base,
            executor=executor,
            now=datetime(2023, 8, 31, tzinfo=timezone.utc),
        )
        earlier = next(
            action
            for action in first["continuation"]["actions"]
            if action["type"] == "earlier_time_range"
        )
        continued_request = dict(
            base,
            continuation=first["continuation"],
            **earlier["request_patch"],
        )
        try:
            second = partner.execute_query(
                continued_request,
                executor=executor,
                now=datetime(2023, 8, 31, tzinfo=timezone.utc),
            )
        except partner.PartnerQueryError as exc:
            self.fail(f"an unchanged month-end continuation action must execute: {exc}")

        self.assertTrue(second["ok"])
        self.assertEqual(
            (
                second["time_range"]["actual_start"],
                second["time_range"]["actual_end"],
            ),
            ("2023-02-28T00:00:00Z", "2023-05-30T23:59:59.999Z"),
        )
        self.assertFalse(
            any(
                action["type"] == "earlier_time_range"
                for action in second["continuation"]["actions"]
            )
        )

    def test_missing_time_is_rejected_when_only_maximum_or_unknown_range_is_public(self) -> None:
        partner = self.require_partner()
        request = {
            "operation": "get-sub-agent-stats",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["20001"], "all_confirmed": False},
            "time_range": None,
            "filters": {"product_type": "FUTURES"},
            "result_mode": "summary_with_first_20",
        }

        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(request, now=datetime(2026, 7, 15, tzinfo=timezone.utc))
        self.assertEqual(exc_info.exception.code, "time_range_required")

    def test_removed_internal_withdrawal_operation_is_rejected_locally(self) -> None:
        partner = self.require_partner()
        request = {
            "operation": "get-internal-withdrawals",
            "profile": "main",
            "scope": {"mode": "none", "all_confirmed": False},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }

        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(request, now=datetime(2026, 7, 15, tzinfo=timezone.utc))
        self.assertEqual(exc_info.exception.code, "unsupported_operation")

    def test_implicit_all_scope_is_rejected_before_any_executor_call(self) -> None:
        partner = self.require_partner()
        request = {
            "operation": "get-referral-deal-data",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": False},
            "time_range": {
                "start": "2026-07-01",
                "end": "2026-07-15",
            },
            "filters": {},
            "result_mode": "summary_with_first_20",
        }

        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(request, now=datetime(2026, 7, 15, tzinfo=timezone.utc))
        self.assertEqual(exc_info.exception.code, "scope_confirmation_required")

    def test_all_scope_requires_literal_json_true(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "time_range": None,
            "filters": {},
        }

        for invalid_confirmation in ("false", "true", 1, 0, None):
            with self.subTest(all_confirmed=invalid_confirmation):
                request = dict(
                    base,
                    scope={"mode": "all", "all_confirmed": invalid_confirmation},
                )
                with self.assertRaises(partner.PartnerQueryError) as exc_info:
                    partner.plan_query(
                        request,
                        now=datetime(2026, 7, 15, tzinfo=timezone.utc),
                    )
                self.assertEqual(exc_info.exception.code, "scope_confirmation_required")

        accepted = partner.plan_query(
            dict(base, scope={"mode": "all", "all_confirmed": True}),
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        self.assertTrue(accepted["query_scope"]["all_confirmed"])

    def test_uid_values_must_be_unsigned_decimal_longs(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "verify-referrals",
            "profile": "main",
            "filters": {},
        }

        for invalid_uid in ("10001,10002", "-1", 0, None, True, {"uid": 1}):
            with self.subTest(uid=invalid_uid):
                with self.assertRaises(partner.PartnerQueryError) as exc_info:
                    partner.plan_query(
                        dict(base, scope={"mode": "uids", "uids": [invalid_uid]}),
                        now=datetime(2026, 7, 15, tzinfo=timezone.utc),
                    )
                self.assertEqual(exc_info.exception.code, "invalid_uid")

        plan = partner.plan_query(
            dict(base, scope={"mode": "uids", "uids": [10001, "00010002"]}),
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(plan["query_scope"]["uids"], ["10001", "10002"])
        wire_values = [
            item["query"]["userIds"]
            for item in partner.build_executor_requests(plan)
        ]
        self.assertEqual(wire_values, ["10001,10002"])

    def test_unknown_and_invalid_filters_fail_before_executor(self) -> None:
        partner = self.require_partner()
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        base = {
            "operation": "get-commission",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["10001"]},
            "time_range": {
                "start": "2026-07-01T00:00:00Z",
                "end": "2026-07-10T00:00:00Z",
            },
        }

        for filters, expected_code in (
            ({"status": "SUCCESS"}, "invalid_filters"),
            ({"product_type": "MARGIN"}, "invalid_product_type"),
            ({"coin": ""}, "invalid_coin"),
            ({"coin": "ETH"}, "invalid_coin"),
        ):
            with self.subTest(filters=filters):
                with self.assertRaises(partner.PartnerQueryError) as exc_info:
                    partner.plan_query(dict(base, filters=filters), now=now)
                self.assertEqual(exc_info.exception.code, expected_code)

    def test_language_is_strict(self) -> None:
        partner = self.require_partner()
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        base = {
            "operation": "verify-referrals",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["10001"]},
            "time_range": None,
            "filters": {},
        }

        with self.assertRaises(partner.PartnerQueryError) as language_error:
            partner.plan_query(dict(base, language="fr"), now=now)
        self.assertEqual(language_error.exception.code, "invalid_language")

    def test_default_result_displays_first_twenty_and_explains_more_records(self) -> None:
        partner = self.require_partner()
        records = [{"uid": str(index)} for index in range(1, 46)]
        result = partner.build_result_envelope(
            operation="list-referral-uids",
            result_mode="summary_with_first_20",
            records=records,
            pages_fetched=1,
            pages_total=3,
            records_total=145,
            next_page=2,
        )

        self.assertEqual(len(result["records"]), 20)
        self.assertFalse(result["complete"])
        self.assertTrue(result["pagination"]["has_more"])
        self.assertEqual(result["pagination"]["remaining_count"], 125)
        self.assertEqual(result["pagination"]["next_page"], 2)
        self.assertTrue(result["continuation"]["can_continue"])
        self.assertIn("next", result["continuation"]["stop_reason"])

    def test_decimal_aggregation_never_uses_binary_float(self) -> None:
        partner = self.require_partner()
        summary = partner.aggregate_decimal_fields(
            [
                {"coin": "USDT", "commission": "0.1"},
                {"coin": "USDT", "commission": "0.2"},
                {"coin": "BTC", "commission": "0.00000001"},
            ],
            group_field="coin",
            amount_field="commission",
        )

        self.assertEqual(summary["USDT"], Decimal("0.3"))
        self.assertEqual(summary["BTC"], Decimal("0.00000001"))

    def test_rate_limit_after_partial_pages_blocks_complete_summary_and_requires_restart(self) -> None:
        partner = self.require_partner()
        result = partner.build_partial_error_envelope(
            operation="get-direct-trade-asset",
            records=[{"uid": "10001"}, {"uid": "10002"}],
            pages_fetched=2,
            next_page=3,
            records_total=530,
            error={"category": "rate_limit", "http_status": 429},
            offset_pagination=True,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["partial"])
        self.assertIsNone(result["summary"])
        self.assertTrue(result["continuation"]["restart_required"])
        self.assertEqual(result["continuation"]["restart_from_page"], 1)
        self.assertFalse(result["continuation"]["can_continue"])

    def test_first_page_failure_reports_unknown_has_more_without_claiming_records_exist(self) -> None:
        partner = self.require_partner()
        result = partner.build_partial_error_envelope(
            operation="get-commission",
            records=[],
            pages_fetched=0,
            next_page=1,
            records_total=None,
            pages_total=None,
            error={"category": "transport", "message": "Partner transport failed."},
            offset_pagination=True,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["partial"])
        self.assertIsNone(result["pagination"]["has_more"])
        self.assertIsNone(result["pagination"]["remaining_count"])
        self.assertFalse(result["continuation"]["can_continue"])
        self.assertTrue(result["continuation"]["restart_required"])

    def test_partial_failure_uses_known_page_metadata_when_next_page_is_missing(self) -> None:
        partner = self.require_partner()
        result = partner.build_partial_error_envelope(
            operation="get-commission",
            records=[{"uid": "10001"}],
            pages_fetched=1,
            next_page=None,
            records_total=None,
            pages_total=3,
            error={"category": "transport", "message": "Partner transport failed."},
            offset_pagination=True,
        )

        self.assertTrue(result["pagination"]["has_more"])
        self.assertIsNone(result["pagination"]["remaining_count"])

    def test_partial_failure_reports_no_more_pages_when_known_pages_are_covered(self) -> None:
        partner = self.require_partner()
        result = partner.build_partial_error_envelope(
            operation="get-commission",
            records=[{"uid": "10001"}],
            pages_fetched=3,
            next_page=None,
            records_total=None,
            pages_total=3,
            error={"category": "schema", "message": "Partner response schema validation failed."},
            offset_pagination=True,
        )

        self.assertFalse(result["pagination"]["has_more"])
        self.assertIsNone(result["pagination"]["remaining_count"])

    def test_continuation_is_bound_to_profile_scope_operation_filters_and_contract(self) -> None:
        partner = self.require_partner()
        continuation = {
            "resolved_profile_id": "profile-a",
            "query_scope": {"mode": "uids", "uids": ["10001"]},
            "operation": "get-commission",
            "contract_version": "partner-v3-2026-06-22",
            "filters": {"product_type": "SPOT"},
            "result_mode": "summary_with_first_20",
        }
        request = {
            "resolved_profile_id": "profile-b",
            "query_scope": {"mode": "uids", "uids": ["10001"]},
            "operation": "get-commission",
            "contract_version": "partner-v3-2026-06-22",
            "filters": {"product_type": "SPOT"},
            "result_mode": "summary_with_first_20",
        }

        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.validate_continuation(continuation, request)
        self.assertEqual(exc_info.exception.code, "continuation_mismatch")

    def test_unknown_response_fields_keep_names_but_hide_values(self) -> None:
        partner = self.require_partner()
        normalized = partner.project_known_fields(
            {"userId": "10001", "totalUsdt": "12.3", "contractTotalUsdt": "999.9"},
            known_fields={"userId", "totalUsdt"},
        )

        self.assertEqual(normalized["record"]["userId"], "10001")
        self.assertNotIn("contractTotalUsdt", normalized["record"])
        self.assertEqual(normalized["unknown_fields"], ["contractTotalUsdt"])
        self.assertNotIn("999.9", repr(normalized))

    def test_all_seven_operations_map_to_the_exact_trader_allowlist_keys(self) -> None:
        partner = self.require_partner()
        expected = {
            "list-referral-uids": "partner.get-affiliate-uids",
            "get-direct-trade-asset": "partner.get-channel-user-trade-and-asset",
            "get-commission": "partner.get-affiliate-commission",
            "get-sub-agent-stats": "partner.query-sub-channel-transactions",
            "verify-referrals": "partner.verify-referrals",
            "get-referral-assets": "partner.get-referral-assets",
            "get-referral-deal-data": "partner.get-referral-deal-data",
        }
        self.assertEqual(
            {name: policy.endpoint for name, policy in partner.OPERATION_POLICIES.items()},
            expected,
        )

    def test_commission_missing_time_uses_official_seven_day_minimum(self) -> None:
        partner = self.require_partner()
        now = datetime(2026, 7, 15, 0, 0, 0, tzinfo=timezone.utc)
        plan = partner.plan_query(
            {
                "operation": "get-commission",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001"], "all_confirmed": False},
                "time_range": None,
                "filters": {},
                "result_mode": "summary_with_first_20",
            },
            now=now,
        )

        self.assertEqual(plan["time_range"]["minimum_days"], 7)
        self.assertEqual(plan["time_range"]["actual_start"], "2026-07-08T00:00:00Z")
        self.assertEqual(plan["filters"]["product_type"], "SPOT")
        self.assertFalse(plan["continuation"]["can_continue"])

    def test_verify_referrals_splits_more_than_one_hundred_uids_without_overlap(self) -> None:
        partner = self.require_partner()
        uids = [str(index) for index in range(1, 206)]
        plan = partner.plan_query(
            {
                "operation": "verify-referrals",
                "profile": "main",
                "scope": {"mode": "uids", "uids": uids, "all_confirmed": False},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        requests = partner.build_executor_requests(plan)

        self.assertEqual(len(requests), 3)
        batches = [item["query"]["userIds"].split(",") for item in requests]
        self.assertEqual([len(batch) for batch in batches], [100, 100, 5])
        self.assertEqual([uid for batch in batches for uid in batch], uids)

    def test_deal_data_repeats_user_ids_in_executor_query(self) -> None:
        partner = self.require_partner()
        plan = partner.plan_query(
            {
                "operation": "get-referral-deal-data",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001", "10002"], "all_confirmed": False},
                "time_range": {"start": "2026-07-01", "end": "2026-07-15"},
                "filters": {},
                "result_mode": "summary_with_first_20",
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        request = partner.build_executor_requests(plan)[0]

        self.assertEqual(request["query"]["userIds"], ["10001", "10002"])
        self.assertEqual(request["query"]["startTime"], "2026-07-01")
        self.assertEqual(request["query"]["endTime"], "2026-07-15")

    def test_default_mode_fetches_one_page_and_discloses_the_next_page(self) -> None:
        partner = self.require_partner()
        calls = []

        def executor(request):
            calls.append(request)
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "code": "00000",
                    "data": {
                        "records": [{"uid": "10001"}, {"uid": "10002"}],
                        "current": 1,
                        "pages": 3,
                        "pageSize": 100,
                        "total": 5,
                    },
                },
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "summary_with_first_20",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(len(calls), 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["pagination"]["pages_total"], 3)
        self.assertEqual(result["pagination"]["records_total"], 5)
        self.assertEqual(result["pagination"]["next_page"], 2)
        self.assertTrue(result["pagination"]["has_more"])

    def test_complete_list_fetches_all_pages_serially(self) -> None:
        partner = self.require_partner()
        requested_pages = []

        def executor(request):
            page = request["query"]["page"]
            requested_pages.append(page)
            page_records = {
                1: [{"uid": "10001"}, {"uid": "10002"}],
                2: [{"uid": "10003"}, {"uid": "10004"}],
                3: [{"uid": "10005"}],
            }[page]
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "code": "00000",
                    "data": {
                        "records": page_records,
                        "current": page,
                        "pages": 3,
                        "pageSize": 100,
                        "total": 5,
                    },
                },
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(requested_pages, [1, 2, 3])
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["records"]), 5)
        self.assertFalse(result["pagination"]["has_more"])

    def test_complete_list_rate_limit_on_later_page_requires_page_one_restart(self) -> None:
        partner = self.require_partner()

        def executor(request):
            page = request["query"]["page"]
            if page == 2:
                return {
                    "ok": False,
                    "environment": "partner_production",
                    "error": {"category": "rate_limit", "http_status": 429},
                }
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "code": "00000",
                    "data": {
                        "records": [{"uid": "10001"}, {"uid": "10002"}],
                        "current": 1,
                        "pages": 3,
                        "pageSize": 100,
                        "total": 5,
                    },
                },
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["complete"])
        self.assertTrue(result["partial"])
        self.assertIsNone(result["summary"])
        self.assertTrue(result["continuation"]["restart_required"])
        self.assertEqual(result["continuation"]["restart_from_page"], 1)

    def test_requested_start_older_than_official_history_is_rejected(self) -> None:
        partner = self.require_partner()
        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(
                {
                    "operation": "list-referral-uids",
                    "profile": "main",
                    "scope": {"mode": "all", "all_confirmed": True},
                    "time_range": {
                        "start": "2025-01-01T00:00:00Z",
                        "end": "2026-07-15T00:00:00Z",
                    },
                    "filters": {},
                    "result_mode": "summary_with_first_20",
                },
                now=datetime(2026, 7, 15, tzinfo=timezone.utc),
            )
        self.assertEqual(exc_info.exception.code, "time_range_out_of_history")

    def test_total_change_between_pages_blocks_complete_result(self) -> None:
        partner = self.require_partner()

        def executor(request):
            page = request["query"]["page"]
            total = 5 if page == 1 else 6
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "code": "00000",
                    "data": {
                        "records": [{"uid": str(page)}],
                        "current": page,
                        "pages": 2,
                        "pageSize": 100,
                        "total": total,
                    },
                },
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["complete"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["error"]["category"], "completeness")
        self.assertTrue(result["continuation"]["restart_required"])

    def test_official_response_list_field_names_are_unwrapped(self) -> None:
        partner = self.require_partner()
        fixtures = {
            "channelUserInfoItemList": [{"uid": "10001"}],
            "channelCommissionInfoItems": [{"uid": "10001", "commission": "1"}],
            "items": [{"uid": "10001"}],
        }
        for field, expected in fixtures.items():
            with self.subTest(field=field):
                records, metadata = partner._unwrap_records(
                    {"data": {field: expected, "pages": 1, "total": 1}}
                )
                self.assertEqual(records, expected)
                self.assertEqual(metadata["total"], 1)

    def test_commission_three_month_limit_uses_calendar_months(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "get-commission",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["10001"], "all_confirmed": False},
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        allowed = dict(base, time_range={
            "start": "2026-04-30T00:00:00Z",
            "end": "2026-07-30T00:00:00Z",
        })
        partner.plan_query(allowed, now=datetime(2026, 7, 30, tzinfo=timezone.utc))

        segmented = dict(base, time_range={
            "start": "2026-04-29T23:59:59Z",
            "end": "2026-07-30T00:00:00Z",
        })
        plan = partner.plan_query(
            segmented,
            now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(plan["time_range"]["actual_start"], "2026-04-30T00:00:00Z")
        self.assertEqual(plan["continuation"]["segment_months"], 3)

    def test_continuation_mismatch_blocks_before_executor_call(self) -> None:
        partner = self.require_partner()
        calls = []
        request = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
            "continuation": {
                "resolved_profile_id": "profile-a",
                "environment": "partner_production",
                "query_scope": {"mode": "uids", "uids": ["10001"]},
                "operation": "get-commission",
                "contract_version": partner.CONTRACT_VERSION,
                "filters": {"product_type": "SPOT"},
                "result_mode": "summary_with_first_20",
            },
        }

        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.execute_query(request, executor=lambda payload: calls.append(payload), now=datetime(2026, 7, 15, tzinfo=timezone.utc))

        self.assertEqual(exc_info.exception.code, "continuation_mismatch")
        self.assertEqual(calls, [])

    def test_output_continuation_binds_query_and_gives_exact_display_patch(self) -> None:
        partner = self.require_partner()
        records = [{"uid": str(index)} for index in range(50)]

        def executor(_request):
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {"1M": 20}, "remaining": {"1M": 1180}},
                "data": {"records": records, "current": 1, "pages": 1, "total": 50},
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "summary_with_first_20",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        continuation = result["continuation"]
        self.assertEqual(continuation["resolved_profile_id"], "profile-a")
        self.assertEqual(continuation["query_scope"], {"mode": "all", "uids": [], "all_confirmed": True})
        self.assertEqual(continuation["operation"], "list-referral-uids")
        self.assertEqual(continuation["contract_version"], partner.CONTRACT_VERSION)
        self.assertEqual(continuation["result_mode"], "summary_with_first_20")
        self.assertEqual(continuation["actions"][0]["request_patch"], {"display_offset": 20})

    def test_display_continuation_reuses_the_original_actual_time_range(self) -> None:
        partner = self.require_partner()
        captured_queries = []
        records = [{"uid": str(index)} for index in range(50)]

        def executor(request):
            captured_queries.append(dict(request["query"]))
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": records, "current": 1, "pages": 1, "total": 50},
            }

        base_request = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        first = partner.execute_query(
            base_request,
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        second_request = dict(
            base_request,
            continuation=first["continuation"],
            display_offset=20,
        )
        second = partner.execute_query(
            second_request,
            executor=executor,
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )

        self.assertEqual(captured_queries[0]["startTime"], captured_queries[1]["startTime"])
        self.assertEqual(captured_queries[0]["endTime"], captured_queries[1]["endTime"])
        self.assertEqual(second["records"][0]["uid"], "20")

    def test_page_continuation_tracks_seen_records_and_finishes(self) -> None:
        partner = self.require_partner()

        def first_executor(_request):
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": "10001"}, {"uid": "10002"}],
                    "current": 1,
                    "pages": 2,
                    "pageSize": 100,
                    "total": 3,
                },
            }

        base_request = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        first = partner.execute_query(
            base_request,
            executor=first_executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        patch = first["continuation"]["actions"][0]["request_patch"]
        self.assertEqual(patch["records_seen"], 2)

        def second_executor(request):
            self.assertEqual(request["query"]["page"], 2)
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": "10003"}],
                    "current": 2,
                    "pages": 2,
                    "pageSize": 100,
                    "total": 3,
                },
            }

        second = partner.execute_query(
            dict(base_request, continuation=first["continuation"], **patch),
            executor=second_executor,
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )

        self.assertFalse(second["pagination"]["has_more"])
        self.assertEqual(second["pagination"]["remaining_count"], 0)

    def test_page_continuation_rejects_total_drift_between_calls(self) -> None:
        partner = self.require_partner()
        base_request = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }

        first = partner.execute_query(
            base_request,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": [{"uid": "1"}], "current": 1, "pages": 2, "pageSize": 100, "total": 2},
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        patch = first["continuation"]["actions"][0]["request_patch"]
        second = partner.execute_query(
            dict(base_request, continuation=first["continuation"], **patch),
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": [{"uid": "2"}], "current": 2, "pages": 2, "pageSize": 100, "total": 3},
            },
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )

        self.assertFalse(second["ok"])
        self.assertEqual(second["error"]["code"], "continuation_pagination_changed")

    def test_continuation_rejects_non_action_patch_before_executor(self) -> None:
        partner = self.require_partner()
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        records = [{"uid": str(10000 + index)} for index in range(30)]
        response = {
            "ok": True,
            "environment": "partner_production",
            "profile": {"resolved_profile_id": "profile-a", "name": "main"},
            "weight": 20,
            "rate_limit": {"used": {}, "remaining": {}},
            "data": {"pages": 1, "total": 30, "channelUserInfoItemList": records},
        }
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
        }
        first = partner.execute_query(base, executor=lambda _request: response, now=now)
        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(
                dict(base, continuation=first["continuation"], display_offset=99),
                now=now,
            )

        self.assertEqual(exc_info.exception.code, "continuation_patch_mismatch")

    def test_page_continuation_rejects_records_seen_tamper(self) -> None:
        partner = self.require_partner()
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
        }
        first = partner.execute_query(
            base,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {}, "remaining": {}},
                "data": {
                    "pages": 2,
                    "pageSize": 100,
                    "total": 2,
                    "channelUserInfoItemList": [{"uid": "10001"}],
                },
            },
            now=now,
        )
        patch = first["continuation"]["actions"][0]["request_patch"]
        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(
                dict(
                    base,
                    continuation=first["continuation"],
                    **dict(patch, records_seen=999),
                ),
                now=now,
            )

        self.assertEqual(exc_info.exception.code, "continuation_patch_mismatch")

    def test_display_continuation_preserves_earlier_time_action(self) -> None:
        partner = self.require_partner()
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        records = [{"uid": str(10000 + index)} for index in range(30)]
        response = {
            "ok": True,
            "environment": "partner_production",
            "profile": {"resolved_profile_id": "profile-a", "name": "main"},
            "weight": 20,
            "rate_limit": {"used": {}, "remaining": {}},
            "data": {"pages": 1, "total": 30, "channelUserInfoItemList": records},
        }
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
        }
        first = partner.execute_query(base, executor=lambda _request: response, now=now)
        display_patch = next(
            action["request_patch"]
            for action in first["continuation"]["actions"]
            if action["type"] == "display_more"
        )
        expected_earlier = next(
            action["request_patch"]
            for action in first["continuation"]["actions"]
            if action["type"] == "earlier_time_range"
        )

        second = partner.execute_query(
            dict(base, continuation=first["continuation"], **display_patch),
            executor=lambda _request: response,
            now=now,
        )
        earlier_actions = [
            action["request_patch"]
            for action in second["continuation"]["actions"]
            if action["type"] == "earlier_time_range"
        ]

        self.assertEqual(earlier_actions, [expected_earlier])

    def test_time_continuation_walks_full_history_without_gaps(self) -> None:
        partner = self.require_partner()
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        response = {
            "ok": True,
            "environment": "partner_production",
            "profile": {"resolved_profile_id": "profile-a", "name": "main"},
            "weight": 20,
            "rate_limit": {"used": {}, "remaining": {}},
            "data": {"pages": 1, "total": 1, "channelUserInfoItemList": [{"uid": "10001"}]},
        }
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
        }
        result = partner.execute_query(base, executor=lambda _request: response, now=now)
        ranges = [result["time_range"]]

        while True:
            earlier_actions = [
                action
                for action in result["continuation"]["actions"]
                if action["type"] == "earlier_time_range"
            ]
            if not earlier_actions:
                break
            result = partner.execute_query(
                dict(
                    base,
                    continuation=result["continuation"],
                    **earlier_actions[0]["request_patch"],
                ),
                executor=lambda _request: response,
                now=now,
            )
            ranges.append(result["time_range"])

        self.assertEqual(len(ranges), 5)
        self.assertEqual(ranges[-1]["actual_start"], "2025-07-15T00:00:00Z")
        for newer, older in zip(ranges, ranges[1:]):
            self.assertEqual(older["actual_end"], newer["actual_start"])

    def test_non_object_record_member_is_schema_failure(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "get-referral-deal-data",
                "profile": "main",
                "scope": {"mode": "uids", "uids": [10001]},
                "time_range": {"start": "2026-07-01", "end": "2026-07-15"},
                "filters": {},
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 10,
                "rate_limit": {"used": {}, "remaining": {}},
                "data": {
                    "data": [{"userId": 10001}, "must-not-be-silently-dropped"],
                    "startTime": "2026-07-01",
                    "endTime": "2026-07-15",
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["error"]["category"], "schema")
        self.assertEqual(result["error"]["code"], "invalid_record_container")
        self.assertNotIn("must-not-be-silently-dropped", repr(result))

    def test_known_scalar_field_with_nested_value_is_schema_failure(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {}, "remaining": {}},
                "data": {
                    "pages": 1,
                    "total": 1,
                    "channelUserInfoItemList": [{"uid": {"unexpected": "hidden"}}],
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["error"]["category"], "schema")
        self.assertEqual(result["error"]["code"], "invalid_record_field_type")
        self.assertNotIn("hidden", repr(result))

    def test_duplicate_logical_record_across_pages_is_incomplete(self) -> None:
        partner = self.require_partner()

        def executor(request):
            page = request["query"]["page"]
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {}, "remaining": {}},
                "data": {
                    "pages": 2,
                    "pageSize": 100,
                    "total": 2,
                    "channelUserInfoItemList": [
                        {"uid": "10001", "inviteCode": f"page-{page}"}
                    ],
                },
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["error"]["category"], "completeness")
        self.assertEqual(result["error"]["code"], "duplicate_record_identity")

    def test_response_date_range_must_match_requested_range(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "get-referral-deal-data",
                "profile": "main",
                "scope": {"mode": "uids", "uids": [10001]},
                "time_range": {"start": "2026-07-01", "end": "2026-07-15"},
                "filters": {},
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 10,
                "rate_limit": {"used": {}, "remaining": {}},
                "data": {
                    "data": [{"userId": 10001}],
                    "startTime": "2020-01-01",
                    "endTime": "2020-01-02",
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["error"]["category"], "completeness")
        self.assertEqual(result["error"]["code"], "response_time_range_mismatch")

    def test_invalid_page_size_metadata_is_schema_failure(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "get-sub-agent-stats",
                "profile": "main",
                "scope": {"mode": "uids", "uids": [10001]},
                "time_range": {
                    "start": "2026-07-01T00:00:00Z",
                    "end": "2026-07-10T00:00:00Z",
                },
                "filters": {"product_type": "SPOT"},
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 10,
                "rate_limit": {"used": {}, "remaining": {}},
                "data": {
                    "records": [{"subAffiliateUid": "10001"}],
                    "current": 1,
                    "pages": 1,
                    "total": 1,
                    "size": 0,
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["error"]["category"], "schema")
        self.assertEqual(result["error"]["code"], "invalid_pagination_page_size")

    def test_uid_batch_partial_result_reports_unqueried_count(self) -> None:
        partner = self.require_partner()
        uids = [str(10000 + index) for index in range(150)]

        def executor(request):
            requested = request["query"]["userIds"].split(",")
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 10,
                "rate_limit": {"used": {"1M": 1200}, "remaining": {"1M": 0}},
                "data": [{"uid": uid, "isRefferal": True} for uid in requested],
            }

        result = partner.execute_query(
            {
                "operation": "verify-referrals",
                "profile": "main",
                "scope": {"mode": "uids", "uids": uids},
                "filters": {},
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertTrue(result["partial"])
        self.assertEqual(result["pagination"]["records_fetched"], 100)
        self.assertEqual(result["pagination"]["records_total"], 150)
        self.assertEqual(result["pagination"]["remaining_count"], 50)
        self.assertEqual(result["pagination"]["pages_total"], 2)

    def test_partner_error_projection_hides_unknown_details(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
            },
            executor=lambda _request: {
                "ok": False,
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {}, "remaining": {}},
                "error": {
                    "category": "upstream",
                    "code": "failed",
                    "message": "Upstream failed",
                    "details": {"undocumented": "must-not-leak"},
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertNotIn("must-not-leak", repr(result))
        self.assertNotIn("details", result["error"])

    def test_partner_error_projection_preserves_only_safe_schema_diagnostics(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "expected_environment": "partner_test",
            },
            executor=lambda _request: {
                "ok": False,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {}, "remaining": {}},
                "error": {
                    "category": "schema",
                    "http_status": 200,
                    "code": None,
                    "message": "Partner response schema validation failed.",
                    "details": {
                        "schema_issue": "non_json_response",
                        "raw_type": "non_json",
                        "values_hidden": True,
                        "field_names": ["code", "message", "raw_type"],
                        "unknown_field_count": 1,
                        "undocumented": {"private": "must-not-leak"},
                    },
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["details"],
            {
                "schema_issue": "non_json_response",
                "raw_type": "non_json",
                "values_hidden": True,
                "field_names": ["code", "message", "raw_type"],
                "unknown_field_count": 1,
            },
        )
        self.assertNotIn("must-not-leak", repr(result))

    def test_partner_error_projection_drops_nested_field_names_without_crashing(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "expected_environment": "partner_test",
            },
            executor=lambda _request: {
                "ok": False,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {}, "remaining": {}},
                "error": {
                    "category": "schema",
                    "message": "Partner response schema validation failed.",
                    "details": {
                        "field_names": [{"private": "must-not-leak"}],
                    },
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertNotIn("details", result["error"])
        self.assertNotIn("must-not-leak", repr(result))

    def test_partner_error_projection_preserves_invalid_business_code_issue(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "expected_environment": "partner_test",
            },
            executor=lambda _request: {
                "ok": False,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {}, "remaining": {}},
                "error": {
                    "category": "schema",
                    "message": "Partner response schema validation failed.",
                    "details": {
                        "schema_issue": "invalid_business_code_type",
                        "values_hidden": True,
                        "field_names": ["code"],
                        "unknown_field_count": 1,
                    },
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(
            result["error"]["details"]["schema_issue"],
            "invalid_business_code_type",
        )

    def test_verify_referrals_missing_uid_blocks_complete_result(self) -> None:
        partner = self.require_partner()

        def executor(_request):
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": [{"uid": "10001", "isRefferal": True}],
            }

        result = partner.execute_query(
            {
                "operation": "verify-referrals",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001", "10002"], "all_confirmed": False},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["error"]["category"], "completeness")
        self.assertEqual(result["error"]["code"], "uid_response_mismatch")

    def test_verify_referrals_duplicate_uid_blocks_complete_result(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "verify-referrals",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001", "10002"], "all_confirmed": False},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": [
                    {"uid": "10001", "isRefferal": True},
                    {"uid": "10001", "isRefferal": True},
                ],
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "uid_response_mismatch")

    def test_remaining_weight_stops_before_next_page_and_is_preserved(self) -> None:
        partner = self.require_partner()
        requested_pages = []

        def executor(request):
            page = request["query"]["page"]
            requested_pages.append(page)
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {"1M": 1200}, "remaining": {"1M": 0}},
                "data": {"records": [{"uid": "10001"}], "current": 1, "pages": 2, "pageSize": 100, "total": 2},
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(requested_pages, [1])
        self.assertFalse(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["error"]["category"], "rate_limit")
        self.assertEqual(result["rate_limit"]["remaining"]["1M"], 0)

    def test_page_sequence_mismatch_stops_without_repeating_the_page(self) -> None:
        partner = self.require_partner()
        requested_pages = []

        def executor(request):
            page = request["query"]["page"]
            requested_pages.append(page)
            current = 1 if page == 2 else page
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": [{"uid": str(current)}], "current": current, "pages": 2, "pageSize": 100, "total": 2},
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(requested_pages, [1, 2])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "pagination_sequence_mismatch")

    def test_duplicate_page_payload_blocks_complete_result(self) -> None:
        partner = self.require_partner()

        def executor(request):
            page = request["query"]["page"]
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": [{"uid": "10001"}], "current": page, "pages": 2, "pageSize": 100, "total": 2},
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "duplicate_page_payload")

    def test_complete_page_record_count_mismatch_is_fail_closed(self) -> None:
        partner = self.require_partner()

        def executor(_request):
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": [{"uid": "10001"}], "current": 1, "pages": 1, "total": 2},
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "pagination_record_count_mismatch")

    def test_partial_commission_does_not_publish_a_summary(self) -> None:
        partner = self.require_partner()

        def executor(_request):
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "channelCommissionInfoItems": [{"uid": "10001", "coin": "USDT", "commission": "1.25"}],
                    "current": 1,
                    "pages": 2,
                    "pageSize": 100,
                    "total": 2,
                },
            }

        result = partner.execute_query(
            {
                "operation": "get-commission",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001"], "all_confirmed": False},
                "time_range": None,
                "filters": {},
                "result_mode": "summary_with_first_20",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["complete"])
        self.assertTrue(result["partial"])
        self.assertIsNone(result["summary"])

    def test_direct_asset_aggregate_all_uses_decimal_totals(self) -> None:
        partner = self.require_partner()

        def executor(request):
            page = request["query"]["page"]
            row = {
                "uid": str(page),
                "depositAmount": "0.1" if page == 1 else "0.2",
                "withdrawalAmount": "0.01",
                "spotTradingAmount": "1.1",
                "futuresTradingAmount": "2.2",
                "commission": "0.001",
            }
            return {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": [row], "current": page, "pages": 2, "pageSize": 100, "total": 2},
            }

        result = partner.execute_query(
            {
                "operation": "get-direct-trade-asset",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "aggregate_all",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertTrue(result["complete"])
        self.assertEqual(
            result["summary"]["direct_trade_asset_totals"],
            {
                "depositAmount": "0.3",
                "withdrawalAmount": "0.02",
                "spotTradingAmount": "2.2",
                "futuresTradingAmount": "4.4",
                "commission": "0.002",
            },
        )

    def test_response_timestamps_are_normalized_to_utc(self) -> None:
        partner = self.require_partner()
        normalized = partner.project_known_fields(
            {"uid": "10001", "registerTime": 1750000000000, "firstTrade": 1750000001000},
            known_fields={"uid", "registerTime", "firstTrade"},
            timestamp_fields={"registerTime", "firstTrade"},
        )

        self.assertEqual(normalized["record"]["registerTime"], "2025-06-15 15:06:40 (UTC)")
        self.assertEqual(normalized["record"]["firstTrade"], "2025-06-15 15:06:41 (UTC)")

    def test_chinese_response_timestamps_use_full_width_utc_parentheses(self) -> None:
        partner = self.require_partner()
        try:
            normalized = partner.project_known_fields(
                {"uid": "10001", "registerTime": 1750000000000},
                known_fields={"uid", "registerTime"},
                timestamp_fields={"registerTime"},
                language="zh",
            )
        except TypeError as exc:
            self.fail(f"timestamp projection must accept the output language: {exc}")

        self.assertEqual(normalized["record"]["registerTime"], "2025-06-15 15:06:40（UTC）")

    def test_date_only_fields_preserve_day_granularity_and_localize_utc_parentheses(self) -> None:
        partner = self.require_partner()
        expected = {
            "zh": "2025-08-20（UTC）",
            "en": "2025-08-20 (UTC)",
        }
        for language, expected_date in expected.items():
            with self.subTest(language=language):
                try:
                    normalized = partner.project_known_fields(
                        {"subAffiliateUid": "10001", "date": "2025-08-20"},
                        known_fields={"subAffiliateUid", "date"},
                        date_fields={"date"},
                        language=language,
                    )
                except TypeError as exc:
                    self.fail(f"date-only projection must accept documented date fields: {exc}")
                self.assertEqual(normalized["record"]["date"], expected_date)

    def test_nested_unknown_values_are_hidden(self) -> None:
        partner = self.require_partner()
        normalized = partner.project_known_fields(
            {"availableBalance": "1", "depositList": [{"mystery": "secret-value"}]},
            known_fields={"availableBalance", "depositList"},
        )

        self.assertEqual(normalized["record"]["depositList"], {"count": 1, "values_hidden": True})
        self.assertIn("depositList.*", normalized["unknown_fields"])
        self.assertNotIn("secret-value", repr(normalized))

    def test_hidden_container_rejects_a_scalar_without_exposing_its_value(self) -> None:
        partner = self.require_partner()
        secret = "SECRET-CONTAINER-VALUE"
        result = partner.execute_query(
            {
                "operation": "get-referral-assets",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001"]},
                "time_range": {"start": "2026-07-01", "end": "2026-07-17"},
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "availableBalance": "1",
                    "fundingTotalUsdt": "2",
                    "spotProTotalUsdt": "3",
                    "unimarginTotalUsdt": "4",
                    "depositTotalAmount": "5",
                    "depositList": secret,
                },
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["category"], "schema")
        self.assertEqual(result["error"]["code"], "invalid_record_field_type")
        self.assertNotIn(secret, repr(result))

    def test_normalized_alias_is_not_accepted_as_an_upstream_wire_field(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "verify-referrals",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001"]},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": [{"uid": 10001, "is_referral": False}],
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["category"], "schema")
        self.assertEqual(result["error"]["code"], "response_schema_mismatch")

    def test_invalid_referral_flag_is_reported_as_schema_error_type(self) -> None:
        partner = self.require_partner()

        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.project_known_fields(
                {"uid": "10001", "isRefferal": {"unexpected": True}},
                known_fields={"uid", "isRefferal"},
            )

        self.assertEqual(exc_info.exception.code, "invalid_boolean")

    def test_trader_subprocess_failure_without_json_has_stable_error_code(self) -> None:
        partner = self.require_partner()
        completed = SimpleNamespace(returncode=1, stdout="", stderr="Traceback: hidden")

        with mock.patch.object(partner.subprocess, "run", return_value=completed):
            with self.assertRaises(partner.PartnerQueryError) as exc_info:
                partner.invoke_trader({"endpoint": "partner.verify-referrals"})

        self.assertEqual(exc_info.exception.code, "trader_process_failed")
        self.assertNotIn("Traceback", str(exc_info.exception))

    def test_local_validation_uses_the_standard_error_envelope(self) -> None:
        partner = self.require_partner()
        result = partner.build_local_error_envelope(
            operation="get-sub-agent-stats",
            code="time_range_required",
            message="Provide start and end.",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["operation"], "get-sub-agent-stats")
        self.assertEqual(result["api_domain"], "partner")
        self.assertIsNone(result["summary"])
        self.assertEqual(result["records"], [])
        self.assertFalse(result["continuation"]["can_continue"])
        self.assertIn("recovery_action", result["error"])
        self.assertIsNone(result["environment"])
        self.assertIsNone(result["pagination"]["has_more"])

    def test_successful_query_propagates_and_binds_test_environment(self) -> None:
        partner = self.require_partner()

        def executor(_request):
            return {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": "10001"}],
                    "current": 1,
                    "pages": 1,
                    "total": 1,
                },
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["environment"], "partner_test")
        self.assertEqual(result["continuation"]["environment"], "partner_test")

    def test_first_success_response_requires_a_known_environment(self) -> None:
        partner = self.require_partner()

        for environment in (None, "partner_unknown"):
            with self.subTest(environment=environment):
                def executor(_request):
                    response = {
                        "ok": True,
                        "environment": "partner_production",
                        "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                        "data": {
                            "records": [{"uid": "10001"}],
                            "current": 1,
                            "pages": 1,
                            "total": 1,
                        },
                    }
                    if environment is None:
                        response.pop("environment")
                    else:
                        response["environment"] = environment
                    return response

                result = partner.execute_query(
                    {
                        "operation": "list-referral-uids",
                        "profile": "main",
                        "scope": {"mode": "all", "all_confirmed": True},
                        "time_range": None,
                        "filters": {},
                        "result_mode": "complete_list",
                    },
                    executor=executor,
                    now=datetime(2026, 7, 15, tzinfo=timezone.utc),
                )

                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "invalid_partner_environment")
                self.assertEqual(result["records"], [])
                self.assertIsNone(result["environment"])

    def test_first_remote_error_requires_a_known_environment(self) -> None:
        partner = self.require_partner()

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=lambda _request: {
                "ok": False,
                "environment": None,
                "error": {"category": "rate_limit", "http_status": 429},
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_partner_environment")
        self.assertIsNone(result["environment"])

    def test_first_local_pre_rest_error_may_have_no_environment(self) -> None:
        partner = self.require_partner()

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=lambda _request: {
                "ok": False,
                "environment": None,
                "error": {
                    "category": "local_policy",
                    "code": "invalid_partner_origin",
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_partner_origin")
        self.assertIsNone(result["environment"])

    def test_continuation_preserves_local_pre_rest_error_without_environment(self) -> None:
        partner = self.require_partner()
        base_request = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }

        first = partner.execute_query(
            base_request,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": "10001"}],
                    "current": 1,
                    "pages": 2,
                    "pageSize": 100,
                    "total": 2,
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        second_request = dict(
            base_request,
            continuation=first["continuation"],
            page=2,
            records_seen=1,
        )
        second = partner.execute_query(
            second_request,
            executor=lambda _request: {
                "ok": False,
                "environment": None,
                "error": {
                    "category": "profile_vault",
                    "code": "vault_credentials_unavailable",
                },
            },
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(second["ok"])
        self.assertEqual(second["error"]["category"], "profile_vault")
        self.assertEqual(second["error"]["code"], "vault_credentials_unavailable")
        self.assertIsNone(second["environment"])

    def test_environment_change_fails_before_second_page_records_are_merged(self) -> None:
        partner = self.require_partner()

        def executor(request):
            page = request["query"]["page"]
            return {
                "ok": True,
                "environment": "partner_production" if page == 1 else "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": str(10000 + page)}],
                    "current": page,
                    "pages": 2,
                    "pageSize": 100,
                    "total": 2,
                },
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["records"], [{"uid": "10001"}])
        self.assertEqual(result["error"]["code"], "partner_environment_changed")
        self.assertEqual(result["environment"], "partner_production")
        self.assertTrue(result["continuation"]["restart_required"])
        self.assertEqual(result["continuation"]["restart_from_page"], 1)

    def test_continuation_binding_rejects_cross_environment_reuse(self) -> None:
        partner = self.require_partner()
        binding = partner._continuation_binding(
            {
                "query_scope": {"mode": "all", "all_confirmed": True},
                "operation": "list-referral-uids",
                "contract_version": partner.CONTRACT_VERSION,
                "filters": {},
                "result_mode": "summary_with_first_20",
                "time_range": None,
                "continuation": {},
            },
            {"resolved_profile_id": "profile-a"},
            environment="partner_test",
        )

        self.assertEqual(binding["environment"], "partner_test")
        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.validate_continuation(
                binding,
                dict(binding, environment="partner_production"),
            )
        self.assertEqual(exc_info.exception.code, "continuation_mismatch")

    def test_cross_environment_continuation_error_preserves_observed_environment(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        first = partner.execute_query(
            base,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_production",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": "10001"}],
                    "current": 1,
                    "pages": 2,
                    "pageSize": 100,
                    "total": 2,
                },
            },
            now=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        patch = first["continuation"]["actions"][0]["request_patch"]

        second = partner.execute_query(
            dict(base, continuation=first["continuation"], **patch),
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": "10002"}],
                    "current": 2,
                    "pages": 2,
                    "pageSize": 100,
                    "total": 2,
                },
            },
            now=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )

        self.assertFalse(second["ok"])
        self.assertEqual(second["error"]["code"], "continuation_environment_mismatch")
        self.assertEqual(second["environment"], "partner_test")
        self.assertEqual(second["records"], [])

    def test_explicit_multi_segment_range_is_incomplete_until_all_segments_are_covered(self) -> None:
        partner = self.require_partner()

        result = partner.execute_query(
            {
                "operation": "get-direct-trade-asset",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001"]},
                "time_range": {
                    "start": "2026-01-18T00:00:00Z",
                    "end": "2026-07-17T00:00:00Z",
                },
                "filters": {},
                "result_mode": "aggregate_all",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {}, "remaining": {}},
                "data": {
                    "records": [
                        {
                            "uid": "10001",
                            "depositAmount": "1",
                            "withdrawalAmount": "0",
                            "spotTradingAmount": "0",
                            "futuresTradingAmount": "0",
                            "commission": "0",
                        }
                    ],
                    "page": 1,
                    "pages": 1,
                    "pageSize": 100,
                    "total": 1,
                },
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertFalse(result["complete"])
        self.assertTrue(result["partial"])
        self.assertIsNone(result["summary"])
        self.assertTrue(result["continuation"]["can_continue"])
        self.assertEqual(result["time_range"]["actual_start"], "2026-04-18T00:00:00Z")

    def test_zero_page_empty_response_is_a_complete_empty_result(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "get-commission",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001"]},
                "time_range": {
                    "start": "2026-07-10T00:00:00Z",
                    "end": "2026-07-17T00:00:00Z",
                },
                "filters": {"product_type": "SPOT"},
                "result_mode": "aggregate_all",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "weight": 20,
                "rate_limit": {"used": {"10S": 20}, "remaining": {"10S": 480}},
                "data": {
                    "channelCommissionInfoItems": [],
                    "pageSize": 100,
                    "pages": 0,
                    "total": 0,
                },
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["complete"])
        self.assertFalse(result["partial"])
        self.assertEqual(result["records"], [])
        self.assertEqual(result["pagination"]["pages_total"], 0)
        self.assertEqual(result["summary"], {"record_count": 0, "commission_by_coin": {}})

    def test_paginated_operation_rejects_missing_total_and_pages_metadata(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": [{"uid": "10001"}],
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["category"], "schema")
        self.assertEqual(result["error"]["code"], "missing_pagination_metadata")

    def test_unknown_request_scope_and_time_fields_are_rejected(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "verify-referrals",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["10001"]},
            "time_range": None,
            "filters": {},
            "result_mode": "complete_list",
        }

        for request, code in (
            (dict(base, unexpected_top="ignored"), "invalid_request_fields"),
            (dict(base, scope=dict(base["scope"], unexpected_scope="ignored")), "invalid_scope"),
            (
                dict(
                    base,
                    operation="get-referral-assets",
                    time_range={"start": "2026-07-01", "end": "2026-07-17", "timezone": "UTC"},
                ),
                "invalid_time_range",
            ),
        ):
            with self.subTest(code=code):
                with self.assertRaises(partner.PartnerQueryError) as exc_info:
                    partner.plan_query(request, now=datetime(2026, 7, 17, tzinfo=timezone.utc))
                self.assertEqual(exc_info.exception.code, code)

    def test_success_response_requires_nonempty_resolved_profile_id(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "verify-referrals",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001"]},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"name": "main"},
                "data": [{"uid": "10001", "isRefferal": True}],
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["category"], "schema")
        self.assertEqual(result["error"]["code"], "invalid_resolved_profile")

    def test_test_environment_accepts_exclusive_deal_end_date(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "get-referral-deal-data",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001"]},
                "time_range": {"start": "2026-07-01", "end": "2026-07-17"},
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "data": [
                        {
                            "userId": 10001,
                            "spotDealAmountUsdt": "0",
                            "futuresProDealAmountUsdt": "0",
                            "spotProDealAmountUsdtTemp": "0",
                        }
                    ],
                    "startTime": "2026-07-01",
                    "endTime": "2026-07-18",
                },
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["complete"])

    def test_later_page_cannot_drop_a_known_page_size(self) -> None:
        partner = self.require_partner()

        def executor(request):
            page = request["query"]["page"]
            data = {
                "records": [{"uid": str(10000 + page)}],
                "current": page,
                "pages": 2,
                "total": 2,
            }
            if page == 1:
                data["pageSize"] = 100
            return {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": data,
            }

        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=executor,
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "pagination_page_size_missing")

    def test_continuation_rejects_tampered_time_state(self) -> None:
        partner = self.require_partner()
        first = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "channelUserInfoItemList": [{"uid": "10001"}],
                    "pageSize": 100,
                    "pages": 1,
                    "total": 1,
                },
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )
        continuation = dict(first["continuation"])
        continuation["time_state"] = dict(continuation["time_state"])
        continuation["time_state"]["remaining_start"] = "2025-01-01T00:00:00Z"
        action = continuation["actions"][0]["request_patch"]

        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(
                {
                    "operation": "list-referral-uids",
                    "profile": "main",
                    "scope": {"mode": "all", "all_confirmed": True},
                    "time_range": action["time_range"],
                    "filters": {},
                    "result_mode": "complete_list",
                    "page": action["page"],
                    "display_offset": action["display_offset"],
                    "records_seen": action["records_seen"],
                    "continuation": continuation,
                },
                now=datetime(2026, 7, 17, tzinfo=timezone.utc),
            )
        self.assertEqual(exc_info.exception.code, "invalid_continuation")

    def test_test_environment_sub_agent_query_uses_documented_null_time_contract(self) -> None:
        partner = self.require_partner()
        plan = partner.plan_query(
            {
                "operation": "get-sub-agent-stats",
                "profile": "main",
                "expected_environment": "partner_test",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {"product_type": "FUTURES"},
                "result_mode": "aggregate_all",
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )
        executor_request = partner.build_executor_requests(plan)[0]

        self.assertEqual(plan["expected_environment"], "partner_test")
        self.assertEqual(plan["time_range"]["source"], "partner_test_upstream_default")
        self.assertIsNone(executor_request["body"]["startTime"])
        self.assertIsNone(executor_request["body"]["endTime"])
        self.assertEqual(executor_request["body"]["productType"], "FUTURES")
        self.assertEqual(executor_request["expected_environment"], "partner_test")

    def test_null_sub_agent_time_is_not_allowed_without_verified_test_environment(self) -> None:
        partner = self.require_partner()
        for expected_environment in (None, "partner_production"):
            request = {
                "operation": "get-sub-agent-stats",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {"product_type": "FUTURES"},
                "result_mode": "aggregate_all",
            }
            if expected_environment is not None:
                request["expected_environment"] = expected_environment
            with self.subTest(expected_environment=expected_environment):
                with self.assertRaises(partner.PartnerQueryError) as exc_info:
                    partner.plan_query(request, now=datetime(2026, 7, 17, tzinfo=timezone.utc))
                self.assertEqual(exc_info.exception.code, "time_range_required")

    def test_falsey_non_object_scope_and_filters_are_rejected(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "verify-referrals",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["10001"]},
            "time_range": None,
            "filters": {},
            "result_mode": "complete_list",
        }

        for value in ([], "", False, 0):
            with self.subTest(field="scope", value=value):
                with self.assertRaises(partner.PartnerQueryError) as exc_info:
                    partner.plan_query(dict(base, scope=value))
                self.assertEqual(exc_info.exception.code, "invalid_scope")
            with self.subTest(field="filters", value=value):
                with self.assertRaises(partner.PartnerQueryError) as exc_info:
                    partner.plan_query(dict(base, filters=value))
                self.assertEqual(exc_info.exception.code, "invalid_filters")

    def test_unknown_null_filter_is_rejected_before_rest(self) -> None:
        partner = self.require_partner()
        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(
                {
                    "operation": "verify-referrals",
                    "profile": "main",
                    "scope": {"mode": "uids", "uids": ["10001"]},
                    "time_range": None,
                    "filters": {"unknown_filter": None},
                    "result_mode": "complete_list",
                }
            )
        self.assertEqual(exc_info.exception.code, "invalid_filters")

    def test_non_scalar_expected_environment_is_rejected_locally(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "verify-referrals",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["10001"]},
            "time_range": None,
            "filters": {},
            "result_mode": "complete_list",
        }
        for value in ([], {}):
            with self.subTest(value=value):
                with self.assertRaises(partner.PartnerQueryError) as exc_info:
                    partner.plan_query(dict(base, expected_environment=value))
                self.assertEqual(exc_info.exception.code, "invalid_partner_environment")

    def test_non_scalar_continuation_environment_is_rejected_stably(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "verify-referrals",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["10001"]},
            "time_range": None,
            "filters": {},
            "result_mode": "complete_list",
        }
        for value in ([], {}):
            with self.subTest(value=value):
                continuation = {
                    "resolved_profile_id": "profile-a",
                    "environment": value,
                    "expected_environment": None,
                    "query_scope": {"mode": "uids", "uids": ["10001"], "all_confirmed": False},
                    "operation": "verify-referrals",
                    "contract_version": partner.CONTRACT_VERSION,
                    "filters": {},
                    "result_mode": "complete_list",
                    "time_range": None,
                    "actions": [],
                }
                with self.assertRaises(partner.PartnerQueryError) as exc_info:
                    partner.plan_query(dict(base, continuation=continuation))
                self.assertEqual(exc_info.exception.code, "invalid_continuation")

    def test_multi_page_response_requires_page_size_on_first_page(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "list-referral-uids",
                "profile": "main",
                "scope": {"mode": "all", "all_confirmed": True},
                "time_range": None,
                "filters": {},
                "result_mode": "complete_list",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": "10001"}],
                    "current": 1,
                    "pages": 2,
                    "total": 2,
                },
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["category"], "schema")
        self.assertEqual(result["error"]["code"], "missing_pagination_page_size")

    def test_zero_page_current_zero_is_normalized_to_requested_page(self) -> None:
        partner = self.require_partner()
        result = partner.execute_query(
            {
                "operation": "get-commission",
                "profile": "main",
                "scope": {"mode": "uids", "uids": ["10001"]},
                "time_range": {"start": "2026-07-10T00:00:00Z", "end": "2026-07-17T00:00:00Z"},
                "filters": {"product_type": "SPOT"},
                "result_mode": "aggregate_all",
            },
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": [], "current": 0, "pages": 0, "pageSize": 100, "total": 0},
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["pagination"]["current_page"], 1)

    def test_final_time_segment_never_publishes_a_cross_segment_aggregate(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "get-direct-trade-asset",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["10001"]},
            "time_range": {"start": "2026-01-18T00:00:00Z", "end": "2026-07-17T00:00:00Z"},
            "filters": {},
            "result_mode": "aggregate_all",
        }

        def response(amount):
            return {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{
                        "uid": "10001", "depositAmount": str(amount), "withdrawalAmount": "0",
                        "spotTradingAmount": "0", "futuresTradingAmount": "0", "commission": "0",
                    }],
                    "page": 1, "pages": 1, "pageSize": 100, "total": 1,
                },
            }

        first = partner.execute_query(base, executor=lambda _request: response(1), now=datetime(2026, 7, 17, tzinfo=timezone.utc))
        patch = next(
            action["request_patch"]
            for action in first["continuation"]["actions"]
            if action["type"] == "earlier_time_range"
        )
        second = partner.execute_query(
            dict(base, continuation=first["continuation"], **patch),
            executor=lambda _request: response(2),
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertTrue(second["ok"])
        self.assertFalse(second["complete"])
        self.assertTrue(second["partial"])
        self.assertIsNone(second["summary"])
        self.assertIn(
            "cross_segment_aggregate_not_combined",
            {warning.get("code") for warning in second["warnings"]},
        )

    def test_display_continuation_on_later_server_page_preserves_page_coverage(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        first = partner.execute_query(
            base,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": [{"uid": "1"}], "current": 1, "pages": 2, "pageSize": 100, "total": 31},
            },
        )
        next_patch = first["continuation"]["actions"][0]["request_patch"]
        second = partner.execute_query(
            dict(base, continuation=first["continuation"], **next_patch),
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": str(index)} for index in range(2, 32)],
                    "current": 2,
                    "pages": 2,
                    "pageSize": 100,
                    "total": 31,
                },
            },
        )

        display_patch = second["continuation"]["actions"][0]["request_patch"]
        self.assertEqual(display_patch, {"page": 2, "records_seen": 1, "display_offset": 20})
        third_plan = partner.plan_query(dict(base, continuation=second["continuation"], **display_patch))
        self.assertEqual(third_plan["page"], 2)
        self.assertEqual(third_plan["records_seen"], 1)

    def test_aggregate_display_continuation_is_executable(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "get-direct-trade-asset",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": {"start": "2026-04-18T00:00:00Z", "end": "2026-07-17T00:00:00Z"},
            "filters": {},
            "result_mode": "aggregate_all",
        }
        rows = [
            {
                "uid": str(10000 + index), "depositAmount": "1", "withdrawalAmount": "0",
                "spotTradingAmount": "0", "futuresTradingAmount": "0", "commission": "0",
            }
            for index in range(30)
        ]
        def executor(request):
            page = request["query"]["page"]
            page_rows = rows[:15] if page == 1 else rows[15:]
            return {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": page_rows, "current": page, "pages": 2, "pageSize": 100, "total": 30},
            }

        first = partner.execute_query(base, executor=executor)
        patch = first["continuation"]["actions"][0]["request_patch"]
        self.assertEqual(patch, {"display_offset": 20})

        second = partner.execute_query(
            dict(base, continuation=first["continuation"], **patch),
            executor=executor,
        )

        self.assertTrue(second["ok"])
        self.assertTrue(second["complete"])
        self.assertEqual(second["pagination"]["display_offset"], 20)
        self.assertEqual(len(second["records"]), 10)

    def test_non_aggregate_cross_segment_warning_uses_generic_wording(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": {"start": "2026-01-18T00:00:00Z", "end": "2026-07-17T00:00:00Z"},
            "filters": {},
            "result_mode": "complete_list",
        }
        response = {
            "ok": True,
            "environment": "partner_test",
            "profile": {"resolved_profile_id": "profile-a", "name": "main"},
            "data": {"records": [{"uid": "10001"}], "current": 1, "pages": 1, "pageSize": 100, "total": 1},
        }
        first = partner.execute_query(base, executor=lambda _request: response)
        patch = next(action["request_patch"] for action in first["continuation"]["actions"] if action["type"] == "earlier_time_range")
        second = partner.execute_query(dict(base, continuation=first["continuation"], **patch), executor=lambda _request: response)

        codes = {warning.get("code") for warning in second["warnings"]}
        self.assertIn("cross_segment_results_not_combined", codes)
        self.assertNotIn("cross_segment_aggregate_not_combined", codes)

    def test_continuation_rejects_unknown_fields_and_coherent_time_origin_forgery(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": {"start": "2026-01-18T00:00:00Z", "end": "2026-07-17T00:00:00Z"},
            "filters": {},
            "result_mode": "complete_list",
        }
        first = partner.execute_query(
            base,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": [{"uid": "10001"}], "page": 1, "pages": 1, "pageSize": 100, "total": 1},
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )
        patch = next(action["request_patch"] for action in first["continuation"]["actions"] if action["type"] == "earlier_time_range")

        with self.assertRaises(partner.PartnerQueryError) as unknown_exc:
            partner.plan_query(dict(base, continuation=dict(first["continuation"], forged=True), **patch))
        self.assertEqual(unknown_exc.exception.code, "invalid_continuation")

        forged = copy.deepcopy(first["continuation"])
        forged["time_state"]["original_start"] = "2025-12-01T00:00:00Z"
        forged["time_state"]["remaining_start"] = "2025-12-01T00:00:00Z"
        with self.assertRaises(partner.PartnerQueryError) as forged_exc:
            partner.plan_query(dict(base, continuation=forged, **patch))
        self.assertEqual(forged_exc.exception.code, "invalid_continuation")

    def test_page_continuation_snapshot_binds_exact_next_page_and_seen_count(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        first = partner.execute_query(
            base,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {"records": [{"uid": "10001"}], "current": 1, "pages": 2, "pageSize": 100, "total": 2},
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )
        snapshot = first["continuation"]["source_pagination"]
        self.assertEqual(snapshot["current_page"], 1)
        self.assertEqual(snapshot["records_seen_before"], 0)
        self.assertEqual(snapshot["records_on_page"], 1)

        forged = copy.deepcopy(first["continuation"])
        forged["actions"][0]["request_patch"]["page"] = 3
        forged["actions"][0]["request_patch"]["records_seen"] = 2
        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(dict(base, continuation=forged, page=3, records_seen=2))
        self.assertEqual(exc_info.exception.code, "invalid_continuation")

    def test_executable_continuation_exposes_machine_readable_usage_warnings(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        result = partner.execute_query(
            base,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": "10001"}],
                    "current": 1,
                    "pages": 2,
                    "pageSize": 100,
                    "total": 2,
                },
            },
            now=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )

        self.assertEqual(
            result["continuation"].get("usage_warnings"),
            [
                "continuation_reuse_may_repeat_or_overwrite",
                "offset_pagination_data_may_change",
            ],
        )

    def test_executable_continuation_rejects_missing_required_usage_warning(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "list-referral-uids",
            "profile": "main",
            "scope": {"mode": "all", "all_confirmed": True},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        result = partner.execute_query(
            base,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{"uid": "10001"}],
                    "current": 1,
                    "pages": 2,
                    "pageSize": 100,
                    "total": 2,
                },
            },
            now=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        continuation = copy.deepcopy(result["continuation"])
        continuation["usage_warnings"] = [
            "continuation_reuse_may_repeat_or_overwrite",
        ]
        patch = continuation["actions"][0]["request_patch"]

        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(
                dict(base, continuation=continuation, **patch),
                now=datetime(2026, 7, 20, tzinfo=timezone.utc),
            )
        self.assertEqual(exc_info.exception.code, "invalid_continuation")

    def test_non_paginated_display_continuation_cannot_be_rewritten(self) -> None:
        partner = self.require_partner()
        uids = [str(10000 + index) for index in range(30)]
        base = {
            "operation": "verify-referrals",
            "profile": "main",
            "scope": {"mode": "uids", "uids": uids},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        first = partner.execute_query(
            base,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": [{"uid": uid_value, "isRefferal": True} for uid_value in uids],
            },
        )
        forged = copy.deepcopy(first["continuation"])
        forged["actions"][0]["request_patch"]["display_offset"] = 21

        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(dict(base, continuation=forged, display_offset=21))
        self.assertEqual(exc_info.exception.code, "invalid_continuation")

    def test_display_continuation_requires_its_source_snapshot(self) -> None:
        partner = self.require_partner()
        uids = [str(10000 + index) for index in range(30)]
        base = {
            "operation": "verify-referrals",
            "profile": "main",
            "scope": {"mode": "uids", "uids": uids},
            "time_range": None,
            "filters": {},
            "result_mode": "summary_with_first_20",
        }
        first = partner.execute_query(
            base,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": [{"uid": uid_value, "isRefferal": True} for uid_value in uids],
            },
        )
        forged = copy.deepcopy(first["continuation"])
        forged.pop("source_pagination")
        forged["actions"][0]["request_patch"]["display_offset"] = 21

        with self.assertRaises(partner.PartnerQueryError) as exc_info:
            partner.plan_query(dict(base, continuation=forged, display_offset=21))
        self.assertEqual(exc_info.exception.code, "invalid_continuation")

    def test_earlier_time_action_requires_all_time_state_fields(self) -> None:
        partner = self.require_partner()
        base = {
            "operation": "get-direct-trade-asset",
            "profile": "main",
            "scope": {"mode": "uids", "uids": ["10001"]},
            "time_range": {"start": "2026-01-18T00:00:00Z", "end": "2026-07-17T00:00:00Z"},
            "filters": {},
            "result_mode": "aggregate_all",
        }
        first = partner.execute_query(
            base,
            executor=lambda _request: {
                "ok": True,
                "environment": "partner_test",
                "profile": {"resolved_profile_id": "profile-a", "name": "main"},
                "data": {
                    "records": [{
                        "uid": "10001", "depositAmount": "1", "withdrawalAmount": "0",
                        "spotTradingAmount": "0", "futuresTradingAmount": "0", "commission": "0",
                    }],
                    "page": 1, "pages": 1, "pageSize": 100, "total": 1,
                },
            },
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )
        action_patch = next(
            action["request_patch"]
            for action in first["continuation"]["actions"]
            if action["type"] == "earlier_time_range"
        )
        for missing_field in ("time_state", "original_time_range", "next_end", "segment_days"):
            forged = copy.deepcopy(first["continuation"])
            forged.pop(missing_field)
            with self.subTest(missing_field=missing_field):
                with self.assertRaises(partner.PartnerQueryError) as exc_info:
                    partner.plan_query(dict(base, continuation=forged, **action_patch))
                self.assertEqual(exc_info.exception.code, "invalid_continuation")


if __name__ == "__main__":
    unittest.main()
