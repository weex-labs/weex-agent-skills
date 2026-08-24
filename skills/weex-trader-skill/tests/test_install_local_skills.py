#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
TOOLS = REPO_ROOT / "tools"
OPENCLAW_UPDATE_SCRIPT = ROOT / "scripts" / "update_openclaw_skills.sh"
OPENCLAW_SKILLS = (
    "weex-trader-skill",
    "weex-analysis-skill",
    "weex-monitor-skill",
    "weex-partner-skill",
)
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

    def commit_all(self, root: Path, message: str) -> None:
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=WEEX Tests",
                "-c",
                "user.email=tests@example.invalid",
                "commit",
                "-q",
                "-m",
                message,
            ],
            cwd=root,
            check=True,
        )

    def openclaw_source_repo(self, root: Path) -> None:
        self.assertTrue(
            OPENCLAW_UPDATE_SCRIPT.exists(),
            "OpenClaw Git + symlink update script is not implemented yet; expected RED.",
        )
        self.init_repo(root)
        subprocess.run(["git", "checkout", "-q", "-b", "feature/Trading-Competition"], cwd=root, check=True)
        for skill in OPENCLAW_SKILLS:
            self.write_file(
                root / "skills" / skill / "SKILL.md",
                f"---\nname: {skill}\ndescription: Test fixture.\n---\n",
            )
        script_target = root / OPENCLAW_UPDATE_SCRIPT.relative_to(REPO_ROOT)
        script_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(OPENCLAW_UPDATE_SCRIPT, script_target)
        script_target.chmod(0o755)
        self.commit_all(root, "initial fixture")

    def fake_openclaw(self, bin_dir: Path, log_path: Path) -> None:
        executable = bin_dir / "openclaw"
        self.write_file(
            executable,
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"printf '%s\\n' \"$*\" >> {str(log_path)!r}\n",
        )
        executable.chmod(0o755)

    def openclaw_env(self, temp_root: Path, source_repo: Path) -> tuple[dict[str, str], Path, Path, Path]:
        fake_bin = temp_root / "fake-bin"
        fake_bin.mkdir()
        openclaw_log = temp_root / "openclaw.log"
        self.fake_openclaw(fake_bin, openclaw_log)

        repo_dir = temp_root / ".openclaw" / "skill-repos" / "weex-agent-skills"
        skills_dir = temp_root / ".openclaw" / "skills"
        bin_link = temp_root / "bin" / "update-weex-openclaw-skills.sh"
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(temp_root),
                "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
                "WEEX_OPENCLAW_REPO_URL": str(source_repo),
                "WEEX_OPENCLAW_REPO_DIR": str(repo_dir),
                "WEEX_OPENCLAW_SKILLS_DIR": str(skills_dir),
                "WEEX_OPENCLAW_BRANCH": "feature/Trading-Competition",
                "WEEX_OPENCLAW_BIN_LINK": str(bin_link),
            }
        )
        return env, repo_dir, skills_dir, openclaw_log

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
        self.assertIn("weex-partner-skill", installer.DEFAULT_SKILLS)

    def test_monitor_skill_install_includes_trader_dependency(self) -> None:
        args = installer.build_parser().parse_args(["--skill", "weex-monitor-skill"])

        self.assertEqual(
            installer.resolve_skills(args),
            ("weex-trader-skill", "weex-monitor-skill"),
        )

    def test_partner_skill_install_includes_trader_dependency(self) -> None:
        args = installer.build_parser().parse_args(["--skill", "weex-partner-skill"])

        self.assertEqual(
            installer.resolve_skills(args),
            ("weex-trader-skill", "weex-partner-skill"),
        )

    def test_all_install_orders_dependencies_before_dependents(self) -> None:
        args = installer.build_parser().parse_args(["--all"])
        skills = installer.resolve_skills(args)

        self.assertLess(skills.index("weex-trader-skill"), skills.index("weex-partner-skill"))
        self.assertLess(skills.index("weex-trader-skill"), skills.index("weex-monitor-skill"))

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

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable on this platform")
    def test_openclaw_update_script_bootstraps_and_fast_forwards_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            source_repo = temp_root / "source"
            source_repo.mkdir()
            self.openclaw_source_repo(source_repo)
            env, repo_dir, skills_dir, openclaw_log = self.openclaw_env(temp_root, source_repo)

            first = subprocess.run(
                ["bash", str(OPENCLAW_UPDATE_SCRIPT), "--dev"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(first.returncode, 0, f"{first.stdout}\n{first.stderr}")
            for skill in OPENCLAW_SKILLS:
                link = skills_dir / skill
                self.assertTrue(link.is_symlink(), skill)
                self.assertEqual(link.resolve(), (repo_dir / "skills" / skill).resolve())
            bin_link = Path(env["WEEX_OPENCLAW_BIN_LINK"])
            self.assertTrue(bin_link.is_symlink())
            self.assertNotEqual(bin_link.resolve(), (repo_dir / OPENCLAW_UPDATE_SCRIPT.relative_to(REPO_ROOT)).resolve())

            marker = source_repo / "UPDATED"
            self.write_file(marker, "second revision\n")
            self.commit_all(source_repo, "second fixture")
            second = subprocess.run(
                ["bash", str(OPENCLAW_UPDATE_SCRIPT), "--dev"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(second.returncode, 0, f"{second.stdout}\n{second.stderr}")
            self.assertEqual((repo_dir / "UPDATED").read_text(encoding="utf-8"), "second revision\n")
            self.assertEqual(
                openclaw_log.read_text(encoding="utf-8").splitlines(),
                [
                    "skills list --eligible",
                    "skills info weex-trader-skill",
                    "skills check",
                    "skills list --eligible",
                    "skills info weex-trader-skill",
                    "skills check",
                ],
            )
            self.assertIn("Latest commit:", second.stdout)

            stable_updater = temp_root / ".openclaw" / "update-weex-openclaw-skills.sh"
            self.assertTrue(stable_updater.is_file())
            rerun = subprocess.run(
                ["bash", str(stable_updater), "--dev"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(rerun.returncode, 0, f"{rerun.stdout}\n{rerun.stderr}")
            self.assertEqual(
                subprocess.check_output(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], text=True).strip(),
                subprocess.check_output(["git", "-C", str(source_repo), "rev-parse", "HEAD"], text=True).strip(),
            )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable on this platform")
    def test_openclaw_update_script_refuses_to_replace_a_real_skill_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            source_repo = temp_root / "source"
            source_repo.mkdir()
            self.openclaw_source_repo(source_repo)
            env, _repo_dir, skills_dir, _openclaw_log = self.openclaw_env(temp_root, source_repo)
            occupied = skills_dir / "weex-trader-skill"
            occupied.mkdir(parents=True)
            self.write_file(occupied / "keep.txt", "do not overwrite\n")

            completed = subprocess.run(
                ["bash", str(OPENCLAW_UPDATE_SCRIPT), "--dev"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("not a symbolic link", completed.stderr)
            self.assertEqual((occupied / "keep.txt").read_text(encoding="utf-8"), "do not overwrite\n")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable on this platform")
    def test_openclaw_update_replaces_symlink_to_directory_instead_of_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            source_repo = temp_root / "source"
            source_repo.mkdir()
            self.openclaw_source_repo(source_repo)
            env, repo_dir, skills_dir, _openclaw_log = self.openclaw_env(temp_root, source_repo)
            old_target = temp_root / "old-skill-target"
            old_target.mkdir()
            occupied = skills_dir / "weex-trader-skill"
            occupied.parent.mkdir(parents=True, exist_ok=True)
            occupied.symlink_to(old_target, target_is_directory=True)

            completed = subprocess.run(
                ["bash", str(OPENCLAW_UPDATE_SCRIPT), "--dev"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, f"{completed.stdout}\n{completed.stderr}")
            self.assertEqual(
                occupied.resolve(), (repo_dir / "skills" / "weex-trader-skill").resolve()
            )
            self.assertFalse((old_target / "weex-trader-skill.tmp").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable on this platform")
    def test_openclaw_check_failure_restores_previous_checkout_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            source_repo = temp_root / "source"
            source_repo.mkdir()
            self.openclaw_source_repo(source_repo)
            env, repo_dir, skills_dir, _openclaw_log = self.openclaw_env(temp_root, source_repo)

            first = subprocess.run(
                ["bash", str(OPENCLAW_UPDATE_SCRIPT), "--dev"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, f"{first.stdout}\n{first.stderr}")
            old_commit = subprocess.check_output(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], text=True).strip()

            self.write_file(source_repo / "UPDATED", "rollback candidate\n")
            self.commit_all(source_repo, "rollback candidate")
            fake_openclaw = Path(env["PATH"].split(os.pathsep)[0]) / "openclaw"
            fake_openclaw.write_text("#!/usr/bin/env bash\nexit 17\n", encoding="utf-8")
            fake_openclaw.chmod(0o755)

            second = subprocess.run(
                ["bash", str(OPENCLAW_UPDATE_SCRIPT), "--dev"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(
                subprocess.check_output(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], text=True).strip(),
                old_commit,
            )
            self.assertFalse((repo_dir / "UPDATED").exists())
            for skill in OPENCLAW_SKILLS:
                self.assertEqual((skills_dir / skill).resolve(), (repo_dir / "skills" / skill).resolve())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable on this platform")
    def test_openclaw_update_rejects_non_official_source_without_explicit_dev_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            source_repo = temp_root / "source"
            source_repo.mkdir()
            self.openclaw_source_repo(source_repo)
            env, repo_dir, _skills_dir, _openclaw_log = self.openclaw_env(temp_root, source_repo)

            completed = subprocess.run(
                ["bash", str(OPENCLAW_UPDATE_SCRIPT)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("--dev", completed.stderr)
            self.assertFalse(repo_dir.exists())


if __name__ == "__main__":
    unittest.main()
