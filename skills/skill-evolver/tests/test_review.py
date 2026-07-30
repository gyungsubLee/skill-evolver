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


class FrozenTranscriptFailureTests(FrozenTranscriptTestCase):
    def assert_transcript_error(
        self,
        lines: list[bytes],
        *,
        reviewed_boundary: int,
        session_id: str,
        code: str,
        retryable: bool,
        config=None,
    ) -> None:
        connection, _, frozen = self.capture_and_claim(
            lines,
            reviewed_boundary=reviewed_boundary,
            session_id=session_id,
        )
        try:
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as raised:
                self.runtime.read_frozen_transcript(
                    self.installation,
                    frozen,
                    config or self.config,
                    self.review,
                )
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(raised.exception.retryable, retryable)
        finally:
            connection.close()

    def assert_data_change_precedes_error(
        self,
        lines: list[bytes],
        *,
        reviewed_boundary: int,
        session_id: str,
        config=None,
    ) -> None:
        connection, transcript, frozen = self.capture_and_claim(
            lines,
            reviewed_boundary=reviewed_boundary,
            session_id=session_id,
        )
        real_pread = os.pread
        changed = False

        def change_after_delta_read(
            descriptor: int,
            length: int,
            offset: int,
        ) -> bytes:
            nonlocal changed
            result = real_pread(descriptor, length, offset)
            if not changed and offset == frozen.frozen_from:
                changed = True
                current = transcript.stat()
                os.utime(
                    transcript,
                    ns=(
                        current.st_atime_ns,
                        frozen.locator.mtime_ns + 1_000_000,
                    ),
                )
            return result

        try:
            with mock.patch.object(
                self.runtime.os,
                "pread",
                side_effect=change_after_delta_read,
            ):
                with self.assertRaises(
                    self.runtime.TranscriptAdapterError
                ) as raised:
                    self.runtime.read_frozen_transcript(
                        self.installation,
                        frozen,
                        config or self.config,
                        self.review,
                    )
            current = transcript.stat()
            self.assertTrue(changed)
            self.assertEqual(
                (current.st_dev, current.st_ino, current.st_size),
                (
                    frozen.locator.device,
                    frozen.locator.inode,
                    frozen.locator.size,
                ),
            )
            self.assertNotEqual(
                current.st_mtime_ns, frozen.locator.mtime_ns
            )
            self.assertEqual(raised.exception.code, "transcript_changed")
            self.assertTrue(raised.exception.retryable)
        finally:
            connection.close()

    def header(self, session_id: str) -> bytes:
        return (
            b'{"type":"session_meta","payload":{"session_id":"'
            + session_id.encode("utf-8")
            + b'"}}\n'
        )

    def message(self, text: bytes) -> bytes:
        return (
            b'{"type":"response_item","payload":{"type":"message",'
            b'"role":"user","content":[{"type":"input_text","text":"'
            + text
            + b'"}]}}\n'
        )

    def test_partial_is_retryable_but_complete_malformed_is_terminal(
        self,
    ) -> None:
        session_id = "partial-session"
        header = self.header(session_id)
        partial = self.message(b"incomplete").removesuffix(b"\n")
        self.assert_transcript_error(
            [header, partial],
            reviewed_boundary=len(header),
            session_id=session_id,
            code="transcript_partial",
            retryable=True,
        )

        session_id = "malformed-session"
        header = self.header(session_id)
        malformed = b'{"type":"response_item",bad}\n'
        self.assert_transcript_error(
            [header, malformed],
            reviewed_boundary=len(header),
            session_id=session_id,
            code="unsupported_transcript",
            retryable=False,
        )

        for label, data in (
            ("partial", partial),
            ("malformed", malformed),
        ):
            race_session = f"{label}-change-priority"
            race_header = self.header(race_session)
            with self.subTest(race=label):
                self.assert_data_change_precedes_error(
                    [race_header, data],
                    reviewed_boundary=len(race_header),
                    session_id=race_session,
                )

    def test_unknown_response_item_is_terminal(self) -> None:
        session_id = "unknown-response-item"
        header = self.header(session_id)
        unknown = (
            b'{"type":"response_item","payload":{"type":"future_message",'
            b'"content":"unsupported"}}\n'
        )
        self.assert_transcript_error(
            [header, unknown],
            reviewed_boundary=len(header),
            session_id=session_id,
            code="unsupported_transcript",
            retryable=False,
        )

    def test_compiled_byte_limit_overrides_hostile_config(self) -> None:
        def exact_message(total: int) -> bytes:
            prefix = (
                b'{"type":"response_item","payload":{"type":"message",'
                b'"role":"user","content":[{"type":"input_text","text":"'
            )
            suffix = b'"}]}}\n'
            return (
                prefix
                + b"x" * (total - len(prefix) - len(suffix))
                + suffix
            )

        exact_session = "exact-byte-session"
        exact_header = self.header(exact_session)
        exact_delta = exact_message(2_097_152)
        connection, _, frozen = self.capture_and_claim(
            [exact_header, exact_delta],
            reviewed_boundary=len(exact_header),
            session_id=exact_session,
        )
        hostile = replace(
            self.config, max_transcript_bytes=20_000_000
        )
        try:
            exported = self.runtime.read_frozen_transcript(
                self.installation,
                frozen,
                hostile,
                self.review,
            )
            self.assertEqual(exported.delta_source_bytes, 2_097_152)
            self.assertEqual(len(exported.records), 1)
        finally:
            connection.close()

        over_session = "over-byte-session"
        over_header = self.header(over_session)
        over_delta = exact_message(2_097_153)
        self.assert_transcript_error(
            [over_header, over_delta],
            reviewed_boundary=len(over_header),
            session_id=over_session,
            code="oversized_session",
            retryable=False,
            config=hostile,
        )

    def test_compiled_record_limit_accepts_100_and_rejects_101(self) -> None:
        hostile = replace(self.config, max_transcript_records=10_000)
        exact_session = "exact-record-session"
        exact_header = self.header(exact_session)
        exact_records = [
            self.message(f"record-{index:03d}".encode())
            for index in range(100)
        ]
        connection, _, frozen = self.capture_and_claim(
            [exact_header, *exact_records],
            reviewed_boundary=len(exact_header),
            session_id=exact_session,
        )
        try:
            exported = self.runtime.read_frozen_transcript(
                self.installation,
                frozen,
                hostile,
                self.review,
            )
            self.assertEqual(len(exported.records), 100)
        finally:
            connection.close()

        over_session = "over-record-session"
        over_header = self.header(over_session)
        over_records = [
            self.message(f"record-{index:03d}".encode())
            for index in range(101)
        ]
        self.assert_transcript_error(
            [over_header, *over_records],
            reviewed_boundary=len(over_header),
            session_id=over_session,
            code="oversized_session",
            retryable=False,
            config=hostile,
        )

        race_session = "over-record-change-priority"
        race_header = self.header(race_session)
        with self.subTest(race="record-limit"):
            self.assert_data_change_precedes_error(
                [race_header, *over_records],
                reviewed_boundary=len(race_header),
                session_id=race_session,
                config=hostile,
            )

    def test_error_sets_match_every_public_classification(self) -> None:
        self.assertEqual(
            self.runtime.TRANSCRIPT_RETRYABLE_CODES,
            frozenset(
                {
                    "transcript_missing",
                    "transcript_changed",
                    "transcript_partial",
                }
            ),
        )
        self.assertEqual(
            self.runtime.TRANSCRIPT_TERMINAL_CODES,
            frozenset(
                {"oversized_session", "unsupported_transcript"}
            ),
        )
        for code in self.runtime.TRANSCRIPT_RETRYABLE_CODES:
            error = self.runtime._transcript_error(code)
            self.assertEqual(error.code, code)
            self.assertTrue(error.retryable)
        for code in self.runtime.TRANSCRIPT_TERMINAL_CODES:
            error = self.runtime._transcript_error(code)
            self.assertEqual(error.code, code)
            self.assertFalse(error.retryable)


class FrozenTranscriptBoundedReadTests(FrozenTranscriptTestCase):
    def test_reverse_context_never_scans_the_historical_prefix(self) -> None:
        session_id = "bounded-pread-session"
        header = (
            b'{"type":"session_meta","payload":{"session_id":"'
            + session_id.encode()
            + b'"}}\n'
        )
        old = [
            (
                b'{"type":"event_msg","payload":{"type":"telemetry",'
                + f'"index":{index}'.encode()
                + b"}}\n"
            )
            for index in range(20_000)
        ]
        context = (
            b'{"type":"response_item","payload":{"type":"message",'
            b'"role":"assistant","content":[{"type":"output_text",'
            b'"text":"newest context"}]}}\n'
        )
        delta = (
            b'{"type":"response_item","payload":{"type":"message",'
            b'"role":"user","content":[{"type":"input_text",'
            b'"text":"exact delta"}]}}\n'
        )
        reviewed = len(header) + sum(len(line) for line in old) + len(context)
        connection, _, frozen = self.capture_and_claim(
            [header, *old, context, delta],
            reviewed_boundary=reviewed,
            session_id=session_id,
        )
        limited = replace(
            self.config,
            max_transcript_bytes=len(delta) + len(context),
        )
        calls: list[tuple[int, int]] = []
        real_pread = os.pread

        def tracked_pread(
            descriptor: int, length: int, offset: int
        ) -> bytes:
            calls.append((offset, length))
            return real_pread(descriptor, length, offset)

        try:
            with mock.patch.object(
                self.runtime.os, "pread", side_effect=tracked_pread
            ):
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
            ["newest context", "exact delta"],
        )
        self.assertTrue(
            all(length <= 65_537 for _, length in calls)
        )

        def sized_header(header_session: str, total: int) -> bytes:
            prefix = (
                b'{"type":"session_meta","payload":{"session_id":"'
                + header_session.encode()
                + b'","padding":"'
            )
            suffix = b'"}}\n'
            return (
                prefix
                + b"x" * (total - len(prefix) - len(suffix))
                + suffix
            )

        exact_session = "exact-header"
        exact_header = sized_header(exact_session, 65_536)
        connection, _, exact_frozen = self.capture_and_claim(
            [exact_header, delta],
            reviewed_boundary=len(exact_header),
            session_id=exact_session,
        )
        try:
            exact_export = self.runtime.read_frozen_transcript(
                self.installation,
                exact_frozen,
                self.config,
                self.review,
            )
            self.assertEqual(
                [record.text for record in exact_export.records],
                ["exact delta"],
            )
        finally:
            connection.close()

        over_session = "over-header"
        over_header = sized_header(over_session, 65_537)
        connection, _, over_frozen = self.capture_and_claim(
            [over_header, delta],
            reviewed_boundary=len(over_header),
            session_id=over_session,
        )
        try:
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as over:
                self.runtime.read_frozen_transcript(
                    self.installation,
                    over_frozen,
                    self.config,
                    self.review,
                )
            self.assertEqual(over.exception.code, "unsupported_transcript")
            self.assertFalse(over.exception.retryable)
        finally:
            connection.close()

        partial_session = "partial-header"
        no_newline = b"x" * 65_536
        connection, _, partial_frozen = self.capture_and_claim(
            [no_newline],
            reviewed_boundary=0,
            session_id=partial_session,
        )
        try:
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as partial:
                self.runtime.read_frozen_transcript(
                    self.installation,
                    partial_frozen,
                    self.config,
                    self.review,
                )
            self.assertEqual(partial.exception.code, "transcript_partial")
            self.assertTrue(partial.exception.retryable)
        finally:
            connection.close()

        initial_calls = [
            (offset, length)
            for offset, length in calls
            if offset == 0
        ]
        self.assertEqual(initial_calls, [(0, 4_096)])
        reverse_calls = [
            (offset, length)
            for offset, length in calls
            if (
                offset
                == frozen.frozen_from - len(context)
                and length == len(context)
            )
        ]
        self.assertEqual(
            reverse_calls,
            [(frozen.frozen_from - len(context), len(context))],
        )

    def test_transcript_adapter_digest_is_static(self) -> None:
        contract = self.runtime.transcript_adapter_contract(self.review)
        self.assertEqual(contract["text_encoding"], "strict-utf-8")
        self.assertEqual(
            contract["relocation"],
            {
                "roots": "installation-transcript-roots",
                "descriptor_relative": True,
                "current_owner_only": True,
                "same_device_inode_only": True,
                "scan_max_entries": 4_096,
                "scan_max_depth": 8,
            },
        )
        self.assertEqual(
            (
                contract["limits"]["evidence_shape_nodes"],
                contract["limits"]["evidence_shape_depth"],
            ),
            (4_096, 64),
        )
        before = self.runtime.transcript_adapter_digest(self.review)
        session_id = "digest-session"
        header = (
            b'{"type":"session_meta","payload":{"session_id":"'
            + session_id.encode()
            + b'"}}\n'
        )
        first = (
            b'{"type":"response_item","payload":{"type":"message",'
            b'"role":"user","content":[{"type":"input_text",'
            b'"text":"first content"}]}}\n'
        )
        connection, transcript, frozen = self.capture_and_claim(
            [header, first],
            reviewed_boundary=len(header),
            session_id=session_id,
        )
        try:
            self.runtime.read_frozen_transcript(
                self.installation,
                frozen,
                self.config,
                self.review,
            )
            transcript.write_bytes(
                header
                + first.replace(b"first content", b"other content")
            )
            self.assertEqual(
                self.runtime.transcript_adapter_digest(self.review),
                before,
            )
        finally:
            connection.close()


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

    def test_changed_malformed_oversized_header_is_retryable_changed(
        self,
    ) -> None:
        header = self.fixture_lines[0]
        delta = self.fixture_lines[5]
        connection, transcript, frozen = self.capture_and_claim(
            [header, delta],
            reviewed_boundary=len(header),
        )
        limited = replace(
            self.config, max_transcript_bytes=len(delta) - 1
        )
        original_pread = self.runtime.os.pread
        changed = False

        def change_before_first_pread(
            descriptor: int,
            length: int,
            offset: int,
        ) -> bytes:
            nonlocal changed
            if not changed:
                changed = True
                with transcript.open("r+b", buffering=0) as stream:
                    stream.write(b"!")
                    os.fsync(stream.fileno())
                current = transcript.stat()
                os.utime(
                    transcript,
                    ns=(
                        current.st_atime_ns,
                        frozen.locator.mtime_ns + 1_000_000,
                    ),
                )
            return original_pread(descriptor, length, offset)

        try:
            with mock.patch.object(
                self.runtime.os,
                "pread",
                side_effect=change_before_first_pread,
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
            current = transcript.stat()
            self.assertEqual(
                (current.st_dev, current.st_ino, current.st_size),
                (
                    frozen.locator.device,
                    frozen.locator.inode,
                    frozen.locator.size,
                ),
            )
            self.assertNotEqual(
                current.st_mtime_ns, frozen.locator.mtime_ns
            )
            self.assertEqual(raised.exception.code, "transcript_changed")
            self.assertTrue(raised.exception.retryable)
        finally:
            connection.close()

    def test_stable_malformed_oversized_header_is_terminal_unsupported(
        self,
    ) -> None:
        header = b"!" + self.fixture_lines[0][1:]
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
            self.assertEqual(
                raised.exception.code, "unsupported_transcript"
            )
            self.assertFalse(raised.exception.retryable)
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


class FrozenTranscriptIdentityTests(FrozenTranscriptTestCase):
    def test_same_inode_relocation_is_accepted_but_replacement_is_changed(
        self,
    ) -> None:
        lines = [self.fixture_lines[0], self.fixture_lines[5]]
        connection, transcript, frozen = self.capture_and_claim(
            lines,
            reviewed_boundary=len(lines[0]),
        )
        relocated = self.sessions / "relocated"
        relocated.mkdir(mode=0o700)
        moved = relocated / "moved.jsonl"
        transcript.rename(moved)
        try:
            exported = self.runtime.read_frozen_transcript(
                self.installation,
                frozen,
                self.config,
                self.review,
            )
            self.assertTrue(exported.read_path_changed)
            self.assertEqual(
                [record.text for record in exported.records],
                ["sanitized direct correction"],
            )
            self.assertEqual(frozen.locator.path, transcript)
            self.assertEqual(
                (moved.stat().st_dev, moved.stat().st_ino),
                (frozen.locator.device, frozen.locator.inode),
            )
        finally:
            connection.close()

        replacement_session = "replacement-session"
        replacement_lines = [
            self.fixture_lines[0].replace(
                b"fixture-session", replacement_session.encode()
            ),
            self.fixture_lines[5],
        ]
        connection, transcript, frozen = self.capture_and_claim(
            replacement_lines,
            reviewed_boundary=len(replacement_lines[0]),
            session_id=replacement_session,
        )
        preserved = self.sessions / "preserved-original-inode.jsonl"
        transcript.rename(preserved)
        transcript.write_bytes(b"".join(replacement_lines))
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
            self.assertEqual(raised.exception.code, "transcript_changed")
            self.assertTrue(raised.exception.retryable)
        finally:
            connection.close()

    def test_relocation_directory_swap_cannot_read_outside(self) -> None:
        session_id = "relocation-directory-swap"
        lines = [
            self.fixture_lines[0].replace(
                b"fixture-session", session_id.encode()
            ),
            self.fixture_lines[5],
        ]
        connection, transcript, frozen = self.capture_and_claim(
            lines,
            reviewed_boundary=len(lines[0]),
            session_id=session_id,
        )
        relocated = self.sessions / "relocated-swap"
        relocated.mkdir(mode=0o700)
        moved = relocated / "moved.jsonl"
        transcript.rename(moved)
        outside = self.base / "outside-relocation"
        outside.mkdir(mode=0o700)
        (outside / "moved.jsonl").write_bytes(
            lines[0]
            + lines[1].replace(
                b"sanitized direct correction",
                b"outside sentinel",
            )
        )
        preserved = self.sessions / "pinned-relocated-swap"
        real_open = os.open
        swapped = False

        def swap_before_final_open(
            name, flags, *args, **kwargs
        ):
            nonlocal swapped
            if (
                name == "moved.jsonl"
                and kwargs.get("dir_fd") is not None
                and not swapped
            ):
                relocated.rename(preserved)
                relocated.symlink_to(
                    outside, target_is_directory=True
                )
                swapped = True
            return real_open(name, flags, *args, **kwargs)

        try:
            with mock.patch.object(
                self.runtime.os,
                "open",
                side_effect=swap_before_final_open,
            ):
                exported = self.runtime.read_frozen_transcript(
                    self.installation,
                    frozen,
                    self.config,
                    self.review,
                )
            self.assertTrue(swapped)
            self.assertTrue(exported.read_path_changed)
            self.assertEqual(
                [record.text for record in exported.records],
                ["sanitized direct correction"],
            )
            self.assertNotIn(
                "outside sentinel",
                [record.text for record in exported.records],
            )
        finally:
            connection.close()

    def test_frozen_locator_digest_includes_claim_time_path_and_stat(
        self,
    ) -> None:
        lines = [self.fixture_lines[0], self.fixture_lines[5]]
        connection, _, frozen = self.capture_and_claim(
            lines,
            reviewed_boundary=len(lines[0]),
        )
        try:
            payload = self.runtime.transcript_locator_payload(
                frozen.locator
            )
            self.assertEqual(
                set(payload),
                {"path", "size", "mtime_ns", "device", "inode"},
            )
            self.assertEqual(
                self.runtime.transcript_locator_digest(frozen.locator),
                self.runtime.sha256_json(payload),
            )
            changed_path = replace(
                frozen.locator,
                path=frozen.locator.path.with_name("relocated.jsonl"),
            )
            self.assertNotEqual(
                self.runtime.transcript_locator_digest(frozen.locator),
                self.runtime.transcript_locator_digest(changed_path),
            )
        finally:
            connection.close()

    def test_missing_and_during_read_change_are_retryable(self) -> None:
        lines = [self.fixture_lines[0], self.fixture_lines[5]]
        connection, transcript, frozen = self.capture_and_claim(
            lines,
            reviewed_boundary=len(lines[0]),
        )
        transcript.unlink()
        try:
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as missing:
                self.runtime.read_frozen_transcript(
                    self.installation,
                    frozen,
                    self.config,
                    self.review,
                )
            self.assertEqual(missing.exception.code, "transcript_missing")
            self.assertTrue(missing.exception.retryable)
            for index in range(3):
                (self.sessions / f"junk-{index}").write_bytes(b"x")
            with mock.patch.object(
                self.runtime,
                "TRANSCRIPT_RELOCATION_SCAN_MAX_ENTRIES",
                2,
            ):
                with self.assertRaises(
                    self.runtime.TranscriptAdapterError
                ) as saturated:
                    self.runtime.read_frozen_transcript(
                        self.installation,
                        frozen,
                        self.config,
                        self.review,
                    )
            self.assertEqual(
                saturated.exception.code, "transcript_changed"
            )
            self.assertTrue(saturated.exception.retryable)
        finally:
            connection.close()

        exact_root = self.base / "exact-scan-root"
        exact_root.mkdir(mode=0o700)
        exact_installation = replace(
            self.installation,
            transcript_roots=(exact_root,),
        )
        for index in range(4_096):
            (exact_root / f"entry-{index:04d}").write_bytes(b"x")
        with self.subTest(relocation_entries=4_096):
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as exact_scan:
                self.runtime.read_frozen_transcript(
                    exact_installation,
                    frozen,
                    self.config,
                    self.review,
                )
            self.assertEqual(
                exact_scan.exception.code, "transcript_missing"
            )
            self.assertTrue(exact_scan.exception.retryable)
        (exact_root / "entry-4096").write_bytes(b"x")
        with self.subTest(relocation_entries=4_097):
            with self.assertRaises(
                self.runtime.TranscriptAdapterError
            ) as saturated_scan:
                self.runtime.read_frozen_transcript(
                    exact_installation,
                    frozen,
                    self.config,
                    self.review,
                )
            self.assertEqual(
                saturated_scan.exception.code, "transcript_changed"
            )
            self.assertTrue(saturated_scan.exception.retryable)

        depth_session = "relocation-depth-boundary"
        depth_header = lines[0].replace(
            b"fixture-session", depth_session.encode()
        )
        connection, transcript, depth_frozen = self.capture_and_claim(
            [depth_header, lines[1]],
            reviewed_boundary=len(depth_header),
            session_id=depth_session,
        )
        depth_parent = self.sessions
        for depth in range(1, 9):
            depth_parent /= f"depth-{depth}"
            depth_parent.mkdir(mode=0o700)
        depth_eight = depth_parent / "moved.jsonl"
        transcript.rename(depth_eight)
        try:
            with self.subTest(relocation_depth=8):
                exported = self.runtime.read_frozen_transcript(
                    self.installation,
                    depth_frozen,
                    self.config,
                    self.review,
                )
                self.assertTrue(exported.read_path_changed)
            depth_nine = depth_parent / "depth-9"
            depth_nine.mkdir(mode=0o700)
            depth_eight.rename(depth_nine / "moved.jsonl")
            with self.subTest(relocation_depth=9):
                with self.assertRaises(
                    self.runtime.TranscriptAdapterError
                ) as too_deep:
                    self.runtime.read_frozen_transcript(
                        self.installation,
                        depth_frozen,
                        self.config,
                        self.review,
                    )
                self.assertEqual(
                    too_deep.exception.code, "transcript_missing"
                )
                self.assertTrue(too_deep.exception.retryable)
        finally:
            connection.close()

        connection, transcript, frozen = self.capture_and_claim(
            [
                lines[0].replace(
                    b"fixture-session", b"changing-during-read"
                ),
                lines[1],
            ],
            reviewed_boundary=len(
                lines[0].replace(
                    b"fixture-session", b"changing-during-read"
                )
            ),
            session_id="changing-during-read",
        )
        real_pread = os.pread
        changed = False

        def append_during_read(
            descriptor: int, length: int, offset: int
        ) -> bytes:
            nonlocal changed
            result = real_pread(descriptor, length, offset)
            if not changed:
                changed = True
                with transcript.open("ab") as stream:
                    stream.write(b'{"type":"event_msg","payload":{}}\n')
            return result

        try:
            with mock.patch.object(
                self.runtime.os,
                "pread",
                side_effect=append_during_read,
            ):
                with self.assertRaises(
                    self.runtime.TranscriptAdapterError
                ) as changing:
                    self.runtime.read_frozen_transcript(
                        self.installation,
                        frozen,
                        self.config,
                        self.review,
                    )
            self.assertEqual(
                changing.exception.code, "transcript_changed"
            )
            self.assertTrue(changing.exception.retryable)
        finally:
            connection.close()

        io_session = "read-io-error"
        io_header = lines[0].replace(
            b"fixture-session", io_session.encode()
        )
        io_context = self.fixture_lines[6]
        io_delta = lines[1]
        connection, _, frozen = self.capture_and_claim(
            [io_header, io_context, io_delta],
            reviewed_boundary=len(io_header) + len(io_context),
            session_id=io_session,
        )
        limited = replace(
            self.config,
            max_transcript_bytes=len(io_context) + len(io_delta),
        )
        real_pread = os.pread
        stable_stat = self.runtime._stable_frozen_descriptor_stat
        close_descriptor = self.runtime._close_transcript_descriptor
        try:
            offsets = {
                "header": 0,
                "delta": frozen.frozen_from,
                "context": len(io_header),
            }
            for error_type in (
                OSError,
                InterruptedError,
                RuntimeError,
            ):
                for phase, target_offset in offsets.items():
                    def fail_selected_pread(
                        descriptor: int,
                        length: int,
                        offset: int,
                        *,
                        target: int = target_offset,
                        exception_type=error_type,
                    ) -> bytes:
                        if offset == target:
                            raise exception_type(
                                "synthetic pread failure"
                            )
                        return real_pread(descriptor, length, offset)

                    with self.subTest(
                        operation="pread",
                        phase=phase,
                        error=error_type.__name__,
                    ):
                        with mock.patch.object(
                            self.runtime.os,
                            "pread",
                            side_effect=fail_selected_pread,
                        ), mock.patch.object(
                            self.runtime,
                            "_stable_frozen_descriptor_stat",
                            wraps=stable_stat,
                        ) as checked_stat, mock.patch.object(
                            self.runtime,
                            "_close_transcript_descriptor",
                            wraps=close_descriptor,
                        ) as close:
                            with self.assertRaises(
                                self.runtime.TranscriptAdapterError
                            ) as raised:
                                self.runtime.read_frozen_transcript(
                                    self.installation,
                                    frozen,
                                    limited,
                                    self.review,
                                )
                        self.assertEqual(
                            raised.exception.code,
                            "transcript_changed",
                        )
                        self.assertTrue(raised.exception.retryable)
                        self.assertEqual(
                            checked_stat.call_count,
                            2 if phase == "header" else 3,
                        )
                        close.assert_called_once()

            real_fstat = os.fstat
            for error_type in (
                OSError,
                InterruptedError,
                RuntimeError,
            ):
                for phase, failure_call in (
                    ("initial", 2),
                    ("final", 4),
                ):
                    calls = 0

                    def fail_selected_fstat(
                        descriptor: int,
                        *,
                        target: int = failure_call,
                        exception_type=error_type,
                    ):
                        nonlocal calls
                        calls += 1
                        if calls == target:
                            raise exception_type(
                                "synthetic fstat failure"
                            )
                        return real_fstat(descriptor)

                    with self.subTest(
                        operation="fstat",
                        phase=phase,
                        error=error_type.__name__,
                    ):
                        with mock.patch.object(
                            self.runtime.os,
                            "fstat",
                            side_effect=fail_selected_fstat,
                        ), mock.patch.object(
                            self.runtime,
                            "_close_transcript_descriptor",
                            wraps=close_descriptor,
                        ) as close:
                            with self.assertRaises(
                                self.runtime.TranscriptAdapterError
                            ) as raised:
                                self.runtime.read_frozen_transcript(
                                    self.installation,
                                    frozen,
                                    limited,
                                    self.review,
                                )
                        self.assertEqual(
                            raised.exception.code,
                            "transcript_changed",
                        )
                        self.assertTrue(raised.exception.retryable)
                        self.assertEqual(calls, failure_call)
                        close.assert_called_once()

            with self.subTest(operation="direct-open-runtime-error"):
                with mock.patch.object(
                    self.runtime.os,
                    "open",
                    side_effect=RuntimeError("synthetic open failure"),
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
                self.assertEqual(
                    raised.exception.code, "transcript_changed"
                )
                self.assertTrue(raised.exception.retryable)

            with self.subTest(operation="best-effort-close-runtime-error"):
                with mock.patch.object(
                    self.runtime.os,
                    "close",
                    side_effect=RuntimeError("synthetic close failure"),
                ):
                    self.runtime._close_transcript_descriptor(-1)
        finally:
            connection.close()
