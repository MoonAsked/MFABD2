"""Regression checks for build version generation and ordering (no network access)."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from version_rules import compare_versions, filter_valid_versions, next_version, parse_semver, sort_versions

DATE, SHA = "260927", "7973ea7a"


def mirrorchyan_channel(version):
    """MirrorChyan/uploading-action 的 auto_channel：按子串猜频道，先 alpha 后 beta/rc，都不含即 stable。"""
    if "alpha" in version:
        return "alpha"
    if "beta" in version or "rc" in version:
        return "beta"
    return "stable"


class VersionOrderingChecks(unittest.TestCase):
    def test_semver_spec_ordering(self):
        ordered = ["v1.0.0-alpha", "v1.0.0-alpha.1", "v1.0.0-alpha.beta", "v1.0.0-beta", "v1.0.0-beta.2",
                   "v1.0.0-beta.11", "v1.0.0-rc.1", "v1.0.0"]
        for older, newer in zip(ordered, ordered[1:]):
            with self.subTest(older=older, newer=newer):
                self.assertLess(compare_versions(older, newer), 0)
                self.assertGreater(compare_versions(newer, older), 0)
        self.assertEqual(sort_versions(ordered[::2] + ordered[1::2]), ordered[::-1])

    def test_legacy_same_day_betas_were_ordered_by_hash(self):
        # 2026-09-26 三个公测按发布时间排列；客户端按 SemVer 比较，结果正好全反。
        published = ["v4.4.5-beta.260926.fc834304", "v4.4.5-beta.260926.dd51f971", "v4.4.5-beta.260926.7973ea7a"]
        for earlier, later in zip(published, published[1:]):
            self.assertGreater(compare_versions(earlier, later), 0)

    def test_sequence_decides_before_date_and_hash(self):
        self.assertLess(compare_versions("v4.4.6-beta.2.261231.gfffffff", "v4.4.6-beta.3.260101.g0000000"), 0)
        self.assertLess(compare_versions("v4.4.6-beta.9.260927.gaaaaaaa", "v4.4.6-beta.10.260927.gaaaaaaa"), 0)

    def test_g_prefix_keeps_all_digit_hash_parseable(self):
        # 纯数字且以 0 开头的段在 SemVer 里非法，MFAAvalonia 会解析失败并当成「没有更新」。
        self.assertIsNone(parse_semver("v4.4.5-beta.260926.01234567"))
        tag = next_version(["v4.4.5"], "beta", DATE, "01234567")
        self.assertEqual(tag, "v4.4.6-beta.1.260927.g01234567")
        self.assertIsNotNone(parse_semver(tag))


class NextVersionChecks(unittest.TestCase):
    def test_first_release_after_formal_starts_at_one(self):
        tags = ["v4.4.4", "v4.4.5-beta.260926.7973ea7a", "v4.4.6-alpha.260822.abcdef1", "v4.4.5"]
        self.assertEqual(next_version(tags, "beta", DATE, SHA), "v4.4.6-beta.1.260927.g7973ea7a")
        self.assertEqual(next_version(tags, "alpha", DATE, SHA), "v4.4.7-alpha.1.260927.g7973ea7a")
        self.assertEqual(next_version(tags, "ci", DATE, SHA), "v4.4.5-ci.260927.7973ea7a")
        self.assertEqual(next_version([], "beta", DATE, SHA), "v0.0.1-beta.1.260927.g7973ea7a")

    def test_sequence_is_max_plus_one_within_base_and_channel(self):
        tags = ["v4.4.5", "v4.4.6-beta.1.260927.gaaaaaaa", "v4.4.6-beta.4.260927.gbbbbbbb",
                "v4.4.7-alpha.9.260926.gccccccc", "v4.4.5-beta.7.260920.gddddddd"]
        self.assertEqual(next_version(tags, "beta", DATE, SHA), "v4.4.6-beta.5.260927.g7973ea7a")
        self.assertEqual(next_version(tags, "alpha", DATE, SHA), "v4.4.7-alpha.10.260927.g7973ea7a")

    def test_refuses_tag_that_existing_same_base_tags_outrank(self):
        cases = {
            "旧式版号仍在同一基线（没先发正式版）": ["v4.4.4", "v4.4.5-beta.260926.7973ea7a"],
            "落后分支用旧工作流发了版": ["v4.4.5", "v4.4.6-beta.2.260927.gaaaaaaa", "v4.4.6-beta.260927.abcdef1"],
            "手打的短版号": ["v4.4.5", "v4.4.6-beta.3"],
        }
        for reason, tags in cases.items():
            with self.subTest(reason):
                with self.assertRaisesRegex(ValueError, "不比已有标签"):
                    next_version(tags, "beta", DATE, SHA)

    def test_rejects_malformed_inputs(self):
        for date, sha in (("26927", SHA), (DATE, "7973EA7A"), (DATE, "79735"), (DATE, "")):
            with self.subTest(date=date, sha=sha), self.assertRaises(ValueError):
                next_version(["v4.4.5"], "beta", date, sha)
        with self.assertRaises(ValueError):
            next_version(["v4.4.5"], "rc", DATE, SHA)

    def test_generated_tags_are_recognized_and_routed(self):
        tags = ["v4.4.5"]
        beta, alpha = next_version(tags, "beta", DATE, SHA), next_version(tags, "alpha", DATE, SHA)
        legacy = ["v4.4.5-beta.260926.7973ea7a", "v4.4.6-alpha.260926.7973ea7a"]
        filtered = filter_valid_versions([beta, alpha, *legacy, next_version(tags, "ci", DATE, SHA), "v4.4.6-beta.3"])
        self.assertEqual(filtered["beta"], [beta, legacy[0]])
        self.assertEqual(filtered["alpha"], [alpha, legacy[1]])
        self.assertEqual(len(filtered["ci"]), 1)
        self.assertEqual(filtered["invalid"], ["v4.4.6-beta.3"])
        self.assertEqual(mirrorchyan_channel(beta), "beta")
        self.assertEqual(mirrorchyan_channel(alpha), "alpha")


class CommandLineChecks(unittest.TestCase):
    def run_in_repo(self, tags):
        with tempfile.TemporaryDirectory() as directory:
            git = ["git", "-C", directory, "-c", "user.name=t", "-c", "user.email=t@example.com",
                   "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + directory]
            subprocess.run([*git, "init", "-q"], check=True)
            subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "init"], check=True)
            for tag in tags:
                subprocess.run([*git, "tag", tag], check=True)
            sha = subprocess.run([*git, "rev-parse", "--short", "HEAD"], check=True, capture_output=True,
                                 text=True).stdout.strip()
            result = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("version_rules.py")),
                                     "next", "beta"], cwd=directory, capture_output=True, encoding="utf-8",
                                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            return result, sha

    def test_prints_tag_from_repository_state(self):
        result, sha = self.run_in_repo(["v4.4.5", "v4.4.6-beta.2.260101.g1234567"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(), rf"^v4\.4\.6-beta\.3\.\d{{6}}\.g{re.escape(sha)}$")

    def test_fails_loudly_instead_of_printing_an_older_tag(self):
        result, _ = self.run_in_repo(["v4.4.4", "v4.4.5-beta.260926.7973ea7a"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("::error::", result.stderr)


if __name__ == "__main__":
    unittest.main()
