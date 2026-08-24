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
    "tax": "tax",
}

KEY_OVERRIDES = {
    ("spot", "GetAllProductInfo"): "spot.config.get_api_trading_symbols",
}

DEMO_KEY_MAP = {
    "GetAccountBalance": "sim.account.get_account_balance",
    "GetAllPositions": "sim.account.get_all_positions",
    "GetOrderHistory": "sim.transaction.get_order_history",
    "PlaceOrder": "sim.transaction.place_order",
}

EXCLUDED_DOC_URLS = {
    "https://www.weex.com/api-doc/partner/rebate-endpoints/GetInternalWithdrawalStatus",
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
    rate_limits: List[Dict[str, Any]]
    request_params: List[Dict[str, str]]
    response_params: List[Dict[str, str]]
    constraints: List[str]
    request_transport: str
    query_fields: List[str]
    body_fields: List[str]
    response_container: str
    permission: Optional[str] = None


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=DOC_TIMEOUT)
    response.raise_for_status()
    # Docusaurus pages are UTF-8, but requests can otherwise guess ISO-8859-1
    # when the origin omits a charset.  That corrupts symbols such as >= and ->.
    response.encoding = "utf-8"
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


def parse_rate_limits(text: str) -> tuple[Optional[int], List[Dict[str, Any]]]:
    weight_ip = None
    ip_match = re.search(r"Weight\(IP\):\s*(\d+)", text, flags=re.IGNORECASE)
    if ip_match:
        weight_ip = int(ip_match.group(1))

    limits: List[Dict[str, Any]] = []
    for match in re.finditer(
        r"(\d+)\s+on\s+[^;]*?\((X-[A-Z0-9-]+)\)",
        text,
        flags=re.IGNORECASE,
    ):
        limits.append({"header": match.group(2).upper(), "limit": int(match.group(1))})
    return weight_ip, limits


def get_group(product: str, path_parts: List[str]) -> Optional[str]:
    if product == "contract" and len(path_parts) > 2 and path_parts[2] == "demo":
        return "sim"
    if product == "spot" and len(path_parts) > 2 and path_parts[1] == "partner":
        return "rebate"
    group_segment = path_parts[2] if len(path_parts) > 2 else ""
    if product == "contract":
        return CONTRACT_GROUP_MAP.get(group_segment)
    return SPOT_GROUP_MAP.get(group_segment)


def _table_prefix(caption: str) -> Optional[str]:
    caption = clean_text(caption)
    match = re.search(r"\(([A-Za-z][A-Za-z0-9_.-]*\[\])\)", caption)
    if match:
        return match.group(1)
    match = re.search(r"(?:element|item)\s+of\s+([A-Za-z][A-Za-z0-9_.-]*)", caption, re.IGNORECASE)
    if match:
        return f"{match.group(1)}[]"
    return None


def _canonical_header(header: str) -> Optional[str]:
    normalized = clean_text(header).rstrip("?").lower()
    aliases = {
        "parameter": "name",
        "parameter name": "name",
        "name": "name",
        "field": "name",
        "field name": "name",
        "index": "name",
        "参数": "name",
        "参数名": "name",
        "字段": "name",
        "字段名": "name",
        "索引": "name",
        "下标": "name",
        "type": "type",
        "parameter type": "type",
        "类型": "type",
        "参数类型": "type",
        "required": "required",
        "required?": "required",
        "是否必填": "required",
        "是否必须": "required",
        "description": "description",
        "meaning": "description",
        "说明": "description",
        "描述": "description",
        "字段说明": "description",
        "含义": "description",
    }
    return aliases.get(normalized)


def _prefixed_name(raw_name: str, *, prefix: Optional[str], parent: Optional[str]) -> tuple[str, bool]:
    raw_name = clean_text(raw_name)
    nested = bool(re.match(r"^(?:->|→|>|›)+", raw_name))
    name = re.sub(r"^(?:->|→|>|›)+\s*", "", raw_name).strip()
    if nested and parent:
        return f"{parent}.{name}", nested
    if prefix and not name.startswith(f"{prefix}.") and name != prefix:
        return f"{prefix}.{name}", nested
    return name, nested


def extract_table_rows(container: Tag, *, prefix: Optional[str] = None) -> List[Dict[str, str]]:
    table = container if container.name == "table" else container.find("table")
    if table is None:
        return []
    rows = table.find_all("tr")
    if not rows:
        return []
    headers = [_canonical_header(cell.get_text(" ", strip=True)) for cell in rows[0].find_all(["th", "td"])]
    results: List[Dict[str, str]] = []
    parent: Optional[str] = None
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        item: Dict[str, str] = {}
        for idx, key in enumerate(headers):
            if key is None:
                continue
            value = values[idx] if idx < len(values) else ""
            item[key] = value
        if "name" in item:
            name, nested = _prefixed_name(item["name"], prefix=prefix, parent=parent)
            item["name"] = name
            if not nested and not prefix and "array" in item.get("type", "").lower():
                parent = name if name.endswith("[]") else f"{name}[]"
        if item:
            results.append(item)
    return results


def _narrative_response_type(description: str) -> str:
    normalized = description.lower()
    if "json object" in normalized:
        return "Object"
    if re.search(r"\b(?:as\s+)?a\s+string\b", normalized):
        return "String"
    if "array" in normalized:
        return "Array"
    return ""


def _extract_sections(markdown: Tag) -> tuple[List[Dict[str, str]], List[Dict[str, str]], List[str]]:
    request_params: List[Dict[str, str]] = []
    response_params: List[Dict[str, str]] = []
    response_narratives: List[str] = []
    constraints: List[str] = []
    section: Optional[str] = None
    caption = ""
    markers = {
        "request parameters": "request",
        "request parameters：": "request",
        "请求参数": "request",
        "request example": None,
        "请求示例": None,
        "response": "response",
        "response parameters": "response",
        "返回参数": "response",
        "response example": None,
        "返回示例": None,
    }
    for node in markdown.find_all(["p", "li", "table"], recursive=True):
        if node.find_parent("table") is not None:
            continue
        if node.name in {"p", "li"}:
            value = clean_text(node.get_text(" ", strip=True))
            marker = markers.get(value.lower()) if value.lower() in markers else markers.get(value)
            if value.lower() in markers or value in markers:
                section = marker
                caption = ""
                continue
            if section == "request" and value and value.lower() not in {"notes", "note"}:
                constraints.append(value)
            if section == "response" and value and value.lower() not in {"notes", "note"}:
                response_narratives.append(value)
            caption = value
            continue
        if section not in {"request", "response"}:
            continue
        rows = extract_table_rows(node, prefix=_table_prefix(caption))
        if section == "request":
            request_params.extend(rows)
        else:
            response_params.extend(rows)
        caption = ""
    if not response_params and response_narratives:
        description = " ".join(response_narratives)
        response_params.append(
            {
                "name": "$",
                "type": _narrative_response_type(description),
                "description": description,
            }
        )
    return request_params, response_params, constraints


def _find_code_example(markdown: Tag, *labels: str) -> str:
    normalized_labels = {clean_text(label).rstrip(":：").lower() for label in labels}
    for node in markdown.find_all(["p", "h2", "h3", "h4"], recursive=True):
        value = clean_text(node.get_text(" ", strip=True)).rstrip(":：").lower()
        if value not in normalized_labels:
            continue
        example = node.find_next("pre")
        if example is not None:
            return example.get_text("", strip=True)
    return ""


def _request_transport(method: str, request_example: str) -> str:
    normalized_method = method.upper()
    if normalized_method == "GET":
        return "query"
    if normalized_method in {"POST", "PUT", "PATCH"}:
        return "body"
    if normalized_method == "DELETE":
        normalized_example = request_example.replace("\\", " ")
        has_data = re.search(
            r"(?:^|\s)(?:-d|--data(?:-raw)?)(?=\s|['\"{])",
            normalized_example,
            flags=re.IGNORECASE,
        )
        return "body" if has_data else "query"
    raise ValueError(f"Unsupported HTTP method in official API document: {method}")


def _response_narrative(markdown: Tag) -> str:
    collecting = False
    values: List[str] = []
    start_markers = {"response", "response parameters", "返回参数"}
    end_markers = {"response example", "返回示例"}
    for node in markdown.find_all(["p", "li", "table"], recursive=True):
        if node.find_parent("table") is not None:
            continue
        value = clean_text(node.get_text(" ", strip=True))
        normalized = value.rstrip(":：").lower()
        if normalized in start_markers:
            collecting = True
            continue
        if normalized in end_markers:
            break
        if collecting and node.name in {"p", "li"} and value:
            values.append(value)
    return " ".join(values)


def _response_container(markdown: Tag, response_params: List[Dict[str, str]]) -> str:
    narrative = _response_narrative(markdown).lower()
    if (
        "single object" in narrative
        and "array" in narrative
        or "either a single object or an array" in narrative
    ):
        return "conditional_object_or_array"

    response_example = _find_code_example(markdown, "Response example", "返回示例")
    if response_example:
        try:
            value = json.loads(response_example)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return "array"
        if isinstance(value, str):
            return "string"

    if len(response_params) == 1 and response_params[0].get("name") == "$":
        narrative_type = response_params[0].get("type", "").strip().lower()
        if narrative_type in {"object", "array", "string"}:
            return narrative_type
    return "object"


def parse_doc(url: str) -> Optional[ParsedDoc]:
    html = fetch_text(url)
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    markdown = soup.select_one("article .theme-doc-markdown.markdown")
    if article is None or markdown is None:
        return None

    article_text = clean_text(article.get_text(" ", strip=True))
    endpoint = re.search(r"\b(GET|POST|PUT|DELETE)\s+(/(?:api|capi)/[^\s]+)", article_text)
    if endpoint is None:
        return None
    method = endpoint.group(1)
    path = endpoint.group(2).rstrip(".,;，。；")

    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 4 or path_parts[0] != "api-doc":
        return None
    source_product = path_parts[1]
    if source_product not in {"contract", "spot", "partner"}:
        return None
    if "V2" in path_parts or "zh-CN" in path_parts:
        return None
    product = "spot" if source_product == "partner" else source_product

    category = get_group(product, path_parts)
    if category is None:
        return None

    title_node = markdown.find("h1") or markdown.find("header")
    title = clean_text(title_node.get_text(" ", strip=True)) if title_node else path_parts[-1]

    if category == "sim":
        key = DEMO_KEY_MAP.get(path_parts[-1], f"sim.{slugify(path_parts[-1])}")
    elif (product, path_parts[-1]) in KEY_OVERRIDES:
        key = KEY_OVERRIDES[(product, path_parts[-1])]
    else:
        key = f"{category}.{slugify(path_parts[-1])}"
        if product == "spot":
            key = f"spot.{key}"

    weight_ip, rate_limits = parse_rate_limits(clean_text(markdown.get_text(" ", strip=True)))
    permission_match = re.search(r"\((USER_DATA|TRADE)\)", title)
    permission = permission_match.group(1) if permission_match else None
    requires_auth = permission is not None or category == "rebate"

    request_params, response_params, constraints = _extract_sections(markdown)
    request_transport = _request_transport(
        method,
        _find_code_example(markdown, "Request example", "请求示例"),
    )
    request_fields = [row["name"] for row in request_params if row.get("name")]
    query_fields = request_fields if request_transport == "query" else []
    body_fields = request_fields if request_transport == "body" else []
    response_container = _response_container(markdown, response_params)

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
        rate_limits=rate_limits,
        request_params=request_params,
        response_params=response_params,
        constraints=constraints,
        request_transport=request_transport,
        query_fields=query_fields,
        body_fields=body_fields,
        response_container=response_container,
        permission=permission,
    )


def iter_doc_urls(product: str, sitemap_urls: Iterable[str]) -> List[str]:
    urls = []
    for url in sitemap_urls:
        if url in EXCLUDED_DOC_URLS:
            continue
        if "/V2/" in url or "/zh-CN/" in url:
            continue
        if product == "spot":
            included = bool(
                re.search(r"/api-doc/spot/(?:AccountAPI|ConfigAPI|MarketDataAPI|orderApi|tax)/", url)
                or re.search(r"/api-doc/partner/rebate-endpoints/", url)
            )
        else:
            included = bool(
                re.search(r"/api-doc/contract/(?:Account_API|Market_API|Transaction_API|demo)/", url)
            )
        if not included:
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
    def has_only_narrative_placeholder(doc: ParsedDoc) -> bool:
        return (
            len(doc.response_params) == 1
            and doc.response_params[0].get("name") == "$"
        )

    def copy_response(target_key: str, source_key: str) -> None:
        target = find_doc(docs, target_key)
        source = find_doc(docs, source_key)
        if (
            target is not None
            and source is not None
            and (not target.response_params or has_only_narrative_placeholder(target))
        ):
            target.response_params = [dict(row) for row in source.response_params]

    if product == "spot":
        api_symbols = find_doc(docs, "spot.config.get_api_trading_symbols")
        if api_symbols is not None and (
            not api_symbols.response_params or has_only_narrative_placeholder(api_symbols)
        ):
            api_symbols.response_params = [
                {
                    "name": "$",
                    "type": "Array<String>",
                    "description": "Raw response is an array of spot symbols available for API trading.",
                }
            ]
        copy_response("spot.order.history_orders", "spot.order.order_details")

    if product == "contract":
        api_symbols = find_doc(docs, "market.get_api_trading_symbols")
        if api_symbols is not None and (
            not api_symbols.response_params or has_only_narrative_placeholder(api_symbols)
        ):
            api_symbols.response_params = [
                {
                    "name": "$",
                    "type": "Array<String>",
                    "description": "Raw response is an array of futures symbols available for API trading.",
                }
            ]
        response_references = {
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
        for target_key, source_key in response_references.items():
            copy_response(target_key, source_key)

        close_positions = find_doc(docs, "transaction.close_positions")
        if close_positions is not None and not any(
            "priority" in item.lower() for item in close_positions.constraints
        ):
            close_positions.constraints.append(
                "When both symbol and positionId are provided, positionId has priority; "
                "the position must belong to the supplied symbol."
            )

        demo_order = find_doc(docs, "sim.transaction.place_order")
        if demo_order is not None:
            time_in_force = next(
                (row for row in demo_order.request_params if row.get("name") == "timeInForce"),
                None,
            )
            if time_in_force is not None and "POST_ONLY" not in time_in_force.get("description", ""):
                description = time_in_force.get("description", "").rstrip(". ")
                time_in_force["description"] = f"{description}, POST_ONLY."


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
            "request_transport": doc.request_transport,
            "query_fields": doc.query_fields,
            "body_fields": doc.body_fields,
            "request_params": doc.request_params,
            "response_container": doc.response_container,
            "response_params": doc.response_params,
            "rate_limits": doc.rate_limits,
            "constraints": doc.constraints,
        }
        if doc.permission is not None:
            row["permission"] = doc.permission
        if doc.weight_ip is not None:
            row["weight_ip"] = doc.weight_ip
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
                f"- Request Transport: `{doc.request_transport}`",
                f"- Response Container: `{doc.response_container}`",
            ]
        )
        if doc.permission is not None:
            lines.append(f"- Permission: `{doc.permission}`")
        if doc.weight_ip is not None:
            lines.append(f"- Weight(IP): `{doc.weight_ip}`")
        if doc.rate_limits:
            rendered_limits = ", ".join(
                f"{item['header']}={item['limit']}" for item in doc.rate_limits
            )
            lines.append(f"- Rate Limits: `{rendered_limits}`")
        lines.append(f"- Source: {doc.doc_url}")
        if doc.constraints:
            lines.append("- Request Constraints:")
            for constraint in doc.constraints:
                lines.append(f"  - {constraint}")
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
        write_outputs(product, docs)
        print(f"{product}: generated {len(docs)} endpoints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
