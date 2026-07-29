from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from support import PLUGIN_ROOT, TEST_ROOT, load_runtime


class ReviewRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()

    def test_fixed_runtime_reference_and_policy_are_bounded(self) -> None:
        review = self.runtime.load_review_runtime()
        self.assertEqual(
            review.mutable_skill_roots,
            (Path("/Users/igyeongseob/.codex/skills"),),
        )
        self.assertEqual(review.review_batch_sessions, 5)
        self.assertEqual(review.max_transcript_bytes, 2_097_152)
        self.assertEqual(review.max_transcript_records, 100)
        self.assertEqual(review.max_review_batch_bytes, 8_388_608)
        self.assertEqual(review.model_envelope_max_bytes, 131_072)
        self.assertEqual(review.catalog_max_skills, 512)
        self.assertEqual(review.catalog_frontmatter_max_bytes, 65_536)
        self.assertEqual(review.catalog_inspect_max_bytes, 65_536)
        self.assertEqual(review.catalog_export_max_bytes, 49_152)
        self.assertEqual(
            (
                review.catalog_identity_max_bytes,
                review.catalog_display_name_max_bytes,
                review.catalog_description_max_bytes,
            ),
            (272, 128, 384),
        )
        self.assertEqual(
            (
                review.policy_max_bytes,
                review.result_schema_instructions_max_bytes,
                review.claim_contract_overhead_max_bytes,
            ),
            (8_192, 8_192, 8_192),
        )
        policy = self.runtime.load_improvement_policy(review)
        self.assertLessEqual(len(policy), 8_192)
        self.assertEqual(
            self.runtime.improvement_policy_digest(policy),
            hashlib.sha256(policy).hexdigest(),
        )
        self.assertIn(b"untrusted analysis data", policy)
        self.assertIn(b"at most one candidate", policy)

    def test_static_runtime_reference_rejects_any_changed_limit(self) -> None:
        source = (
            PLUGIN_ROOT
            / "skills/skill-evolver/references/runtime.json"
        )
        payload = json.loads(source.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.json"
            payload["review_limits"]["catalog_inspect_max_bytes"] = 65_537
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)
            with mock.patch.object(
                self.runtime, "RUNTIME_REFERENCE_PATH", path
            ):
                with self.assertRaisesRegex(
                    ValueError, "invalid_review_runtime"
                ):
                    self.runtime.load_review_runtime()

    def test_policy_rejects_empty_invalid_utf8_and_overflow(self) -> None:
        review = self.runtime.load_review_runtime()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.md"
            path.write_bytes(b"")
            with mock.patch.object(self.runtime, "POLICY_PATH", path):
                with self.assertRaisesRegex(
                    ValueError, "invalid_improvement_policy"
                ):
                    self.runtime.load_improvement_policy(review)
            path.write_bytes(b"\xff")
            with mock.patch.object(self.runtime, "POLICY_PATH", path):
                with self.assertRaisesRegex(
                    ValueError, "invalid_improvement_policy"
                ):
                    self.runtime.load_improvement_policy(review)
            bounded_path = mock.MagicMock()
            bounded_reader = (
                bounded_path.open.return_value.__enter__.return_value
            )
            bounded_reader.read.return_value = b"x" * 8_193
            with mock.patch.object(
                self.runtime, "POLICY_PATH", bounded_path
            ):
                with self.assertRaisesRegex(
                    ValueError, "improvement_policy_too_large"
                ):
                    self.runtime.load_improvement_policy(review)
            bounded_path.open.assert_called_once_with("rb")
            bounded_reader.read.assert_called_once_with(
                review.policy_max_bytes + 1
            )

    def test_runtime_queue_entry_is_the_committed_pass_report(self) -> None:
        path = PLUGIN_ROOT / "docs/release-reports/runtime-queue.json"
        encoded = path.read_bytes()
        report = json.loads(encoded)
        self.assertEqual(report["decision"], "PASS")
        self.assertEqual(
            hashlib.sha256(encoded).hexdigest(),
            "c551176e5925a4c18ed54875ca28dcd089c0de2b38a873819fe219a8dcf29674",
        )


class ReviewConfigLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.sessions = self.base / "sessions"
        self.sessions.mkdir(mode=0o700)
        self.installation_path = self.runtime.initialize_runtime(
            self.base / "review-config-data",
            (self.sessions,),
            {"capture_paused": False, "exclude_roots": []},
        )
        self.installation = self.runtime.load_installation(
            self.installation_path
        )

    def test_hostile_review_caps_are_rejected_with_exact_keys(self) -> None:
        current = json.loads(
            self.installation.config_path.read_text(encoding="utf-8")
        )
        hostile_values = {
            "review_batch_sessions": 6,
            "max_transcript_bytes": 2_097_153,
            "max_transcript_records": 101,
            "max_review_batch_bytes": 8_388_609,
            "max_candidates_per_session": 2,
            "max_candidates_per_batch": 4,
        }
        for key, value in hostile_values.items():
            with self.subTest(key=key):
                self.installation.config_path.write_text(
                    json.dumps({**current, key: value}),
                    encoding="utf-8",
                )
                self.installation.config_path.chmod(0o600)
                with self.assertRaisesRegex(
                    ValueError, f"invalid_config_{key}"
                ):
                    self.runtime.load_config(self.installation)

        self.installation.config_path.write_text(
            json.dumps({**current, "review_batch_sessions": True}),
            encoding="utf-8",
        )
        self.installation.config_path.chmod(0o600)
        with self.assertRaisesRegex(
            ValueError, "invalid_config_review_batch_sessions"
        ):
            self.runtime.load_config(self.installation)

        self.installation.config_path.write_text(
            json.dumps(
                {**current, "model_envelope_max_bytes": 10**9}
            ),
            encoding="utf-8",
        )
        self.installation.config_path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "invalid_config_keys"):
            self.runtime.load_config(self.installation)
