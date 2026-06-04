#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
NOISE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".git"}
NOISE_FILE_NAMES = {".DS_Store"}
NOISE_FILE_SUFFIXES = {".pyc"}
DEFAULT_SKILLS = ("weex-trader-skill", "weex-analysis-skill", "weex-monitor-skill")
SKILL_DEPENDENCIES = {
    "weex-monitor-skill": ("weex-trader-skill",),
}
SUPPORTED_AGENTS = {
    "github-copilot",
    "claude-code",
    "cursor",
    "codex",
    "gemini",
    "antigravity",
}
AGENT_HINTS = {
    "claude": "Use --agent claude-code for Claude Code.",
    "openclaw": "Openclaw is not supported by gh skill install --agent; use --dir for an Openclaw skills directory if that host expects one.",
}
ROOT_METADATA_FILES = (
    Path("README.md"),
    Path("LICENSE"),
)


def discover_skills() -> tuple[str, ...]:
    names = []
    for path in sorted((REPO_ROOT / "skills").iterdir()):
        if path.is_dir() and (path / "SKILL.md").exists():
            names.append(path.name)
    return tuple(names)


def resolve_skills(args: argparse.Namespace) -> tuple[str, ...]:
    if args.all:
        return discover_skills()
    if args.skill:
        selected = list(args.skill)
        for skill in list(selected):
            for dependency in SKILL_DEPENDENCIES.get(skill, ()):
                if dependency not in selected:
                    selected.insert(0, dependency)
        return tuple(dict.fromkeys(selected))
    return DEFAULT_SKILLS


def validate_skills(root: Path, skills: tuple[str, ...]) -> None:
    missing: list[str] = []
    for skill in skills:
        skill_root = root / "skills" / skill
        if not skill_root.is_dir() or not (skill_root / "SKILL.md").exists():
            missing.append(skill)
    if missing:
        raise SystemExit(f"Unknown or invalid skill(s): {', '.join(missing)}")


def validate_agent(agent: str | None) -> None:
    if agent is None:
        return
    if agent in SUPPORTED_AGENTS:
        return
    hint = AGENT_HINTS.get(agent)
    suffix = f" {hint}" if hint else f" Supported agents: {', '.join(sorted(SUPPORTED_AGENTS))}."
    raise SystemExit(f"Unsupported agent: {agent}.{suffix}")


def git_path_list(root: Path, *args: str) -> list[Path]:
    completed = subprocess.run(
        ["git", "-C", str(root), *args, "-z"],
        text=False,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return []
    return [Path(raw.decode("utf-8")) for raw in completed.stdout.split(b"\0") if raw]


def should_ignore(name: str) -> bool:
    return (
        name in NOISE_DIR_NAMES
        or name in NOISE_FILE_NAMES
        or any(name.endswith(suffix) for suffix in NOISE_FILE_SUFFIXES)
    )


def copy_entry(src: Path, dest: Path) -> None:
    if should_ignore(src.name):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        target = src.resolve(strict=True)
        if not target.is_file():
            raise SystemExit(f"Unsupported symlink in local skill export: {src}")
        shutil.copy2(target, dest)
        return
    shutil.copy2(src, dest)


def is_export_path(rel_path: Path, skills: tuple[str, ...]) -> bool:
    if rel_path in ROOT_METADATA_FILES:
        return True
    parts = rel_path.parts
    return len(parts) >= 2 and parts[0] == "skills" and parts[1] in skills


def scoped_git_paths(root: Path, skills: tuple[str, ...], *, include_untracked: bool) -> tuple[list[Path], str]:
    tracked = [path for path in git_path_list(root, "ls-files") if is_export_path(path, skills)]
    strategy = "tracked selected skill paths"
    if not include_untracked:
        return tracked, strategy

    untracked = [
        path
        for path in git_path_list(root, "ls-files", "--others", "--exclude-standard")
        if is_export_path(path, skills)
    ]
    return sorted({*tracked, *untracked}), f"{strategy} (+ opted-in untracked)"


def export_repo(root: Path, destination: Path, skills: tuple[str, ...], *, include_untracked: bool) -> str:
    validate_skills(root, skills)
    snapshot_paths, strategy = scoped_git_paths(root, skills, include_untracked=include_untracked)

    if not snapshot_paths:
        raise SystemExit("No exportable files were found. Run this from a git checkout with tracked skill files.")

    for rel_path in snapshot_paths:
        copy_entry(root / rel_path, destination / rel_path)
    return strategy


def build_install_command(clean_root: Path, skill: str, args: argparse.Namespace) -> list[str]:
    command = ["gh", "skill", "install", str(clean_root), skill, "--from-local"]
    if args.agent:
        command.extend(["--agent", args.agent])
    if args.scope:
        command.extend(["--scope", args.scope])
    if args.dir:
        command.extend(["--dir", args.dir])
    if args.force:
        command.append("--force")
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install one or more local skills from a clean exported checkout.",
    )
    parser.add_argument(
        "--skill",
        action="append",
        help="Skill name to install. Repeat to install multiple skills.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Install every discovered skill under skills/*/SKILL.md.",
    )
    parser.add_argument("--agent", default=None, help="Pass through to gh skill install --agent.")
    parser.add_argument("--scope", default=None, help="Pass through to gh skill install --scope.")
    parser.add_argument("--dir", default=None, help="Pass through to gh skill install --dir.")
    parser.add_argument("--force", action="store_true", help="Pass through to gh skill install --force.")
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="Also include untracked files under the selected skill directories.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the clean export strategy and gh commands without executing them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validate_agent(args.agent)
    skills = resolve_skills(args)

    with tempfile.TemporaryDirectory(prefix="weex-local-skills-") as tempdir:
        clean_root = Path(tempdir) / "repo"
        export_strategy = export_repo(
            REPO_ROOT,
            clean_root,
            skills,
            include_untracked=bool(args.include_untracked),
        )
        commands = [build_install_command(clean_root, skill, args) for skill in skills]

        if args.dry_run:
            print(f"Export strategy: {export_strategy}")
            print(f"Clean checkout: {clean_root}")
            for command in commands:
                print(shlex.join(command))
            return 0

        for command in commands:
            completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
            if completed.returncode != 0:
                return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
