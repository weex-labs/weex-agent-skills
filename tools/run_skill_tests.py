#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIRS = (
    ROOT / "skills" / "weex-trader-skill",
    ROOT / "skills" / "weex-analysis-skill",
    ROOT / "skills" / "weex-monitor-skill",
)


def run_skill_tests(skill_dir: Path) -> int:
    tests_dir = skill_dir / "tests"
    if not tests_dir.exists():
        return 0
    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        str(tests_dir),
        "-t",
        str(skill_dir),
        "-q",
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    return completed.returncode


def main() -> int:
    failed: list[str] = []
    for skill_dir in SKILL_DIRS:
        if not skill_dir.exists():
            continue
        print(f"==> Running tests for {skill_dir.name}", flush=True)
        if run_skill_tests(skill_dir) != 0:
            failed.append(skill_dir.name)

    if failed:
        print(f"Test failures: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
