#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class SkillSuiteEntryPointTests(unittest.TestCase):
    def test_all_skill_suites_pass_from_root_discover(self) -> None:
        completed = subprocess.run(
            [sys.executable, "tools/run_skill_tests.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
