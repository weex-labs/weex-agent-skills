#!/usr/bin/env python3
"""Read-only drift check for the seven supported WEEX Partner API pages."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

import requests
from bs4 import BeautifulSoup, Tag


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = SKILL_ROOT / "references" / "partner-field-catalog.json"
DEFAULT_TRADER_DEFINITIONS = (
    SKILL_ROOT.parent / "weex-trader-skill" / "references" / "partner-api-definitions.json"
)
DEFAULT_TIMEOUT = 30.0


def clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value.replace("\u200b", " ")).strip()
    return re.sub(r"\s+([,.;:，。；：)])", r"\1", value)


def normalized_wire_name(value: str) -> str:
    return re.sub(r"^(?:->|→|>|›)+\s*", "", clean_text(value))


def _canonical_header(value: str) -> str | None:
    aliases = {
        "parameter": "name",
        "parameter name": "name",
        "field": "name",
        "field name": "name",
        "name": "name",
        "参数": "name",
        "参数名": "name",
        "字段": "name",
        "字段名": "name",
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
    return aliases.get(clean_text(value).rstrip("?").lower())


def _table_rows(table: Tag, *, request: bool) -> list[dict[str, Any]]:
    rows = table.find_all("tr")
    if not rows:
        return []
    headers = [
        _canonical_header(cell.get_text(" ", strip=True))
        for cell in rows[0].find_all(["th", "td"])
    ]
    parsed: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        item: dict[str, str] = {}
        for index, header in enumerate(headers):
            if header is not None:
                item[header] = values[index] if index < len(values) else ""
        wire_name = normalized_wire_name(item.get("name", ""))
        if not wire_name:
            continue
        output: dict[str, Any] = {
            "wire_name": wire_name,
            "type": item.get("type", ""),
        }
        if request:
            required = item.get("required", "").strip().lower()
            output["official_required"] = required in {"yes", "true", "required", "是", "必填"}
        output["official_description"] = item.get("description", "")
        parsed.append(output)
    return parsed


def _find_code_example(markdown: Tag, labels: set[str]) -> str:
    normalized_labels = {clean_text(value).rstrip(":：").lower() for value in labels}
    for node in markdown.find_all(["p", "h2", "h3", "h4"]):
        value = clean_text(node.get_text(" ", strip=True)).rstrip(":：").lower()
        if value in normalized_labels:
            example = node.find_next("pre")
            return example.get_text("", strip=True) if example is not None else ""
    return ""


def _request_transport(method: str, request_example: str) -> str:
    if method == "GET":
        return "query"
    if method in {"POST", "PUT", "PATCH"}:
        return "body"
    normalized = request_example.replace("\\", " ")
    if method == "DELETE" and re.search(
        r"(?:^|\s)(?:-d|--data(?:-raw)?)(?=\s|['\"{])",
        normalized,
        flags=re.IGNORECASE,
    ):
        return "body"
    return "query"


def parse_official_page(html: str, *, language: str) -> dict[str, Any]:
    if language not in {"zh", "en"}:
        raise ValueError("language must be zh or en")
    soup = BeautifulSoup(html, "html.parser")
    markdown = soup.select_one("article .theme-doc-markdown.markdown")
    if markdown is None:
        raise ValueError("official page is missing the article markdown container")
    page_text = clean_text(markdown.get_text(" ", strip=True))
    endpoint = re.search(r"\b(GET|POST|PUT|DELETE)\s+(/(?:api|capi)/[^\s]+)", page_text)
    if endpoint is None:
        raise ValueError("official page is missing method/path")
    title_node = markdown.find("h1")
    title = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""

    request_fields: list[dict[str, Any]] = []
    response_fields: list[dict[str, Any]] = []
    section: str | None = None
    markers = {
        "request parameters": "request",
        "请求参数": "request",
        "request example": None,
        "请求示例": None,
        "response": "response",
        "response parameters": "response",
        "返回参数": "response",
        "response example": None,
        "返回示例": None,
    }
    for node in markdown.find_all(["p", "table"], recursive=True):
        if node.find_parent("table") is not None:
            continue
        if node.name == "p":
            value = clean_text(node.get_text(" ", strip=True)).rstrip(":：").lower()
            if value in markers:
                section = markers[value]
            continue
        if section == "request":
            request_fields.extend(_table_rows(node, request=True))
        elif section == "response":
            response_fields.extend(_table_rows(node, request=False))

    method = endpoint.group(1)
    weight_match = re.search(r"(?:Weight|权重)\s*\(IP\)\s*:\s*(\d+)", page_text, re.IGNORECASE)
    return {
        "title": title,
        "method": method,
        "path": endpoint.group(2).rstrip(".,;，。；"),
        "weight": int(weight_match.group(1)) if weight_match else None,
        "request_transport": _request_transport(
            method,
            _find_code_example(markdown, {"Request example", "请求示例"}),
        ),
        "request_fields": request_fields,
        "response_fields": response_fields,
    }


def fetch_text(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "weex-partner-skill-official-contract-check/1.0"},
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def _field_map(fields: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    result: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for field in fields:
        name = str(field.get("wire_name", ""))
        if name in result:
            duplicates.append(name)
        result[name] = field
    return result, sorted(set(duplicates))


def _compare_fields(
    *,
    operation: str,
    language: str,
    section: str,
    official_fields: list[dict[str, Any]],
    expected_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    official, official_duplicates = _field_map(official_fields)
    expected, expected_duplicates = _field_map(expected_fields)
    if official_duplicates or expected_duplicates:
        mismatches.append({
            "operation": operation,
            "language": language,
            "section": section,
            "kind": "duplicate_fields",
            "official": official_duplicates,
            "expected": expected_duplicates,
        })
    if set(official) != set(expected):
        mismatches.append({
            "operation": operation,
            "language": language,
            "section": section,
            "kind": "field_names",
            "missing_local": sorted(set(official) - set(expected)),
            "extra_local": sorted(set(expected) - set(official)),
        })
    description_key = f"official_description_{language}"
    for name in sorted(set(official) & set(expected)):
        official_field = official[name]
        expected_field = expected[name]
        comparisons = {
            "type": (official_field.get("type", ""), expected_field.get("type", "")),
            "description": (
                clean_text(str(official_field.get("official_description", ""))),
                clean_text(str(expected_field.get(description_key, ""))),
            ),
        }
        if section == "request":
            comparisons["required"] = (
                bool(official_field.get("official_required")),
                bool(expected_field.get("official_required")),
            )
        for kind, (actual, expected_value) in comparisons.items():
            if actual != expected_value:
                mismatches.append({
                    "operation": operation,
                    "language": language,
                    "section": section,
                    "field": name,
                    "kind": kind,
                    "official": actual,
                    "expected": expected_value,
                })
    return mismatches


def check_official_contract(
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    trader_definitions_path: Path = DEFAULT_TRADER_DEFINITIONS,
    fetcher: Callable[[str], str] = fetch_text,
) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    trader_payload = json.loads(trader_definitions_path.read_text(encoding="utf-8"))
    trader_by_key = {item["key"]: item for item in trader_payload["definitions"]}
    mismatches: list[dict[str, Any]] = []
    checked_pages = 0

    for operation, expected in catalog["operations"].items():
        endpoint_key = expected["endpoint"]
        trader = trader_by_key.get(endpoint_key)
        if trader is None:
            mismatches.append({"operation": operation, "kind": "missing_trader_definition"})
            continue
        transports = {field["transport"] for field in expected["request_fields"]}
        expected_transport = next(iter(transports), "body" if trader["method"] == "POST" else "query")
        if len(transports) > 1:
            mismatches.append({"operation": operation, "kind": "mixed_catalog_transports"})

        for language, url_key, title_key in (
            ("zh", "doc_url", "official_name_zh"),
            ("en", "doc_url_en", "official_name_en"),
        ):
            url = expected[url_key]
            try:
                parsed = parse_official_page(fetcher(url), language=language)
            except Exception as exc:
                mismatches.append({
                    "operation": operation,
                    "language": language,
                    "kind": "fetch_or_parse_error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            checked_pages += 1
            scalar_checks = {
                "title": (parsed["title"], expected[title_key]),
                "method": (parsed["method"], trader["method"]),
                "path": (parsed["path"], trader["path"]),
                "weight": (parsed["weight"], trader["weight"]),
                "request_transport": (parsed["request_transport"], expected_transport),
            }
            for kind, (actual, expected_value) in scalar_checks.items():
                if actual != expected_value:
                    mismatches.append({
                        "operation": operation,
                        "language": language,
                        "kind": kind,
                        "official": actual,
                        "expected": expected_value,
                    })
            mismatches.extend(_compare_fields(
                operation=operation,
                language=language,
                section="request",
                official_fields=parsed["request_fields"],
                expected_fields=expected["request_fields"],
            ))
            mismatches.extend(_compare_fields(
                operation=operation,
                language=language,
                section="response",
                official_fields=parsed["response_fields"],
                expected_fields=expected["response_fields"],
            ))

    return {
        "ok": not mismatches,
        "contract_version": catalog.get("contract_version"),
        "checked_operations": len(catalog["operations"]),
        "checked_pages": checked_pages,
        "mismatches": mismatches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare the seven supported Partner operations with current official Chinese and English pages."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--trader-definitions", type=Path, default=DEFAULT_TRADER_DEFINITIONS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    def configured_fetcher(url: str) -> str:
        return fetch_text(url, timeout=args.timeout)

    report = check_official_contract(
        catalog_path=args.catalog,
        trader_definitions_path=args.trader_definitions,
        fetcher=configured_fetcher,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
