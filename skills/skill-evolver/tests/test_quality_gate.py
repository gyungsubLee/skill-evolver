from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import unittest
from pathlib import Path
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
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            epoch = self.runtime.load_quality_epoch(
                self.connection, epoch_id
            )
            self.runtime._invalidate_quality_epoch(
                self.connection,
                epoch,
                "quality_provenance_drift",
                now,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        result = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            epoch_id,
            now + 1,
        )
        return str(result["report_digest"])

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


class QualitySealTests(CandidateBatchFixture):
    def open_epoch(self, now: float) -> dict[str, object]:
        return self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now,
            predecessor=None,
        )

    def collect_batch(
        self,
        count: int,
        now: float,
        *,
        candidates: bool = True,
    ) -> dict[str, object]:
        claim = self.claim(count, now)
        payload = self.result_payload(claim)
        if not candidates:
            payload["sessions"] = [
                {
                    "session_ref": item["session_ref"],
                    "decision": "excluded",
                    "excluded_reason": "environment",
                }
                for item in payload["sessions"]
            ]
        result_path = self.write_result(claim, payload)
        self.commit(claim, result_path, now + 1)
        return claim

    def collect_ten(
        self, now: float = 2_000_000_000.0
    ) -> tuple[dict[str, object], dict[str, object]]:
        self.open_epoch(now)
        first = self.collect_batch(5, now + 1)
        second = self.collect_batch(5, now + 3)
        return first, second

    def test_seal_rejects_small_sample(self) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        self.collect_batch(5, now + 1)

        with self.assertRaisesRegex(
            ValueError, "quality_sample_too_small"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 3
            )
        self.assertEqual(
            self.runtime.load_quality_epoch(
                self.connection, "Q-001"
            )["state"],
            "collecting",
        )

    def test_seal_rejects_empty_candidate_set(self) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        self.collect_batch(5, now + 1, candidates=False)
        self.collect_batch(5, now + 3, candidates=False)
        with self.assertRaisesRegex(
            ValueError, "quality_sample_candidate_required"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 5
            )

    def test_seal_freezes_ordered_observations_and_subjects(
        self,
    ) -> None:
        now = 2_000_000_000.0
        first, second = self.collect_ten(now)

        result = self.runtime.seal_quality_epoch(
            self.connection, self.installation, now + 5
        )
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        sealed = epoch["sealed"]

        self.assertEqual(result["state"], "sealed")
        self.assertEqual(epoch["state"], "sealed")
        self.assertEqual(
            sealed["high_water_batch_id"], second["batch_id"]
        )
        self.assertEqual(
            [
                item["batch_id"]
                for item in sealed["observations"]
            ],
            [first["batch_id"], second["batch_id"]],
        )
        self.assertEqual(sealed["distinct_session_count"], 10)
        self.assertEqual(sealed["candidate_count"], 1)
        self.assertEqual(
            [item["candidate_id"] for item in sealed["candidates"]],
            [1],
        )
        subject = self.runtime.quality_candidate_subject(
            self.connection, 1
        )
        self.assertEqual(
            set(subject),
            {
                "schema_version",
                "target_identity",
                "classification",
                "problem_summary",
                "proposal_summary",
                "validation_plan",
                "risk_level",
                "evidence",
            },
        )
        self.assertEqual(
            set(subject["classification"]),
            {
                "problem_category",
                "target_locator",
                "proposal_intent",
            },
        )
        self.assertTrue(subject["evidence"])
        self.assertTrue(
            all(
                set(item)
                == {"signal_type", "source_kind", "summary"}
                for item in subject["evidence"]
            )
        )
        self.assertEqual(
            sealed["candidates"][0]["subject_digest"],
            self.runtime.sha256_json(subject),
        )
        self.assertEqual(
            sealed["observation_set_digest"],
            self.runtime.sha256_json(sealed["observations"]),
        )
        self.assertRegex(sealed["seal_digest"], r"\A[0-9a-f]{64}\Z")

    def test_seal_deduplicates_session_refs_across_batches(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        first = self.collect_batch(5, now + 1)
        self.collect_batch(5, now + 3)
        third = self.collect_batch(1, now + 5)
        first_observation = self.runtime.load_quality_observation(
            self.connection,
            "Q-001",
            int(first["batch_id"]),
        )
        third_key = self.runtime.quality_observation_key(
            "Q-001", int(third["batch_id"])
        )
        third_observation = self.runtime.load_quality_observation(
            self.connection,
            "Q-001",
            int(third["batch_id"]),
        )
        third_observation["decisions"][0]["session_ref"] = (
            first_observation["decisions"][0]["session_ref"]
        )
        third_observation["decisions"].sort(
            key=lambda item: item["session_ref"]
        )
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (
                self.runtime.canonical_json_bytes(
                    third_observation
                ).decode("utf-8"),
                third_key,
            ),
        )

        sealed = self.runtime.seal_quality_epoch(
            self.connection, self.installation, now + 7
        )

        self.assertEqual(
            sealed["sealed"]["distinct_session_count"], 10
        )

    def test_seal_rejects_extra_observation_outside_batch_witness(
        self,
    ) -> None:
        now = 2_000_000_000.0
        _first, second = self.collect_ten(now)
        observation = self.runtime.load_quality_observation(
            self.connection,
            "Q-001",
            int(second["batch_id"]),
        )
        extra_batch_id = int(second["batch_id"]) + 1
        observation["batch_id"] = extra_batch_id
        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (
                self.runtime.quality_observation_key(
                    "Q-001", extra_batch_id
                ),
                self.runtime.canonical_json_bytes(
                    observation
                ).decode("utf-8"),
            ),
        )

        with self.assertRaisesRegex(
            ValueError, "quality_observation_coverage"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 5
            )

    def test_seal_rejects_reclassified_terminal_batch(
        self,
    ) -> None:
        now = 2_000_000_000.0
        first, _second = self.collect_ten(now)
        self.connection.execute(
            "UPDATE review_batches SET status='aborted' WHERE id=?",
            (int(first["batch_id"]),),
        )
        self.connection.execute(
            "DELETE FROM metadata WHERE key=?",
            (
                self.runtime.quality_observation_key(
                    "Q-001", int(first["batch_id"])
                ),
            ),
        )

        with self.assertRaisesRegex(
            ValueError, "invalid_quality_review_audit"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 5
            )

    def test_seal_exact_collection_expiry_commits_invalidation(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.collect_ten(now)
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        expires_at = self.runtime.parse_iso_utc(
            epoch["collection_expires_at"]
        )

        result = self.runtime.seal_quality_epoch(
            self.connection, self.installation, expires_at
        )
        persisted = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )

        self.assertEqual(result["state"], "invalid")
        self.assertEqual(
            result["invalid_reason"], "quality_collection_expired"
        )
        self.assertEqual(persisted, result)

    def test_review_at_exact_collection_expiry_commits_without_observation(
        self,
    ) -> None:
        now = 2_000_000_000.0
        epoch = self.open_epoch(now)
        expires_at = self.runtime.parse_iso_utc(
            epoch["collection_expires_at"]
        )
        claim = self.claim(1, expires_at - 2)
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )

        committed = self.commit(claim, result_path, expires_at)

        self.assertEqual(committed["status"], "completed")
        self.assertEqual(
            self.runtime.load_quality_epoch(
                self.connection, "Q-001"
            )["invalid_reason"],
            "quality_collection_expired",
        )
        self.assertIsNone(
            self.runtime.load_quality_observation(
                self.connection,
                "Q-001",
                int(claim["batch_id"]),
                required=False,
            )
        )
        self.assertIsNotNone(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (
                    self.runtime.review_audit_key(
                        int(claim["batch_id"])
                    ),
                ),
            ).fetchone()
        )

    def test_seal_provenance_drift_commits_invalidation(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.collect_ten(now)
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        provenance = {
            name: epoch[name]
            for name in self.runtime.QUALITY_PROVENANCE_FIELDS
        }
        provenance["runtime_digest"] = "0" * 64

        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value=provenance,
        ):
            result = self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 5
            )

        self.assertEqual(result["state"], "invalid")
        self.assertEqual(
            result["invalid_reason"], "quality_provenance_drift"
        )
        self.assertEqual(
            self.runtime.load_quality_epoch(
                self.connection, "Q-001"
            ),
            result,
        )

    def test_collecting_status_reports_expiry_or_drift_read_only(
        self,
    ) -> None:
        now = 2_000_000_000.0
        epoch = self.open_epoch(now)
        before = self.installation.database.read_bytes()
        expires_at = self.runtime.parse_iso_utc(
            epoch["collection_expires_at"]
        )

        expired = self.runtime.quality_status(
            self.connection, self.installation, expires_at
        )

        self.assertEqual(expired["status"], "INVALID")
        self.assertEqual(
            before, self.installation.database.read_bytes()
        )
        provenance = {
            name: epoch[name]
            for name in self.runtime.QUALITY_PROVENANCE_FIELDS
        }
        provenance["runtime_digest"] = "0" * 64
        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value=provenance,
        ):
            drifted = self.runtime.quality_status(
                self.connection, self.installation, now + 1
            )

        self.assertEqual(drifted["status"], "INVALID")
        self.assertEqual(
            before, self.installation.database.read_bytes()
        )

    def test_seal_rejects_batch_session_or_exclusion_mismatch(
        self,
    ) -> None:
        now = 2_000_000_000.0
        first, _second = self.collect_ten(now)
        batch_id = int(first["batch_id"])
        original = self.connection.execute(
            """
            SELECT session_count,exclusion_counts_json
            FROM review_batches WHERE id=?
            """,
            (batch_id,),
        ).fetchone()
        self.connection.execute(
            """
            UPDATE review_batches SET session_count=?
            WHERE id=?
            """,
            (int(original["session_count"]) + 1, batch_id),
        )
        with self.assertRaisesRegex(
            ValueError, "quality_observation_audit_mismatch"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 5
            )

        self.connection.execute(
            """
            UPDATE review_batches
            SET session_count=?,exclusion_counts_json='{}'
            WHERE id=?
            """,
            (original["session_count"], batch_id),
        )
        with self.assertRaisesRegex(
            ValueError, "quality_observation_audit_mismatch"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 5
            )
        self.connection.execute(
            """
            UPDATE review_batches SET exclusion_counts_json=?
            WHERE id=?
            """,
            (original["exclusion_counts_json"], batch_id),
        )

    def test_seal_rejects_noncanonical_exclusion_counts(
        self,
    ) -> None:
        now = 2_000_000_000.0
        first, _second = self.collect_ten(now)
        batch_id = int(first["batch_id"])
        original = self.connection.execute(
            """
            SELECT exclusion_counts_json
            FROM review_batches WHERE id=?
            """,
            (batch_id,),
        ).fetchone()["exclusion_counts_json"]
        noncanonical = json.dumps(
            json.loads(original), indent=2
        )
        self.assertNotEqual(original, noncanonical)
        self.connection.execute(
            """
            UPDATE review_batches SET exclusion_counts_json=?
            WHERE id=?
            """,
            (noncanonical, batch_id),
        )

        with self.assertRaisesRegex(
            ValueError, "quality_observation_audit_mismatch"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 5
            )

    def test_seal_rejects_time_before_latest_completed_audit(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.collect_ten(now)

        with self.assertRaisesRegex(
            ValueError, "invalid_quality_seal_time"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 3
            )

    def test_second_seal_refuses_without_mutating_witness(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.collect_ten(now)
        self.runtime.seal_quality_epoch(
            self.connection, self.installation, now + 5
        )
        key = self.runtime.quality_epoch_key("Q-001")
        before = self.connection.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()["value"]

        with self.assertRaisesRegex(
            ValueError, "quality_epoch_not_collecting"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 6
            )

        after = self.connection.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()["value"]
        self.assertEqual(before, after)

    def test_rehashed_seal_rejects_oversized_batch_span(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.collect_ten(now)
        self.runtime.seal_quality_epoch(
            self.connection, self.installation, now + 5
        )
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        sealed = epoch["sealed"]
        sealed["high_water_batch_id"] = (
            int(epoch["first_batch_id"])
            + self.runtime.QUALITY_BATCH_MAX
        )
        payload = {
            name: value
            for name, value in sealed.items()
            if name != "seal_digest"
        }
        sealed["seal_digest"] = self.runtime.sha256_json(payload)

        self.assertFalse(
            self.runtime._valid_quality_seal(
                sealed, int(epoch["first_batch_id"])
            )
        )
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (
                self.runtime.canonical_json_bytes(epoch).decode(
                    "utf-8"
                ),
                self.runtime.quality_epoch_key("Q-001"),
            ),
        )
        with self.assertRaisesRegex(
            ValueError, "invalid_quality_epoch"
        ):
            self.runtime.load_quality_epoch(
                self.connection, "Q-001"
            )

    def test_rehashed_seal_binds_completed_observation_set(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.collect_ten(now)
        epoch = self.runtime.seal_quality_epoch(
            self.connection, self.installation, now + 5
        )
        sealed = epoch["sealed"]
        sealed["batches"][0]["terminal_status"] = "aborted"
        sealed["batch_set_digest"] = self.runtime.sha256_json(
            sealed["batches"]
        )
        payload = {
            name: value
            for name, value in sealed.items()
            if name != "seal_digest"
        }
        sealed["seal_digest"] = self.runtime.sha256_json(payload)
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (
                self.runtime.canonical_json_bytes(epoch).decode(
                    "utf-8"
                ),
                self.runtime.quality_epoch_key("Q-001"),
            ),
        )

        self.assertFalse(
            self.runtime._valid_quality_seal(
                sealed, int(epoch["first_batch_id"])
            )
        )
        with self.assertRaisesRegex(
            ValueError, "invalid_quality_epoch"
        ):
            self.runtime.load_quality_epoch(
                self.connection, "Q-001"
            )

    def test_seal_rejects_a_live_prospective_batch(self) -> None:
        now = 2_000_000_000.0
        self.collect_ten(now)
        self.claim(1, now + 5)

        with self.assertRaisesRegex(
            ValueError, "quality_review_batch_live"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 6
            )

    def test_seal_rejects_missing_or_audit_mismatched_observation(
        self,
    ) -> None:
        now = 2_000_000_000.0
        first, _second = self.collect_ten(now)
        key = self.runtime.quality_observation_key(
            "Q-001", int(first["batch_id"])
        )
        original = self.connection.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()["value"]
        self.connection.execute(
            "DELETE FROM metadata WHERE key=?", (key,)
        )
        with self.assertRaisesRegex(
            ValueError, "quality_observation_coverage"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 5
            )

        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (key, original),
        )
        observation = json.loads(original)
        observation["audit_digest"] = "0" * 64
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (
                self.runtime.canonical_json_bytes(
                    observation
                ).decode("utf-8"),
                key,
            ),
        )
        with self.assertRaisesRegex(
            ValueError, "quality_observation_audit_mismatch"
        ):
            self.runtime.seal_quality_epoch(
                self.connection, self.installation, now + 5
            )

    def test_post_seal_review_does_not_extend_witness(self) -> None:
        now = 2_000_000_000.0
        _first, second = self.collect_ten(now)
        self.runtime.seal_quality_epoch(
            self.connection, self.installation, now + 5
        )
        claim = self.collect_batch(1, now + 6)
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )

        self.assertEqual(
            epoch["sealed"]["high_water_batch_id"],
            second["batch_id"],
        )
        self.assertIsNone(
            self.runtime.load_quality_observation(
                self.connection,
                "Q-001",
                int(claim["batch_id"]),
                required=False,
            )
        )

    def test_candidate_subject_ignores_operational_state_only(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        self.collect_batch(1, now + 1)
        before = self.runtime.quality_candidate_subject_digest(
            self.connection, 1
        )
        self.connection.execute(
            """
            UPDATE candidates
            SET status='deferred',occurrence_count=99,
                last_seen_at=?,updated_at=?
            WHERE id=1
            """,
            (
                self.runtime.iso_utc(now + 10),
                self.runtime.iso_utc(now + 10),
            ),
        )
        self.connection.execute(
            """
            UPDATE candidate_evidence SET created_at=?
            WHERE candidate_id=1
            """,
            (self.runtime.iso_utc(now + 10),),
        )
        operational_change = (
            self.runtime.quality_candidate_subject_digest(
                self.connection, 1
            )
        )
        self.connection.execute(
            """
            UPDATE candidates SET problem_summary=?
            WHERE id=1
            """,
            ("A materially different problem.",),
        )
        semantic_change = (
            self.runtime.quality_candidate_subject_digest(
                self.connection, 1
            )
        )

        self.assertEqual(before, operational_change)
        self.assertNotEqual(before, semantic_change)

    def test_candidate_subject_ignores_evidence_aggregate_counters(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        self.collect_batch(1, now + 1)
        before = self.runtime.quality_candidate_subject_digest(
            self.connection, 1
        )
        evidence = self.connection.execute(
            """
            SELECT signal_type,source_kind
            FROM candidate_evidence WHERE candidate_id=1
            LIMIT 1
            """
        ).fetchone()
        aggregate = {
            "schema_version": 1,
            "counts": [
                {
                    "signal_type": evidence["signal_type"],
                    "source_kind": evidence["source_kind"],
                    "count": 999,
                }
            ],
            "updated_at": self.runtime.iso_utc(now + 10),
        }
        self.connection.execute(
            """
            INSERT INTO metadata(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (
                self.runtime.candidate_evidence_aggregate_key(1),
                self.runtime.canonical_json_bytes(
                    aggregate
                ).decode("utf-8"),
            ),
        )

        after = self.runtime.quality_candidate_subject_digest(
            self.connection, 1
        )

        self.assertEqual(before, after)

    def test_candidate_subject_deduplicates_identical_evidence(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.open_epoch(now)
        self.collect_batch(1, now + 1)
        before = self.runtime.quality_candidate_subject_digest(
            self.connection, 1
        )
        evidence = self.connection.execute(
            """
            SELECT signal_type,source_kind,summary
            FROM candidate_evidence WHERE candidate_id=1
            LIMIT 1
            """
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO candidate_evidence(
              candidate_id,review_item_id,session_key,generation,
              signal_type,source_kind,summary,created_at
            ) VALUES(1,NULL,?,1,?,?,?,?)
            """,
            (
                "f" * 64,
                evidence["signal_type"],
                evidence["source_kind"],
                evidence["summary"],
                self.runtime.iso_utc(now + 10),
            ),
        )

        after = self.runtime.quality_candidate_subject_digest(
            self.connection, 1
        )

        self.assertEqual(before, after)

    def test_quality_seal_parser_and_handler(self) -> None:
        now = 2_000_000_000.0
        self.collect_ten(now)
        parser = self.runtime.build_parser()
        args = parser.parse_args(
            [
                "quality-seal",
                "--installation",
                str(
                    self.installation.data_root
                    / "installation.json"
                ),
            ]
        )
        action = next(
            item
            for item in parser._actions
            if isinstance(
                item, self.runtime.argparse._SubParsersAction
            )
        )
        options = {
            option
            for item in action.choices["quality-seal"]._actions
            for option in item.option_strings
        }
        output = io.BytesIO()
        stdout = mock.Mock()
        stdout.buffer = output

        self.assertEqual(options, {"-h", "--help", "--installation"})
        self.assertIs(args.handler, self.runtime.cmd_quality_seal)
        with mock.patch.object(
            self.runtime.time, "time", return_value=now + 5
        ), mock.patch.object(self.runtime.sys, "stdout", stdout):
            self.assertEqual(args.handler(args), 0)

        self.assertEqual(
            json.loads(output.getvalue())["state"], "sealed"
        )


class QualityLabelTests(CandidateBatchFixture):
    def seal_sample(
        self, now: float = 2_000_000_000.0
    ) -> dict[str, object]:
        self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now,
            predecessor=None,
        )
        for offset in (1, 3):
            claim = self.claim(5, now + offset)
            result_path = self.write_result(
                claim, self.result_payload(claim)
            )
            self.commit(claim, result_path, now + offset + 1)
        return self.runtime.seal_quality_epoch(
            self.connection, self.installation, now + 5
        )

    def answers(self) -> dict[str, bool]:
        return {
            "evaluation_worthy": True,
            "target_correct": True,
            "external_content_adoption": False,
        }

    def test_label_locale_defaults_to_korean_and_rejects_unknown(
        self,
    ) -> None:
        parser = self.runtime.build_parser()
        installation_path = str(
            self.installation.data_root / "installation.json"
        )
        default_args = parser.parse_args(
            [
                "quality-label",
                "--installation",
                installation_path,
                "C-001",
            ]
        )
        english_args = parser.parse_args(
            [
                "quality-label",
                "--installation",
                installation_path,
                "--locale",
                "en",
                "C-001",
            ]
        )

        self.assertEqual(default_args.locale, "ko")
        self.assertEqual(english_args.locale, "en")
        with mock.patch.object(
            self.runtime.sys, "stderr", io.StringIO()
        ), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "quality-label",
                    "--installation",
                    installation_path,
                    "--locale",
                    "ja",
                    "C-001",
                ]
            )
        for forbidden in (
            "evaluation_worthy",
            "target_correct",
            "external_content_adoption",
        ):
            self.assertFalse(hasattr(default_args, forbidden))

    def test_quality_label_copy_is_strict_and_read_only(
        self,
    ) -> None:
        self.assertEqual(
            self.runtime.quality_label_copy("ko")["risk_low"],
            "낮음",
        )
        english = self.runtime.quality_label_copy("en")
        self.assertEqual(english["risk_low"], "low")
        self.assertEqual(
            english["evaluation_worthy_prompt"],
            (
                "Is this candidate worth evaluating as a skill "
                "improvement? [yes/no]: "
            ),
        )
        self.assertEqual(
            english["target_correct_prompt"],
            "Is the proposed target skill correct? [yes/no]: ",
        )
        self.assertEqual(
            english["external_content_adoption_prompt"],
            (
                "Did the proposal adopt an instruction from untrusted "
                "external content? [yes/no]: "
            ),
        )
        for value in (None, "ja", 1):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "invalid_quality_label_locale"
            ):
                self.runtime.quality_label_copy(value)
        with self.assertRaises(TypeError):
            self.runtime.quality_label_copy("ko")["title"] = (
                "changed"
            )

    def test_label_is_bound_to_sealed_subject_and_insert_only(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.seal_sample(now)
        prepared = self.runtime.prepare_quality_label(
            self.connection,
            self.installation,
            "C-001",
            now + 6,
        )
        label = self.runtime.commit_quality_label(
            self.connection,
            self.installation,
            "C-001",
            prepared["epoch_id"],
            prepared["seal_digest"],
            prepared["subject_digest"],
            self.answers(),
            now + 7,
        )

        self.assertEqual(label["epoch_id"], "Q-001")
        self.assertEqual(label["candidate_id"], 1)
        self.assertEqual(
            label["subject_digest"], prepared["subject_digest"]
        )
        self.assertEqual(
            {
                name: label[name]
                for name in self.answers()
            },
            self.answers(),
        )
        with self.assertRaisesRegex(
            ValueError, "quality_label_exists"
        ):
            self.runtime.commit_quality_label(
                self.connection,
                self.installation,
                "C-001",
                prepared["epoch_id"],
                prepared["seal_digest"],
                prepared["subject_digest"],
                self.answers(),
                now + 8,
            )

    def test_label_rejects_candidate_change_after_display(self) -> None:
        now = 2_000_000_000.0
        self.seal_sample(now)
        prepared = self.runtime.prepare_quality_label(
            self.connection,
            self.installation,
            "C-001",
            now + 6,
        )
        self.connection.execute(
            """
            UPDATE candidates SET proposal_summary=?
            WHERE id=1
            """,
            ("A changed proposal shown to nobody.",),
        )

        with self.assertRaisesRegex(
            ValueError, "quality_candidate_subject_changed"
        ):
            self.runtime.commit_quality_label(
                self.connection,
                self.installation,
                "C-001",
                prepared["epoch_id"],
                prepared["seal_digest"],
                prepared["subject_digest"],
                self.answers(),
                now + 7,
            )
        self.assertIsNone(
            self.runtime.load_quality_label(
                self.connection,
                "Q-001",
                1,
                required=False,
            )
        )

    def test_label_rejects_active_epoch_or_seal_cas_change(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.seal_sample(now)
        prepared = self.runtime.prepare_quality_label(
            self.connection,
            self.installation,
            "C-001",
            now + 6,
        )

        for epoch_id, seal_digest in (
            ("Q-002", prepared["seal_digest"]),
            (prepared["epoch_id"], "0" * 64),
        ):
            with self.subTest(
                epoch_id=epoch_id, seal_digest=seal_digest
            ), self.assertRaisesRegex(
                ValueError, "quality_label_epoch_changed"
            ):
                self.runtime.commit_quality_label(
                    self.connection,
                    self.installation,
                    "C-001",
                    epoch_id,
                    seal_digest,
                    prepared["subject_digest"],
                    self.answers(),
                    now + 7,
                )
        self.assertIsNone(
            self.runtime.load_quality_label(
                self.connection,
                "Q-001",
                1,
                required=False,
            )
        )

    def test_label_provenance_drift_invalidates_without_insert(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.seal_sample(now)
        prepared = self.runtime.prepare_quality_label(
            self.connection,
            self.installation,
            "C-001",
            now + 6,
        )
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        provenance = {
            name: epoch[name]
            for name in self.runtime.QUALITY_PROVENANCE_FIELDS
        }
        provenance["runtime_digest"] = "0" * 64

        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value=provenance,
        ), self.assertRaisesRegex(
            ValueError, "quality_provenance_drift"
        ):
            self.runtime.commit_quality_label(
                self.connection,
                self.installation,
                "C-001",
                prepared["epoch_id"],
                prepared["seal_digest"],
                prepared["subject_digest"],
                self.answers(),
                now + 7,
            )

        result = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        self.assertEqual(result["state"], "invalid")
        self.assertEqual(
            result["invalid_reason"], "quality_provenance_drift"
        )
        self.assertIsNone(
            self.runtime.load_quality_label(
                self.connection,
                "Q-001",
                1,
                required=False,
            )
        )

    def test_sealed_status_reports_provenance_or_subject_drift_read_only(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.seal_sample(now)
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        before = self.installation.database.read_bytes()
        provenance = {
            name: epoch[name]
            for name in self.runtime.QUALITY_PROVENANCE_FIELDS
        }
        provenance["runtime_digest"] = "0" * 64

        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value=provenance,
        ):
            drifted = self.runtime.quality_status(
                self.connection, self.installation, now + 6
            )

        self.assertEqual(drifted["status"], "INVALID")
        self.assertEqual(
            before, self.installation.database.read_bytes()
        )
        self.connection.execute(
            """
            UPDATE candidates SET proposal_summary=?
            WHERE id=1
            """,
            ("A semantically changed proposal.",),
        )
        before_subject_status = (
            self.installation.database.read_bytes()
        )

        subject_changed = self.runtime.quality_status(
            self.connection, self.installation, now + 6
        )

        self.assertEqual(subject_changed["status"], "INVALID")
        self.assertEqual(
            before_subject_status,
            self.installation.database.read_bytes(),
        )

    def test_sealed_status_rederives_sample_counts(
        self,
    ) -> None:
        now = 2_000_000_000.0
        epoch = self.seal_sample(now)
        sealed = epoch["sealed"]
        sealed["distinct_session_count"] += 1
        payload = {
            name: value
            for name, value in sealed.items()
            if name != "seal_digest"
        }
        sealed["seal_digest"] = self.runtime.sha256_json(payload)
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (
                self.runtime.canonical_json_bytes(epoch).decode(
                    "utf-8"
                ),
                self.runtime.quality_epoch_key("Q-001"),
            ),
        )
        self.assertTrue(
            self.runtime._valid_quality_seal(
                sealed, int(epoch["first_batch_id"])
            )
        )
        before = self.installation.database.read_bytes()

        status = self.runtime.quality_status(
            self.connection, self.installation, now + 6
        )

        self.assertEqual(status["status"], "INVALID")
        self.assertEqual(
            before, self.installation.database.read_bytes()
        )

    def test_status_rejects_malformed_or_extra_label_namespace(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.seal_sample(now)
        malformed_key = "quality.epoch.Q-001.label.C-001.extra"
        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (malformed_key, "{}"),
        )
        with self.assertRaisesRegex(
            ValueError, "invalid_quality_label"
        ):
            self.runtime.quality_status(
                self.connection, self.installation, now + 6
            )
        self.connection.execute(
            "DELETE FROM metadata WHERE key=?", (malformed_key,)
        )
        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            ("quality.epoch.Q-001.label.C-002", "{}"),
        )

        with self.assertRaisesRegex(
            ValueError,
            "quality_candidate_not_in_sample|invalid_quality_label",
        ):
            self.runtime.quality_status(
                self.connection, self.installation, now + 6
            )

    def test_quality_status_is_read_only_and_lists_only_missing_ids(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.seal_sample(now)
        before = self.installation.database.read_bytes()
        with mock.patch.object(
            self.runtime,
            "read_frozen_transcript",
            side_effect=AssertionError("transcript opened"),
        ):
            status = self.runtime.quality_status(
                self.connection, self.installation, now + 6
            )
        after = self.installation.database.read_bytes()

        self.assertEqual(status["status"], "AWAITING_LABELS")
        self.assertEqual(status["missing_labels"], ["C-001"])
        self.assertNotIn("subject_digest", status)
        self.assertEqual(before, after)

        prepared = self.runtime.prepare_quality_label(
            self.connection,
            self.installation,
            "C-001",
            now + 6,
        )
        self.runtime.commit_quality_label(
            self.connection,
            self.installation,
            "C-001",
            prepared["epoch_id"],
            prepared["seal_digest"],
            prepared["subject_digest"],
            self.answers(),
            now + 7,
        )
        before = self.installation.database.read_bytes()
        ready = self.runtime.quality_status(
            self.connection, self.installation, now + 8
        )
        self.assertEqual(ready["status"], "READY_TO_GATE")
        self.assertEqual(ready["missing_labels"], [])
        self.assertEqual(
            before, self.installation.database.read_bytes()
        )

    def test_quality_status_parser_and_handler_are_read_only(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.seal_sample(now)
        parser = self.runtime.build_parser()
        args = parser.parse_args(
            [
                "quality-status",
                "--installation",
                str(
                    self.installation.data_root
                    / "installation.json"
                ),
            ]
        )
        action = next(
            item
            for item in parser._actions
            if isinstance(
                item, self.runtime.argparse._SubParsersAction
            )
        )
        options = {
            option
            for item in action.choices["quality-status"]._actions
            for option in item.option_strings
        }
        output = io.BytesIO()
        stdout = mock.Mock()
        stdout.buffer = output
        before = self.installation.database.read_bytes()

        self.assertEqual(options, {"-h", "--help", "--installation"})
        self.assertIs(args.handler, self.runtime.cmd_quality_status)
        with mock.patch.object(
            self.runtime,
            "open_database",
            wraps=self.runtime.open_database,
        ) as opened, mock.patch.object(
            self.runtime.time, "time", return_value=now + 6
        ), mock.patch.object(
            self.runtime.sys, "stdout", stdout
        ), mock.patch.object(
            self.runtime,
            "read_frozen_transcript",
            side_effect=AssertionError("transcript opened"),
        ):
            self.assertEqual(args.handler(args), 0)

        self.assertTrue(opened.call_args.kwargs["read_only"])
        self.assertEqual(
            json.loads(output.getvalue())["status"],
            "AWAITING_LABELS",
        )
        self.assertEqual(
            before, self.installation.database.read_bytes()
        )

    def test_label_surface_rejects_non_tty_and_has_no_judgment_flags(
        self,
    ) -> None:
        parser = self.runtime.build_parser()
        action = next(
            item
            for item in parser._actions
            if isinstance(item, self.runtime.argparse._SubParsersAction)
        )
        label_parser = action.choices["quality-label"]
        options = {
            option
            for item in label_parser._actions
            for option in item.option_strings
        }
        self.assertEqual(
            options,
            {"-h", "--help", "--installation", "--locale"},
        )
        stdin = mock.Mock()
        stdout = mock.Mock()
        stdin.isatty.return_value = False
        stdout.isatty.return_value = True

        with mock.patch.object(
            self.runtime.sys, "stdin", stdin
        ), mock.patch.object(
            self.runtime.sys, "stdout", stdout
        ), self.assertRaisesRegex(
            ValueError, "quality_label_external_tty_required"
        ):
            self.runtime.cmd_quality_label(
                self.runtime.argparse.Namespace(
                    installation=str(
                        self.installation.data_root
                        / "installation.json"
                    ),
                    candidate_id="C-001",
                )
            )

    def test_label_answers_and_confirmation_are_exact(self) -> None:
        self.assertIs(
            self.runtime.parse_quality_yes_no("yes"), True
        )
        self.assertIs(
            self.runtime.parse_quality_yes_no("no"), False
        )
        for value in ("y", "YES", " no", "no ", ""):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "invalid_quality_label_answer"
            ):
                self.runtime.parse_quality_yes_no(value)
        digest = "a" * 64
        self.runtime.validate_quality_confirmation(
            "C-001", digest, f"C-001@{digest}"
        )
        with self.assertRaisesRegex(
            ValueError, "invalid_quality_label_confirmation"
        ):
            self.runtime.validate_quality_confirmation(
                "C-001", digest, "C-001@a"
            )

    def test_label_handler_accepts_fake_tty_attestation(self) -> None:
        class FakeOutput:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()

            def isatty(self) -> bool:
                return True

            def write(self, value: str) -> int:
                self.buffer.write(value.encode("utf-8"))
                return len(value)

            def flush(self) -> None:
                pass

        class FakeInput(io.StringIO):
            def __init__(
                self, value: str, output: FakeOutput
            ) -> None:
                super().__init__(value)
                self.output = output

            def isatty(self) -> bool:
                return True

            def readline(self, size: int = -1) -> str:
                value = super().readline(size)
                self.output.write(value)
                return value

        now = 2_000_000_000.0
        self.seal_sample(now)
        subject_digest = (
            self.runtime.quality_candidate_subject_digest(
                self.connection, 1
            )
        )
        stdout = FakeOutput()
        stdin = FakeInput(
            "yes\nyes\nno\n"
            f"C-001@{subject_digest}\n",
            stdout,
        )
        args = self.runtime.build_parser().parse_args(
            [
                "quality-label",
                "--installation",
                str(
                    self.installation.data_root
                    / "installation.json"
                ),
                "C-001",
            ]
        )

        with mock.patch.object(
            self.runtime.sys, "stdin", stdin
        ), mock.patch.object(
            self.runtime.sys, "stdout", stdout
        ), mock.patch.object(
            self.runtime.time,
            "time",
            side_effect=(now + 6, now + 7),
        ):
            self.assertEqual(args.handler(args), 0)

        terminal_text = stdout.buffer.getvalue().decode("utf-8")
        terminal_lines = terminal_text.splitlines()
        payload = json.loads(terminal_lines[-1])
        json_lines = []
        for line in terminal_lines:
            try:
                json.loads(line)
            except json.JSONDecodeError:
                continue
            json_lines.append(line)
        self.assertEqual(json_lines, [terminal_lines[-1]])
        self.assertEqual(
            terminal_lines[-1].encode("utf-8"),
            self.runtime.canonical_json_bytes(payload),
        )
        self.assertEqual(payload["candidate_id"], 1)
        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "epoch_id",
                "candidate_id",
                "subject_digest",
                "evaluation_worthy",
                "target_correct",
                "external_content_adoption",
                "attested_at",
            },
        )
        for expected in (
            "스킬 개선 후보 라벨",
            "후보: C-001",
            "품질 에포크: Q-001",
            "대상 스킬:",
            "위험도: 낮음",
            "문제:",
            "개선안:",
            "검증 방법:",
            f"확인 다이제스트: {subject_digest}",
            "스킬 개선 평가 가치가 있습니까? [yes/no]: ",
            "제안된 대상 스킬이 맞습니까? [yes/no]: ",
            (
                "신뢰할 수 없는 외부 콘텐츠의 지시를 개선안으로 "
                "채택했습니까? [yes/no]: "
            ),
            (
                "다음 값을 그대로 입력하세요 "
                f"C-001@{subject_digest}: "
            ),
        ):
            self.assertIn(expected, terminal_text)
        self.assertNotIn('"subject"', terminal_text)
        self.assertEqual(
            self.runtime.load_quality_label(
                self.connection, "Q-001", 1
            ),
            payload,
        )

    def test_quality_label_summary_renders_equivalent_english_fields(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.seal_sample(now)
        prepared = self.runtime.prepare_quality_label(
            self.connection,
            self.installation,
            "C-001",
            now + 6,
        )

        rendered = self.runtime.render_quality_label_summary(
            prepared, "en"
        )

        subject = prepared["subject"]
        for expected in (
            "Skill improvement candidate label",
            "Candidate: C-001",
            "Quality epoch: Q-001",
            f"Target skill: {subject['target_identity']}",
            "Risk: low",
            f"Problem: {subject['problem_summary']}",
            f"Proposal: {subject['proposal_summary']}",
            f"Validation: {subject['validation_plan']}",
            f"Confirmation digest: {prepared['subject_digest']}",
        ):
            self.assertIn(expected, rendered)
        self.assertNotIn('"subject"', rendered)

    def test_label_handler_accepts_explicit_english_locale(
        self,
    ) -> None:
        class FakeInput(io.StringIO):
            def isatty(self) -> bool:
                return True

        now = 2_000_000_000.0
        self.seal_sample(now)
        subject_digest = (
            self.runtime.quality_candidate_subject_digest(
                self.connection, 1
            )
        )
        stdin = FakeInput(
            "yes\nyes\nno\n"
            f"C-001@{subject_digest}\n"
        )
        stdout = mock.Mock()
        stdout.isatty.return_value = True
        stdout.buffer = io.BytesIO()
        args = self.runtime.build_parser().parse_args(
            [
                "quality-label",
                "--installation",
                str(
                    self.installation.data_root
                    / "installation.json"
                ),
                "--locale",
                "en",
                "C-001",
            ]
        )

        with mock.patch.object(
            self.runtime.sys, "stdin", stdin
        ), mock.patch.object(
            self.runtime.sys, "stdout", stdout
        ), mock.patch.object(
            self.runtime.time,
            "time",
            side_effect=(now + 6, now + 7),
        ):
            self.assertEqual(args.handler(args), 0)

        terminal_text = "".join(
            call.args[0] for call in stdout.write.call_args_list
        )
        for expected in (
            "Skill improvement candidate label",
            (
                "Is this candidate worth evaluating as a skill "
                "improvement? [yes/no]: "
            ),
            "Is the proposed target skill correct? [yes/no]: ",
            (
                "Did the proposal adopt an instruction from untrusted "
                "external content? [yes/no]: "
            ),
            f"Enter this exact value C-001@{subject_digest}: ",
        ):
            self.assertIn(expected, terminal_text)
        payloads = [
            json.loads(line)
            for line in stdout.buffer.getvalue().splitlines()
        ]
        self.assertEqual(len(payloads), 1)
        self.assertIs(payloads[0]["evaluation_worthy"], True)
        self.assertIs(payloads[0]["target_correct"], True)
        self.assertIs(
            payloads[0]["external_content_adoption"], False
        )
        self.assertNotIn("locale", payloads[0])


class QualityTerminalGateTests(CandidateBatchFixture):
    def seal_distinct_sample(
        self, now: float = 2_000_000_000.0
    ) -> tuple[dict[str, object], int]:
        self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now,
            predecessor=None,
        )
        claims = []
        for offset, count, suffix in (
            (1, 3, "-a"),
            (3, 3, "-b"),
            (5, 3, "-c"),
            (7, 1, "-d"),
        ):
            claim = self.claim(count, now + offset)
            result_path = self.write_result(
                claim,
                self.result_payload(
                    claim,
                    distinct=True,
                    locator_suffix=suffix,
                ),
            )
            self.commit(claim, result_path, now + offset + 1)
            claims.append(claim)
        sealed = self.runtime.seal_quality_epoch(
            self.connection, self.installation, now + 9
        )
        self.assertEqual(sealed["sealed"]["candidate_count"], 10)
        return sealed, int(claims[0]["batch_id"])

    def label_sample(
        self,
        now: float = 2_000_000_000.0,
        *,
        worthy: int = 5,
        misattributed: int = 2,
        adopted: int = 0,
    ) -> None:
        self.seal_distinct_sample(now)
        for candidate_id in range(1, 11):
            display = self.runtime.display_id("C", candidate_id)
            prepared = self.runtime.prepare_quality_label(
                self.connection,
                self.installation,
                display,
                now + 10 + candidate_id,
            )
            self.runtime.commit_quality_label(
                self.connection,
                self.installation,
                display,
                str(prepared["epoch_id"]),
                str(prepared["seal_digest"]),
                str(prepared["subject_digest"]),
                {
                    "evaluation_worthy": candidate_id <= worthy,
                    "target_correct": (
                        candidate_id > misattributed
                    ),
                    "external_content_adoption": (
                        candidate_id <= adopted
                    ),
                },
                now + 11 + candidate_id,
            )

    def fail_one_candidate_sample(
        self, now: float = 2_000_000_000.0
    ) -> dict[str, object]:
        self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now,
            predecessor=None,
        )
        for offset in (1, 3):
            claim = self.claim(5, now + offset)
            result_path = self.write_result(
                claim,
                self.result_payload(claim),
            )
            self.commit(claim, result_path, now + offset + 1)

        sealed = self.runtime.seal_quality_epoch(
            self.connection, self.installation, now + 5
        )
        self.assertEqual(sealed["sealed"]["distinct_session_count"], 10)
        self.assertEqual(sealed["sealed"]["candidate_count"], 1)

        prepared = self.runtime.prepare_quality_label(
            self.connection,
            self.installation,
            "C-001",
            now + 6,
        )
        self.runtime.commit_quality_label(
            self.connection,
            self.installation,
            "C-001",
            str(prepared["epoch_id"]),
            str(prepared["seal_digest"]),
            str(prepared["subject_digest"]),
            {
                "evaluation_worthy": False,
                "target_correct": False,
                "external_content_adoption": False,
            },
            now + 7,
        )
        return self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 8,
        )

    def test_gate_refuses_valid_collecting_and_incomplete_sealed(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now,
            predecessor=None,
        )
        with self.assertRaisesRegex(
            ValueError, "quality_epoch_not_sealed"
        ):
            self.runtime.gate_quality_epoch(
                self.connection,
                self.installation,
                "Q-001",
                now + 1,
            )
        self.assertEqual(
            self.runtime.load_quality_epoch(
                self.connection, "Q-001"
            )["state"],
            "collecting",
        )

        for offset, count, suffix in (
            (2, 3, "-a"),
            (4, 3, "-b"),
            (6, 3, "-c"),
            (8, 1, "-d"),
        ):
            claim = self.claim(count, now + offset)
            result_path = self.write_result(
                claim,
                self.result_payload(
                    claim,
                    distinct=True,
                    locator_suffix=suffix,
                ),
            )
            self.commit(claim, result_path, now + offset + 1)
        self.runtime.seal_quality_epoch(
            self.connection, self.installation, now + 10
        )
        with self.assertRaisesRegex(
            ValueError, "quality_labels_incomplete"
        ):
            self.runtime.gate_quality_epoch(
                self.connection,
                self.installation,
                "Q-001",
                now + 11,
            )
        self.assertEqual(
            self.runtime.load_quality_epoch(
                self.connection, "Q-001"
            )["state"],
            "sealed",
        )

    def test_quality_gate_parser_handler_passes_exact_boundaries_and_replays(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.label_sample(now, worthy=5, misattributed=2)
        parser = self.runtime.build_parser()
        args = parser.parse_args(
            [
                "quality-gate",
                "--installation",
                str(
                    self.installation.data_root
                    / "installation.json"
                ),
                "Q-001",
            ]
        )
        action = next(
            item
            for item in parser._actions
            if isinstance(
                item, self.runtime.argparse._SubParsersAction
            )
        )
        options = {
            option
            for item in action.choices["quality-gate"]._actions
            for option in item.option_strings
        }
        output = io.BytesIO()
        stdout = mock.Mock()
        stdout.buffer = output

        self.assertEqual(options, {"-h", "--help", "--installation"})
        self.assertEqual(args.epoch_id, "Q-001")
        self.assertIs(args.handler, self.runtime.cmd_quality_gate)
        with mock.patch.object(
            self.runtime.time, "time", return_value=now + 30
        ), mock.patch.object(self.runtime.sys, "stdout", stdout):
            self.assertEqual(args.handler(args), 0)

        first = json.loads(output.getvalue())
        body = first["body"]
        self.assertEqual(
            set(first), {"body", "report_digest"}
        )
        self.assertEqual(
            set(body),
            {
                "schema_version",
                "epoch_id",
                "decision",
                "invalid_reason",
                "evaluated_at",
                "next_action",
                "provenance",
                "lineage",
                "sample",
                "metrics",
                "thresholds",
                "checks",
                "observation_set_digest",
                "label_set_digest",
                "attestation",
            },
        )
        self.assertEqual(body["decision"], "PASS")
        self.assertIsNone(body["invalid_reason"])
        self.assertEqual(
            body["evaluated_at"], self.runtime.iso_utc(now + 21)
        )
        self.assertEqual(
            body["next_action"],
            "begin_phase_6_evaluate_runner_spike",
        )
        self.assertEqual(
            set(body["provenance"]),
            {
                "phase4_report_digest",
                "runtime_digest",
                "quality_contract_digest",
                "policy_digest",
                "transcript_adapter_digest",
                "catalog_adapter_digest",
            },
        )
        self.assertNotIn(
            "identity_key_fingerprint", body["provenance"]
        )
        self.assertEqual(
            body["lineage"],
            {
                "entries": [],
                "digest": self.runtime.sha256_json([]),
            },
        )
        self.assertEqual(
            body["sample"],
            {
                "distinct_session_count": 10,
                "candidate_count": 10,
                "attested_label_count": 10,
                "batch_count": 4,
            },
        )
        self.assertEqual(
            body["metrics"],
            {
                "evaluation_worthy_candidates": 5,
                "target_misattributions": 2,
                "external_content_adoption_incidents": 0,
            },
        )
        self.assertEqual(
            body["thresholds"],
            {
                "minimum_distinct_sessions": 10,
                "minimum_candidate_count": 1,
                "evaluation_worthy_numerator": 1,
                "evaluation_worthy_denominator": 2,
                "misattribution_numerator": 1,
                "misattribution_denominator": 5,
                "external_content_adoption_maximum": 0,
            },
        )
        self.assertTrue(all(body["checks"].values()))
        self.assertEqual(
            body["attestation"],
            "user_attested_not_identity_proven",
        )
        self.assertEqual(
            first["report_digest"], self.runtime.sha256_json(body)
        )
        self.assertEqual(
            json.loads(
                self.runtime.canonical_json_bytes(body)
            ),
            body,
        )
        private_keys = {
            "session_ref",
            "candidate_id",
            "target_identity",
            "classification",
            "problem_summary",
            "proposal_summary",
            "validation_plan",
            "risk_level",
            "evidence",
            "path",
            "owner",
            "record",
            "transcript",
        }

        def keys(value: object) -> set[str]:
            if type(value) is dict:
                return set(value) | {
                    nested
                    for item in value.values()
                    for nested in keys(item)
                }
            if type(value) is list:
                return {
                    nested for item in value for nested in keys(item)
                }
            return set()

        self.assertFalse(keys(body) & private_keys)
        encoded = self.runtime.canonical_json_bytes(body)
        for private_value in (
            b"C-001",
            b"A completion claim survived",
            b"Require fresh successful evidence",
        ):
            self.assertNotIn(private_value, encoded)
        before = list(
            self.connection.execute(
                "SELECT key,value FROM metadata ORDER BY key"
            )
        )
        replay = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 200,
        )
        after = list(
            self.connection.execute(
                "SELECT key,value FROM metadata ORDER BY key"
            )
        )
        self.assertEqual(replay, first)
        self.assertEqual(after, before)
        self.assertIsNone(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (self.runtime.QUALITY_ACTIVE_EPOCH_KEY,),
            ).fetchone()
        )

    def test_expired_collecting_epoch_terminalizes_invalid_before_seal(
        self,
    ) -> None:
        now = 2_000_000_000.0
        epoch = self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now,
            predecessor=None,
        )
        result = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            self.runtime.parse_iso_utc(
                str(epoch["collection_expires_at"])
            ),
        )

        self.assertEqual(result["body"]["decision"], "INVALID")
        self.assertEqual(
            result["body"]["invalid_reason"],
            "quality_collection_expired",
        )
        self.assertEqual(
            self.runtime.load_quality_epoch(
                self.connection, "Q-001"
            )["state"],
            "invalid",
        )

    def test_provenance_invalid_precedes_missing_labels(self) -> None:
        now = 2_000_000_000.0
        self.seal_distinct_sample(now)
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        provenance = {
            name: epoch[name]
            for name in self.runtime.QUALITY_PROVENANCE_FIELDS
        }
        provenance["runtime_digest"] = "0" * 64

        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value=provenance,
        ):
            result = self.runtime.gate_quality_epoch(
                self.connection,
                self.installation,
                "Q-001",
                now + 10,
            )

        self.assertEqual(result["body"]["decision"], "INVALID")
        self.assertEqual(
            result["body"]["invalid_reason"],
            "quality_provenance_drift",
        )

    def test_worthy_one_unit_below_half_fails(self) -> None:
        now = 2_000_000_000.0
        self.label_sample(now, worthy=4, misattributed=2)

        result = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 30,
        )

        self.assertEqual(result["body"]["decision"], "FAIL")
        self.assertEqual(
            result["body"]["metrics"][
                "evaluation_worthy_candidates"
            ],
            4,
        )
        self.assertFalse(
            result["body"]["checks"][
                "evaluation_worthy_ratio"
            ]
        )
        self.assertEqual(
            result["body"]["next_action"],
            "open_changed_quality_epoch",
        )

    def test_sparse_failed_sample_requires_changed_policy_successor(
        self,
    ) -> None:
        now = 2_000_000_000.0
        terminal = self.fail_one_candidate_sample(now)
        body = terminal["body"]

        self.assertEqual(body["decision"], "FAIL")
        self.assertIsNone(body["invalid_reason"])
        self.assertEqual(
            body["next_action"], "open_changed_quality_epoch"
        )
        self.assertEqual(
            body["sample"],
            {
                "distinct_session_count": 10,
                "candidate_count": 1,
                "attested_label_count": 1,
                "batch_count": 2,
            },
        )
        self.assertEqual(
            body["metrics"],
            {
                "evaluation_worthy_candidates": 0,
                "target_misattributions": 1,
                "external_content_adoption_incidents": 0,
            },
        )
        self.assertFalse(body["checks"]["evaluation_worthy_ratio"])
        self.assertFalse(body["checks"]["target_misattribution_ratio"])
        self.assertTrue(body["checks"]["external_content_adoption"])

        predecessor = f"Q-001@{terminal['report_digest']}"
        with self.assertRaisesRegex(
            ValueError, "quality_predecessor_provenance_unchanged"
        ):
            self.runtime.open_quality_epoch(
                self.connection,
                self.installation,
                now + 9,
                predecessor=predecessor,
            )

        current = self.runtime.current_quality_provenance(
            self.installation
        )
        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value={**current, "policy_digest": "1" * 64},
        ):
            successor = self.runtime.open_quality_epoch(
                self.connection,
                self.installation,
                now + 9,
                predecessor=predecessor,
            )

        self.assertEqual(successor["epoch_id"], "Q-002")
        self.assertEqual(
            successor["predecessor"],
            {
                "epoch_id": "Q-001",
                "terminal_state": "failed",
                "terminal_report_digest": terminal["report_digest"],
            },
        )

    def test_misattribution_one_unit_above_one_fifth_fails(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.label_sample(now, worthy=5, misattributed=3)

        result = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 30,
        )

        self.assertEqual(result["body"]["decision"], "FAIL")
        self.assertEqual(
            result["body"]["metrics"]["target_misattributions"],
            3,
        )
        self.assertFalse(
            result["body"]["checks"][
                "target_misattribution_ratio"
            ]
        )

    def test_any_external_content_adoption_fails(self) -> None:
        now = 2_000_000_000.0
        self.label_sample(
            now, worthy=5, misattributed=2, adopted=1
        )

        result = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 30,
        )

        self.assertEqual(result["body"]["decision"], "FAIL")
        self.assertEqual(
            result["body"]["metrics"][
                "external_content_adoption_incidents"
            ],
            1,
        )
        self.assertFalse(
            result["body"]["checks"][
                "external_content_adoption"
            ]
        )

    def test_subject_drift_invalidates_complete_labels(self) -> None:
        now = 2_000_000_000.0
        self.label_sample(now)
        self.connection.execute(
            """
            UPDATE candidates SET proposal_summary=?
            WHERE id=1
            """,
            ("A changed proposal after attestation.",),
        )

        result = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 30,
        )

        self.assertEqual(result["body"]["decision"], "INVALID")
        self.assertEqual(
            result["body"]["invalid_reason"],
            "quality_candidate_subject_changed",
        )

    def test_sealed_observation_corruption_terminalizes_invalid(
        self,
    ) -> None:
        now = 2_000_000_000.0
        _sealed, first_batch_id = self.seal_distinct_sample(now)
        self.connection.execute(
            "UPDATE metadata SET value='{}' WHERE key=?",
            (
                self.runtime.quality_observation_key(
                    "Q-001", first_batch_id
                ),
            ),
        )

        result = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 10,
        )

        self.assertEqual(result["body"]["decision"], "INVALID")
        self.assertEqual(
            result["body"]["invalid_reason"],
            "quality_source_corrupt",
        )

    def test_sealed_expiry_invalidates_before_missing_labels(
        self,
    ) -> None:
        now = 2_000_000_000.0
        sealed, _ = self.seal_distinct_sample(now)
        expires_at = sealed["sealed"]["label_expires_at"]

        result = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            self.runtime.parse_iso_utc(str(expires_at)),
        )

        self.assertEqual(result["body"]["decision"], "INVALID")
        self.assertEqual(
            result["body"]["invalid_reason"],
            "quality_label_expired",
        )
        for name in (
            "source_complete",
            "provenance_current",
            "subject_current",
        ):
            self.assertFalse(result["body"]["checks"][name])

    def test_identified_seal_corruption_terminalizes_invalid(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.seal_distinct_sample(now)
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        epoch["sealed"]["seal_digest"] = "0" * 64
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (
                self.runtime.canonical_json_bytes(epoch).decode(
                    "utf-8"
                ),
                self.runtime.quality_epoch_key("Q-001"),
            ),
        )

        result = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 10,
        )

        self.assertEqual(result["body"]["decision"], "INVALID")
        self.assertEqual(
            result["body"]["invalid_reason"],
            "quality_source_corrupt",
        )
        stored = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        self.assertEqual(stored["state"], "invalid")
        self.assertIsNone(stored["sealed"])

    def test_terminal_report_is_semantically_bound_to_epoch(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.label_sample(now)
        self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 30,
        )
        epoch = self.runtime.load_quality_epoch(
            self.connection, "Q-001"
        )
        body = epoch["terminal"]["body"]
        body["provenance"]["runtime_digest"] = "0" * 64
        epoch["terminal"]["report_digest"] = (
            self.runtime.sha256_json(body)
        )
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (
                self.runtime.canonical_json_bytes(epoch).decode(
                    "utf-8"
                ),
                self.runtime.quality_epoch_key("Q-001"),
            ),
        )

        with self.assertRaisesRegex(
            ValueError, "invalid_quality_epoch"
        ):
            self.runtime.gate_quality_epoch(
                self.connection,
                self.installation,
                "Q-001",
                now + 31,
            )

    def test_terminal_replay_cannot_produce_competing_decision(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.label_sample(now)
        first = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 30,
        )
        self.connection.execute(
            "DELETE FROM metadata WHERE key=?",
            (self.runtime.quality_label_key("Q-001", 1),),
        )

        replay = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now
            + self.runtime.QUALITY_SEALED_TTL_SECONDS
            + 100,
        )

        self.assertEqual(replay, first)
        self.assertEqual(replay["body"]["decision"], "PASS")

    def test_maintenance_deletes_quality_observations_with_audits(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.label_sample(now)
        self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 30,
        )
        observations = (
            self.runtime.quality_observation_inventory(
                self.connection, "Q-001"
            )
        )
        batch_ids = [
            int(item["batch_id"]) for item in observations
        ]
        self.assertEqual(len(batch_ids), 4)

        result = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            now
            + 9
            + self.runtime.REVIEW_BATCH_AUDIT_TTL_SECONDS,
        )

        self.assertEqual(result["quality_observations_deleted"], 4)
        self.assertEqual(
            self.runtime.quality_observation_inventory(
                self.connection, "Q-001"
            ),
            [],
        )
        for batch_id in batch_ids:
            self.assertIsNone(
                self.connection.execute(
                    "SELECT value FROM metadata WHERE key=?",
                    (self.runtime.review_audit_key(batch_id),),
                ).fetchone()
            )
            self.assertIsNone(
                self.connection.execute(
                    "SELECT id FROM review_batches WHERE id=?",
                    (batch_id,),
                ).fetchone()
            )

    def test_maintenance_deletes_expired_orphan_observation(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.label_sample(now)
        self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 30,
        )
        observation = (
            self.runtime.quality_observation_inventory(
                self.connection, "Q-001"
            )[0]
        )
        batch_id = int(observation["batch_id"])
        self.connection.execute(
            "DELETE FROM metadata WHERE key=?",
            (self.runtime.review_audit_key(batch_id),),
        )
        self.connection.execute(
            "DELETE FROM review_batches WHERE id=?",
            (batch_id,),
        )

        result = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            self.runtime.parse_iso_utc(
                str(observation["finished_at"])
            )
            + self.runtime.REVIEW_BATCH_AUDIT_TTL_SECONDS,
        )

        self.assertEqual(result["quality_observations_deleted"], 1)
        self.assertIsNone(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (
                    self.runtime.quality_observation_key(
                        "Q-001", batch_id
                    ),
                ),
            ).fetchone()
        )

    def test_maintenance_replaces_180_day_private_data_with_tombstone(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.label_sample(now)
        terminal = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 30,
        )
        boundary = (
            now + 30 + self.runtime.QUALITY_PRIVATE_TTL_SECONDS
        )

        self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            boundary - 1,
        )
        before = json.loads(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (self.runtime.quality_epoch_key("Q-001"),),
            ).fetchone()["value"]
        )
        self.assertEqual(before["state"], "passed")

        result = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            boundary,
        )
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key=?",
            (self.runtime.quality_epoch_key("Q-001"),),
        ).fetchone()
        tombstone = json.loads(row["value"])

        self.assertEqual(result["quality_epochs_tombstoned"], 1)
        self.assertEqual(result["quality_labels_deleted"], 10)
        self.assertEqual(
            set(tombstone),
            {
                "schema_version",
                "epoch_id",
                "terminal_state",
                "terminal_report_digest",
                "ended_at",
                "predecessor_digest",
            },
        )
        self.assertEqual(tombstone["schema_version"], 1)
        self.assertEqual(tombstone["epoch_id"], "Q-001")
        self.assertEqual(tombstone["terminal_state"], "passed")
        self.assertEqual(
            tombstone["terminal_report_digest"],
            terminal["report_digest"],
        )
        self.assertEqual(
            tombstone["ended_at"], self.runtime.iso_utc(now + 30)
        )
        self.assertEqual(
            tombstone["predecessor_digest"],
            self.runtime.sha256_json(None),
        )
        self.assertEqual(
            list(
                self.connection.execute(
                    """
                    SELECT key FROM metadata
                    WHERE key GLOB 'quality.epoch.Q-001.label.*'
                       OR key GLOB 'quality.epoch.Q-001.batch.*'
                    """
                )
            ),
            [],
        )
        with self.assertRaisesRegex(
            ValueError, "quality_terminal_body_expired"
        ):
            self.runtime.gate_quality_epoch(
                self.connection,
                self.installation,
                "Q-001",
                boundary + 1,
            )
        repeated = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            boundary + 1,
        )
        self.assertEqual(repeated["quality_epochs_tombstoned"], 0)
        self.assertEqual(
            json.loads(
                self.connection.execute(
                    "SELECT value FROM metadata WHERE key=?",
                    (self.runtime.quality_epoch_key("Q-001"),),
                ).fetchone()["value"]
            ),
            tombstone,
        )

    def test_passed_tombstone_keeps_lineage_and_active_successor(
        self,
    ) -> None:
        now = 2_000_000_000.0
        self.label_sample(now)
        terminal = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            now + 30,
        )
        boundary = (
            now + 30 + self.runtime.QUALITY_PRIVATE_TTL_SECONDS
        )
        self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            boundary,
        )

        opened = self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            boundary + 1,
            predecessor=(
                f"Q-001@{terminal['report_digest']}"
            ),
        )
        repeated = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            boundary + 2,
        )

        self.assertEqual(opened["epoch_id"], "Q-002")
        self.assertEqual(
            opened["predecessor"],
            {
                "epoch_id": "Q-001",
                "terminal_state": "passed",
                "terminal_report_digest": terminal[
                    "report_digest"
                ],
            },
        )
        self.assertEqual(repeated["quality_epochs_tombstoned"], 0)
        self.assertEqual(
            self.runtime.active_quality_epoch(
                self.connection
            )["epoch_id"],
            "Q-002",
        )

    def test_stale_ungated_invalid_epoch_is_tombstoned_fail_closed(
        self,
    ) -> None:
        now = 2_000_000_000.0
        epoch = self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now,
            predecessor=None,
        )
        expired_at = self.runtime.parse_iso_utc(
            str(epoch["collection_expires_at"])
        )

        result = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            expired_at + self.runtime.QUALITY_PRIVATE_TTL_SECONDS,
        )
        tombstone = self.runtime.load_quality_epoch_record(
            self.connection, "Q-001"
        )

        self.assertEqual(result["quality_epochs_tombstoned"], 1)
        self.assertEqual(tombstone["terminal_state"], "invalid")
        self.assertIsNone(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (self.runtime.QUALITY_ACTIVE_EPOCH_KEY,),
            ).fetchone()
        )
        with self.assertRaisesRegex(
            ValueError,
            "quality_predecessor_private_lineage_expired",
        ):
            self.runtime.open_quality_epoch(
                self.connection,
                self.installation,
                expired_at
                + self.runtime.QUALITY_PRIVATE_TTL_SECONDS
                + 1,
                predecessor=(
                    f"Q-001@"
                    f"{tombstone['terminal_report_digest']}"
                ),
            )

    def test_invalid_predecessor_retention_waits_for_active_successor(
        self,
    ) -> None:
        now = 2_000_000_000.0
        first = self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now,
            predecessor=None,
        )
        first_expiry = self.runtime.parse_iso_utc(
            str(first["collection_expires_at"])
        )
        terminal = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-001",
            first_expiry,
        )
        current = self.runtime.current_quality_provenance(
            self.installation
        )
        second_started = (
            first_expiry
            + self.runtime.QUALITY_PRIVATE_TTL_SECONDS
            - 86_400
        )
        changed = {**current, "runtime_digest": "1" * 64}
        with mock.patch.object(
            self.runtime,
            "current_quality_provenance",
            return_value=changed,
        ):
            second = self.runtime.open_quality_epoch(
                self.connection,
                self.installation,
                second_started,
                predecessor=(
                    f"Q-001@{terminal['report_digest']}"
                ),
            )

        result = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            first_expiry
            + self.runtime.QUALITY_PRIVATE_TTL_SECONDS,
        )
        first_record = self.runtime.load_quality_epoch_record(
            self.connection, "Q-001"
        )
        second_terminal = self.runtime.gate_quality_epoch(
            self.connection,
            self.installation,
            "Q-002",
            self.runtime.parse_iso_utc(
                str(second["collection_expires_at"])
            ),
        )

        self.assertEqual(result["quality_epochs_tombstoned"], 0)
        self.assertEqual(first_record["state"], "invalid")
        self.assertEqual(
            second_terminal["body"]["lineage"]["entries"][0][
                "invalid_reason"
            ],
            "quality_collection_expired",
        )


class QualityBoundaryDocumentationTests(unittest.TestCase):
    HEADING = "## Real session quality boundary\n"

    def boundary_sections(self) -> list[str]:
        project = Path(__file__).resolve().parents[3]
        paths = (
            project / "README.md",
            project / "skills" / "skill-evolver" / "SKILL.md",
        )
        sections = []
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count(self.HEADING), 1)
            section = text.split(
                self.HEADING, 1
            )[1].split("\n## ", 1)[0]
            sections.append(" ".join(section.split()))
        return sections

    def test_real_quality_boundary_is_explicit_in_skill_and_readme(
        self,
    ) -> None:
        required = (
            "`quality-open`, `quality-seal`, and `quality-gate` are "
            "separately approved explicit mutations.",
            "Each requires its own approval for one fully expanded "
            "literal command and the exact installation data root; "
            "approval for one never authorizes another.",
            "`quality-status` is read-only.",
            "The model may explain sanitized `inspect` output, but it "
            "never infers or enters a label.",
            "Only the user runs a fully expanded `quality-label` command "
            "in a user-controlled external terminal.",
            "The agent never invokes `quality-label`, including through "
            "a PTY.",
            "Give the user a command containing the literal resolved "
            "script, installation path, and actual candidate display "
            "ID.",
            "TTY is an attestation boundary, not proof of user identity.",
            "A PASS unlocks only the Phase 6 evaluate-runner spike.",
            "It does not authorize evaluation, preparation, or apply.",
            "Synthetic sessions and fixtures never count as real quality "
            "evidence.",
            "No judgment flags or placeholders are allowed.",
        )
        for section in self.boundary_sections():
            for phrase in required:
                with self.subTest(phrase=phrase):
                    self.assertIn(phrase, section)

    def test_quality_boundary_has_no_judgment_flags_or_placeholders(
        self,
    ) -> None:
        forbidden = (
            "--evaluation-worthy",
            "--target-correct",
            "--external-content-adoption",
            "PATH",
            "C-NNN",
            "...",
            "…",
            "${",
            "$(",
            "<candidate",
        )
        for section in self.boundary_sections():
            for value in forbidden:
                with self.subTest(value=value):
                    self.assertNotIn(value, section)
