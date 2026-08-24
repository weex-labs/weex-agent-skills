#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "scripts" / "generate_weex_api_definitions.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_weex_api_definitions_contract_test", GENERATOR_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load API definition generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_definitions(product: str) -> tuple[dict, dict[str, dict]]:
    path = ROOT / "references" / f"{product}-api-definitions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, {item["key"]: item for item in payload["definitions"]}


def params_by_name(definition: dict, section: str) -> dict[str, dict]:
    return {row["name"]: row for row in definition[section]}


class GeneratorRegressionTests(unittest.TestCase):
    def test_fetch_text_forces_utf8_before_reading_response_text(self) -> None:
        generator = load_generator()

        class FakeResponse:
            encoding = None

            def raise_for_status(self) -> None:
                return None

            @property
            def text(self) -> str:
                return "new / hot / >="

        response = FakeResponse()
        with mock.patch.object(generator.requests, "get", return_value=response):
            self.assertEqual(generator.fetch_text("https://example.invalid"), "new / hot / >=")

        self.assertEqual(response.encoding, "utf-8")

    def test_parameter_type_header_does_not_overwrite_parameter_name(self) -> None:
        generator = load_generator()
        soup = BeautifulSoup(
            """
            <div><table>
              <tr><th>Parameter</th><th>Parameter Type</th><th>Required</th><th>Description</th></tr>
              <tr><td>uid</td><td>Long</td><td>No</td><td>Invited user UID</td></tr>
            </table></div>
            """,
            "html.parser",
        )

        rows = generator.extract_table_rows(soup.div)

        self.assertEqual(rows, [{"name": "uid", "type": "Long", "required": "No", "description": "Invited user UID"}])

    def test_narrative_only_response_is_preserved_as_root_response(self) -> None:
        generator = load_generator()
        soup = BeautifulSoup(
            """
            <div>
              <p><strong>Response</strong></p>
              <p>Returns the transfer ID as a string when the request succeeds.</p>
              <p><strong>Response example</strong></p>
            </div>
            """,
            "html.parser",
        )

        request_params, response_params, constraints = generator._extract_sections(soup.div)

        self.assertEqual(request_params, [])
        self.assertEqual(constraints, [])
        self.assertEqual(
            response_params,
            [
                {
                    "name": "$",
                    "type": "String",
                    "description": "Returns the transfer ID as a string when the request succeeds.",
                }
            ],
        )

    def test_parse_doc_extracts_delete_body_transport_and_array_container(self) -> None:
        generator = load_generator()
        html = """
        <article>
          <div class="theme-doc-markdown markdown">
            <h1>Cancel Orders Batch (TRADE)</h1>
            <p>DELETE /capi/v3/batchOrders</p>
            <p>Request parameters</p>
            <table>
              <tr><th>Parameter</th><th>Type</th><th>Required</th><th>Description</th></tr>
              <tr><td>orderIdList</td><td>Array&lt;Long&gt;</td><td>No</td><td>Order IDs</td></tr>
            </table>
            <p>Request example</p>
            <pre>curl -X DELETE "https://api-contract.weex.com/capi/v3/batchOrders" -H "Content-Type: application/json" -d '{"orderIdList":[1]}'</pre>
            <p>Response parameters</p>
            <p>Returns an array of cancelled orders.</p>
            <p>Response example</p>
            <pre>[{"orderId":1}]</pre>
          </div>
        </article>
        """

        with mock.patch.object(generator, "fetch_text", return_value=html):
            parsed = generator.parse_doc(
                "https://www.weex.com/api-doc/contract/Transaction_API/CancelOrdersBatch"
            )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.request_transport, "body")
        self.assertEqual(parsed.query_fields, [])
        self.assertEqual(parsed.body_fields, ["orderIdList"])
        self.assertEqual(parsed.response_container, "array")

    def test_parse_doc_marks_documented_object_or_array_response_as_conditional(self) -> None:
        generator = load_generator()
        html = """
        <article>
          <div class="theme-doc-markdown markdown">
            <h1>Get Book Ticker</h1>
            <p>GET /api/v3/ticker/bookTicker</p>
            <p>Request parameters</p>
            <table>
              <tr><th>Parameter</th><th>Type</th><th>Required</th><th>Description</th></tr>
              <tr><td>symbol</td><td>String</td><td>No</td><td>Trading pair</td></tr>
            </table>
            <p>Request example</p>
            <pre>curl "https://api-spot.weex.com/api/v3/ticker/bookTicker?symbol=BTCUSDT"</pre>
            <p>Response parameters</p>
            <p>If symbol is supplied the response is a single object; otherwise, it is an array.</p>
            <p>Response example</p>
            <pre>[{"symbol":"BTCUSDT"}]</pre>
          </div>
        </article>
        """

        with mock.patch.object(generator, "fetch_text", return_value=html):
            parsed = generator.parse_doc(
                "https://www.weex.com/api-doc/spot/MarketDataAPI/GetBookTicker"
            )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.request_transport, "query")
        self.assertEqual(parsed.query_fields, ["symbol"])
        self.assertEqual(parsed.body_fields, [])
        self.assertEqual(parsed.response_container, "conditional_object_or_array")

    def test_url_collection_includes_tax_current_partner_rebate_and_demo_pages(self) -> None:
        generator = load_generator()
        tax = "https://www.weex.com/api-doc/spot/tax/GetSpotAccountRecord"
        rebate = "https://www.weex.com/api-doc/partner/rebate-endpoints/GetAffiliateCommission"
        removed = "https://www.weex.com/api-doc/partner/rebate-endpoints/GetInternalWithdrawalStatus"
        demo = "https://www.weex.com/api-doc/contract/demo/PlaceOrder"
        urls = [tax, rebate, removed, demo]

        spot_urls = generator.iter_doc_urls("spot", urls)
        contract_urls = generator.iter_doc_urls("contract", urls)

        self.assertIn(tax, spot_urls)
        self.assertIn(rebate, spot_urls)
        self.assertNotIn(removed, spot_urls)
        self.assertIn(demo, contract_urls)


class CheckedInDefinitionRegressionTests(unittest.TestCase):
    def test_all_definitions_publish_machine_readable_transport_and_response_container(self) -> None:
        expected_delete_body = {
            "transaction.cancel_orders_batch",
            "spot.order.bulk_cancel",
        }
        conditional = {
            "spot.market.get_all_ticker_info",
            "spot.market.get_book_ticker",
            "spot.market.get_ticker_info",
        }
        for product in ("contract", "spot"):
            payload, _ = load_definitions(product)
            for definition in payload["definitions"]:
                with self.subTest(product=product, key=definition["key"]):
                    self.assertIn(definition["request_transport"], {"query", "body"})
                    request_names = [item["name"] for item in definition["request_params"]]
                    self.assertEqual(
                        definition["query_fields"] + definition["body_fields"],
                        request_names,
                    )
                    if definition["key"] in expected_delete_body:
                        self.assertEqual(definition["request_transport"], "body")
                    self.assertIn(
                        definition["response_container"],
                        {"object", "array", "conditional_object_or_array", "string"},
                    )
                    if definition["key"] in conditional:
                        self.assertEqual(
                            definition["response_container"],
                            "conditional_object_or_array",
                        )
    def test_spot_definitions_match_current_official_contract(self) -> None:
        payload, by_key = load_definitions("spot")
        self.assertEqual(len(payload["definitions"]), 33)

        account = by_key["spot.account.get_account_balance"]
        self.assertEqual(account["path"], "/api/v3/account")

        klines = by_key["spot.market.get_k_line_data"]
        interval = params_by_name(klines, "request_params")["interval"]["description"]
        self.assertNotIn("1M", interval)
        self.assertEqual(set(params_by_name(klines, "response_params")), {str(index) for index in range(11)})

        exchange = by_key["spot.config.get_product_info"]
        status = params_by_name(exchange, "request_params")["symbolStatus"]["description"]
        for expected in ("TRADING", "HALT", "BREAK"):
            self.assertIn(expected, status)
        exchange_response = params_by_name(exchange, "response_params")
        for expected in (
            "rateLimits",
            "rateLimits[].interval",
            "rateLimits[].intervalNum",
            "rateLimits[].limit",
            "rateLimits[].rateLimitType",
            "symbols[].status",
        ):
            self.assertIn(expected, exchange_response)

        tax = by_key["spot.tax.get_spot_account_record"]
        self.assertEqual((tax["method"], tax["path"]), ("POST", "/api/v3/tax/income"))
        self.assertEqual(set(params_by_name(tax, "request_params")), {"coin", "bizType", "month", "limit", "page"})
        self.assertEqual(
            set(params_by_name(tax, "response_params")),
            {"billId", "coinId", "coinName", "bizType", "fillSize", "fillValue", "deltaAmount", "afterAmount", "fees", "cTime"},
        )

        rebate = by_key["spot.rebate.get_affiliate_commission"]
        self.assertIn("/partner/rebate-endpoints/", rebate["doc_url"])
        self.assertEqual(
            set(params_by_name(rebate, "request_params")),
            {"uid", "startTime", "endTime", "coin", "productType", "page", "pageSize"},
        )

        ping_response = params_by_name(by_key["spot.config.ping"], "response_params")["$"]
        self.assertEqual(ping_response["type"], "Object")
        self.assertIn("empty JSON object", ping_response["description"])

        withdrawal_response = params_by_name(
            by_key["spot.rebate.internal_withdrawal"],
            "response_params",
        )["$"]
        self.assertEqual(withdrawal_response["type"], "String")
        self.assertIn("transfer ID", withdrawal_response["description"])

        api_symbols = by_key["spot.config.get_api_trading_symbols"]
        self.assertEqual(api_symbols["response_container"], "array")
        self.assertEqual(
            params_by_name(api_symbols, "response_params"),
            {
                "$": {
                    "name": "$",
                    "type": "Array<String>",
                    "description": "Raw response is an array of spot symbols available for API trading.",
                }
            },
        )

    def test_contract_definitions_match_current_official_contract(self) -> None:
        payload, by_key = load_definitions("contract")
        self.assertEqual(len(payload["definitions"]), 47)

        bills = by_key["account.get_contract_bills"]
        self.assertTrue({"nextKeyId", "nextKeyTime"}.issubset(params_by_name(bills, "request_params")))
        self.assertTrue({"nextKey", "nextKey.nextKeyId", "nextKey.nextKeyTime"}.issubset(params_by_name(bills, "response_params")))

        place_order = by_key["transaction.place_order"]
        self.assertIn("POST_ONLY", params_by_name(place_order, "request_params")["timeInForce"]["description"])
        batch = by_key["transaction.place_orders_batch"]
        self.assertIn("5", params_by_name(batch, "request_params")["batchOrders"]["description"])

        tp_sl = by_key["transaction.place_tp_sl_order"]
        quantity = params_by_name(tp_sl, "request_params")["quantity"]
        self.assertEqual(quantity["required"], "No")
        self.assertIn("full position", quantity["description"].lower())

        close_positions = by_key["transaction.close_positions"]
        self.assertIn("positionId", params_by_name(close_positions, "request_params"))
        self.assertTrue(any("positionId" in constraint and "priority" in constraint.lower() for constraint in close_positions["constraints"]))

        exchange = params_by_name(by_key["market.get_contract_info"], "response_params")
        for expected in (
            "rateLimits",
            "rateLimits[].interval",
            "symbols[].displaySymbol",
            "symbols[].baseAssetPrecision",
            "symbols[].quotePrecision",
            "symbols[].delivery",
            "symbols[].forwardContractFlag",
        ):
            self.assertIn(expected, exchange)

        self.assertEqual(
            set(params_by_name(by_key["market.get_klines"], "response_params")),
            {f"index[{index}]" for index in range(11)},
        )

        api_symbols = by_key["market.get_api_trading_symbols"]
        self.assertEqual(api_symbols["response_container"], "array")
        self.assertEqual(
            params_by_name(api_symbols, "response_params"),
            {
                "$": {
                    "name": "$",
                    "type": "Array<String>",
                    "description": "Raw response is an array of futures symbols available for API trading.",
                }
            },
        )

    def test_cross_page_response_references_are_expanded(self) -> None:
        _, by_key = load_definitions("contract")
        expected_sources = {
            "account.get_single_position": "account.get_all_positions",
            "market.get_history_klines": "market.get_klines",
            "market.get_index_price_klines": "market.get_klines",
            "market.get_mark_price_klines": "market.get_klines",
            "transaction.cancel_orders_batch": "transaction.cancel_order",
            "transaction.cancel_pending_order": "transaction.cancel_order",
            "transaction.get_current_order_status": "transaction.get_single_order_info",
            "transaction.get_order_history": "transaction.get_single_order_info",
            "transaction.place_orders_batch": "transaction.place_order",
            "transaction.place_pending_order": "transaction.place_order",
            "sim.transaction.get_order_history": "transaction.get_single_order_info",
        }
        for key, source_key in expected_sources.items():
            with self.subTest(key=key):
                self.assertEqual(by_key[key]["response_params"], by_key[source_key]["response_params"])
                self.assertNotIn("$", params_by_name(by_key[key], "response_params"))

        _, spot_by_key = load_definitions("spot")
        self.assertEqual(
            spot_by_key["spot.order.history_orders"]["response_params"],
            spot_by_key["spot.order.order_details"]["response_params"],
        )
        self.assertNotIn(
            "$",
            params_by_name(spot_by_key["spot.order.history_orders"], "response_params"),
        )

    def test_narrative_constraints_and_current_rate_limit_model_are_preserved(self) -> None:
        _, by_key = load_definitions("contract")
        self.assertTrue(any("At least one" in item for item in by_key["account.update_leverage_trade"].get("constraints", [])))
        self.assertTrue(any("7 days" in item for item in by_key["market.get_funding_rate_history"].get("constraints", [])))
        trade_constraints = " ".join(by_key["transaction.get_trade_details"].get("constraints", []))
        self.assertIn("7 days", trade_constraints)
        self.assertIn("365 days", trade_constraints)
        execute_price = params_by_name(by_key["transaction.modify_tp_sl_order"], "request_params")["executePrice"]["description"]
        self.assertIn("Copy-trading API keys", execute_price)

        expected_order_limits = {
            "X-ORDER-COUNT-10S": 1,
            "X-ORDER-COUNT-1M": 1,
            "X-USED-WEIGHT-1M": 0,
        }
        actual_order_limits = {
            item["header"]: item["limit"]
            for item in by_key["transaction.place_order"]["rate_limits"]
        }
        self.assertEqual(actual_order_limits, expected_order_limits)
        self.assertEqual(by_key["transaction.cancel_order"]["weight_ip"], 1)
        self.assertEqual(by_key["transaction.cancel_pending_order"]["weight_ip"], 1)

        spot_payload, spot_by_key = load_definitions("spot")
        self.assertEqual(spot_by_key["spot.order.cancel_symbol_orders"]["weight_ip"], 5)
        for definition in [*spot_payload["definitions"], *load_definitions("contract")[0]["definitions"]]:
            self.assertNotIn("weight_uid", definition)

    def test_demo_definitions_are_generated_from_current_pages_with_chinese_contract_override(self) -> None:
        _, by_key = load_definitions("contract")
        demo = by_key["sim.transaction.place_order"]
        self.assertTrue(demo["doc_url"].endswith("/contract/demo/PlaceOrder"))
        self.assertIn("POST_ONLY", params_by_name(demo, "request_params")["timeInForce"]["description"])
        self.assertEqual(
            {item["header"]: item["limit"] for item in demo["rate_limits"]},
            {"X-ORDER-COUNT-10S": 1, "X-ORDER-COUNT-1M": 1, "X-USED-WEIGHT-1M": 0},
        )

    def test_generated_definitions_do_not_contain_mojibake(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "references" / "spot-api-definitions.json",
                ROOT / "references" / "spot-api-definitions.md",
                ROOT / "references" / "contract-api-definitions.json",
                ROOT / "references" / "contract-api-definitions.md",
            )
        )
        for marker in ("â", "â", "â"):
            self.assertNotIn(marker, combined)


if __name__ == "__main__":
    unittest.main()
