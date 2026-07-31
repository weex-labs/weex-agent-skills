#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
SKILL = ROOT / "SKILL.md"
MANIFEST = ROOT / "manifest.json"
FILE_INDEX = ROOT / "file-index.json"
QUERY_POLICY = ROOT / "references" / "partner-query-policy.md"
OUTPUT_SCHEMA = ROOT / "references" / "partner-output-schema.md"
NATURAL_LANGUAGE_REGRESSION = ROOT / "references" / "natural-language-regression.json"
FIELD_CATALOG = ROOT / "references" / "partner-field-catalog.json"
TRADER_PARTNER_DEFINITIONS = (
    ROOT.parent / "weex-trader-skill" / "references" / "partner-api-definitions.json"
)
CLI = ROOT / "scripts" / "weex_partner_cli.py"
TESTS = ROOT / "tests"

EXPECTED_COMMANDS = {
    "list-referral-uids",
    "get-direct-trade-asset",
    "get-commission",
    "get-sub-agent-stats",
    "verify-referrals",
    "get-referral-assets",
    "get-referral-deal-data",
}


class PartnerDocsConsistencyTests(unittest.TestCase):
    def test_internal_withdrawal_status_is_removed_from_partner_contracts(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        query_policy = QUERY_POLICY.read_text(encoding="utf-8")
        fixtures = json.loads(NATURAL_LANGUAGE_REGRESSION.read_text(encoding="utf-8"))
        catalog = json.loads(FIELD_CATALOG.read_text(encoding="utf-8"))

        self.assertNotIn("get-internal-withdrawals", skill_text)
        self.assertNotIn("get-internal-withdrawals", manifest["routing"]["operations"])
        self.assertNotIn("get-internal-withdrawals", query_policy)
        self.assertNotIn("get-internal-withdrawals", catalog["operations"])
        self.assertFalse(any(
            scenario["expected"].get("operation") == "get-internal-withdrawals"
            for scenario in fixtures["scenarios"]
        ))
        self.assertNotIn("partner.internal-withdrawal", skill_text)

    def test_skill_and_indexes_require_the_official_field_catalog_for_descriptions(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        schema_text = OUTPUT_SCHEMA.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))

        self.assertIn("references/partner-field-catalog.json", skill_text)
        self.assertIn("official_description_zh", skill_text)
        self.assertIn("official_description_en", skill_text)
        self.assertIn("original field name", skill_text)
        self.assertIn("unimarginTotalUsdt", skill_text)
        self.assertIn("Total contract account equity in USDT", skill_text)
        self.assertIn("contractTotalUsdt", skill_text)
        self.assertIn("currently undocumented", skill_text)
        self.assertIn("partner-field-catalog.json", schema_text)
        self.assertIn("official_description_zh", schema_text)
        self.assertIn("official_description_en", schema_text)
        self.assertNotIn("localized official label", skill_text)
        self.assertIn(
            "field_catalog",
            manifest["read_order"]["open_if_needed"],
        )
        self.assertEqual(
            manifest["read_order"]["open_if_needed"]["field_catalog"],
            "references/partner-field-catalog.json",
        )
        self.assertIn(
            "references/partner-field-catalog.json",
            file_index["file_guide"],
        )

    def test_official_field_catalog_covers_all_operations_and_wire_request_fields(self) -> None:
        self.assertTrue(FIELD_CATALOG.exists(), "official Partner field catalog is missing")
        catalog = json.loads(FIELD_CATALOG.read_text(encoding="utf-8"))
        trader = json.loads(TRADER_PARTNER_DEFINITIONS.read_text(encoding="utf-8"))

        self.assertEqual(catalog["schema_version"], 2)
        self.assertEqual(catalog["last_verified_utc"], "2026-07-28")
        self.assertEqual(set(catalog["operations"]), EXPECTED_COMMANDS)
        trader_by_key = {item["key"]: item for item in trader["definitions"]}

        for operation, definition in catalog["operations"].items():
            with self.subTest(operation=operation):
                endpoint = definition["endpoint"]
                self.assertIn(endpoint, trader_by_key)
                trader_definition = trader_by_key[endpoint]
                request_by_name = {
                    item["wire_name"]: item for item in definition["request_fields"]
                }
                self.assertEqual(
                    {name for name, item in request_by_name.items() if item["transport"] == "query"},
                    set(trader_definition["query_fields"]),
                )
                self.assertEqual(
                    {name for name, item in request_by_name.items() if item["transport"] == "body"},
                    set(trader_definition["body_fields"]),
                )
                self.assertEqual(definition["doc_url"], trader_definition["doc_url"])
                self.assertTrue(str(definition["official_name_zh"]).strip())
                self.assertEqual(
                    len(request_by_name), len(definition["request_fields"]),
                    "request wire names must be unique",
                )

    def test_official_field_catalog_descriptions_and_aliases_match_the_documented_contract(self) -> None:
        self.assertTrue(FIELD_CATALOG.exists(), "official Partner field catalog is missing")
        catalog = json.loads(FIELD_CATALOG.read_text(encoding="utf-8"))
        operations = catalog["operations"]

        assets = {
            item["wire_name"]: item
            for item in operations["get-referral-assets"]["response_fields"]
        }
        self.assertIn("official_description_en", assets["unimarginTotalUsdt"])
        self.assertEqual(
            assets["unimarginTotalUsdt"]["official_description_zh"],
            "合约账户权益（USDT）",
        )
        self.assertEqual(
            assets["unimarginTotalUsdt"]["official_description_en"],
            "Total contract account equity in USDT",
        )
        self.assertNotIn("统一账户", assets["unimarginTotalUsdt"]["official_description_zh"])
        self.assertNotIn("contractTotalUsdt", assets)
        self.assertEqual(assets["depositList"]["format"], "hidden_container")

        referrals = {
            item["wire_name"]: item
            for item in operations["verify-referrals"]["response_fields"]
        }
        self.assertEqual(referrals["isRefferal"]["output_name"], "is_referral")
        self.assertEqual(referrals["isRefferal"]["alias_kind"], "normalized_output")
        self.assertIn("official_description_en", referrals["isRefferal"])
        self.assertEqual(
            referrals["isRefferal"]["official_description_en"],
            "true if the UID belongs to the current affiliate",
        )

        for operation, definition in operations.items():
            with self.subTest(operation=operation, section="operation"):
                self.assertTrue(str(definition["official_name_zh"]).strip())
                self.assertTrue(str(definition["official_name_en"]).strip())
                self.assertRegex(
                    definition["doc_url_en"],
                    r"^https://www\.weex\.com/api-doc/partner/rebate-endpoints/",
                )
            for section in ("request_fields", "response_fields"):
                for field in definition[section]:
                    with self.subTest(
                        operation=operation,
                        section=section,
                        field=field["wire_name"],
                    ):
                        self.assertTrue(str(field["official_description_zh"]).strip())
                        self.assertTrue(str(field["official_description_en"]).strip())
                        self.assertNotIn("label_zh", field)
                        self.assertNotIn("label_en", field)

    def test_official_bilingual_titles_and_descriptions_preserve_verbatim_examples(self) -> None:
        catalog = json.loads(FIELD_CATALOG.read_text(encoding="utf-8"))
        operations = catalog["operations"]

        self.assertTrue(
            all("official_name_en" in definition for definition in operations.values()),
            "every operation must preserve the official English title",
        )
        self.assertTrue(
            all("doc_url_en" in definition for definition in operations.values()),
            "every operation must preserve the official English URL",
        )

        self.assertEqual(operations["list-referral-uids"]["official_name_en"], "Get Affiliate UIDs")
        self.assertEqual(
            operations["get-direct-trade-asset"]["official_name_en"],
            "Get Affiliate Referral Data",
        )
        self.assertEqual(
            operations["get-sub-agent-stats"]["official_name_en"],
            "Get Subaffiliates Data (affiliate only)",
        )

        direct_fields = {
            item["wire_name"]: item
            for item in operations["get-direct-trade-asset"]["response_fields"]
        }
        self.assertEqual(direct_fields["depositAmount"]["official_description_en"], "Deposit Amount")

        asset_requests = {
            item["wire_name"]: item
            for item in operations["get-referral-assets"]["request_fields"]
        }
        self.assertEqual(asset_requests["userId"]["official_description_en"], "Direct customer UID")

        deal_fields = {
            item["wire_name"]: item
            for item in operations["get-referral-deal-data"]["response_fields"]
        }
        self.assertEqual(
            deal_fields["spotProDealAmountUsdtTemp"]["official_description_en"],
            "Spot trading volume (raw value returned by partner system)",
        )
    def test_official_bilingual_contract_matches_the_verified_full_snapshot(self) -> None:
        catalog = json.loads(FIELD_CATALOG.read_text(encoding="utf-8"))
        operations = catalog["operations"]
        self.assertEqual(
            sum(len(definition["request_fields"]) for definition in operations.values()),
            30,
        )
        self.assertEqual(
            sum(len(definition["response_fields"]) for definition in operations.values()),
            63,
        )

        snapshot = {}
        for operation, definition in operations.items():
            normalized = {
                key: definition[key]
                for key in ("official_name_zh", "official_name_en", "doc_url", "doc_url_en")
            }
            for section in ("request_fields", "response_fields"):
                fields = []
                for field in sorted(definition[section], key=lambda item: item["wire_name"]):
                    official = {
                        key: field[key]
                        for key in ("wire_name", "type")
                    }
                    if section == "request_fields":
                        official["official_required"] = field["official_required"]
                    official["official_description_zh"] = field["official_description_zh"]
                    official["official_description_en"] = field["official_description_en"]
                    fields.append(official)
                normalized[section] = fields
            snapshot[operation] = normalized

        serialized = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(serialized).hexdigest(),
            "8ddc20ce6cbbfe5cebfbcc49e3f447238dfaeb992e6237a1eb7d1b444d4d57d9",
        )

    def test_natural_language_regression_fixture_is_executable_and_read_only(self) -> None:
        self.assertTrue(
            NATURAL_LANGUAGE_REGRESSION.exists(),
            "machine-readable natural-language regression fixture is missing",
        )
        payload = json.loads(NATURAL_LANGUAGE_REGRESSION.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        scenarios = payload["scenarios"]
        self.assertGreaterEqual(len(scenarios), 15)

        scenario_ids = [item["id"] for item in scenarios]
        self.assertEqual(len(scenario_ids), len(set(scenario_ids)))
        self.assertTrue(all(str(item["prompt"]).strip() for item in scenarios))

        routed = {
            item["expected"]["operation"]
            for item in scenarios
            if item["expected"]["disposition"] == "route"
        }
        self.assertEqual(routed, EXPECTED_COMMANDS)
        routed_by_operation = {
            item["expected"]["operation"]: item
            for item in scenarios
            if item["expected"]["disposition"] == "route"
        }
        required_context = {
            "list-referral-uids": {"saved_profile", "scope_mode"},
            "get-direct-trade-asset": {"saved_profile", "scope_mode"},
            "get-commission": {"saved_profile", "uid"},
            "get-sub-agent-stats": {"saved_profile", "product_type"},
            "verify-referrals": {"saved_profile", "uid"},
            "get-referral-assets": {"saved_profile", "uid", "time_range"},
            "get-referral-deal-data": {"saved_profile", "scope_mode", "time_range"},
        }
        for operation, fields in required_context.items():
            with self.subTest(operation=operation):
                context = routed_by_operation[operation].get("context", {})
                self.assertTrue(fields.issubset(context))
                if context.get("scope_mode") == "all":
                    self.assertIs(context.get("all_confirmed"), True)
        sub_agent_context = routed_by_operation["get-sub-agent-stats"].get("context", {})
        self.assertTrue(
            "time_range" in sub_agent_context
            or sub_agent_context.get("partner_environment") == "partner_test"
        )

        clarify_by_id = {
            item["id"]: item
            for item in scenarios
            if item["expected"]["disposition"] == "clarify"
        }
        clarify_contracts = {
            "clarify_ambiguous_trade_stats": (
                {"saved_profile", "uid", "time_range"},
                ["intent_detail"],
            ),
            "clarify_missing_uid": (
                {"saved_profile"},
                ["uid_or_explicit_all_scope"],
            ),
            "clarify_sub_agent_product": (
                {"saved_profile", "uid", "time_range"},
                ["product_type"],
            ),
        }
        self.assertEqual(set(clarify_by_id), set(clarify_contracts))
        for scenario_id, (present_fields, missing_fields) in clarify_contracts.items():
            with self.subTest(scenario_id=scenario_id):
                scenario = clarify_by_id[scenario_id]
                context = scenario.get("context", {})
                self.assertTrue(present_fields.issubset(context))
                self.assertEqual(scenario["expected"]["missing_fields"], missing_fields)
                self.assertTrue(set(missing_fields).isdisjoint(context))

        rejected_ids = {
            item["id"]
            for item in scenarios
            if item["expected"]["disposition"] == "reject"
        }
        self.assertTrue(
            {
                "reject_internal_transfer",
                "reject_uid_contact_match",
                "reject_cross_partner",
                "reject_fund_transfer",
                "reject_withdrawal",
            }.issubset(rejected_ids)
        )
        self.assertIn("delegate_order_to_trader", scenario_ids)
        delegated_order = next(
            item for item in scenarios if item["id"] == "delegate_order_to_trader"
        )
        self.assertEqual(delegated_order["expected"]["disposition"], "delegate")
        self.assertEqual(
            delegated_order["expected"]["target_skill"],
            "weex-trader-skill",
        )
        self.assertTrue(delegated_order["expected"]["preserve_confirmation_gate"])
        self.assertFalse(delegated_order["expected"]["partner_rest_request_sent"])
        for item in scenarios:
            expected = item["expected"]
            self.assertIn(
                expected["disposition"],
                {"route", "clarify", "reject", "delegate"},
            )
            if expected["disposition"] == "route":
                self.assertIn(expected["operation"], EXPECTED_COMMANDS)
            else:
                self.assertNotIn("operation", expected)
                self.assertFalse(expected["partner_rest_request_sent"])

        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))
        self.assertIn(
            "references/natural-language-regression.json",
            file_index["file_guide"],
        )

    def test_partner_environment_and_origin_policy_are_consistent(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))
        origin_policy = manifest["routing"]["origin_policy"]

        self.assertEqual(origin_policy["production_default"], "https://api-spot.weex.com")
        self.assertEqual(origin_policy["saved_profile_test_pattern"], "https://*.weex.tech")
        self.assertEqual(origin_policy["environment_overrides"], "forbidden")
        self.assertEqual(origin_policy["authenticated_redirects"], "forbidden")
        self.assertIn(
            "environment-bound continuation",
            file_index["file_guide"]["scripts/weex_partner_cli.py"]["role"],
        )
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (SKILL, QUERY_POLICY, OUTPUT_SCHEMA)
        )
        self.assertIn("`partner_production`", combined)
        self.assertIn("`partner_test`", combined)
        self.assertIn("environment", combined.lower())
        self.assertIn("continuation", combined.lower())

    def test_partner_docs_do_not_publish_a_concrete_test_origin(self) -> None:
        paths = (
            SKILL,
            MANIFEST,
            FILE_INDEX,
            QUERY_POLICY,
            OUTPUT_SCHEMA,
            REPO_ROOT / "README.md",
            REPO_ROOT / "README.zh-CN.md",
            REPO_ROOT / ".cursor" / "rules" / "weex-safety.mdc",
        )
        concrete_test_origin = re.compile(
            r"https://(?!\*\.)(?:[a-z0-9-]+\.)+weex\.tech",
            flags=re.IGNORECASE,
        )
        offenders = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in paths
            if concrete_test_origin.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [])

    def test_required_skill_files_exist(self) -> None:
        for path in (SKILL, MANIFEST, FILE_INDEX, QUERY_POLICY, OUTPUT_SCHEMA, CLI):
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertTrue(path.exists(), f"missing required Partner skill file: {path}")

    def test_skill_frontmatter_and_manifest_identity_match(self) -> None:
        self.assertTrue(SKILL.exists(), "Partner SKILL.md is not implemented yet; expected T1 RED.")
        self.assertTrue(MANIFEST.exists(), "Partner manifest is not implemented yet; expected T1 RED.")
        skill_text = SKILL.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        name_match = re.search(r"^name:\s*(\S+)\s*$", skill_text, flags=re.MULTILINE)
        description_match = re.search(r"^description:\s*(.+)\s*$", skill_text, flags=re.MULTILINE)

        self.assertIsNotNone(name_match)
        self.assertEqual(name_match.group(1), "weex-partner-skill")
        self.assertIsNotNone(description_match)
        self.assertTrue(description_match.group(1).startswith("Use when "))
        self.assertEqual(manifest["identity"]["name"], "weex-partner-skill")

    def test_skill_routes_rest_profile_and_vault_to_trader(self) -> None:
        self.assertTrue(SKILL.exists(), "Partner SKILL.md is not implemented yet; expected T1 RED.")
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn("weex-trader-skill", text)
        self.assertIn("profile", text.lower())
        self.assertIn("Vault", text)
        self.assertIn("scripts/weex_partner_cli.py", text)
        self.assertIn("read-only", text.lower())
        self.assertNotIn("Partner account type", text)

    def test_skill_uses_safe_preflight_and_reuses_explicit_profile_across_followups(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn(
            "weex_partner_api.py preflight --profile <saved-profile> --language <zh|en> --pretty",
            text,
        )
        self.assertIn("same conversation", text)
        self.assertIn("reuse that profile", text)
        self.assertIn("Do not ask for it again", text)
        self.assertIn("changes it or the reference is ambiguous", text)
        self.assertIn("current conversation", text)
        self.assertIn("general trader preflight payload", text)

    def test_skill_uses_trader_numbered_profile_choice_when_account_is_ambiguous(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn("trader's numbered profile-choice rule", text)
        self.assertIn("number or exact profile name", text)
        self.assertIn("standalone account-selection question", text)
        self.assertIn("Do not choose a profile on the user's behalf", text)

    def test_skill_routes_normal_orders_to_existing_trader_confirmation_gate(self) -> None:
        self.assertTrue(SKILL.exists(), "Partner SKILL.md is not implemented yet; expected T1 RED.")
        text = SKILL.read_text(encoding="utf-8").lower()

        self.assertIn("order", text)
        self.assertIn("weex-trader-skill", text)
        self.assertIn("confirm", text)
        self.assertIn("--confirm-live", text)

    def test_query_policy_documents_all_commands_and_minimum_time_rule(self) -> None:
        self.assertTrue(
            QUERY_POLICY.exists(),
            "Partner query policy is not implemented yet; expected T1 RED.",
        )
        text = QUERY_POLICY.read_text(encoding="utf-8")

        for command in EXPECTED_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, text)
        self.assertIn("official minimum", text.lower())
        self.assertIn("30", text)
        self.assertIn("90", text)
        self.assertIn("365", text)
        self.assertIn("UTC", text)

    def test_output_schema_requires_partial_and_pagination_disclosure(self) -> None:
        self.assertTrue(
            OUTPUT_SCHEMA.exists(),
            "Partner output schema is not implemented yet; expected T1 RED.",
        )
        text = OUTPUT_SCHEMA.read_text(encoding="utf-8")

        for field in (
            "complete",
            "partial",
            "has_more",
            "remaining_count",
            "next_page",
            "continuation",
            "actual_start",
            "actual_end",
        ):
            with self.subTest(field=field):
                self.assertIn(field, text)

    def test_output_schema_keeps_first_page_failure_coverage_unknown(self) -> None:
        schema = OUTPUT_SCHEMA.read_text(encoding="utf-8")
        policy = QUERY_POLICY.read_text(encoding="utf-8")

        self.assertIn("`has_more=null`", schema)
        self.assertIn("first page", schema.lower())
        self.assertIn("unknown", schema.lower())
        self.assertIn("`has_more=null`", policy)

    def test_skill_documents_fail_closed_interpretation_and_host_retention(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn("remaining weight", text.lower())
        self.assertIn("continuation", text.lower())
        self.assertIn("false", text)
        self.assertIn("UTC", text)
        self.assertIn("transcript", text.lower())

    def test_skill_documents_forward_test_and_test_environment_compatibility_rules(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (SKILL, QUERY_POLICY, OUTPUT_SCHEMA)
        ).lower()

        self.assertIn("repeated continuation", combined)
        self.assertIn("yyyy-mm-dd (utc)", combined)
        self.assertIn("data may change", combined)
        self.assertIn("expected_environment", combined)
        self.assertIn("partner_test_upstream_default", combined)

    def test_skill_documents_use_english_for_operations_and_excluded_queries(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        expected_mappings = {
            "list-referral-uids": "referral UID list",
            "get-direct-trade-asset": "direct-user trading and funding statistics",
            "get-commission": "commission records",
            "get-sub-agent-stats": "sub-agent volume, fees, and commission",
            "verify-referrals": "direct-referral relationship verification",
            "get-referral-assets": "direct-user asset snapshots",
            "get-referral-deal-data": "direct-user deal statistics",
        }
        for operation, intent in expected_mappings.items():
            with self.subTest(operation=operation):
                self.assertIn(operation, text)
                self.assertIn(intent, text)
        self.assertIn("UID-to-phone/email account identity checks are unsupported", text)
        self.assertIn("Cross-Partner queries are unsupported", text)

    def test_query_policy_contains_natural_language_regression_matrix(self) -> None:
        text = QUERY_POLICY.read_text(encoding="utf-8")
        expected_rows = {
            "route_referral_uids": "`list-referral-uids`",
            "route_direct_trade_asset": "`get-direct-trade-asset`",
            "route_commission": "`get-commission`",
            "route_sub_agent_stats": "`get-sub-agent-stats`",
            "route_verify_referral": "`verify-referrals`",
            "route_referral_assets": "`get-referral-assets`",
            "route_referral_deal_data": "`get-referral-deal-data`",
            "reject_internal_transfer": "`partner_write_unsupported`",
            "reject_uid_contact_match": "`account_identity_check_unsupported`",
            "reject_cross_partner": "`cross_partner_query_unsupported`",
        }
        for fixture_id, outcome in expected_rows.items():
            with self.subTest(fixture_id=fixture_id):
                self.assertRegex(
                    text,
                    rf"\|\s*`?{re.escape(fixture_id)}`?\s*\|\s*{re.escape(outcome)}\s*\|",
                )
        self.assertIn("Reuse a profile selected in an earlier turn", text)
        self.assertIn("An all-referrals scope does not imply a complete list", text)

    def test_partner_markdown_instructions_are_english_only(self) -> None:
        for path in (SKILL, QUERY_POLICY, OUTPUT_SCHEMA):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"[\u4e00-\u9fff]", text))

    def test_skill_uses_full_width_utc_parentheses_for_chinese_output(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        schema = OUTPUT_SCHEMA.read_text(encoding="utf-8")

        self.assertIn("YYYY-MM-DD HH:mm:ss（UTC）", text)
        self.assertIn("YYYY-MM-DD（UTC）", text)
        self.assertIn("Chinese output", text)
        self.assertIn("English output", text)
        self.assertIn("YYYY-MM-DD HH:mm:ss（UTC）", schema)
        self.assertIn("YYYY-MM-DD（UTC）", schema)
        self.assertIn("Chinese output", schema)
        self.assertIn("English output", schema)
        self.assertIsNone(re.search(r"[\u4e00-\u9fff]", schema))

    def test_output_schema_requires_machine_readable_continuation_usage_warnings(self) -> None:
        text = OUTPUT_SCHEMA.read_text(encoding="utf-8")

        self.assertIn("usage_warnings", text)
        self.assertIn("continuation_reuse_may_repeat_or_overwrite", text)
        self.assertIn("offset_pagination_data_may_change", text)

    def test_docs_define_commission_boundary_and_required_warning_contracts(self) -> None:
        policy = QUERY_POLICY.read_text(encoding="utf-8").lower()
        schema = OUTPUT_SCHEMA.read_text(encoding="utf-8").lower()

        self.assertIn("one millisecond before", policy)
        self.assertIn("closed millisecond", policy)
        self.assertIn("must exactly match", schema)
        self.assertIn("date-only", schema)

    def test_docs_do_not_require_a_fixed_partner_environment_prefix(self) -> None:
        for path in (SKILL, OUTPUT_SCHEMA):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8").lower()

                self.assertNotIn("first user-visible line must state", text)
                self.assertIn("not a standalone fixed prefix", text)

    def test_docs_explain_unresolved_environment_with_the_error_details(self) -> None:
        for path in (SKILL, OUTPUT_SCHEMA):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8").lower()

                self.assertIn("environment was not resolved", text)
                self.assertIn("no partner request was sent", text)
                self.assertIn("with the error details", text)

    def test_skill_requires_every_warning_to_be_explained(self) -> None:
        text = SKILL.read_text(encoding="utf-8").lower()

        self.assertIn("explain every returned warning", text)
        self.assertIn("cross_segment_aggregate_not_combined", text)
        self.assertIn("cross_segment_results_not_combined", text)

    def test_skill_limits_empty_result_interpretation_to_the_query_range(self) -> None:
        text = SKILL.read_text(encoding="utf-8").lower()

        self.assertIn("current query range returned no records", text)
        self.assertIn("never traded", text)
        self.assertIn("never deposited", text)
        self.assertIn("never transferred", text)

    def test_all_scope_does_not_imply_complete_list_or_aggregate_mode(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        policy = QUERY_POLICY.read_text(encoding="utf-8")

        self.assertIn("all-referrals scope does not imply", skill.lower())
        self.assertIn("`complete_list`", skill)
        self.assertIn("`aggregate_all`", skill)
        self.assertIn("natural-language-regression.json", skill)
        self.assertIn("server page", policy.lower())

    def test_five_host_and_openclaw_installation_docs_are_explicit(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        cursor_rule = (REPO_ROOT / ".cursor" / "rules" / "weex-safety.mdc").read_text(encoding="utf-8")
        installer = (REPO_ROOT / "tools" / "install_local_skills.py").read_text(encoding="utf-8")

        for host in ("Codex", "Claude Code", "Cursor", "GitHub Copilot", "OpenClaw"):
            with self.subTest(host=host):
                self.assertIn(host, readme)
        self.assertIn("openclaw skills install", readme)
        self.assertIn("weex-partner-skill", cursor_rule)
        self.assertNotIn("use --dir for an Openclaw", installer)

    def test_output_schema_defines_safe_schema_issue_diagnostics(self) -> None:
        text = OUTPUT_SCHEMA.read_text(encoding="utf-8")

        self.assertIn("`error.details.schema_issue`", text)
        self.assertIn("`non_json_response`", text)
        self.assertIn("`invalid_business_code_type`", text)
        self.assertIn("must not contain response values", text)

    def test_file_index_accounts_for_every_portable_skill_file(self) -> None:
        self.assertTrue(FILE_INDEX.exists(), "Partner file index is not implemented yet; expected T1 RED.")
        index_text = FILE_INDEX.read_text(encoding="utf-8")
        indexed_paths = set(re.findall(r"(?:scripts|references|tests)/[A-Za-z0-9_./-]+", index_text))
        actual_paths = {
            path.relative_to(ROOT).as_posix()
            for family in (ROOT / "scripts", ROOT / "references", TESTS)
            if family.exists()
            for path in family.iterdir()
            if path.is_file() and path.name != "__init__.py"
        }
        self.assertEqual(indexed_paths, actual_paths)


if __name__ == "__main__":
    unittest.main()
