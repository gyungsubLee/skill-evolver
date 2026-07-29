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
        metadata_scalars = (
            b"---\nname: metadata\ndescription: Metadata safe.\n"
            b"disable-model-invocation: true\npriority: 2\n"
            b"released: 2026-07-30\n---\n"
        )
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
            (metadata_scalars, ("metadata", "Metadata safe.")),
        )
        for raw, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    self.runtime.parse_frontmatter_scalars(raw, 65_536),
                    expected,
                )
        with tempfile.TemporaryDirectory() as temporary:
            skills = Path(temporary).resolve() / "skills"
            skills.mkdir(mode=0o700)
            skill_dir = skills / "metadata"
            skill_dir.mkdir(mode=0o700)
            skill = skill_dir / "SKILL.md"
            skill.write_bytes(metadata_scalars)
            skill.chmod(0o600)
            review = replace(
                self.runtime.load_review_runtime(),
                mutable_skill_roots=(skills,),
            )
            snapshot = self.runtime.build_catalog_snapshot(review)
        self.assertEqual(
            [
                (
                    entry.identity,
                    entry.display_name,
                    entry.description,
                )
                for entry in snapshot.entries
            ],
            [
                (
                    "user-skill:metadata",
                    "metadata",
                    "Metadata safe.",
                )
            ],
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
            b"---\nname: safe\ndescription: Safe.\nextra:\n  child: value\n---\n",
            b"---\nname: safe\ndescription: Safe.\nextra: [one, two]\n---\n",
            b"---\nname: safe\ndescription: Safe.\nextra: {child: value}\n---\n",
            b"---\nname: safe\ndescription: Safe.\nextra: *alias\n---\n",
            b"---\nname: safe\ndescription: Safe.\nextra: &anchor\n---\n",
            b"---\nname: safe\ndescription: Safe.\nextra: !tag\n---\n",
            b"---\nname: safe\ndescription: Safe.\nextra: 'bad' tail'\n---\n",
            b"---\nname: safe\ndescription: Safe.\nextra: safe # hidden\n---\n",
            b"---\nname: safe\ndescription: Safe.\nextra: foo: bar\n---\n",
            b'---\nname: safe\ndescription: Safe.\nextra: ""\n---\n',
            b"---\nname: safe\ndescription: Safe.\nextra: ''\n---\n",
            b"---\nname: safe\ndescription: Safe.\nextra: - item\n---\n",
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


class FrozenTranscriptTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.sessions = self.base / "sessions"
        self.workspace = self.base / "workspace"
        self.sessions.mkdir(mode=0o700)
        self.workspace.mkdir(mode=0o700)
        self.installation_path = self.runtime.initialize_runtime(
            self.base / "data",
            (self.sessions,),
            {"capture_paused": False, "exclude_roots": []},
        )
        self.installation = self.runtime.load_installation(
            self.installation_path
        )
        self.config = self.runtime.load_config(self.installation)
        self.review = self.runtime.load_review_runtime()
        self.fixture_lines = (
            TEST_ROOT / "fixtures/review-current-layout.jsonl"
        ).read_bytes().splitlines(keepends=True)

    def capture_and_claim(
        self,
        lines: list[bytes],
        *,
        reviewed_boundary: int,
        session_id: str = "fixture-session",
        owner: str = "review-owner",
        now: float = 2_000_000_000.0,
    ):
        transcript = self.sessions / f"{session_id}.jsonl"
        transcript.write_bytes(b"".join(lines))
        payload = {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "cwd": str(self.workspace),
            "transcript_path": str(transcript),
        }
        event = self.runtime.parse_session_stop(
            json.dumps(payload).encode(),
            self.installation,
            self.config,
        )
        assert event is not None
        key = self.runtime.session_key(self.installation, session_id)
        connection = self.runtime.open_database(self.installation)
        self.runtime.upsert_session(
            connection, event, key, self.config, now
        )
        connection.execute(
            """
            UPDATE review_items SET reviewed_boundary=?
            WHERE session_key=?
            """,
            (reviewed_boundary, key),
        )
        self.runtime.claim_review_generation(
            connection, key, owner, now + 1, self.config
        )
        row = connection.execute(
            "SELECT * FROM review_items WHERE session_key=?",
            (key,),
        ).fetchone()
        frozen = self.runtime.frozen_transcript_from_row(row)
        return connection, transcript, frozen


class FrozenTranscriptLayoutTests(FrozenTranscriptTestCase):
    def test_half_open_delta_reverse_context_and_provenance(self) -> None:
        context_end = sum(len(line) for line in self.fixture_lines[:4])
        connection, transcript, frozen = self.capture_and_claim(
            self.fixture_lines[:13],
            reviewed_boundary=context_end,
        )
        try:
            with transcript.open("ab") as stream:
                stream.write(self.fixture_lines[13])
            exported = self.runtime.read_frozen_transcript(
                self.installation,
                frozen,
                self.config,
                self.review,
            )
        finally:
            connection.close()
        self.assertEqual(
            [record.text for record in exported.records],
            [
                "sanitized context record",
                "sanitized direct correction",
                "sanitized assistant context",
                "sanitized verification failure",
                "sanitized custom output",
            ],
        )
        self.assertEqual(
            [record.source_kind for record in exported.records],
            [
                "user_direct",
                "user_direct",
                "assistant",
                "tool_output",
                "tool_output",
            ],
        )
        self.assertEqual(exported.records[0].scope, "context_only")
        self.assertFalse(exported.records[0].evidence_eligible)
        self.assertTrue(
            all(
                record.scope == "delta"
                and record.evidence_eligible
                for record in exported.records[1:]
            )
        )
        canonical = self.runtime.canonical_json_bytes(
            [
                {
                    "source_kind": record.source_kind,
                    "text": record.text,
                    "evidence_eligible": record.evidence_eligible,
                    "scope": record.scope,
                }
                for record in exported.records
            ]
        )
        self.assertEqual(
            exported.canonical_records_bytes, len(canonical)
        )
        self.assertEqual(
            exported.delta_source_bytes,
            frozen.frozen_to - frozen.frozen_from,
        )
        self.assertLessEqual(
            exported.delta_source_bytes + exported.context_source_bytes,
            2_097_152,
        )
        self.assertFalse(exported.read_path_changed)
        self.assertNotIn(
            "sanitized unread suffix",
            [record.text for record in exported.records],
        )

    def test_initial_and_repeated_session_meta_use_session_hmac(self) -> None:
        context_end = sum(len(line) for line in self.fixture_lines[:4])
        connection, _, frozen = self.capture_and_claim(
            self.fixture_lines[:13],
            reviewed_boundary=context_end,
        )
        try:
            exported = self.runtime.read_frozen_transcript(
                self.installation,
                frozen,
                self.config,
                self.review,
            )
            self.assertGreater(len(exported.records), 0)
        finally:
            connection.close()

        mismatched = list(self.fixture_lines[:13])
        mismatched[0] = mismatched[0].replace(
            b"fixture-session", b"fixture-session-mismatch"
        )
        mismatched[4] = mismatched[4].replace(
            b"fixture-session", b"foreign-session"
        )
        connection, _, frozen = self.capture_and_claim(
            mismatched,
            reviewed_boundary=sum(
                len(line) for line in mismatched[:4]
            ),
            session_id="fixture-session-mismatch",
        )
        try:
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as raised:
                self.runtime.read_frozen_transcript(
                    self.installation,
                    frozen,
                    self.config,
                    self.review,
                )
            self.assertEqual(raised.exception.code, "unsupported_transcript")
            self.assertFalse(raised.exception.retryable)
        finally:
            connection.close()

        wrong_initial = list(self.fixture_lines[:13])
        wrong_initial[0] = wrong_initial[0].replace(
            b"fixture-session", b"foreign-session"
        )
        connection, _, frozen = self.capture_and_claim(
            wrong_initial,
            reviewed_boundary=0,
            session_id="fixture-session-wrong-initial",
        )
        try:
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as raised:
                self.runtime.read_frozen_transcript(
                    self.installation,
                    frozen,
                    self.config,
                    self.review,
                )
            self.assertEqual(raised.exception.code, "unsupported_transcript")
            self.assertFalse(raised.exception.retryable)
        finally:
            connection.close()

    def test_lone_surrogate_session_meta_is_typed_terminal(
        self,
    ) -> None:
        session_id = "surrogate-session-meta"
        header = (
            b'{"type":"session_meta","payload":'
            b'{"session_id":"\\ud800"}}\n'
        )
        connection, _, frozen = self.capture_and_claim(
            [header, self.fixture_lines[5]],
            reviewed_boundary=0,
            session_id=session_id,
        )
        try:
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as raised:
                self.runtime.read_frozen_transcript(
                    self.installation,
                    frozen,
                    self.config,
                    self.review,
                )
            self.assertEqual(
                raised.exception.code,
                "unsupported_transcript",
            )
            self.assertFalse(raised.exception.retryable)
        finally:
            connection.close()

    def test_lone_surrogate_export_text_is_typed_terminal(
        self,
    ) -> None:
        cases = (
            (
                "message",
                b'{"type":"response_item","payload":'
                b'{"type":"message","role":"user","content":'
                b'[{"type":"input_text","text":"\\ud800"}]}}\n',
            ),
            (
                "function_call_output",
                b'{"type":"response_item","payload":'
                b'{"type":"function_call_output",'
                b'"output":"\\ud800"}}\n',
            ),
            (
                "custom_tool_call_output",
                b'{"type":"response_item","payload":'
                b'{"type":"custom_tool_call_output",'
                b'"output":"\\ud800"}}\n',
            ),
        )
        for item_type, unsafe in cases:
            with self.subTest(item_type=item_type):
                session_id = f"surrogate-{item_type}"
                header = (
                    b'{"type":"session_meta","payload":'
                    b'{"session_id":"'
                    + session_id.encode("utf-8")
                    + b'"}}\n'
                )
                connection, _, frozen = self.capture_and_claim(
                    [header, unsafe],
                    reviewed_boundary=len(header),
                    session_id=session_id,
                )
                try:
                    with self.assertRaises(
                        self.runtime.TranscriptAdapterError
                    ) as raised:
                        self.runtime.read_frozen_transcript(
                            self.installation,
                            frozen,
                            self.config,
                            self.review,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "unsupported_transcript",
                    )
                    self.assertFalse(raised.exception.retryable)
                finally:
                    connection.close()

        record = self.runtime.TranscriptRecord(
            source_kind="user_direct",
            text=chr(0xD800),
            evidence_eligible=True,
            scope="delta",
            byte_start=0,
            byte_end=1,
        )
        with self.assertRaises(
            self.runtime.TranscriptAdapterError
        ) as canonical:
            self.runtime._canonical_transcript_records((record,))
        self.assertEqual(
            canonical.exception.code,
            "unsupported_transcript",
        )
        self.assertFalse(canonical.exception.retryable)

    def test_unknown_telemetry_is_ignored_but_unknown_evidence_fails(self) -> None:
        safe = [
            self.fixture_lines[0],
            b'{"type":"future_telemetry","payload":{"counter":9}}\n',
            self.fixture_lines[5],
        ]
        connection, _, frozen = self.capture_and_claim(
            safe, reviewed_boundary=len(safe[0])
        )
        try:
            exported = self.runtime.read_frozen_transcript(
                self.installation,
                frozen,
                self.config,
                self.review,
            )
            self.assertEqual(
                [record.text for record in exported.records],
                ["sanitized direct correction"],
            )
        finally:
            connection.close()

        hostile = [
            self.fixture_lines[0].replace(
                b"fixture-session", b"unknown-evidence-session"
            ),
            b'{"type":"future_record","payload":{"content":"unknown"}}\n',
        ]
        connection, _, frozen = self.capture_and_claim(
            hostile,
            reviewed_boundary=len(hostile[0]),
            session_id="unknown-evidence-session",
        )
        try:
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as raised:
                self.runtime.read_frozen_transcript(
                    self.installation,
                    frozen,
                    self.config,
                    self.review,
                )
            self.assertEqual(raised.exception.code, "unsupported_transcript")
            self.assertFalse(raised.exception.retryable)
        finally:
            connection.close()

    def test_evidence_shape_depth_and_node_overflow_are_terminal(
        self,
    ) -> None:
        deep_payload: object = {"leaf": 0}
        for _ in range(350):
            deep_payload = {"nested": deep_payload}
        cases = (
            ("deep-evidence-shape", deep_payload),
            (
                "wide-evidence-shape",
                {"values": list(range(4_097))},
            ),
        )
        for session_id, payload in cases:
            with self.subTest(session_id=session_id):
                header = (
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"session_id": session_id},
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                unknown = (
                    json.dumps(
                        {
                            "type": "future_record",
                            "payload": payload,
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                connection, _, frozen = self.capture_and_claim(
                    [header, unknown],
                    reviewed_boundary=len(header),
                    session_id=session_id,
                )
                try:
                    with self.assertRaises(
                        self.runtime.TranscriptAdapterError
                    ) as raised:
                        self.runtime.read_frozen_transcript(
                            self.installation,
                            frozen,
                            self.config,
                            self.review,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "unsupported_transcript",
                    )
                    self.assertFalse(raised.exception.retryable)
                finally:
                    connection.close()

    def test_context_yields_to_exact_delta_for_bytes_and_records(self) -> None:
        header = (
            b'{"type":"session_meta","payload":'
            b'{"session_id":"bounded-context"}}\n'
        )
        context = [
            (
                b'{"type":"response_item","payload":{"type":"message",'
                b'"role":"assistant","content":[{"type":"output_text",'
                + f'"text":"context-{index:03d}"'.encode()
                + b"}]}}\n"
            )
            for index in range(150)
        ]
        delta = (
            b'{"type":"response_item","payload":{"type":"message",'
            b'"role":"user","content":[{"type":"input_text",'
            b'"text":"delta-record"}]}}\n'
        )
        reviewed = len(header) + sum(len(line) for line in context)
        connection, _, frozen = self.capture_and_claim(
            [header, *context, delta],
            reviewed_boundary=reviewed,
            session_id="bounded-context",
        )
        limited = replace(
            self.config,
            max_transcript_bytes=len(delta) + len(context[-1]),
            max_transcript_records=2,
        )
        try:
            exported = self.runtime.read_frozen_transcript(
                self.installation,
                frozen,
                limited,
                self.review,
            )
        finally:
            connection.close()
        self.assertEqual(
            [record.text for record in exported.records],
            ["context-149", "delta-record"],
        )
        self.assertEqual(
            [record.scope for record in exported.records],
            ["context_only", "delta"],
        )
        self.assertLessEqual(
            exported.delta_source_bytes + exported.context_source_bytes,
            limited.max_transcript_bytes,
        )

    def test_missing_oversized_delta_is_retryable_missing(self) -> None:
        header = self.fixture_lines[0]
        delta = self.fixture_lines[5]
        connection, transcript, frozen = self.capture_and_claim(
            [header, delta],
            reviewed_boundary=len(header),
        )
        limited = replace(
            self.config, max_transcript_bytes=len(delta) - 1
        )
        transcript.unlink()
        try:
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as raised:
                self.runtime.read_frozen_transcript(
                    self.installation,
                    frozen,
                    limited,
                    self.review,
                )
            self.assertEqual(raised.exception.code, "transcript_missing")
            self.assertTrue(raised.exception.retryable)
        finally:
            connection.close()

    def test_changed_oversized_delta_is_retryable_changed(self) -> None:
        header = self.fixture_lines[0]
        delta = self.fixture_lines[5]
        connection, transcript, frozen = self.capture_and_claim(
            [header, delta],
            reviewed_boundary=len(header),
        )
        limited = replace(
            self.config, max_transcript_bytes=len(delta) - 1
        )
        initial_session_meta = self.runtime._initial_session_meta

        def change_after_initial_meta(*args) -> None:
            initial_session_meta(*args)
            os.utime(
                transcript,
                ns=(
                    frozen.locator.mtime_ns,
                    frozen.locator.mtime_ns + 1_000_000,
                ),
            )

        try:
            with mock.patch.object(
                self.runtime,
                "_initial_session_meta",
                side_effect=change_after_initial_meta,
            ):
                with self.assertRaises(
                    self.runtime.TranscriptAdapterError
                ) as raised:
                    self.runtime.read_frozen_transcript(
                        self.installation,
                        frozen,
                        limited,
                        self.review,
                    )
            self.assertEqual(raised.exception.code, "transcript_changed")
            self.assertTrue(raised.exception.retryable)
        finally:
            connection.close()

    def test_stable_oversized_delta_is_terminal_oversized(self) -> None:
        header = self.fixture_lines[0]
        delta = self.fixture_lines[5]
        connection, _, frozen = self.capture_and_claim(
            [header, delta],
            reviewed_boundary=len(header),
        )
        limited = replace(
            self.config, max_transcript_bytes=len(delta) - 1
        )
        try:
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as raised:
                self.runtime.read_frozen_transcript(
                    self.installation,
                    frozen,
                    limited,
                    self.review,
                )
            self.assertEqual(raised.exception.code, "oversized_session")
            self.assertFalse(raised.exception.retryable)
        finally:
            connection.close()
