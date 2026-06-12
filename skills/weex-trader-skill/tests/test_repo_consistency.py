#!/usr/bin/env python3
from __future__ import annotations

import ast
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
SKILL = ROOT / "SKILL.md"
README = ROOT / "README.md"
MANIFEST = ROOT / "manifest.json"
FILE_INDEX = ROOT / "file-index.json"
REPO_README = REPO_ROOT / "README.md"
AGENTS_GUIDE = REPO_ROOT / "AGENTS.md"
CLAUDE_GUIDE = REPO_ROOT / "CLAUDE.md"
COPILOT_GUIDE = REPO_ROOT / ".github" / "copilot-instructions.md"
CLEAN_CHECKOUT_TOOL = REPO_ROOT / "tools" / "clean_local_skill_checkout.py"
INSTALL_LOCAL_SKILLS_TOOL = REPO_ROOT / "tools" / "install_local_skills.py"
SYNC_RISK_REVIEW_TOOL = REPO_ROOT / "tools" / "sync_weex_risk_review_core.py"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skills-ci.yml"
SHARED_RISK_REVIEW_SOURCE = REPO_ROOT / "skills" / "_shared" / "weex_risk_review_core.py"
VENDORED_RISK_REVIEW_MODULES = (
    ROOT / "scripts" / "weex_risk_review_core.py",
    REPO_ROOT / "skills" / "weex-analysis-skill" / "scripts" / "weex_risk_review_core.py",
)
AUTH_REFERENCE = ROOT / "references" / "auth-and-signing.md"
PROFILE_MANAGER_REFERENCE = ROOT / "references" / "profile-manager.md"
SCRIPT_OPERATIONS_REFERENCE = ROOT / "references" / "script-operations.md"
PROFILE_ONBOARDING_REFERENCE = ROOT / "references" / "profile-onboarding.md"
LINUX_VAULT_REFERENCE = ROOT / "references" / "linux-vault.md"
TROUBLESHOOTING_REFERENCE = ROOT / "references" / "troubleshooting.md"
TRADE_DATA_SCHEMA_REFERENCE = ROOT / "references" / "trade-data-schema.md"
CONTRACT_API_DEFINITIONS_REFERENCE = ROOT / "references" / "contract-api-definitions.md"
CONTRACT_API_SCRIPT = ROOT / "scripts" / "weex_contract_api.py"
TRADE_DATA_AGGREGATOR_SCRIPT = ROOT / "scripts" / "weex_trade_data_aggregator.py"
TRADE_GUARD_SCRIPT = ROOT / "scripts" / "weex_trade_guard.py"
API_DEFINITION_GENERATOR = ROOT / "scripts" / "generate_weex_api_definitions.py"
REQUIREMENTS = ROOT / "requirements.txt"
REQUIREMENTS_LOCK = ROOT / "requirements.lock"
PUBLISHED_REPO_URL = "https://github.com/weex-labs/weex-trader-skill"
DOC_FILES = (
    SKILL,
    README,
    MANIFEST,
    FILE_INDEX,
    PROFILE_MANAGER_REFERENCE,
    SCRIPT_OPERATIONS_REFERENCE,
    PROFILE_ONBOARDING_REFERENCE,
    LINUX_VAULT_REFERENCE,
    TROUBLESHOOTING_REFERENCE,
)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
CJK_MARKDOWN_EXCLUDE_PREFIXES = (
    "docs/superpowers/specs/",
    "memory/",
    "plans/",
    "需求分析/",
    "需求资源/",
    "发版事项/",
)
ALLOWED_CJK_MARKDOWN_TERMS = {
    "SKILL.md": ("模拟盘", "真实盘"),
    "README.md": ("模拟盘", "真实盘"),
}


def parse_requirement_names(text: str) -> set[str]:
    names: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=!~]", line, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def extract_frontmatter_name(text: str) -> str:
    match = re.match(r"---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        raise AssertionError("SKILL.md is missing YAML frontmatter")
    for line in match.group(1).splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("SKILL.md frontmatter is missing name")


def extract_frontmatter_field(text: str, field_name: str) -> str:
    match = re.match(r"---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        raise AssertionError("SKILL.md is missing YAML frontmatter")
    prefix = f"{field_name}:"
    for line in match.group(1).splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"SKILL.md frontmatter is missing {field_name}")


def extract_repo_paths(text: str) -> set[str]:
    return set(re.findall(r"(?:scripts|references)/[A-Za-z0-9_./-]+", text))


def lines_with_trailing_backslash(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.rstrip().endswith("\\")]


def normalized_script_paths(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in (root / "scripts").iterdir()
        if path.is_file() and path.suffix in {".py", ".sh"}
    }


def extract_python_comments_and_docstrings(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    snippets: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            snippets.append(token.string)

    module = ast.parse(text)
    for node in [module, *ast.walk(module)]:
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            docstring = ast.get_docstring(node, clean=False)
            if docstring:
                snippets.append(docstring)
    return snippets


def extract_shell_comments(path: Path) -> list[str]:
    comments: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            comments.append(stripped)
    return comments


class RepoConsistencyTests(unittest.TestCase):
    def test_docs_and_comments_do_not_contain_cjk_text(self) -> None:
        markdown_files = sorted(ROOT.rglob("*.md"))
        python_files = sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "tests").glob("*.py"))
        shell_files = sorted((ROOT / "scripts").glob("*.sh"))

        offenders: list[str] = []

        for path in markdown_files:
            rel_path = path.relative_to(ROOT).as_posix()
            if any(rel_path.startswith(prefix) for prefix in CJK_MARKDOWN_EXCLUDE_PREFIXES):
                continue
            text = path.read_text(encoding="utf-8")
            for allowed_term in ALLOWED_CJK_MARKDOWN_TERMS.get(rel_path, ()):
                text = text.replace(allowed_term, "")
            if CJK_RE.search(text):
                offenders.append(rel_path)

        for path in python_files:
            snippets = extract_python_comments_and_docstrings(path)
            if any(CJK_RE.search(snippet) for snippet in snippets):
                offenders.append(path.relative_to(ROOT).as_posix())

        for path in shell_files:
            snippets = extract_shell_comments(path)
            if any(CJK_RE.search(snippet) for snippet in snippets):
                offenders.append(path.relative_to(ROOT).as_posix())

        self.assertEqual(offenders, [])

    def test_non_zh_linux_wizard_entrypoints_do_not_contain_cjk_text(self) -> None:
        paths = (
            ROOT / "scripts" / "weex_linux_profile_wizard.sh",
            ROOT / "scripts" / "weex_linux_profile_wizard_en.sh",
        )

        offenders = [
            path.relative_to(ROOT).as_posix()
            for path in paths
            if CJK_RE.search(path.read_text(encoding="utf-8"))
        ]

        self.assertEqual(offenders, [])

    def test_generator_dependencies_are_declared(self) -> None:
        requirements = parse_requirement_names(REQUIREMENTS.read_text(encoding="utf-8"))
        requirements_lock_text = REQUIREMENTS_LOCK.read_text(encoding="utf-8")

        self.assertIn("cryptography", requirements)
        self.assertIn("requests", requirements)
        self.assertIn("beautifulsoup4", requirements)
        self.assertIn("cryptography==", requirements_lock_text)
        self.assertIn("--hash=sha256:", requirements_lock_text)

    def test_runtime_dependency_guidance_uses_locked_requirements(self) -> None:
        script_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "scripts").glob("weex_*.py")
        )

        self.assertNotIn("Install requirements.txt", script_text)
        self.assertNotIn("安装 requirements.txt", script_text)
        self.assertIn("requirements.lock", script_text)
        self.assertIn("--require-hashes", script_text)

    def test_split_references_exist(self) -> None:
        self.assertTrue(PROFILE_MANAGER_REFERENCE.exists())
        self.assertTrue(PROFILE_ONBOARDING_REFERENCE.exists())
        self.assertTrue(LINUX_VAULT_REFERENCE.exists())
        self.assertTrue(TROUBLESHOOTING_REFERENCE.exists())

    def test_readmes_reference_published_github_install_source(self) -> None:
        readme_text = README.read_text(encoding="utf-8")
        repo_readme_text = REPO_README.read_text(encoding="utf-8")

        self.assertIn(PUBLISHED_REPO_URL, readme_text)
        self.assertIn(PUBLISHED_REPO_URL, repo_readme_text)
        self.assertNotIn("https://github.com/drgnchan/weex-trader-skill", readme_text)
        self.assertNotIn("https://github.com/drgnchan/weex-trader-skill", repo_readme_text)

    def test_skill_identity_matches_manifest(self) -> None:
        skill_name = extract_frontmatter_name(SKILL.read_text(encoding="utf-8"))
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(skill_name, manifest["identity"]["name"])
        self.assertEqual(manifest["identity"]["source_of_truth"], "SKILL.md")

    def test_skill_documents_localized_user_facing_trading_mode_labels(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        readme_text = README.read_text(encoding="utf-8")

        for text in (skill_text, readme_text):
            self.assertIn("localized trading-mode labels", text)
            self.assertIn("`模拟盘` and `真实盘`", text)
            self.assertIn("`demo trading` and `real trading`", text)
            self.assertIn("not environment labels", text)
            self.assertIn("not account labels", text)
            self.assertIn("raw `live` or `demo`", text)
            self.assertNotIn("localized full trading-environment names", text)
            self.assertNotIn("real trading environment versus simulated futures environment", text)

    def test_skill_documents_preview_defaults_to_combined_confirmation_when_mode_is_missing(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")
        readme_text = README.read_text(encoding="utf-8")

        for text in (skill_text, readme_text):
            self.assertIn("do not ask a standalone trading-mode question", text)
            self.assertIn("most likely initial preview mode", text)
            self.assertIn("preview-only default", text)
            self.assertIn("confirmation block must put the mode and funds warning first", text)
            self.assertIn("include the switch prompt", text)
            self.assertIn("profile names or notes can only be weak preview-default signals", text)
            self.assertIn("same saved profile can target either trading mode", text)

        for text in (skill_text, readme_text):
            self.assertNotIn("real account versus simulated account", text)

    def test_environment_language_does_not_call_trading_environment_an_account(self) -> None:
        scanned_paths = (
            SKILL,
            README,
            MANIFEST,
            TRADE_DATA_SCHEMA_REFERENCE,
            CONTRACT_API_DEFINITIONS_REFERENCE,
            CONTRACT_API_SCRIPT,
            TRADE_DATA_AGGREGATOR_SCRIPT,
            TRADE_GUARD_SCRIPT,
            API_DEFINITION_GENERATOR,
        )
        forbidden_phrases = (
            "real account versus simulated account",
            "real account`",
            "simulated account`",
            "real account\"",
            "simulated account\"",
            "real WEEX futures account environment",
            "WEEX simulated futures account environment",
            "real WEEX account",
            "WEEX simulated futures account",
        )

        offenders: list[str] = []
        for path in scanned_paths:
            text = path.read_text(encoding="utf-8")
            for phrase in forbidden_phrases:
                if phrase in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {phrase}")

        self.assertEqual(offenders, [])

    def test_skill_frontmatter_declares_compatibility(self) -> None:
        compatibility = extract_frontmatter_field(SKILL.read_text(encoding="utf-8"), "compatibility")

        self.assertIn("Python", compatibility)
        self.assertIn("network", compatibility)
        self.assertIn("Tk", compatibility)

    def test_documented_repo_paths_exist(self) -> None:
        referenced_paths: set[str] = set()
        for path in DOC_FILES:
            referenced_paths.update(extract_repo_paths(path.read_text(encoding="utf-8")))

        self.assertTrue(referenced_paths, "expected at least one documented repo path")
        missing = sorted(path for path in referenced_paths if not (ROOT / path).exists())
        self.assertEqual(missing, [])

    def test_script_operation_and_setup_references_define_command_context(self) -> None:
        expected = "Run the shell commands below from the skill root"

        self.assertIn(expected, SCRIPT_OPERATIONS_REFERENCE.read_text(encoding="utf-8"))
        self.assertIn(expected, PROFILE_ONBOARDING_REFERENCE.read_text(encoding="utf-8"))
        self.assertIn(expected, LINUX_VAULT_REFERENCE.read_text(encoding="utf-8"))

    def test_script_operations_documents_raw_call_argument_order_and_post_query_guard(self) -> None:
        text = SCRIPT_OPERATIONS_REFERENCE.read_text(encoding="utf-8")

        self.assertIn("--profile is a global argument", text)
        self.assertIn("place it before `call`", text)
        self.assertIn("use `--endpoint <key>`", text)
        self.assertIn("Some official query endpoints use POST", text)
        self.assertIn("protected as mutating by the local guard", text)

    def test_setup_docs_avoid_shell_specific_line_continuations(self) -> None:
        offenders: list[str] = []
        for path in (SCRIPT_OPERATIONS_REFERENCE, PROFILE_ONBOARDING_REFERENCE, LINUX_VAULT_REFERENCE):
            lines = lines_with_trailing_backslash(path.read_text(encoding="utf-8"))
            if lines:
                offenders.append(path.relative_to(ROOT).as_posix())

        self.assertEqual(offenders, [])

    def test_script_operations_documents_runtime_setup_helper(self) -> None:
        script_ops_text = SCRIPT_OPERATIONS_REFERENCE.read_text(encoding="utf-8")

        self.assertIn("scripts/weex_runtime_setup.py", script_ops_text)
        self.assertIn("ensurepip", script_ops_text)
        self.assertIn("current interpreter", script_ops_text)

    def test_skill_requires_manual_once_linux_vault_mode(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8").lower()

        self.assertIn("manual_once", skill_text)
        self.assertNotIn("auto_unlock", skill_text)

    def test_local_install_wrapper_supports_dry_run(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(INSTALL_LOCAL_SKILLS_TOOL),
                "--skill",
                "weex-trader-skill",
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        combined = f"{completed.stdout}\n{completed.stderr}"
        self.assertEqual(completed.returncode, 0, combined)
        self.assertIn("gh skill install", combined)
        self.assertIn("weex-trader-skill", combined)

    def test_shared_risk_review_core_is_synced(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SYNC_RISK_REVIEW_TOOL), "--check"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        combined = f"{completed.stdout}\n{completed.stderr}"
        self.assertEqual(completed.returncode, 0, combined)
        self.assertTrue(SHARED_RISK_REVIEW_SOURCE.exists())
        expected = SHARED_RISK_REVIEW_SOURCE.read_text(encoding="utf-8")
        for path in VENDORED_RISK_REVIEW_MODULES:
            self.assertTrue(path.exists(), path.as_posix())
            self.assertEqual(path.read_text(encoding="utf-8"), expected)

    def test_repo_has_skills_ci_workflow(self) -> None:
        self.assertTrue(CI_WORKFLOW.exists())
        workflow_text = CI_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("actions/checkout@v6", workflow_text)
        self.assertIn("actions/setup-python@v6", workflow_text)
        self.assertIn("GH_CLI_VERSION", workflow_text)
        self.assertIn("Install GitHub CLI with skill support", workflow_text)
        self.assertIn("--allow-downgrades", workflow_text)
        self.assertIn("gh skill --help", workflow_text)
        self.assertIn("python3 -m pip install --require-hashes -r skills/weex-trader-skill/requirements.lock", workflow_text)
        self.assertIn("tools/run_skill_tests.py", workflow_text)
        self.assertIn("tools/clean_local_skill_checkout.py --check", workflow_text)
        self.assertIn("tools/install_local_skills.py", workflow_text)
        self.assertIn("--skill weex-monitor-skill", workflow_text)
        self.assertIn("gh skill publish --dry-run", workflow_text)

    def test_root_readme_describes_three_skills_including_monitor(self) -> None:
        readme_text = REPO_README.read_text(encoding="utf-8")
        zh_readme_text = (REPO_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

        self.assertIn("The Three Skills", readme_text)
        self.assertIn("weex-monitor-skill", readme_text)
        self.assertIn("automated monitor", readme_text.lower())
        self.assertIn("三个 Skill", zh_readme_text)
        self.assertIn("weex-monitor-skill", zh_readme_text)
        self.assertIn("自动化监控", zh_readme_text)

    def test_machine_readable_metadata_describes_application_vault_consistently(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))

        self.assertEqual(manifest["state"]["secure_store_backends"], ["Application Vault"])
        self.assertIn(
            "application-vault backend",
            file_index["file_guide"]["scripts/weex_profile_store.py"]["role"],
        )
        self.assertNotIn(
            "OS-keychain",
            file_index["file_guide"]["scripts/weex_profile_store.py"]["role"],
        )

    def test_docs_and_indexes_describe_agent_state_cache(self) -> None:
        combined = "\n".join(path.read_text(encoding="utf-8") for path in (SKILL, README))
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))

        self.assertIn("agent-init.json", combined)
        self.assertIn("agent-runtime.json", combined)
        self.assertIn("scripts/weex_agent_state.py", combined)
        self.assertIn("agent_state_paths", manifest["state"])
        self.assertEqual(
            manifest["state"]["agent_state_paths"]["init"],
            "~/.weex-trader-skill/agent-init.json",
        )
        self.assertEqual(
            manifest["state"]["agent_state_paths"]["runtime"],
            "~/.weex-trader-skill/agent-runtime.json",
        )
        self.assertIn("scripts/weex_agent_state.py", file_index["file_guide"])

    def test_skill_requires_ai_to_preflight_agent_state_on_every_turn(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")

        self.assertIn("For every turn that uses this skill", skill_text)
        self.assertIn("before routing or UI launch", skill_text)
        self.assertIn("scripts/weex_agent_state.py --command skill.preflight --language <zh|en> --pretty", skill_text)

    def test_skill_requires_managed_runtime_for_windows_and_macos_gui(self) -> None:
        skill_text = SKILL.read_text(encoding="utf-8")

        self.assertIn("must use the managed GUI runtime", skill_text)
        self.assertIn("must not launch GUI entrypoints with the system", skill_text)
        self.assertIn("system, miniforge, pyenv, Homebrew, or OS Python", skill_text)

    def test_manifest_and_docs_do_not_publish_weex_profile_lang_override(self) -> None:
        combined = "\n".join(path.read_text(encoding="utf-8") for path in (SKILL, README))
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertNotIn("WEEX_PROFILE_LANG", combined)
        self.assertNotIn("WEEX_PROFILE_LANG", manifest["state"]["env_vars"])

    def test_file_index_covers_vault_ui_and_session_agent(self) -> None:
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))

        self.assertIn("scripts/weex_vault_manager_app.py", file_index["file_guide"])
        self.assertIn("scripts/weex_vault_agent.py", file_index["file_guide"])

    def test_file_index_ignore_by_default_covers_generated_noise(self) -> None:
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))

        self.assertIn(".pytest_cache/", file_index["ignore_by_default"])

    def test_file_index_covers_all_script_entrypoints(self) -> None:
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))
        script_paths = normalized_script_paths(ROOT)
        missing = sorted(script_paths - set(file_index["file_guide"]))

        self.assertEqual(missing, [])

    def test_compact_spot_reference_uses_valid_example_endpoint_keys(self) -> None:
        spot_reference_text = (ROOT / "references" / "spot-endpoints.md").read_text(encoding="utf-8")
        spot_definitions = json.loads((ROOT / "references" / "spot-api-definitions.json").read_text(encoding="utf-8"))
        valid_keys = {definition["key"] for definition in spot_definitions["definitions"]}

        example_keys = re.findall(r"--endpoint\s+([A-Za-z0-9_.-]+)", spot_reference_text)
        invalid = sorted(key for key in example_keys if key not in valid_keys)

        self.assertEqual(invalid, [])
        self.assertIn("spot.market.get_ticker_info", spot_reference_text)

    def test_contract_definitions_include_futures_demo_endpoints(self) -> None:
        definitions = json.loads((ROOT / "references" / "contract-api-definitions.json").read_text(encoding="utf-8"))
        by_key = {definition["key"]: definition for definition in definitions["definitions"]}

        expected = {
            "sim.account.get_account_balance": ("GET", "/capi/v3/sim/balance", "USER_DATA", 5, 10),
            "sim.transaction.place_order": ("POST", "/capi/v3/sim/order", "TRADE", 2, 5),
            "sim.account.get_all_positions": ("GET", "/capi/v3/sim/position/allPosition", "USER_DATA", 10, 15),
            "sim.transaction.get_order_history": ("GET", "/capi/v3/sim/order/history", "USER_DATA", 10, 10),
        }

        for key, (method, path, permission, weight_ip, weight_uid) in expected.items():
            with self.subTest(key=key):
                definition = by_key[key]
                self.assertEqual(definition["category"], "sim")
                self.assertEqual(definition["method"], method)
                self.assertEqual(definition["path"], path)
                self.assertTrue(definition["requires_auth"])
                self.assertEqual(definition["permission"], permission)
                self.assertEqual(definition["weight_ip"], weight_ip)
                self.assertEqual(definition["weight_uid"], weight_uid)

        order_params = {
            row["name"]: row
            for row in by_key["sim.transaction.place_order"]["request_params"]
        }
        self.assertEqual(order_params["newClientOrderId"]["required"], "Yes")
        self.assertIn("TpWorkingType", order_params)
        self.assertIn("SlWorkingType", order_params)

    def test_script_operations_demo_history_example_omits_symbol_filter(self) -> None:
        script_operations = SCRIPT_OPERATIONS_REFERENCE.read_text(encoding="utf-8")

        self.assertNotRegex(
            script_operations,
            r"sim\.transaction\.get_order_history[^\n]*--query\s+'[^']*\"symbol\"",
        )
        self.assertIn(
            "sim.transaction.get_order_history --trading-mode demo --query '{\"limit\":50}'",
            script_operations,
        )

    def test_contract_demo_api_uses_contract_definition_catalog(self) -> None:
        demo_reference = "references/contract-demo-api.zh-CN.md"
        skill_text = SKILL.read_text(encoding="utf-8")
        readme_text = README.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))
        compact_reference = (ROOT / "references" / "contract-endpoints.md").read_text(encoding="utf-8")
        definitions = json.loads((ROOT / "references" / "contract-api-definitions.json").read_text(encoding="utf-8"))
        definitions_md = (ROOT / "references" / "contract-api-definitions.md").read_text(encoding="utf-8")

        self.assertFalse((ROOT / demo_reference).exists())
        self.assertNotIn(demo_reference, skill_text)
        self.assertNotIn(demo_reference, readme_text)
        self.assertNotIn(demo_reference, compact_reference)
        self.assertNotIn(demo_reference, manifest["routing"]["domains"]["contract"]["open_first"])
        self.assertNotIn(demo_reference, json.dumps(file_index, ensure_ascii=False))
        self.assertNotIn(demo_reference, file_index["file_guide"]["scripts/weex_contract_api.py"]["depends_on"])
        for definition in definitions["definitions"]:
            if definition["key"].startswith("sim."):
                with self.subTest(key=definition["key"]):
                    self.assertTrue(definition["doc_url"].startswith("https://www.weex.com/api-doc/"))
        self.assertIn("sim.transaction.place_order", definitions_md)
        self.assertIn("Demo is not a local dry-run", definitions_md)

    def test_contract_api_definition_markdown_keeps_grouped_contents(self) -> None:
        definitions_md = (ROOT / "references" / "contract-api-definitions.md").read_text(encoding="utf-8")

        for expected in (
            "- `account.*` endpoint sections",
            "- `market.*` endpoint sections",
            "- `sim.*` endpoint sections",
            "- `transaction.*` endpoint sections",
            "Use in-page search with the exact endpoint key from the summary table",
            "## Account Endpoint Sections",
            "## Market Endpoint Sections",
            "## Sim Endpoint Sections",
            "## Transaction Endpoint Sections",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, definitions_md)

    def test_skill_ships_root_readme(self) -> None:
        self.assertTrue(README.exists())

    def test_local_checkout_cleaner_removes_packaging_noise(self) -> None:
        self.assertTrue(CLEAN_CHECKOUT_TOOL.exists())

        with tempfile.TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            skills_dir = temp_root / "skills" / "demo-skill"
            scripts_cache_dir = skills_dir / "scripts" / "__pycache__"
            tests_cache_dir = temp_root / "tests" / "__pycache__"
            scripts_cache_dir.mkdir(parents=True)
            tests_cache_dir.mkdir(parents=True)
            (skills_dir / ".DS_Store").write_text("noise", encoding="utf-8")
            (scripts_cache_dir / "demo.cpython-313.pyc").write_bytes(b"pyc")
            (tests_cache_dir / "temp.pyc").write_bytes(b"pyc")
            (skills_dir / "SKILL.md").write_text("# demo\n", encoding="utf-8")

            check_before = subprocess.run(
                [shutil.which("python3") or "python3", str(CLEAN_CHECKOUT_TOOL), "--root", str(temp_root), "--check"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(check_before.returncode, 1, check_before.stdout + check_before.stderr)
            self.assertIn(".DS_Store", check_before.stdout)
            self.assertIn("__pycache__", check_before.stdout)

            clean_run = subprocess.run(
                [shutil.which("python3") or "python3", str(CLEAN_CHECKOUT_TOOL), "--root", str(temp_root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(clean_run.returncode, 0, clean_run.stdout + clean_run.stderr)
            self.assertFalse((skills_dir / ".DS_Store").exists())
            self.assertFalse(scripts_cache_dir.exists())
            self.assertFalse(tests_cache_dir.exists())
            self.assertTrue((skills_dir / "SKILL.md").exists())

            check_after = subprocess.run(
                [shutil.which("python3") or "python3", str(CLEAN_CHECKOUT_TOOL), "--root", str(temp_root), "--check"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(check_after.returncode, 0, check_after.stdout + check_after.stderr)

    def test_agent_guides_keep_cross_skill_safety_rules_aligned(self) -> None:
        agents_text = AGENTS_GUIDE.read_text(encoding="utf-8")
        claude_text = CLAUDE_GUIDE.read_text(encoding="utf-8")
        copilot_text = COPILOT_GUIDE.read_text(encoding="utf-8")

        for text in (agents_text, claude_text, copilot_text):
            self.assertIn("skills/weex-trader-skill", text)
            self.assertIn("skills/weex-analysis-skill", text)
            self.assertIn("live-confirmation flag", text)
            self.assertIn(
                "collect it first, normalize it into JSON, then pass it into the analysis skill",
                text,
            )

    def test_trade_guard_is_not_runtime_coupled_to_analysis_skill(self) -> None:
        trade_guard_text = (ROOT / "scripts" / "weex_trade_guard.py").read_text(encoding="utf-8")
        file_index = json.loads(FILE_INDEX.read_text(encoding="utf-8"))

        self.assertNotIn("weex-analysis-skill", trade_guard_text)
        self.assertNotIn("weex_analysis_cli", trade_guard_text)
        self.assertIn("scripts/weex_trade_risk_review.py", file_index["file_guide"])
        self.assertEqual(
            file_index["file_guide"]["scripts/weex_trade_guard.py"]["depends_on"],
            [
                "scripts/weex_trade_data_aggregator.py",
                "scripts/weex_order_intent_state.py",
                "scripts/weex_trade_risk_review.py",
            ],
        )

    def test_agent_state_script_is_tracked_when_git_metadata_is_present(self) -> None:
        if not (REPO_ROOT / ".git").exists():
            self.skipTest("git metadata not present")
        git = shutil.which("git")
        if git is None:
            self.skipTest("git executable not available")

        completed = subprocess.run(
            [git, "ls-files", "--error-unmatch", "skills/weex-trader-skill/scripts/weex_agent_state.py"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
