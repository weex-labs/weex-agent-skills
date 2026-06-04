#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import install_local_skills as installer  # noqa: E402


class InstallLocalSkillsTests(unittest.TestCase):
    def init_repo(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    def write_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def tracked_demo_repo(self, root: Path) -> None:
        self.write_file(root / "README.md", "# demo\n")
        self.write_file(
            root / "skills" / "demo-skill" / "SKILL.md",
            "---\nname: demo-skill\ndescription: Use when testing local export behavior.\n---\n",
        )
        self.write_file(root / "skills" / "demo-skill" / "scripts" / "demo.py", "print('demo')\n")
        subprocess.run(
            ["git", "add", "README.md", "skills/demo-skill/SKILL.md", "skills/demo-skill/scripts/demo.py"],
            cwd=root,
            check=True,
        )

    def test_export_repo_excludes_untracked_files_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir) / "repo"
            repo_root.mkdir()
            self.init_repo(repo_root)
            self.tracked_demo_repo(repo_root)
            self.write_file(repo_root / "skills" / "demo-skill" / "secret.txt", "should-not-export\n")
            self.write_file(repo_root / "notes.txt", "top-level scratch\n")

            export_root = Path(tempdir) / "export"
            strategy = installer.export_repo(repo_root, export_root, ("demo-skill",), include_untracked=False)

            self.assertEqual(strategy, "tracked selected skill paths")
            self.assertTrue((export_root / "skills" / "demo-skill" / "SKILL.md").exists())
            self.assertFalse((export_root / "skills" / "demo-skill" / "secret.txt").exists())
            self.assertFalse((export_root / "notes.txt").exists())

    def test_export_repo_can_include_untracked_files_with_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir) / "repo"
            repo_root.mkdir()
            self.init_repo(repo_root)
            self.tracked_demo_repo(repo_root)
            self.write_file(repo_root / "skills" / "demo-skill" / "draft.txt", "include-me\n")
            self.write_file(repo_root / "notes.txt", "still-exclude\n")

            export_root = Path(tempdir) / "export"
            strategy = installer.export_repo(repo_root, export_root, ("demo-skill",), include_untracked=True)

            self.assertEqual(strategy, "tracked selected skill paths (+ opted-in untracked)")
            self.assertTrue((export_root / "skills" / "demo-skill" / "draft.txt").exists())
            self.assertFalse((export_root / "notes.txt").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable on this platform")
    def test_export_repo_does_not_recreate_agent_entrypoint_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir) / "repo"
            repo_root.mkdir()
            self.init_repo(repo_root)
            self.tracked_demo_repo(repo_root)
            agents_dir = repo_root / ".agents" / "skills"
            agents_dir.mkdir(parents=True)
            os.symlink("../../skills/demo-skill", agents_dir / "demo-skill")

            export_root = Path(tempdir) / "export"
            installer.export_repo(repo_root, export_root, ("demo-skill",), include_untracked=False)

            self.assertFalse((export_root / ".agents").exists())
            self.assertTrue((export_root / "skills" / "demo-skill" / "SKILL.md").exists())

    def test_default_local_install_includes_monitor_skill(self) -> None:
        self.assertIn("weex-trader-skill", installer.DEFAULT_SKILLS)
        self.assertIn("weex-analysis-skill", installer.DEFAULT_SKILLS)
        self.assertIn("weex-monitor-skill", installer.DEFAULT_SKILLS)

    def test_monitor_skill_install_includes_trader_dependency(self) -> None:
        args = installer.build_parser().parse_args(["--skill", "weex-monitor-skill"])

        self.assertEqual(
            installer.resolve_skills(args),
            ("weex-trader-skill", "weex-monitor-skill"),
        )

    def test_invalid_agent_is_rejected_before_printing_dry_run_command(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Unsupported agent"):
            installer.main(["--skill", "weex-monitor-skill", "--agent", "openclaw", "--dry-run"])

        with self.assertRaisesRegex(SystemExit, "claude-code"):
            installer.main(["--skill", "weex-monitor-skill", "--agent", "claude", "--dry-run"])

    def test_supported_agent_values_include_codex_and_claude_code(self) -> None:
        args = installer.build_parser().parse_args(["--agent", "codex"])
        self.assertEqual(args.agent, "codex")
        installer.validate_agent(args.agent)

        args = installer.build_parser().parse_args(["--agent", "claude-code"])
        self.assertEqual(args.agent, "claude-code")
        installer.validate_agent(args.agent)


if __name__ == "__main__":
    unittest.main()
