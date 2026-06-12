#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
SKILL = ROOT / "SKILL.md"
MANIFEST = ROOT / "manifest.json"
FILE_INDEX = ROOT / "file-index.json"
SCRIPT = ROOT / "scripts" / "weex_monitor_cli.py"


def extract_frontmatter_field(text: str, field_name: str) -> str:
    match = re.match(r"---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        raise AssertionError("SKILL.md is missing YAML frontmatter")
    prefix = f"{field_name}:"
    for line in match.group(1).splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"SKILL.md frontmatter is missing {field_name}")


class MonitorDocsConsistencyTests(unittest.TestCase):
    def test_skill_frontmatter_and_manifest_identity_match(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(extract_frontmatter_field(skill_text, "name"), "weex-monitor-skill")
        self.assertEqual(manifest["identity"]["name"], "weex-monitor-skill")
        self.assertIn("automated monitor", extract_frontmatter_field(skill_text, "description").lower())

    def test_skill_declares_trader_skill_as_live_execution_boundary(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")

        self.assertIn("weex-trader-skill", skill_text)
        self.assertIn("`真实盘` access", skill_text)
        self.assertIn("real order execution", skill_text)
        self.assertNotIn("--confirm-live", skill_text)
        self.assertIn("Never send mutating requests", skill_text)
        self.assertIn("does not own API credentials", skill_text)

    def test_skill_documents_ai_natural_language_to_dsl_rules(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")

        self.assertIn("AI natural-language parsing", skill_text)
        self.assertIn("convert the user's monitor instruction into the Task DSL", skill_text)
        self.assertIn("ask for the missing field", skill_text)
        self.assertIn("profile is always required", skill_text)
        self.assertIn("metric must be `unrealized_pnl`", skill_text)
        self.assertIn("dry-run commands still write local SQLite task state and events", skill_text)
        self.assertIn("Do not submit orders", skill_text)
        self.assertIn("Close the BTCUSDT long position automatically", skill_text)
        self.assertIn("unrealized PnL is above 50", skill_text)

    def test_skill_documents_combined_monitor_and_live_run_flow(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertIn("confirm-and-run-loop", skill_text)
        self.assertIn("confirm-text-live", skill_text)
        self.assertIn("combined confirmation", skill_text)
        self.assertIn("matched live position", skill_text)
        self.assertIn("detailed real-trading position snapshot", skill_text)
        self.assertIn("not returned", skill_text)
        self.assertIn("finite `duration_seconds`", skill_text)
        self.assertIn("--duration-seconds", skill_text)
        self.assertNotIn("720 iterations", skill_text)
        self.assertIn(
            "confirm-and-run-loop",
            manifest["routing"]["domains"]["pnl_live_runner"]["commands"],
        )

    def test_skill_documents_order_origin_requests_use_aggregate_position_pnl(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")

        self.assertIn("Order-origin monitor requests", skill_text)
        self.assertIn("aggregate `symbol` + `position_side` position unrealized PnL", skill_text)
        self.assertIn("not isolated single-order PnL", skill_text)
        self.assertIn("show both the matched aggregate position size and fixed close quantity", skill_text)

    def test_skill_documents_codex_heartbeat_status_reporting(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")

        self.assertIn("Codex heartbeat", skill_text)
        self.assertIn("default reporting interval is `60` seconds", skill_text)
        self.assertIn("reporting_interval_seconds", skill_text)
        self.assertIn("automation_update", skill_text)
        self.assertIn("agent_reporting", skill_text)
        self.assertIn("Claude Code", skill_text)
        self.assertIn("`/loop`", skill_text)
        self.assertIn("OpenClaw", skill_text)
        self.assertIn("`openclaw cron add`", skill_text)
        self.assertIn("current value", skill_text)
        self.assertIn("terminal task state", skill_text)
        self.assertIn("sanitized summaries", skill_text)
        self.assertIn("Do not output HTML entities", skill_text)
        self.assertIn("`&lt;`", skill_text)
        self.assertIn("less than", skill_text)

    def test_skill_body_uses_english_except_localized_confirmation_word(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        without_allowed_reply_word = (
            skill_text
            .replace("确认", "")
            .replace("模拟盘", "")
            .replace("真实盘", "")
            .replace("当前交易环境： ", "")
            .replace("盘别", "")
        )

        self.assertIsNone(re.search(r"[\u4e00-\u9fff]", without_allowed_reply_word))

    def test_skill_requires_explicit_confirmation_language_selection(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")

        self.assertIn("Always pass `--language zh` for Chinese user copy", skill_text)
        self.assertIn("Always pass `--language en` for English user copy", skill_text)
        self.assertIn("do not rely on the script default language", skill_text)

    def test_skill_documents_localized_user_facing_trading_mode_labels(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")

        self.assertIn("localized trading-mode labels", skill_text)
        self.assertIn("`模拟盘` and `真实盘`", skill_text)
        self.assertIn("`demo trading` and `real trading`", skill_text)
        self.assertIn("`当前交易环境： `", skill_text)
        self.assertIn("not environment labels", skill_text)
        self.assertIn("not account labels", skill_text)
        self.assertIn("raw `live` or `demo`", skill_text)

    def test_skill_documents_price_threshold_tasks_are_routed_out(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))

        self.assertIn("Do not create a monitor for price-threshold conditions", skill_text)
        self.assertIn("WEEX official conditional orders", skill_text)
        self.assertNotIn("symbol_price_monitor", skill_text)
        self.assertNotIn("price_condition_submission", manifest["routing"]["domains"])
        self.assertNotIn("build-price-order", file_index["file_guide"]["scripts/weex_monitor_cli.py"]["surface"])
        self.assertNotIn("submit-price-order", file_index["file_guide"]["scripts/weex_monitor_cli.py"]["surface"])
        self.assertNotIn("reconcile-price-order", file_index["file_guide"]["scripts/weex_monitor_cli.py"]["surface"])

    def test_root_agent_routing_mentions_monitor_skill(self) -> None:
        for relative_path in (
            "AGENTS.md",
            "CLAUDE.md",
            ".github/copilot-instructions.md",
            ".cursor/rules/weex-safety.mdc",
        ):
            text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("weex-monitor-skill", text, relative_path)
            self.assertIn("automated monitor", text.lower(), relative_path)

    def test_file_index_covers_script_and_script_avoids_trader_imports(self) -> None:
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))
        script_text = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(script_text)

        self.assertIn("scripts/weex_monitor_cli.py", file_index["file_guide"])
        self.assertIn("file-index.json", file_index["file_guide"])
        self.assertIn("tests/test_weex_monitor_cli.py", file_index["file_guide"])
        self.assertIn("tests/test_docs_consistency.py", file_index["file_guide"])
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        from_imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        all_imports = imports | from_imports
        self.assertNotIn("weex_contract_api", all_imports)
        self.assertNotIn("weex_trade_guard", all_imports)
        self.assertNotIn("weex_profile_store", all_imports)


if __name__ == "__main__":
    unittest.main()
