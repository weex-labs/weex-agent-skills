#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from weex_url_policy import BaseUrlPolicyError, validate_weex_base_url  # noqa: E402


class WeexUrlPolicyTests(unittest.TestCase):
    def test_accepts_weex_com_and_weex_tech_hosts(self) -> None:
        self.assertEqual(
            validate_weex_base_url("https://api-contract.weex.com"),
            "https://api-contract.weex.com",
        )
        self.assertEqual(
            validate_weex_base_url("https://gateway.weex.tech/v1/"),
            "https://gateway.weex.tech/v1",
        )

    def test_rejects_http_and_non_weex_hosts(self) -> None:
        for raw_url in [
            "http://api-contract.weex.com",
            "https://contract.example.test",
            "https://evilweex.com",
            "https://weex.com.attacker.test",
            "https://127.0.0.1",
        ]:
            with self.subTest(raw_url=raw_url):
                with self.assertRaises(BaseUrlPolicyError):
                    validate_weex_base_url(raw_url)

    def test_rejects_credentials_query_and_fragment(self) -> None:
        for raw_url in [
            "https://user:pass@api-contract.weex.com",
            "https://api-contract.weex.com?debug=1",
            "https://api-contract.weex.com#fragment",
        ]:
            with self.subTest(raw_url=raw_url):
                with self.assertRaises(BaseUrlPolicyError):
                    validate_weex_base_url(raw_url)

    def test_formats_invalid_host_message_by_language(self) -> None:
        with self.assertRaises(BaseUrlPolicyError) as exc_info:
            validate_weex_base_url("https://contract.example.test", label="合约 Base URL")

        self.assertIn("合约 Base URL", exc_info.exception.localized_message("zh"))
        self.assertIn("weex.com", exc_info.exception.localized_message("zh"))
        self.assertIn("contract.example.test", exc_info.exception.localized_message("zh"))
        self.assertNotIn("must use", exc_info.exception.localized_message("zh"))
        self.assertIn("must use", exc_info.exception.localized_message("en"))


if __name__ == "__main__":
    unittest.main()
