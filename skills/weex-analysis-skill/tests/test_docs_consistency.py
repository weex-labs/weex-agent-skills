#!/usr/bin/env python3
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SKILL = ROOT / "SKILL.md"
EXPECTED_STANDARD_DISCLAIMER = (
    "Disclaimer: This result is generated solely from the current input data and is for reference only. "
    "It does not constitute any investment or trading advice. Please make your own independent judgment "
    "based on real-time data, official rules, and your own risk tolerance. Responsibility for related "
    "decisions and execution rests solely with the user."
)


def extract_frontmatter_description(text: str) -> str:
    match = re.match(r"---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        raise AssertionError("SKILL.md is missing YAML frontmatter")

    for line in match.group(1).splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()

    raise AssertionError("SKILL.md frontmatter is missing description")


def extract_frontmatter_field(text: str, field_name: str) -> str:
    match = re.match(r"---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        raise AssertionError("SKILL.md is missing YAML frontmatter")

    prefix = f"{field_name}:"
    for line in match.group(1).splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()

    raise AssertionError(f"SKILL.md frontmatter is missing {field_name}")


class AnalysisDocsConsistencyTests(unittest.TestCase):
    def test_readme_cross_skill_verification_does_not_assume_repo_root_layout(self) -> None:
        readme_text = README.read_text(encoding="utf-8")

        self.assertIn("requires both this skill and `weex-trader-skill`", readme_text)
        self.assertNotIn("Run this from the repo root", readme_text)
        self.assertNotIn("skills/weex-trader-skill/scripts/", readme_text)
        self.assertNotIn("skills/weex-analysis-skill/scripts/", readme_text)

    def test_skill_description_covers_replay_profile_and_account_risk(self) -> None:
        description = extract_frontmatter_description(SKILL.read_text(encoding="utf-8"))

        self.assertIn("replay", description)
        self.assertIn("profile", description)
        self.assertIn("account-risk", description)

    def test_skill_frontmatter_declares_compatibility(self) -> None:
        compatibility = extract_frontmatter_field(SKILL.read_text(encoding="utf-8"), "compatibility")

        self.assertIn("Python", compatibility)
        self.assertIn("JSON", compatibility)
        self.assertIn("network", compatibility.lower())

    def test_input_policy_targets_the_correct_normalized_shape_for_each_command(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")

        self.assertIn("accepted normalized JSON shape for the target analysis command", skill_text)
        self.assertNotIn("convert it into the snapshot schema before analysis", skill_text)

    def test_skill_documents_localized_user_facing_trading_mode_labels(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")

        self.assertIn("localized trading-mode labels", skill_text)
        self.assertIn("`模拟盘` and `真实盘`", skill_text)
        self.assertIn("`demo trading` and `real trading`", skill_text)
        self.assertIn("not environment labels", skill_text)
        self.assertIn("not account labels", skill_text)
        self.assertIn("raw `live` or `demo`", skill_text)

    def test_skill_and_readme_pin_the_exact_standard_disclaimer(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        readme_text = README.read_text(encoding="utf-8")

        self.assertIn(EXPECTED_STANDARD_DISCLAIMER, skill_text)
        self.assertIn(EXPECTED_STANDARD_DISCLAIMER, readme_text)

    def test_readme_prepare_replay_example_lists_all_filter_options(self) -> None:
        readme_text = README.read_text(encoding="utf-8")

        self.assertIn("--account-scope", readme_text)
        self.assertIn("--start-time-ms", readme_text)
        self.assertIn("--end-time-ms", readme_text)


if __name__ == "__main__":
    unittest.main()
