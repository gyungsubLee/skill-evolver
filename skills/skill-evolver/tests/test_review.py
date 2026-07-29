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
            hostile_values = (
                (
                    "changed_limit",
                    ("review_limits", "catalog_inspect_max_bytes"),
                    65_537,
                ),
                ("bool_schema", ("schema_version",), True),
                (
                    "bool_limit",
                    ("review_limits", "max_candidates_per_session"),
                    True,
                ),
                (
                    "float_limit",
                    ("review_limits", "review_batch_sessions"),
                    5.0,
                ),
            )
            for label, keys, value in hostile_values:
                hostile = json.loads(json.dumps(payload))
                target = hostile
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                path.write_text(json.dumps(hostile), encoding="utf-8")
                path.chmod(0o600)
                with self.subTest(label=label):
                    with mock.patch.object(
                        self.runtime, "RUNTIME_REFERENCE_PATH", path
                    ):
                        with self.assertRaisesRegex(
                            ValueError, "invalid_review_runtime"
                        ):
                            self.runtime.load_review_runtime()

            for label, encoded in (
                ("invalid_utf8", b"\xff"),
                ("invalid_json", b"{"),
                ("not_object", b"[]"),
            ):
                path.write_bytes(encoded)
                with self.subTest(label=label):
                    with mock.patch.object(
                        self.runtime, "RUNTIME_REFERENCE_PATH", path
                    ):
                        with self.assertRaisesRegex(
                            ValueError, "invalid_review_runtime"
                        ):
                            self.runtime.load_review_runtime()

            self.assertEqual(
                self.runtime.RUNTIME_REFERENCE_MAX_BYTES,
                8_192,
            )
            bounded_path = mock.MagicMock()
            bounded_reader = (
                bounded_path.open.return_value.__enter__.return_value
            )
            bounded_reader.read.return_value = b"\xff" * 8_193
            with mock.patch.object(
                self.runtime, "RUNTIME_REFERENCE_PATH", bounded_path
            ):
                with self.assertRaisesRegex(
                    ValueError, "review_runtime_too_large"
                ):
                    self.runtime.load_review_runtime()
            bounded_path.open.assert_called_once_with("rb")
            bounded_reader.read.assert_called_once_with(
                self.runtime.RUNTIME_REFERENCE_MAX_BYTES + 1
            )

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


class FrontmatterScalarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()

    def test_plain_quoted_and_folded_scalars(self) -> None:
        cases = (
            (
                b"---\nname: plain\ndescription: Plain description.\n---\n",
                ("plain", "Plain description."),
            ),
            (
                b'---\nname: "quoted"\ndescription: "Quoted: safe"\n---\n',
                ("quoted", "Quoted: safe"),
            ),
            (
                b"---\nname: folded\ndescription: >\n  first line\n"
                b"  second line\nlicense: local\n---\n",
                ("folded", "first line second line"),
            ),
        )
        for raw, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    self.runtime.parse_frontmatter_scalars(raw, 65_536),
                    expected,
                )

    def test_parser_rejects_non_scalar_duplicate_and_overflow(self) -> None:
        invalid = (
            b"---\nname: nested\ndescription:\n  child: value\n---\n",
            b"---\nname: alias\ndescription: *external\n---\n",
            b"---\nname: quote\ndescription: 'ok' trailing'\n---\n",
            b"---\nname: quote\ndescription: 'safe' # injected'\n---\n",
            b"---\nname: comment\ndescription: safe # hidden\n---\n",
            b"---\nname: null\ndescription: null\n---\n",
            b"---\nname: boolean\ndescription: TRUE\n---\n",
            b"---\nname: yes-no\ndescription: No\n---\n",
            b"---\nname: on-off\ndescription: ON\n---\n",
            b"---\nname: number\ndescription: 123\n---\n",
            b"---\nname: date\ndescription: 2026-07-30\n---\n",
            b"---\nname: sequence\ndescription: - item\n---\n",
            b"---\nname: special\ndescription: .inf\n---\n",
            b"---\nname: mapping\ndescription: foo: bar\n---\n",
            b"---\nname: mapping\ndescription: foo:\n---\n",
            b"---\nname: one\nname: two\ndescription: duplicate\n---\n",
            b"---\nname: missing-description\n---\n",
            b"name: no-frontmatter\ndescription: invalid\n",
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(
                    ValueError, "invalid_skill_frontmatter"
                ):
                    self.runtime.parse_frontmatter_scalars(raw, 65_536)
        with self.assertRaisesRegex(
            ValueError, "skill_frontmatter_too_large"
        ):
            self.runtime.parse_frontmatter_scalars(
                b"---\nname: x\ndescription: " + b"x" * 65_536,
                65_536,
            )


class TrustedCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.skills = self.base / "skills"
        self.skills.mkdir(mode=0o700)
        self.review = replace(
            self.runtime.load_review_runtime(),
            mutable_skill_roots=(self.skills,),
        )

    def write_skill(
        self,
        directory_name: str,
        display_name: str,
        description: str,
        body: bytes = b"# Body\n",
    ) -> Path:
        directory = self.skills / directory_name
        directory.mkdir(mode=0o700)
        encoded = (
            b"---\nname: "
            + display_name.encode("utf-8")
            + b"\ndescription: "
            + description.encode("utf-8")
            + b"\n---\n"
            + body
        )
        skill = directory / "SKILL.md"
        skill.write_bytes(encoded)
        skill.chmod(0o600)
        return skill

    def test_snapshot_exports_only_bounded_public_fields(self) -> None:
        self.write_skill("beta", "Beta", "Second safe skill.")
        self.write_skill("alpha", "Alpha", "First safe skill.")
        snapshot = self.runtime.build_catalog_snapshot(self.review)
        export = self.runtime.catalog_export_payload(snapshot)
        self.assertEqual(
            export,
            [
                {
                    "identity": "user-skill:alpha",
                    "display_name": "Alpha",
                    "description": "First safe skill.",
                },
                {
                    "identity": "user-skill:beta",
                    "display_name": "Beta",
                    "description": "Second safe skill.",
                },
            ],
        )
        self.assertEqual(
            snapshot.export_bytes,
            self.runtime.canonical_json_bytes(export),
        )
        self.assertNotIn(str(self.skills).encode(), snapshot.export_bytes)
        self.assertNotIn(b"skill_sha256", snapshot.export_bytes)
        target = self.runtime.resolve_catalog_target(
            snapshot, "user-skill:alpha"
        )
        self.assertEqual(target.skill_dir, self.skills / "alpha")
        self.assertEqual(
            self.runtime.inspect_catalog_target(
                self.review, snapshot, target.identity
            ),
            (target.skill_dir / "SKILL.md").read_bytes(),
        )

    def test_static_adapter_digest_and_dynamic_snapshot_are_separate(self) -> None:
        self.write_skill("alpha", "Alpha", "First description.")
        static_before = self.runtime.catalog_adapter_digest(self.review)
        first = self.runtime.build_catalog_snapshot(self.review)
        skill = self.skills / "alpha/SKILL.md"
        skill.write_bytes(
            b"---\nname: Alpha\ndescription: Changed description.\n---\n"
        )
        skill.chmod(0o600)
        second = self.runtime.build_catalog_snapshot(self.review)
        self.assertEqual(
            self.runtime.catalog_adapter_digest(self.review),
            static_before,
        )
        self.assertNotEqual(first.snapshot_digest, second.snapshot_digest)
        with self.assertRaisesRegex(
            self.runtime.CatalogAdapterError, "catalog_target_changed"
        ):
            self.runtime.inspect_catalog_target(
                self.review, first, "user-skill:alpha"
            )

    def test_symlink_owner_and_mode_checks_fail_closed_per_entry(self) -> None:
        safe = self.write_skill("safe", "Safe", "Safe entry.")
        outside = self.base / "outside"
        outside.mkdir(mode=0o700)
        (outside / "SKILL.md").write_bytes(
            b"---\nname: Outside\ndescription: Outside entry.\n---\n"
        )
        linked = self.skills / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        group_dir = self.write_skill(
            "group-dir", "GroupDir", "Unsafe directory."
        ).parent
        group_dir.chmod(0o720)
        self.addCleanup(group_dir.chmod, 0o700)
        group_file = self.write_skill(
            "group-file", "GroupFile", "Unsafe file."
        )
        group_file.chmod(0o620)
        self.addCleanup(group_file.chmod, 0o600)
        world_file = self.write_skill(
            "world-file", "WorldFile", "Unsafe file."
        )
        world_file.chmod(0o602)
        self.addCleanup(world_file.chmod, 0o600)
        wrong_owner = self.write_skill(
            "wrong-owner", "WrongOwner", "Unsafe owner."
        )
        wrong_inode = wrong_owner.stat().st_ino
        real_fstat = os.fstat

        def fstat_with_wrong_owner(descriptor: int):
            info = real_fstat(descriptor)
            if info.st_ino == wrong_inode:
                values = list(info)
                values[4] = info.st_uid + 1
                return os.stat_result(values)
            return info

        with mock.patch.object(
            self.runtime.os, "fstat", side_effect=fstat_with_wrong_owner
        ):
            snapshot = self.runtime.build_catalog_snapshot(self.review)
        self.assertEqual(
            [entry.identity for entry in snapshot.entries],
            ["user-skill:safe"],
        )
        self.assertEqual(snapshot.rejected_count, 5)
        self.assertEqual(safe.read_bytes(), (safe.parent / "SKILL.md").read_bytes())

        self.skills.chmod(0o720)
        self.addCleanup(self.skills.chmod, 0o700)
        with self.assertRaisesRegex(
            self.runtime.CatalogAdapterError, "catalog_root_invalid"
        ):
            self.runtime.build_catalog_snapshot(self.review)

    def test_exclusions_inventory_and_export_caps_are_exact(self) -> None:
        self.write_skill(".system", "Managed", "Managed entry.")
        self.write_skill(
            "skill-evolver", "Self", "Self operation is forbidden."
        )
        self.write_skill("safe", "Safe", "Safe entry.")
        snapshot = self.runtime.build_catalog_snapshot(self.review)
        self.assertEqual(
            [entry.identity for entry in snapshot.entries],
            ["user-skill:safe"],
        )

        tiny_export = replace(self.review, catalog_export_max_bytes=10)
        with self.assertRaisesRegex(
            self.runtime.CatalogAdapterError, "catalog_export_too_large"
        ):
            self.runtime.build_catalog_snapshot(tiny_export)

        for index in range(512):
            path = self.skills / f"junk-{index:03d}"
            path.mkdir(mode=0o700)
        with self.assertRaisesRegex(
            self.runtime.CatalogAdapterError,
            "catalog_inventory_saturated",
        ):
            self.runtime.build_catalog_snapshot(self.review)

    def test_field_and_inspection_byte_limits_are_utf8_exact(self) -> None:
        exact_body = b"x" * (
            65_536
            - len(b"---\nname: Exact\ndescription: Exact bytes.\n---\n")
        )
        exact = self.write_skill(
            "exact", "Exact", "Exact bytes.", exact_body
        )
        snapshot = self.runtime.build_catalog_snapshot(self.review)
        self.assertEqual(exact.stat().st_size, 65_536)
        self.assertEqual(
            len(
                self.runtime.inspect_catalog_target(
                    self.review, snapshot, "user-skill:exact"
                )
            ),
            65_536,
        )
        with exact.open("ab") as stream:
            stream.write(b"x")
        with self.assertRaisesRegex(
            self.runtime.CatalogAdapterError,
            "catalog_inspect_too_large",
        ):
            self.runtime.inspect_catalog_target(
                self.review, snapshot, "user-skill:exact"
            )

        long_description = "가" * 129
        self.write_skill(
            "long-description", "Long", long_description
        )
        rebuilt = self.runtime.build_catalog_snapshot(self.review)
        self.assertNotIn(
            "user-skill:long-description",
            [entry.identity for entry in rebuilt.entries],
        )
