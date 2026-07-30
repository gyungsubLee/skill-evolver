from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
from unittest import mock

from test_review import CandidateBatchFixture


class ProspectiveQualityEpochTests(CandidateBatchFixture):
    def open_epoch(
        self, now: float = 2_000_000_100.0
    ) -> dict[str, object]:
        return self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now,
            predecessor=None,
        )

    def terminalize_invalid_epoch(
        self,
        epoch_id: str,
        now: float,
    ) -> str:
        epoch = self.runtime.load_quality_epoch(
            self.connection, epoch_id
        )
        body = {
            "schema_version": 1,
            "epoch_id": epoch_id,
            "decision": "INVALID",
        }
        digest = self.runtime.sha256_json(body)
        terminal = {
            **epoch,
            "state": "invalid",
            "invalid_reason": "quality_provenance_drift",
            "invalidated_at": self.runtime.iso_utc(now),
            "terminal": {
                "body": body,
                "report_digest": digest,
            },
            "ended_at": self.runtime.iso_utc(now),
        }
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (
                self.runtime.canonical_json_bytes(terminal).decode(
                    "utf-8"
                ),
                self.runtime.quality_epoch_key(epoch_id),
            ),
        )
        self.connection.execute(
            "DELETE FROM metadata WHERE key=?",
            (self.runtime.QUALITY_ACTIVE_EPOCH_KEY,),
        )
        return digest

    def test_open_is_prospective_and_pins_provenance(self) -> None:
        now = 2_000_000_000.0
        old_claim = self.claim(1, now)
        old_path = self.write_result(
            old_claim, self.result_payload(old_claim)
        )
        self.commit(old_claim, old_path, now + 1)

        opened = self.open_epoch(now + 2)
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        pointer = self.connection.execute(
            "SELECT value FROM metadata WHERE key='quality.active_epoch'"
        ).fetchone()

        self.assertEqual(opened["epoch_id"], "Q-001")
        self.assertEqual(epoch["state"], "collecting")
        self.assertEqual(
            epoch["first_batch_id"], int(old_claim["batch_id"]) + 1
        )
        self.assertEqual(pointer["value"], "Q-001")
        self.assertIsNone(epoch["predecessor"])
        for name in (
            "phase4_report_digest",
            "runtime_digest",
            "quality_contract_digest",
            "identity_key_fingerprint",
            "policy_digest",
            "transcript_adapter_digest",
            "catalog_adapter_digest",
        ):
            self.assertRegex(epoch[name], r"\A[0-9a-f]{64}\Z")
        self.assertEqual(
            epoch["phase4_report_digest"],
            hashlib.sha256(
                self.runtime.PHASE4_RELEASE_REPORT_PATH.read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            epoch["runtime_digest"],
            hashlib.sha256(
                self.runtime.Path(
                    self.runtime.__file__
                ).resolve().read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            epoch["quality_contract_digest"],
            self.runtime.quality_contract_digest(),
        )
        self.assertEqual(
            epoch["identity_key_fingerprint"],
            hashlib.sha256(
                self.runtime.load_quality_identity_key(
                    self.installation
                )
            ).hexdigest(),
        )
        self.assertEqual(
            epoch["policy_digest"],
            self.runtime.improvement_policy_digest(self.policy),
        )
        self.assertEqual(
            epoch["transcript_adapter_digest"],
            self.runtime.transcript_adapter_digest(
                self.review_runtime
            ),
        )
        self.assertEqual(
            epoch["catalog_adapter_digest"],
            self.runtime.catalog_adapter_digest(
                self.review_runtime
            ),
        )

    def test_quality_open_command_is_the_only_epoch_creation_surface(
        self,
    ) -> None:
        now = 2_000_000_000.0
        args = self.runtime.build_parser().parse_args(
            [
                "quality-open",
                "--installation",
                str(
                    self.installation.data_root
                    / "installation.json"
                ),
            ]
        )
        output = io.BytesIO()
        stdout = mock.Mock()
        stdout.buffer = output

        original_cwd = os.getcwd()
        try:
            os.chdir(self.base)
            with mock.patch.object(
                self.runtime.time, "time", return_value=now
            ), mock.patch.object(self.runtime.sys, "stdout", stdout):
                self.assertEqual(args.handler(args), 0)
        finally:
            os.chdir(original_cwd)

        self.assertEqual(
            json.loads(output.getvalue())["epoch_id"], "Q-001"
        )
        self.assertEqual(
            self.runtime.load_quality_epoch(
                self.connection, "Q-001"
            )["state"],
            "collecting",
        )

    def test_failed_or_invalid_restart_requires_exact_changed_provenance(
        self,
    ) -> None:
        now = 2_000_000_000.0
        first = self.open_epoch(now)
        digest = self.terminalize_invalid_epoch("Q-001", now + 1)
        predecessor = f"Q-001@{digest}"

        with self.assertRaisesRegex(
            ValueError, "quality_predecessor_provenance_unchanged"
        ):
            self.runtime.open_quality_epoch(
                self.connection,
                self.installation,
                now + 2,
                predecessor=predecessor,
            )
        current = self.runtime.current_quality_provenance(
            self.installation
        )
        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value={
                **current,
                "phase4_report_digest": "0" * 64,
            },
        ), self.assertRaisesRegex(
            ValueError, "quality_predecessor_provenance_unchanged"
        ):
            self.runtime.open_quality_epoch(
                self.connection,
                self.installation,
                now + 2,
                predecessor=predecessor,
            )
        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value={**current, "runtime_digest": "1" * 64},
        ):
            second = self.runtime.open_quality_epoch(
                self.connection,
                self.installation,
                now + 2,
                predecessor=predecessor,
            )

        self.assertEqual(first["epoch_id"], "Q-001")
        self.assertEqual(second["epoch_id"], "Q-002")
        self.assertEqual(
            second["predecessor"]["terminal_report_digest"], digest
        )

    def test_lineage_is_sequential_and_capped_at_eight_epochs(
        self,
    ) -> None:
        now = 2_000_000_000.0
        current = self.runtime.current_quality_provenance(
            self.installation
        )
        self.open_epoch(now)
        digest = self.terminalize_invalid_epoch("Q-001", now + 1)
        for number in range(2, 9):
            epoch_id = f"Q-{number:03d}"
            with mock.patch.object(
                self.runtime,
                "current_quality_provenance",
                return_value={
                    **current,
                    "runtime_digest": f"{number:x}" * 64,
                },
            ):
                opened = self.runtime.open_quality_epoch(
                    self.connection,
                    self.installation,
                    now + number * 2,
                    predecessor=(
                        f"Q-{number - 1:03d}@{digest}"
                    ),
                )
            self.assertEqual(opened["epoch_id"], epoch_id)
            digest = self.terminalize_invalid_epoch(
                epoch_id, now + number * 2 + 1
            )

        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value={**current, "runtime_digest": "9" * 64},
        ), self.assertRaisesRegex(
            ValueError, "quality_epoch_inventory_saturated"
        ):
            self.runtime.open_quality_epoch(
                self.connection,
                self.installation,
                now + 20,
                predecessor=f"Q-008@{digest}",
            )

    def test_successor_refuses_missing_persistent_batch_sequence(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        digest = self.terminalize_invalid_epoch("Q-001", now + 1)
        self.connection.execute(
            "DELETE FROM metadata WHERE key=?",
            (self.runtime.REVIEW_NEXT_BATCH_ID_KEY,),
        )
        current = self.runtime.current_quality_provenance(
            self.installation
        )

        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value={**current, "runtime_digest": "1" * 64},
        ), self.assertRaisesRegex(
            ValueError, "review_batch_sequence_missing"
        ):
            self.runtime.open_quality_epoch(
                self.connection,
                self.installation,
                now + 2,
                predecessor=f"Q-001@{digest}",
            )

    def test_commit_records_final_candidate_and_exclusion_decisions(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        claim = self.claim(2, now + 1)
        payload = self.result_payload(claim, distinct=True)
        payload["sessions"][1] = {
            "session_ref": payload["sessions"][1]["session_ref"],
            "decision": "excluded",
            "excluded_reason": "environment",
        }
        result_path = self.write_result(claim, payload)

        committed = self.commit(claim, result_path, now + 2)
        observation = self.runtime.load_quality_observation(
            self.connection,
            "Q-001",
            int(claim["batch_id"]),
        )
        encoded = self.runtime.canonical_json_bytes(observation)

        self.assertEqual(committed["status"], "completed")
        self.assertEqual(observation["epoch_id"], "Q-001")
        decisions = {
            item["outcome"]: item
            for item in observation["decisions"]
        }
        self.assertEqual(set(decisions), {"candidate", "excluded"})
        self.assertEqual(decisions["candidate"]["candidate_id"], 1)
        self.assertEqual(
            decisions["excluded"]["excluded_reason"], "environment"
        )
        for item in observation["decisions"]:
            self.assertRegex(
                item["session_ref"], r"\A[0-9a-f]{64}\Z"
            )
        for forbidden in (
            b"raw-session",
            b"candidate-session",
            b'"transcript":',
            b"owner",
            b"problem_summary",
            b"evidence",
        ):
            self.assertNotIn(forbidden, encoded)
        audit = json.loads(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (
                    self.runtime.review_audit_key(
                        int(claim["batch_id"])
                    ),
                ),
            ).fetchone()["value"]
        )
        self.assertEqual(
            observation["audit_digest"],
            self.runtime.sha256_json(audit),
        )

    def test_one_identity_key_snapshot_binds_fingerprint_and_hmacs(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        claim = self.claim(2, now + 1)
        result_path = self.write_result(
            claim, self.result_payload(claim, distinct=True)
        )
        load_identity = self.runtime.load_quality_identity_key

        with mock.patch.object(
            self.runtime,
            "load_quality_identity_key",
            wraps=load_identity,
        ) as loaded:
            self.commit(claim, result_path, now + 2)

        self.assertEqual(loaded.call_count, 1)

    def test_batch_high_water_survives_old_batch_deletion(self) -> None:
        now = 2_000_000_000.0
        self.connection.execute(
            """
            INSERT INTO review_batches(
              id,status,started_at,finished_at
            ) VALUES(7,'completed',?,?)
            """,
            (
                self.runtime.iso_utc(now - 100),
                self.runtime.iso_utc(now - 90),
            ),
        )
        epoch = self.open_epoch(now)
        self.connection.execute(
            "DELETE FROM review_batches WHERE id=7"
        )

        claim = self.claim(1, now + 1)
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )
        self.commit(claim, result_path, now + 2)

        self.assertEqual(epoch["first_batch_id"], 8)
        self.assertEqual(claim["batch_id"], 8)
        self.assertIsNotNone(
            self.runtime.load_quality_observation(
                self.connection, "Q-001", 8
            )
        )

    def test_expected_provenance_drift_invalidates_but_commits_review(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        claim = self.claim(1, now + 1)
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )
        current = self.runtime.current_quality_provenance(
            self.installation
        )

        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value={**current, "runtime_digest": "0" * 64},
        ):
            committed = self.commit(claim, result_path, now + 2)

        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        self.assertEqual(committed["status"], "completed")
        self.assertEqual(epoch["state"], "invalid")
        self.assertEqual(
            epoch["invalid_reason"], "quality_provenance_drift"
        )
        self.assertIsNone(
            self.runtime.load_quality_observation(
                self.connection,
                "Q-001",
                int(claim["batch_id"]),
                required=False,
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0],
            1,
        )

    def test_orphaned_collecting_epoch_rolls_back_review(self) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        self.connection.execute(
            "DELETE FROM metadata WHERE key=?",
            (self.runtime.QUALITY_ACTIVE_EPOCH_KEY,),
        )
        claim = self.claim(1, now + 1)
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )

        with self.assertRaisesRegex(
            ValueError, "invalid_quality_epoch_pointer"
        ):
            self.commit(claim, result_path, now + 2)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0],
            0,
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (
                    self.runtime.review_audit_key(
                        int(claim["batch_id"])
                    ),
                ),
            ).fetchone()
        )

    def test_corrupt_observation_overflow_rolls_back_review(self) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        for batch_id in range(1, 102):
            observation = {
                "schema_version": 1,
                "epoch_id": "Q-001",
                "batch_id": batch_id,
                "audit_digest": "a" * 64,
                "policy_digest": epoch["policy_digest"],
                "transcript_adapter_digest": (
                    epoch["transcript_adapter_digest"]
                ),
                "catalog_adapter_digest": (
                    epoch["catalog_adapter_digest"]
                ),
                "catalog_snapshot_digest": "c" * 64,
                "decisions": [
                    {
                        "session_ref": f"{batch_id:064x}",
                        "outcome": "excluded",
                        "excluded_reason": "environment",
                    }
                ],
                "finished_at": self.runtime.iso_utc(now),
            }
            self.connection.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                (
                    self.runtime.quality_observation_key(
                        "Q-001", batch_id
                    ),
                    self.runtime.canonical_json_bytes(
                        observation
                    ).decode("utf-8"),
                ),
            )
        claim = self.claim(1, now + 1)
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )

        with self.assertRaisesRegex(
            ValueError, "invalid_quality_observation"
        ):
            self.commit(claim, result_path, now + 2)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.runtime.load_quality_epoch(
                self.connection, "Q-001"
            )["state"],
            "collecting",
        )

    def test_malformed_observation_namespace_rolls_back_review(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            ("quality.epoch.Q-001.batch.not-an-id", "{}"),
        )
        claim = self.claim(1, now + 1)
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )

        with self.assertRaisesRegex(
            ValueError, "invalid_quality_observation"
        ):
            self.commit(claim, result_path, now + 2)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0],
            0,
        )

    def test_decision_cap_invalidates_but_commits_review(self) -> None:
        now = 2_000_000_000.0
        with mock.patch.object(
            self.runtime, "QUALITY_OBSERVATION_MAX", 1
        ):
            self.open_epoch(now)
            claim = self.claim(2, now + 1)
            result_path = self.write_result(
                claim, self.result_payload(claim, distinct=True)
            )
            committed = self.commit(claim, result_path, now + 2)

        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        self.assertEqual(committed["status"], "completed")
        self.assertEqual(epoch["state"], "invalid")
        self.assertEqual(
            epoch["invalid_reason"], "quality_observation_capacity"
        )
        self.assertIsNone(
            self.runtime.load_quality_observation(
                self.connection,
                "Q-001",
                int(claim["batch_id"]),
                required=False,
            )
        )

    def test_quality_session_reference_is_stable_only_within_epoch(
        self,
    ) -> None:
        session_key = "a" * 64
        first = self.runtime.quality_session_ref(
            self.installation, "Q-001", session_key
        )

        self.assertEqual(
            first,
            self.runtime.quality_session_ref(
                self.installation, "Q-001", session_key
            ),
        )
        self.assertNotEqual(
            first,
            self.runtime.quality_session_ref(
                self.installation, "Q-002", session_key
            ),
        )
        self.assertRegex(first, r"\A[0-9a-f]{64}\Z")

    def test_quality_identity_key_is_read_from_one_private_descriptor(
        self,
    ) -> None:
        self.installation.identity_key.chmod(0o644)

        with self.assertRaisesRegex(
            ValueError, "invalid_identity_key"
        ):
            self.runtime.load_quality_identity_key(
                self.installation
            )

    def test_unexpected_observation_write_failure_rolls_back_review(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        claim = self.claim(1, now + 1)
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )

        with mock.patch.object(
            self.runtime,
            "_insert_quality_observation",
            side_effect=sqlite3.OperationalError("synthetic failure"),
        ), self.assertRaisesRegex(
            sqlite3.OperationalError, "synthetic failure"
        ):
            self.commit(claim, result_path, now + 2)

        batch_id = int(claim["batch_id"])
        batch = self.connection.execute(
            "SELECT status FROM review_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        items = list(
            self.connection.execute(
                "SELECT status,batch_id FROM review_items"
            )
        )
        self.assertEqual(batch["status"], "ready")
        self.assertEqual(
            [(row["status"], row["batch_id"]) for row in items],
            [("reviewing", batch_id)],
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidate_evidence"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM metadata
                WHERE key LIKE 'candidate.session.%'
                """
            ).fetchone()[0],
            0,
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (self.runtime.review_audit_key(batch_id),),
            ).fetchone()
        )

    def test_malformed_epoch_rolls_back_review(self) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        self.connection.execute(
            "UPDATE metadata SET value='{}' WHERE key=?",
            (self.runtime.quality_epoch_key("Q-001"),),
        )
        claim = self.claim(1, now + 1)
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )

        with self.assertRaisesRegex(
            ValueError, "invalid_quality_epoch"
        ):
            self.commit(claim, result_path, now + 2)

        batch_id = int(claim["batch_id"])
        self.assertEqual(
            self.connection.execute(
                "SELECT status FROM review_batches WHERE id=?",
                (batch_id,),
            ).fetchone()["status"],
            "ready",
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0],
            0,
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (self.runtime.review_audit_key(batch_id),),
            ).fetchone()
        )

    def test_phase4_entry_report_rejects_minimal_fabricated_pass(
        self,
    ) -> None:
        fake = self.base / "fake-phase4-report.json"
        fake.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "decision": "PASS",
                    "checks": {"fabricated": True},
                    "quality_gate_claimed": False,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        with mock.patch.object(
            self.runtime, "PHASE4_RELEASE_REPORT_PATH", fake
        ), self.assertRaisesRegex(
            ValueError, "invalid_phase4_release_report"
        ):
            self.runtime.phase4_release_report_digest()

    def test_phase4_entry_report_rejects_duplicate_keys(self) -> None:
        fake = self.base / "duplicate-phase4-report.json"
        encoded = (
            self.runtime.PHASE4_RELEASE_REPORT_PATH.read_text(
                encoding="utf-8"
            )
            .replace(
                '"decision": "PASS",',
                '"decision": "FAIL",\n  "decision": "PASS",',
                1,
            )
        )
        fake.write_text(encoded, encoding="utf-8")

        with mock.patch.object(
            self.runtime, "PHASE4_RELEASE_REPORT_PATH", fake
        ), self.assertRaisesRegex(
            ValueError, "invalid_phase4_release_report"
        ):
            self.runtime.phase4_release_report_digest()

    def test_phase4_entry_report_requires_exact_approved_bytes(
        self,
    ) -> None:
        fake = self.base / "modified-phase4-report.json"
        encoded = self.runtime.PHASE4_RELEASE_REPORT_PATH.read_text(
            encoding="utf-8"
        ).replace(
            self.runtime.PHASE4_IMPLEMENTATION_COMMIT,
            "0" * 40,
            1,
        )
        fake.write_text(encoded, encoding="utf-8")

        with mock.patch.object(
            self.runtime, "PHASE4_RELEASE_REPORT_PATH", fake
        ), self.assertRaisesRegex(
            ValueError, "invalid_phase4_release_report"
        ):
            self.runtime.phase4_release_report_digest()

    def test_epoch_counter_must_match_empty_inventory(self) -> None:
        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (self.runtime.QUALITY_NEXT_EPOCH_KEY, "2"),
        )

        with self.assertRaisesRegex(
            ValueError, "invalid_quality_epoch_sequence"
        ):
            self.open_epoch()

        self.assertEqual(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM metadata
                WHERE key LIKE 'quality.epoch.%'
                   OR key=?
                """,
                (self.runtime.QUALITY_ACTIVE_EPOCH_KEY,),
            ).fetchone()[0],
            0,
        )

    def test_no_active_epoch_preserves_phase4_behavior(self) -> None:
        now = 2_000_000_000.0
        claim = self.claim(1, now)
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )
        committed = self.commit(claim, result_path, now + 1)

        self.assertEqual(committed["status"], "completed")
        self.assertEqual(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM metadata
                WHERE key LIKE 'quality.%'
                """
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0],
            1,
        )
