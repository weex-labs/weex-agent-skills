#!/usr/bin/env python3
"""Regenerate local WEEX REST API definitions from the live V3 docs."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag


ROOT = Path(__file__).resolve().parent.parent
REFS = ROOT / "references"
SITEMAP_URL = "https://www.weex.com/api-doc/sitemap.xml"
DOC_TIMEOUT = 20
MAX_WORKERS = 12

CONTRACT_GROUP_MAP = {
    "Market_API": "market",
    "Account_API": "account",
    "Transaction_API": "transaction",
}

SPOT_GROUP_MAP = {
    "ConfigAPI": "config",
    "MarketDataAPI": "market",
    "AccountAPI": "account",
    "orderApi": "order",
    "rebate-endpoints": "rebate",
}

KEY_OVERRIDES = {
    ("spot", "GetAllProductInfo"): "spot.config.get_api_trading_symbols",
}


@dataclass
class ParsedDoc:
    product: str
    key: str
    title: str
    category: str
    method: str
    path: str
    doc_url: str
    requires_auth: bool
    weight_ip: Optional[int]
    weight_uid: Optional[int]
    request_params: List[Dict[str, str]]
    response_params: List[Dict[str, str]]
    permission: Optional[str] = None


CONTRACT_DEMO_SOURCE = "https://www.weex.com/api-doc/zh-CN/contract/intro"

CONTRACT_DEMO_BALANCE_FIELDS = [
    {"name": "asset", "type": "String", "description": "Asset name."},
    {"name": "balance", "type": "String", "description": "Total balance."},
    {"name": "availableBalance", "type": "String", "description": "Available balance."},
    {"name": "frozen", "type": "String", "description": "Frozen amount."},
    {"name": "unrealizePnl", "type": "String", "description": "Unrealized profit and loss."},
]

CONTRACT_DEMO_ORDER_REQUEST = [
    {"name": "symbol", "type": "String", "required": "Yes", "description": "Trading pair, for example BTCSUSDT."},
    {"name": "side", "type": "String", "required": "Yes", "description": "Order side. Supported values: BUY, SELL."},
    {"name": "positionSide", "type": "String", "required": "Yes", "description": "Position side. Supported values: LONG, SHORT."},
    {"name": "type", "type": "String", "required": "Yes", "description": "Order type. Demo order supports LIMIT and MARKET."},
    {"name": "timeInForce", "type": "String", "required": "Conditional", "description": "Required when type = LIMIT. Supported values: GTC, IOC, FOK, POST_ONLY."},
    {"name": "quantity", "type": "String", "required": "Yes", "description": "Order quantity. Must be greater than 0."},
    {"name": "price", "type": "String", "required": "Conditional", "description": "Limit price. Required when type = LIMIT."},
    {"name": "newClientOrderId", "type": "String", "required": "Yes", "description": "Client-defined order ID, 1-36 chars matching ^[.A-Z:/a-z0-9_-]{1,36}$."},
    {"name": "tpTriggerPrice", "type": "String", "required": "No", "description": "Optional take-profit trigger price."},
    {"name": "slTriggerPrice", "type": "String", "required": "No", "description": "Optional stop-loss trigger price."},
    {"name": "TpWorkingType", "type": "String", "required": "No", "description": "Take-profit trigger price source. Preserve this official field casing."},
    {"name": "SlWorkingType", "type": "String", "required": "No", "description": "Stop-loss trigger price source. Preserve this official field casing."},
]

CONTRACT_DEMO_ORDER_RESPONSE = [
    {"name": "orderId", "type": "String", "description": "Order ID assigned by the system."},
    {"name": "clientOrderId", "type": "String", "description": "Echo of newClientOrderId."},
    {"name": "success", "type": "Boolean", "description": "Whether the order request was accepted."},
    {"name": "errorCode", "type": "String", "description": "Error code when success = false; otherwise empty."},
    {"name": "errorMessage", "type": "String", "description": "Error message when success = false; otherwise empty."},
]

CONTRACT_DEMO_POSITION_FIELDS = [
    {"name": "id", "type": "Long", "description": "Position ID."},
    {"name": "asset", "type": "String", "description": "Associated collateral asset."},
    {"name": "symbol", "type": "String", "description": "Trading pair."},
    {"name": "side", "type": "String", "description": "Position direction such as LONG or SHORT."},
    {"name": "marginType", "type": "String", "description": "Margin mode: CROSSED or ISOLATED."},
    {"name": "separatedMode", "type": "String", "description": "Position separation mode: COMBINED or SEPARATED."},
    {"name": "separatedOpenOrderId", "type": "Long", "description": "Separated-position open order ID."},
    {"name": "leverage", "type": "String", "description": "Position leverage."},
    {"name": "size", "type": "String", "description": "Current position size."},
    {"name": "openValue", "type": "String", "description": "Open position value."},
    {"name": "openFee", "type": "String", "description": "Open fee."},
    {"name": "fundingFee", "type": "String", "description": "Funding fee."},
    {"name": "marginSize", "type": "String", "description": "Margin amount in the collateral asset."},
    {"name": "isolatedMargin", "type": "String", "description": "Isolated margin amount."},
    {"name": "isAutoAppendIsolatedMargin", "type": "Boolean", "description": "Whether automatic isolated-margin append is enabled."},
    {"name": "cumOpenSize", "type": "String", "description": "Cumulative open size."},
    {"name": "cumOpenValue", "type": "String", "description": "Cumulative open value."},
    {"name": "cumOpenFee", "type": "String", "description": "Cumulative open fee."},
    {"name": "cumCloseSize", "type": "String", "description": "Cumulative close size."},
    {"name": "cumCloseValue", "type": "String", "description": "Cumulative close value."},
    {"name": "cumCloseFee", "type": "String", "description": "Cumulative close fee."},
    {"name": "cumFundingFee", "type": "String", "description": "Cumulative settled funding fee."},
    {"name": "cumLiquidateFee", "type": "String", "description": "Cumulative liquidation fee."},
    {"name": "createdMatchSequenceId", "type": "Long", "description": "Match engine sequence ID at creation."},
    {"name": "updatedMatchSequenceId", "type": "Long", "description": "Latest match engine sequence ID."},
    {"name": "createdTime", "type": "Long", "description": "Creation time in Unix milliseconds."},
    {"name": "updatedTime", "type": "Long", "description": "Update time in Unix milliseconds."},
    {"name": "unrealizePnl", "type": "String", "description": "Unrealized profit and loss."},
    {"name": "liquidatePrice", "type": "String", "description": "Estimated liquidation price; 0 means no current liquidation risk."},
]

CONTRACT_DEMO_HISTORY_REQUEST = [
    {
        "name": "symbol",
        "type": "String",
        "required": "No",
        "description": (
            "Optional trading pair filter. Omit by default for demo history because normal "
            "contract symbols such as BTCUSDT may be rejected unless the API accepts the "
            "exact simulated symbol filter."
        ),
    },
    {"name": "limit", "type": "Integer", "required": "No", "description": "Number of records per page, 1-1000. Default 500."},
    {"name": "startTime", "type": "Long", "required": "No", "description": "Start time in Unix milliseconds. Must be less than or equal to endTime."},
    {"name": "endTime", "type": "Long", "required": "No", "description": "End time in Unix milliseconds. Must be within 90 days of startTime."},
    {"name": "page", "type": "Integer", "required": "No", "description": "Page index starting from 0. Default 0."},
]

CONTRACT_DEMO_HISTORY_RESPONSE = [
    {"name": "avgPrice", "type": "String", "description": "Average fill price."},
    {"name": "clientOrderId", "type": "String", "description": "Client-defined order ID."},
    {"name": "cumQuote", "type": "String", "description": "Cumulative filled amount in the quote asset."},
    {"name": "executedQty", "type": "String", "description": "Filled quantity in the base asset."},
    {"name": "orderId", "type": "Long", "description": "System order ID."},
    {"name": "origQty", "type": "String", "description": "Original order quantity."},
    {"name": "price", "type": "String", "description": "Order price."},
    {"name": "reduceOnly", "type": "Boolean", "description": "Whether the order is reduce-only."},
    {"name": "side", "type": "String", "description": "Order side."},
    {"name": "positionSide", "type": "String", "description": "Position side."},
    {"name": "status", "type": "String", "description": "Order status."},
    {"name": "stopPrice", "type": "String", "description": "Trigger or stop price when applicable."},
    {"name": "symbol", "type": "String", "description": "Trading pair."},
    {"name": "time", "type": "Long", "description": "Order time in Unix milliseconds."},
    {"name": "timeInForce", "type": "String", "description": "Time-in-force policy."},
    {"name": "type", "type": "String", "description": "Order type."},
    {"name": "updateTime", "type": "Long", "description": "Last update time in Unix milliseconds."},
    {"name": "workingType", "type": "String", "description": "Trigger price source."},
]

CONTRACT_DEMO_ENDPOINTS = (
    {
        "key": "sim.account.get_account_balance",
        "title": "Get Demo Account Balance (USER_DATA)",
        "method": "GET",
        "path": "/capi/v3/sim/balance",
        "permission": "USER_DATA",
        "weight_ip": 5,
        "weight_uid": 10,
        "response_params": CONTRACT_DEMO_BALANCE_FIELDS,
    },
    {
        "key": "sim.account.get_all_positions",
        "title": "Get Demo All Positions (USER_DATA)",
        "method": "GET",
        "path": "/capi/v3/sim/position/allPosition",
        "permission": "USER_DATA",
        "weight_ip": 10,
        "weight_uid": 15,
        "response_params": CONTRACT_DEMO_POSITION_FIELDS,
    },
    {
        "key": "sim.transaction.get_order_history",
        "title": "Get Demo Order History (USER_DATA)",
        "method": "GET",
        "path": "/capi/v3/sim/order/history",
        "permission": "USER_DATA",
        "weight_ip": 10,
        "weight_uid": 10,
        "request_params": CONTRACT_DEMO_HISTORY_REQUEST,
        "response_params": CONTRACT_DEMO_HISTORY_RESPONSE,
    },
    {
        "key": "sim.transaction.place_order",
        "title": "Place Demo Order (TRADE)",
        "method": "POST",
        "path": "/capi/v3/sim/order",
        "permission": "TRADE",
        "weight_ip": 2,
        "weight_uid": 5,
        "request_params": CONTRACT_DEMO_ORDER_REQUEST,
        "response_params": CONTRACT_DEMO_ORDER_RESPONSE,
    },
)


def build_contract_demo_docs() -> List[ParsedDoc]:
    return [
        ParsedDoc(
            product="contract",
            key=definition["key"],
            title=definition["title"],
            category="sim",
            method=definition["method"],
            path=definition["path"],
            doc_url=CONTRACT_DEMO_SOURCE,
            requires_auth=True,
            weight_ip=definition["weight_ip"],
            weight_uid=definition["weight_uid"],
            request_params=definition.get("request_params", []),
            response_params=definition["response_params"],
            permission=definition["permission"],
        )
        for definition in CONTRACT_DEMO_ENDPOINTS
    ]


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=DOC_TIMEOUT)
    response.raise_for_status()
    return response.text


def load_sitemap_urls() -> List[str]:
    xml_text = fetch_text(SITEMAP_URL)
    root = ET.fromstring(xml_text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [node.text for node in root.findall("sm:url/sm:loc", ns) if node.text]
    return urls


def slugify(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text.strip())
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text or "unnamed"


def clean_text(text: str) -> str:
    text = " ".join(text.split())
    text = text.replace("â", "->")
    text = text.replace("→", "->")
    return text


def parse_weight(text: str) -> tuple[Optional[int], Optional[int]]:
    ip = None
    uid = None
    ip_match = re.search(r"Weight\(IP\):\s*(\d+)", text)
    uid_match = re.search(r"Weight\(UID\):\s*(\d+)", text)
    if ip_match:
        ip = int(ip_match.group(1))
    if uid_match:
        uid = int(uid_match.group(1))
    return ip, uid


def get_group(product: str, path_parts: List[str]) -> Optional[str]:
    group_segment = path_parts[2] if len(path_parts) > 2 else ""
    if product == "contract":
        return CONTRACT_GROUP_MAP.get(group_segment)
    return SPOT_GROUP_MAP.get(group_segment)


def extract_table_rows(container: Tag) -> List[Dict[str, str]]:
    table = container.find("table")
    if table is None:
        return []
    rows = table.find_all("tr")
    if not rows:
        return []
    headers = [
        clean_text(cell.get_text(" ", strip=True))
        for cell in rows[0].find_all(["th", "td"])
    ]
    results: List[Dict[str, str]] = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        item: Dict[str, str] = {}
        for idx, header in enumerate(headers):
            key = header.lower().replace("?", "")
            value = values[idx] if idx < len(values) else ""
            if key.startswith("parameter") or key.startswith("name") or key.startswith("field"):
                item["name"] = value
            elif key.startswith("type"):
                item["type"] = value
            elif key.startswith("required"):
                item["required"] = value
            elif key.startswith("description"):
                item["description"] = value
        if item:
            results.append(item)
    return results


def parse_doc(url: str) -> Optional[ParsedDoc]:
    html = fetch_text(url)
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    markdown = soup.select_one("article .theme-doc-markdown.markdown")
    if article is None or markdown is None:
        return None

    lines = [line for line in article.get_text("\n", strip=True).splitlines() if line.strip()]
    method = None
    path = None
    for idx, line in enumerate(lines):
        if line in {"GET", "POST", "PUT", "DELETE"} and idx + 1 < len(lines):
            candidate = lines[idx + 1].strip()
            if candidate.startswith("/"):
                method = line
                path = candidate
                break
    if method is None or path is None:
        return None

    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 4 or path_parts[0] != "api-doc":
        return None
    product = path_parts[1]
    if product not in {"contract", "spot"}:
        return None
    if "V2" in path_parts or path_parts[1] == "zh-CN":
        return None

    category = get_group(product, path_parts)
    if category is None:
        return None

    title_node = markdown.find("header")
    title = clean_text(title_node.get_text(" ", strip=True)) if title_node else path_parts[-1]

    override_key = KEY_OVERRIDES.get((product, path_parts[-1]))
    if override_key:
        key = override_key
    else:
        key = f"{category}.{slugify(path_parts[-1])}"
        if product == "spot":
            key = f"spot.{key}"

    weight_text = clean_text(markdown.get_text(" ", strip=True))
    weight_ip, weight_uid = parse_weight(weight_text)
    requires_auth = "ACCESS-KEY" in clean_text(article.get_text(" ", strip=True))

    wraps = markdown.select(":scope > .api-content-wrap")
    request_params = extract_table_rows(wraps[0]) if len(wraps) >= 1 else []
    response_params = extract_table_rows(wraps[1]) if len(wraps) >= 2 else []

    return ParsedDoc(
        product=product,
        key=key,
        title=title,
        category=category,
        method=method,
        path=path,
        doc_url=url,
        requires_auth=requires_auth,
        weight_ip=weight_ip,
        weight_uid=weight_uid,
        request_params=request_params,
        response_params=response_params,
    )


def iter_doc_urls(product: str, sitemap_urls: Iterable[str]) -> List[str]:
    prefix = f"https://www.weex.com/api-doc/{product}/"
    urls = []
    for url in sitemap_urls:
        if not url.startswith(prefix):
            continue
        if "/V2/" in url or "/zh-CN/" in url:
            continue
        urls.append(url)
    return sorted(set(urls))


def collect_docs(product: str, urls: List[str]) -> List[ParsedDoc]:
    docs: List[ParsedDoc] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {executor.submit(parse_doc, url): url for url in urls}
        for future in as_completed(future_map):
            doc = future.result()
            if doc is not None:
                docs.append(doc)
    docs.sort(key=lambda item: (item.category, item.key))
    return docs


def find_doc(docs: List[ParsedDoc], key: str) -> Optional[ParsedDoc]:
    for doc in docs:
        if doc.key == key:
            return doc
    return None


def apply_known_overrides(product: str, docs: List[ParsedDoc]) -> None:
    if product == "spot":
        api_symbols = find_doc(docs, "spot.config.get_api_trading_symbols")
        if api_symbols is not None and not api_symbols.response_params:
            api_symbols.response_params = [
                {
                    "name": "symbols[]",
                    "type": "Array<String>",
                    "description": "Raw response is an array of spot symbols available for API trading.",
                }
            ]

        history_orders = find_doc(docs, "spot.order.history_orders")
        order_details = find_doc(docs, "spot.order.order_details")
        if history_orders is not None and order_details is not None and not history_orders.response_params:
            history_orders.response_params = [dict(row) for row in order_details.response_params]

    if product == "contract":
        api_symbols = find_doc(docs, "market.get_api_trading_symbols")
        if api_symbols is not None and not api_symbols.response_params:
            api_symbols.response_params = [
                {
                    "name": "symbols[]",
                    "type": "Array<String>",
                    "description": "Raw response is an array of futures symbols available for API trading.",
                }
            ]

        contract_info = find_doc(docs, "market.get_contract_info")
        if contract_info is not None and len(contract_info.response_params) <= 2:
            contract_info.response_params = [
                {
                    "name": "assets[]",
                    "type": "Array<Object>",
                    "description": "Collateral assets list.",
                },
                {
                    "name": "assets[].asset",
                    "type": "String",
                    "description": "Collateral asset symbol.",
                },
                {
                    "name": "assets[].marginAvailable",
                    "type": "Boolean",
                    "description": "Whether the asset can be used as margin.",
                },
                {
                    "name": "symbols[]",
                    "type": "Array<Object>",
                    "description": "Contract symbol configuration list.",
                },
                {
                    "name": "symbols[].symbol",
                    "type": "String",
                    "description": "Contract trading pair symbol.",
                },
                {
                    "name": "symbols[].baseAsset",
                    "type": "String",
                    "description": "Base asset symbol.",
                },
                {
                    "name": "symbols[].quoteAsset",
                    "type": "String",
                    "description": "Quote asset symbol.",
                },
                {
                    "name": "symbols[].marginAsset",
                    "type": "String",
                    "description": "Margin asset symbol.",
                },
                {
                    "name": "symbols[].pricePrecision",
                    "type": "Integer",
                    "description": "Price precision.",
                },
                {
                    "name": "symbols[].quantityPrecision",
                    "type": "Integer",
                    "description": "Quantity precision.",
                },
                {
                    "name": "symbols[].contractVal",
                    "type": "Number",
                    "description": "Contract value.",
                },
                {
                    "name": "symbols[].minLeverage",
                    "type": "Integer",
                    "description": "Minimum leverage.",
                },
                {
                    "name": "symbols[].maxLeverage",
                    "type": "Integer",
                    "description": "Maximum leverage.",
                },
                {
                    "name": "symbols[].buyLimitPriceRatio",
                    "type": "Number",
                    "description": "Maximum allowed buy-side limit price deviation ratio.",
                },
                {
                    "name": "symbols[].sellLimitPriceRatio",
                    "type": "Number",
                    "description": "Maximum allowed sell-side limit price deviation ratio.",
                },
                {
                    "name": "symbols[].makerFeeRate",
                    "type": "Number",
                    "description": "Maker fee rate.",
                },
                {
                    "name": "symbols[].takerFeeRate",
                    "type": "Number",
                    "description": "Taker fee rate.",
                },
                {
                    "name": "symbols[].minOrderSize",
                    "type": "Number",
                    "description": "Minimum order size.",
                },
                {
                    "name": "symbols[].maxOrderSize",
                    "type": "Number",
                    "description": "Maximum order size.",
                },
                {
                    "name": "symbols[].maxPositionSize",
                    "type": "Number",
                    "description": "Maximum position size.",
                },
                {
                    "name": "symbols[].marketOpenLimitSize",
                    "type": "Number",
                    "description": "Maximum market-open order size.",
                },
            ]

        order_history = find_doc(docs, "transaction.get_order_history")
        single_order = find_doc(docs, "transaction.get_single_order_info")
        if order_history is not None and single_order is not None and not order_history.response_params:
            order_history.response_params = [dict(row) for row in single_order.response_params]


def docs_to_json(product: str, docs: List[ParsedDoc]) -> Dict[str, Any]:
    generated_at = datetime.now(timezone.utc).astimezone().date().isoformat()
    definitions = []
    for doc in docs:
        row: Dict[str, Any] = {
            "key": doc.key,
            "title": doc.title,
            "category": doc.category,
            "method": doc.method,
            "path": doc.path,
            "doc_url": doc.doc_url,
            "requires_auth": doc.requires_auth,
            "request_params": doc.request_params,
            "response_params": doc.response_params,
        }
        if doc.permission is not None:
            row["permission"] = doc.permission
        if doc.weight_ip is not None:
            row["weight_ip"] = doc.weight_ip
        if doc.weight_uid is not None:
            row["weight_uid"] = doc.weight_uid
        definitions.append(row)
    return {
        "generated_at": generated_at,
        "source": SITEMAP_URL,
        "product": product,
        "definitions": definitions,
    }


def endpoint_key_prefix(product: str, category: str) -> str:
    if product == "spot":
        return f"spot.{category}"
    return category


def endpoint_group_heading(product: str, category: str) -> str:
    category_title = category.replace("_", " ").title()
    if product == "spot":
        return f"Spot {category_title} Endpoint Sections"
    return f"{category_title} Endpoint Sections"


def ordered_categories(docs: List[ParsedDoc]) -> List[str]:
    seen = set()
    categories = []
    for doc in docs:
        if doc.category in seen:
            continue
        seen.add(doc.category)
        categories.append(doc.category)
    return categories


def render_md(product: str, docs: List[ParsedDoc], generated_at: str) -> str:
    categories = ordered_categories(docs)
    lines = [
        f"# WEEX {product.capitalize()} API Definitions",
        "",
        f"Generated from live V3 docs on {generated_at}.",
    ]
    if product == "contract" and any(doc.key.startswith("sim.") for doc in docs):
        lines.extend(
            [
                "",
                "Contract simulated futures endpoints are maintained in this generated catalog from the official WEEX contract demo API docs.",
                "Demo is not a local dry-run; demo mutating endpoints send requests to WEEX futures demo mode.",
            ]
        )
    lines.extend(
        [
            "",
            "## Contents",
            "",
            "- Summary table",
        ]
    )
    for category in categories:
        lines.append(f"- `{endpoint_key_prefix(product, category)}.*` endpoint sections")
    lines.extend(
        [
            "",
            "Use in-page search with the exact endpoint key from the summary table to jump to a specific generated section quickly.",
            "",
            "## Summary Table",
            "",
            f"Total endpoints: **{len(docs)}**",
            "",
            "| Key | Method | Path | Auth |",
            "|---|---|---|---|",
        ]
    )
    for doc in docs:
        lines.append(f"| `{doc.key}` | `{doc.method}` | `{doc.path}` | `{doc.requires_auth}` |")

    current_category = None
    for doc in docs:
        if doc.category != current_category:
            current_category = doc.category
            lines.extend(["", f"## {endpoint_group_heading(product, doc.category)}"])
        lines.extend(
            [
                "",
                f"## {doc.key} — {doc.title}",
                "",
                f"- Method: `{doc.method}`",
                f"- Path: `{doc.path}`",
                f"- Category: `{doc.category}`",
                f"- Requires Auth: `{doc.requires_auth}`",
            ]
        )
        if doc.permission is not None:
            lines.append(f"- Permission: `{doc.permission}`")
        if doc.weight_ip is not None or doc.weight_uid is not None:
            lines.append(f"- Weight(IP/UID): `{doc.weight_ip or '-'} / {doc.weight_uid or '-'}`")
        lines.append(f"- Source: {doc.doc_url}")
        lines.append("")
        lines.append("### Request Parameters")
        lines.append("")
        if doc.request_params:
            lines.extend(
                [
                    "| Name | Type | Required | Description |",
                    "|---|---|---|---|",
                ]
            )
            for row in doc.request_params:
                lines.append(
                    f"| `{row.get('name', '')}` | `{row.get('type', '')}` | `{row.get('required', '')}` | {row.get('description', '')} |"
                )
        else:
            lines.append("NONE")
        lines.append("")
        lines.append("### Response Parameters")
        lines.append("")
        if doc.response_params:
            lines.extend(
                [
                    "| Name | Type | Description |",
                    "|---|---|---|",
                ]
            )
            for row in doc.response_params:
                lines.append(
                    f"| `{row.get('name', '')}` | `{row.get('type', '')}` | {row.get('description', '')} |"
                )
        else:
            lines.append("NONE")
    return "\n".join(lines)


def write_outputs(product: str, docs: List[ParsedDoc]) -> None:
    payload = docs_to_json(product, docs)
    json_path = REFS / f"{product}-api-definitions.json"
    md_path = REFS / f"{product}-api-definitions.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_md(product, docs, payload["generated_at"]) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate WEEX REST API definitions from live docs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--product",
        choices=["contract", "spot", "all"],
        default="all",
        help="Which API definition set to regenerate: contract only, spot only, or both",
    )
    args = parser.parse_args()

    sitemap_urls = load_sitemap_urls()
    products = ["contract", "spot"] if args.product == "all" else [args.product]
    for product in products:
        urls = iter_doc_urls(product, sitemap_urls)
        docs = collect_docs(product, urls)
        apply_known_overrides(product, docs)
        if product == "contract":
            docs.extend(build_contract_demo_docs())
            docs.sort(key=lambda item: (item.category, item.key))
        write_outputs(product, docs)
        print(f"{product}: generated {len(docs)} endpoints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
