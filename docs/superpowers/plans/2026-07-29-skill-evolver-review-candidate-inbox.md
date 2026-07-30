# Skill Evolver Review Candidate Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate one strict declarative decision for every claimed session, atomically create or merge a bounded candidate inbox, and retain only privacy-safe recurrence aggregates without exposing a CLI surface.

**Architecture:** Plans 4A and 4B provide trusted transcript/catalog adapters, frozen batch contracts, owner-bound leases, and inode-bound temporary result files. This plan adds deterministic result validation, Unicode normalization and secret redaction, then performs candidate, evidence, recurrence-link, generation, and batch completion in one caller-owned SQLite transaction. Existing schema v1 tables and metadata rows are reused; no migration, dependency, model call, Hook change, or installed-skill write is introduced.

**Tech Stack:** Python 3.9 standard library, SQLite schema v1 with `journal_mode=DELETE`, `unittest`, canonical JSON, SHA-256, HMAC-SHA-256

## Global Constraints

- Run every command from the exact workspace root `/Users/igyeongseob/Documents/오픈소스`.
- The entry gate is `/Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/runtime-queue.json` with `schema_version == 1`, `decision == "PASS"`, twelve true checks, implementation commit `d7fb9b95d8f5816fde13a274a9b421c7b69412c7`, seven matching production digests, and predecessor digest `ced4503adb44bd041de063c04e0c6c64d0831370fc12e96a920fe97244d8ae15`.
- Keep `SCHEMA_VERSION = 1`; reuse `review_batches`, `review_items`, `candidates`, `candidate_evidence`, and `metadata`.
- Do not add a dependency, model call, network call, Hook registration, schema bump, background worker, queue, skill mutation, staging write, or snapshot write.
- Do not add parser subcommands or change `SKILL.md`, `README.md`, `.codex-plugin/plugin.json`, `hooks/hooks.json`, or `.agents/plugins/marketplace.json` in this plan.
- One result object is required for every final-contract `session_ref`; unknown, omitted, or duplicate refs reject the whole result.
- Each session yields at most one candidate across all fingerprints and generations. Each batch introduces at most three new fingerprints.
- Candidate evidence is eligible only for `explicit_correction:user_direct`, `unnecessary_rework:user_direct`, or `verification_failure:tool_output`.
- The exact model enums are:
  - `problem_category`: `verification`, `instruction_clarity`, `workflow`, `safety`, `efficiency`, `tooling`;
  - `risk_level`: `low`, `medium`, `high`;
  - `excluded_reason`: `no_reusable_improvement`, `environment`, `one_off`, `external_content`, `attribution_uncertain`, `unsupported_target`, `privacy_redaction_required`.
- Free-text character limits after NFKC normalization are 280 for problem, proposal, and evidence summaries; 160 for target locator and proposal intent; and 500 for validation plan.
- A result file is at most 262,144 bytes; the validated canonical result is at most 32,768 bytes; each candidate references at most three evidence records.
- `candidate_limit` is an internal exclusion only. It is never accepted from model JSON.
- Deferred candidates become stale after 30 days. Rejected fingerprints remain tombstoned for 90 days. Rejected or stale target paths, classification text, summaries, validation plans, risk levels, and individual evidence are retained for at most 90 terminal days; `target_path IS NULL` is the system-owned redaction invariant. Session identity, per-session evidence, and recurrence links are removed at the existing 180-day dedupe boundary.
- Raw transcript text, session IDs, `session_key`, transcript paths, record refs, result paths, and free text never enter batch audit or evidence-aggregate metadata.
- Stage only the paths named in each task. Never use `git add .`.

## Entry Gate

Test-count contract: the committed Plan 4B boundary is exactly 332 tests
before Plan 4C. This plan adds exactly 20 methods: 18 in `test_review.py` and
2 in `test_capture.py`, for exactly 352 tests at exit. The exit inventory is
97 `test_review.py` tests and 82 `test_capture.py` tests, with the same three
historical full-suite skips.

- [ ] **Run the immutable Phase 3 gate and baseline suite (2–5 min)**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -c 'import hashlib,json,subprocess; from pathlib import Path; path=Path("skill-evolver/docs/release-reports/runtime-queue.json"); assert hashlib.sha256(path.read_bytes()).hexdigest() == "c551176e5925a4c18ed54875ca28dcd089c0de2b38a873819fe219a8dcf29674"; report=json.loads(path.read_text(encoding="utf-8")); assert report["schema_version"] == 1; assert report["decision"] == "PASS"; assert len(report["checks"]) == 12 and all(report["checks"].values()); commit=report["implementation_commit"]; assert commit == "d7fb9b95d8f5816fde13a274a9b421c7b69412c7"; assert len(report["production_sha256"]) == 7; actual={name:hashlib.sha256(subprocess.check_output(["git","show",f"{commit}:{name}"])).hexdigest() for name in report["production_sha256"]}; assert actual == report["production_sha256"]; assert report["upstream"]["predecessor_sha256"] == "ced4503adb44bd041de063c04e0c6c64d0831370fc12e96a920fe97244d8ae15"; print("runtime-queue-entry-gate: PASS")'
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py'
git diff --check
git status --short
```

Expected: `runtime-queue-entry-gate: PASS`; full discovery prints `Ran 332
tests` and `OK (skipped=3)`, with only the three historical `test_skeleton.py`
skips; `git diff --check` exits `0`; status contains no uncommitted Plan 4A or
Plan 4B implementation path.

## Exact Dependencies from Plan 4A

Plan 4C consumes these constants without changing their values:

```python
REVIEW_BATCH_SESSIONS_MAX = 5
TRANSCRIPT_SESSION_MAX_BYTES = 2_097_152
TRANSCRIPT_SESSION_MAX_RECORDS = 100
REVIEW_BATCH_MAX_BYTES = 8_388_608
MODEL_ENVELOPE_MAX_BYTES = 131_072
CATALOG_MAX_SKILLS = 512
CATALOG_FRONTMATTER_MAX_BYTES = 65_536
CATALOG_INSPECT_MAX_BYTES = 65_536
CATALOG_EXPORT_MAX_BYTES = 49_152
CATALOG_IDENTITY_MAX_BYTES = 272
CATALOG_DISPLAY_NAME_MAX_BYTES = 128
CATALOG_DESCRIPTION_MAX_BYTES = 384
POLICY_MAX_BYTES = 8_192
RESULT_SCHEMA_INSTRUCTIONS_MAX_BYTES = 8_192
CLAIM_CONTRACT_OVERHEAD_MAX_BYTES = 8_192
```

Plan 4A dataclasses are exactly:

- `ReviewRuntime(mutable_skill_roots: tuple of Path values, review_batch_sessions: int, max_transcript_bytes: int, max_transcript_records: int, max_review_batch_bytes: int, max_candidates_per_session: int, max_candidates_per_batch: int, model_envelope_max_bytes: int, catalog_max_skills: int, catalog_frontmatter_max_bytes: int, catalog_inspect_max_bytes: int, catalog_export_max_bytes: int, catalog_identity_max_bytes: int, catalog_display_name_max_bytes: int, catalog_description_max_bytes: int, policy_max_bytes: int, result_schema_instructions_max_bytes: int, claim_contract_overhead_max_bytes: int)`.
- `CatalogEntry(identity: str, display_name: str, description: str, skill_dir: Path, skill_sha256: str)`.
- `CatalogSnapshot(entries: tuple of CatalogEntry values, export_bytes: bytes, snapshot_digest: str, rejected_count: int)`.
- `TranscriptLocator(path: Path, size: int, mtime_ns: int, device: int, inode: int)`.
- `FrozenTranscript(review_item_id: int, session_key: str, generation: int, transcript_epoch: int, frozen_from: int, frozen_to: int, locator: TranscriptLocator, read_path: Path)`.
- `TranscriptRecord(source_kind: str, text: str, evidence_eligible: bool, scope: str, byte_start: int, byte_end: int)`.
- `TranscriptExport(records: tuple of TranscriptRecord values, delta_source_bytes: int, context_source_bytes: int, canonical_records_bytes: int, read_path_changed: bool)`.

Plan 4A functions are exactly:

- `load_review_runtime() -> ReviewRuntime`
- `load_improvement_policy(runtime: ReviewRuntime) -> bytes`
- `improvement_policy_digest(policy: bytes) -> str`
- `catalog_adapter_contract(runtime: ReviewRuntime) -> dict[str, object]`
- `catalog_adapter_digest(runtime: ReviewRuntime) -> str`
- `transcript_adapter_contract(runtime: ReviewRuntime) -> dict[str, object]`
- `transcript_adapter_digest(runtime: ReviewRuntime) -> str`
- `parse_frontmatter_scalars(raw: bytes, maximum: int) -> tuple[str, str]`
- `build_catalog_snapshot(runtime: ReviewRuntime) -> CatalogSnapshot`
- `catalog_export_payload(snapshot: CatalogSnapshot) -> list[dict[str, str]]`
- `resolve_catalog_target(snapshot: CatalogSnapshot, target_identity: str) -> CatalogEntry`
- `inspect_catalog_target(runtime: ReviewRuntime, snapshot: CatalogSnapshot, target_identity: str) -> bytes`
- `transcript_locator_payload(locator: TranscriptLocator) -> dict[str, object]`
- `transcript_locator_digest(locator: TranscriptLocator) -> str`
- `transcript_content_identity(locator: TranscriptLocator) -> tuple[int, int, int, int]`, returning size, mtime-nanoseconds, device, and inode; it is adapter-only and does not replace the path-sensitive frozen generation locator digest.
- `frozen_transcript_from_row(row: sqlite3.Row) -> FrozenTranscript`
- `read_frozen_transcript(installation: Installation, frozen: FrozenTranscript, config: Config, runtime: ReviewRuntime) -> TranscriptExport`

`CatalogAdapterError.code` is one of `catalog_root_invalid`, `catalog_inventory_saturated`, `catalog_export_too_large`, `catalog_target_unknown`, `catalog_target_changed`, or `catalog_inspect_too_large`. `TranscriptAdapterError.code` is retryable for `transcript_missing`, `transcript_changed`, and `transcript_partial`; it is terminal for `oversized_session` and `unsupported_transcript`. A same-inode path move sets `TranscriptExport.read_path_changed` to `True`; an inode or device change is retryable `transcript_changed`.

## Exact Dependencies from Plan 4B

Plan 4C consumes these exact Plan 4B interfaces without renaming them:

- `load_review_contract(connection: sqlite3.Connection, batch_id: int, expected_stage: str) -> dict[str, object]`
- `load_review_result_binding(connection: sqlite3.Connection, batch_id: int) -> dict[str, object]`
- `require_live_review_batch(connection: sqlite3.Connection, installation: Installation, batch_id: int, owner_token: str, now: float) -> tuple[sqlite3.Row, dict[str, object], str]`
- `require_bound_review_result_binding(connection: sqlite3.Connection, batch_id: int, opened: BoundReviewResult) -> dict[str, object]`; the caller owns an active transaction and invokes it before candidate or evidence mutation.
- `read_bound_review_result(connection: sqlite3.Connection, installation: Installation, batch_id: int, owner_token: str, result_path: Path, now: float) -> BoundReviewResult`
- `replace_invalid_review_result(connection: sqlite3.Connection, installation: Installation, batch_id: int, owner_token: str, opened: BoundReviewResult, now: float) -> Path`
- `delete_bound_review_result(opened: BoundReviewResult) -> bool`
- `finalize_review_batch(connection: sqlite3.Connection, batch_id: int, owner_digest: str, terminal_status: str, candidate_count: int, exclusion_counts: dict[str, int], now: float) -> dict[str, object]`; the caller owns the transaction.
- `complete_batch_review_generation(connection: sqlite3.Connection, review_item_id: int, batch_id: int, owner_digest: str, expected_generation: int, expected_epoch: int, expected_from: int, expected_to: int, expected_locator_digest: str, outcome: str, reason: Optional[str], now: float) -> dict[str, object]`; the caller owns the transaction.
- `claim_review_batch(connection: sqlite3.Connection, installation: Installation, config: Config, now: float) -> dict[str, object]`
- `heartbeat_review_batch(connection: sqlite3.Connection, installation: Installation, batch_id: int, owner_token: str, now: float, config: Config) -> bool`
- `abort_review_batch(connection: sqlite3.Connection, installation: Installation, batch_id: int, owner_token: str, now: float) -> dict[str, object]`
- `cleanup_review_results(now: float) -> dict[str, int]`

The existing Phase 3 interface `complete_review_generation(connection: sqlite3.Connection, session_key_value: str, owner: str, outcome: str, reason: Optional[str], now: float) -> dict[str, object]` remains unchanged.

`ReviewResultError(ValueError)` exposes `code: str` and `opened: Optional[BoundReviewResult]`. `read_bound_review_result` raises with `opened is None` for cross-batch, unallocated, and unsafe-path input so the foreign file is preserved. It raises with the exact bound `basename`, `device`, and `inode` in `opened` for a batch-bound oversized or changed-content result. Plan 4C rotates only semantic failures after a successful `BoundReviewResult`, or a `ReviewResultError` whose `opened` field is not `None`.

Plan 4B metadata keys are exactly:

```python
def review_contract_key(batch_id: int) -> str:
    return f"review.batch.{batch_id}.contract"


def review_result_key(batch_id: int) -> str:
    return f"review.batch.{batch_id}.result"


def review_audit_key(batch_id: int) -> str:
    return f"review.batch.{batch_id}.audit"
```

`BoundReviewResult` fields are exactly `batch_id`, `path`, `basename`, `device`, `inode`, and `encoded`. The result binding value fields are exactly `schema_version`, `batch_id`, `basename`, `device`, `inode`, and `allocated_at`.

The final contract top-level fields are exactly `schema_version`, `stage`, `batch_id`, `owner_digest`, `sessions`, `policy_digest`, `transcript_adapter_digest`, `catalog_adapter_digest`, `catalog_snapshot_digest`, `created_at`, and `lease_expires_at`; `stage` is `final`. Each session contains exactly `session_ref`, `review_item_id`, `expected_generation`, `frozen_epoch`, `frozen_from`, `frozen_to`, `frozen_locator_digest`, and `records`. Each record contains exactly `record_ref`, `source_kind`, `evidence_eligible`, and `content_hmac`.

The audit fields are exactly `schema_version`, `batch_id`, `terminal_status`, `owner_digest`, `policy_digest`, `transcript_adapter_digest`, `catalog_adapter_digest`, `catalog_snapshot_digest`, `session_count`, `generation_count`, `candidate_count`, `exclusion_counts`, `batch_capacity_released`, and `finished_at`. `finalize_review_batch` merges persisted export exclusions and capacity with only the semantic exclusion delta supplied by Plan 4C, and raises `review_batch_members_remain` before metadata deletion if any batch member is still live.

`claim_review_batch` generates the raw lease owner with `secrets.token_hex(32)`. Only the ready claim output returns that raw token. `review_items.lease_owner`, the seed and final contracts, and the audit store only the HMAC-SHA-256 owner digest produced in domain `b"review-owner\0"`. A ready claim has exactly `schema_version`, `status`, `batch_id`, `owner_token`, `contract_digest`, `lease_expires_at`, `result_path`, and `envelope`; `status` is `ready`.

Plan 4B's reusable test base is `BatchExportTestCase(unittest.TestCase)`. Its exact helpers are `fixed_review_inputs(self)` as a context manager; `insert_pending(self, connection, number, *, text="record\n", error_code: Optional[str]=None, now=2_000_000_000.0) -> sqlite3.Row`; `make_export(self, *texts, context=0) -> TranscriptExport`; `write_result_bytes(self, path: Path, value: bytes, now: float) -> None`; and `claim_ready_batch(self, connection, exports, *, now=2_000_000_000.0) -> dict[str, object]`. The claim helper patches the fixed Plan 4A inputs and private result parent, invokes `claim_review_batch`, and asserts `status == "ready"`.

The result namespace constants are `REVIEW_RESULT_PARENT = Path("/private/tmp")`, `REVIEW_RESULT_PREFIX = "skill-evolver-review-results-"`, name pattern `\Aresult-[0-9a-f]{32}\.json\Z`, maximum bytes `262_144`, maximum files `200`, detection scan `201`, stale age `3_600` seconds, terminal audit retention `90 * 86_400` seconds, and `REVIEW_MAINTENANCE_BATCH_MAX = 200`. `review_result_root() -> Path` resolves `Path("/private/tmp") / f"skill-evolver-review-results-{os.getuid()}"`. `cleanup_review_results(now)` returns exactly `result_scan_entries`, `result_files_deleted`, `result_files_preserved`, and `result_scan_saturated`; entry 201 raises `ValueError("review_result_namespace_saturated")` before deleting anything.

Plan 4B already commits the maintenance database transaction before deleting
captured bound result files and attempting best-effort namespace cleanup. It
also owns the ordered, `LIMIT 200` terminal batch cohort and atomically deletes
the exact audit keys and batch IDs selected by that cohort. Plan 4C preserves
both behaviors unchanged.

The empty claim output is exactly `{"schema_version":1,"status":"empty","batch_id":null,"owner_token":null,"claims":[],"contract":null}`. Failed claims have exactly `schema_version`, `status`, `batch_id`, and `error_code`; the configuration and zero-survivor codes are `configuration_envelope_error` and `no_exportable_sessions`. `abort_review_batch` returns the exact audit object, releases rows error-free and cursor-stable, removes contract/result metadata and writes the audit in one transaction, then identity-safely deletes the bound file.

## File Structure

| Path | Responsibility |
|---|---|
| `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py` | Strict result validator, deterministic sanitizer, fingerprint and recurrence metadata, atomic candidate commit, and 30/90/180-day maintenance. |
| `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/test_review.py` | Result-schema, rollback, recurrence, limits, revival, and retention tests built on Plans 4A and 4B helpers. |

---

### Task 1: Enforce the Exact Declarative Result Schema

**Files:**
- Modify: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/test_review.py`

**Interfaces:**
- Consumes: the Plan 4B final-contract shape and Plan 4A catalog identities.
- Produces: `normalize_candidate_text(value: object, maximum: int) -> str` and `validate_declarative_result(payload: object, contract: dict[str, object], allowed_target_identities: frozenset[str]) -> dict[str, object]`.

- [ ] **Step 1 (2–5 min): Add strict-schema, enum, exact-coverage, provenance, Unicode-redaction, and residual-secret tests**

Add `import copy` to `test_review.py`, then add this class before its `unittest.main()` block:

```python
class ReviewCandidateValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.contract = {
            "schema_version": 1,
            "stage": "final",
            "batch_id": 7,
            "owner_digest": "a" * 64,
            "sessions": [
                {
                    "session_ref": "S-001",
                    "review_item_id": 11,
                    "expected_generation": 2,
                    "frozen_epoch": 0,
                    "frozen_from": 10,
                    "frozen_to": 90,
                    "frozen_locator_digest": "b" * 64,
                    "records": [
                        {
                            "record_ref": "S-001-R-001",
                            "source_kind": "user_direct",
                            "evidence_eligible": True,
                            "content_hmac": "c" * 64,
                        },
                        {
                            "record_ref": "S-001-R-002",
                            "source_kind": "assistant",
                            "evidence_eligible": False,
                            "content_hmac": "d" * 64,
                        },
                    ],
                },
                {
                    "session_ref": "S-002",
                    "review_item_id": 12,
                    "expected_generation": 1,
                    "frozen_epoch": 1,
                    "frozen_from": 0,
                    "frozen_to": 70,
                    "frozen_locator_digest": "e" * 64,
                    "records": [
                        {
                            "record_ref": "S-002-R-001",
                            "source_kind": "tool_output",
                            "evidence_eligible": True,
                            "content_hmac": "f" * 64,
                        }
                    ],
                },
            ],
            "policy_digest": "1" * 64,
            "transcript_adapter_digest": "2" * 64,
            "catalog_adapter_digest": "3" * 64,
            "catalog_snapshot_digest": "4" * 64,
            "created_at": "2033-05-18T03:33:20Z",
            "lease_expires_at": "2033-05-18T03:43:20Z",
        }
        self.payload = {
            "schema_version": 1,
            "contract_digest": self.runtime.sha256_json(self.contract),
            "sessions": [
                {
                    "session_ref": "S-001",
                    "decision": "candidate",
                    "target_identity": "user-skill:verification-before-completion",
                    "classification": {
                        "problem_category": "verification",
                        "target_locator": "completion claim",
                        "proposal_intent": "require successful verification",
                    },
                    "problem_summary": (
                        "A leaked ｓｋ－abcdefghijklmnopqrst value was corrected."
                    ),
                    "proposal_summary": (
                        "Require fresh successful evidence before completion."
                    ),
                    "validation_plan": (
                        "Reproduce the failure and add focused regressions."
                    ),
                    "risk_level": "low",
                    "evidence": [
                        {
                            "record_ref": "S-001-R-001",
                            "signal_type": "explicit_correction",
                            "summary": "The user corrected a completion claim.",
                        }
                    ],
                },
                {
                    "session_ref": "S-002",
                    "decision": "excluded",
                    "excluded_reason": "environment",
                },
            ],
        }
        self.targets = frozenset(
            {"user-skill:verification-before-completion"}
        )

    def test_exact_schema_normalizes_unicode_and_redacts_secret(self) -> None:
        result = self.runtime.validate_declarative_result(
            self.payload, self.contract, self.targets
        )
        self.assertEqual(
            [item["session_ref"] for item in result["sessions"]],
            ["S-001", "S-002"],
        )
        self.assertEqual(
            result["sessions"][0]["problem_summary"],
            "A leaked [REDACTED:api-key] value was corrected.",
        )
        self.assertEqual(
            result["sessions"][1],
            {
                "session_ref": "S-002",
                "decision": "excluded",
                "excluded_reason": "environment",
            },
        )

    def test_result_requires_the_exact_session_ref_set_once(self) -> None:
        mutations = []
        missing = copy.deepcopy(self.payload)
        missing["sessions"].pop()
        mutations.append(missing)
        duplicate = copy.deepcopy(self.payload)
        duplicate["sessions"][1]["session_ref"] = "S-001"
        mutations.append(duplicate)
        unknown = copy.deepcopy(self.payload)
        unknown["sessions"][1]["session_ref"] = "S-999"
        mutations.append(unknown)
        for payload in mutations:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(
                    ValueError, "invalid_result_session_coverage"
                ):
                    self.runtime.validate_declarative_result(
                        payload, self.contract, self.targets
                    )

    def test_unknown_keys_enums_and_context_evidence_fail_closed(self) -> None:
        cases = []
        unknown_key = copy.deepcopy(self.payload)
        unknown_key["sessions"][0]["confidence"] = 1
        cases.append(unknown_key)
        category = copy.deepcopy(self.payload)
        category["sessions"][0]["classification"][
            "problem_category"
        ] = "other"
        cases.append(category)
        risk = copy.deepcopy(self.payload)
        risk["sessions"][0]["risk_level"] = "critical"
        cases.append(risk)
        reason = copy.deepcopy(self.payload)
        reason["sessions"][1]["excluded_reason"] = "candidate_limit"
        cases.append(reason)
        context = copy.deepcopy(self.payload)
        context["sessions"][0]["evidence"][0][
            "record_ref"
        ] = "S-001-R-002"
        cases.append(context)
        bad_pair = copy.deepcopy(self.payload)
        bad_pair["sessions"][0]["evidence"][0][
            "signal_type"
        ] = "verification_failure"
        cases.append(bad_pair)
        boolean_version = copy.deepcopy(self.payload)
        boolean_version["schema_version"] = True
        cases.append(boolean_version)
        non_string_signal = copy.deepcopy(self.payload)
        non_string_signal["sessions"][0]["evidence"][0][
            "signal_type"
        ] = ["explicit_correction"]
        cases.append(non_string_signal)
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.runtime.validate_declarative_result(
                        payload, self.contract, self.targets
                    )

    def test_residual_private_key_rejects_the_whole_result(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["sessions"][0]["validation_plan"] = (
            "-----BEGIN PRIVATE KEY-----"
        )
        with self.assertRaisesRegex(ValueError, "residual_secret"):
            self.runtime.validate_declarative_result(
                payload, self.contract, self.targets
            )
```

- [ ] **Step 2 (2–5 min): Run the new tests and verify RED**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -k ReviewCandidateValidationTests \
  -v
```

Expected: the four tests error because `validate_declarative_result` is not defined.

- [ ] **Step 3 (2–5 min): Add exact enums, key sets, normalization, and secret rules**

Add `import re` beside the current standard-library imports in `evolver.py`, then add this block after `sha256_json`:

```python
PROBLEM_CATEGORIES = frozenset(
    {
        "verification",
        "instruction_clarity",
        "workflow",
        "safety",
        "efficiency",
        "tooling",
    }
)
RISK_LEVELS = frozenset({"low", "medium", "high"})
EXCLUDED_REASONS = frozenset(
    {
        "no_reusable_improvement",
        "environment",
        "one_off",
        "external_content",
        "attribution_uncertain",
        "unsupported_target",
        "privacy_redaction_required",
    }
)
SIGNAL_SOURCE_PAIRS = frozenset(
    {
        ("explicit_correction", "user_direct"),
        ("unnecessary_rework", "user_direct"),
        ("verification_failure", "tool_output"),
    }
)
RESULT_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "contract_digest", "sessions"}
)
CANDIDATE_RESULT_KEYS = frozenset(
    {
        "session_ref",
        "decision",
        "target_identity",
        "classification",
        "problem_summary",
        "proposal_summary",
        "validation_plan",
        "risk_level",
        "evidence",
    }
)
EXCLUDED_RESULT_KEYS = frozenset(
    {"session_ref", "decision", "excluded_reason"}
)
CLASSIFICATION_KEYS = frozenset(
    {"problem_category", "target_locator", "proposal_intent"}
)
EVIDENCE_RESULT_KEYS = frozenset(
    {"record_ref", "signal_type", "summary"}
)
RESULT_FILE_MAX_BYTES = 262_144
CANONICAL_RESULT_MAX_BYTES = 32_768
EVIDENCE_PER_CANDIDATE_MAX = 3
SECRET_REDACTIONS = (
    (
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        "[REDACTED:api-key]",
    ),
    (
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        "[REDACTED:access-token]",
    ),
    (
        re.compile(
            r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b"
        ),
        "[REDACTED:bearer-token]",
    ),
    (
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|"
            r"access[_-]?token)\s*[:=]\s*[^\s,;]{4,}"
        ),
        "[REDACTED:secret]",
    ),
)
RESIDUAL_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
        r"[A-Za-z0-9_-]{8,}\b"
    ),
)


def require_exact_object(
    value: object,
    expected_keys: frozenset[str],
    error_code: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError(error_code)
    return value


def normalize_candidate_text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_candidate_text")
    normalized = unicodedata.normalize("NFKC", value)
    if "\r" in normalized or "\n" in normalized:
        raise ValueError("multiline_candidate_text")
    stripped = normalized.strip()
    if (
        not stripped
        or stripped.startswith(">")
        or stripped.startswith("```")
        or "```" in stripped
    ):
        raise ValueError("quoted_candidate_text")
    redacted = stripped
    for pattern, replacement in SECRET_REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    if any(pattern.search(redacted) for pattern, _ in SECRET_REDACTIONS):
        raise ValueError("residual_secret")
    if any(pattern.search(redacted) for pattern in RESIDUAL_SECRET_PATTERNS):
        raise ValueError("residual_secret")
    if len(redacted) > maximum:
        raise ValueError("candidate_text_too_long")
    return redacted
```

- [ ] **Step 4 (2–5 min): Implement exact coverage and provenance validation**

Add this block immediately after `normalize_candidate_text`:

```python
def validate_declarative_result(
    payload: object,
    contract: dict[str, object],
    allowed_target_identities: frozenset[str],
) -> dict[str, object]:
    result = require_exact_object(
        payload, RESULT_TOP_LEVEL_KEYS, "invalid_result_fields"
    )
    if (
        type(result["schema_version"]) is not int
        or result["schema_version"] != 1
    ):
        raise ValueError("invalid_result_schema")
    if (
        not isinstance(result["contract_digest"], str)
        or result["contract_digest"] != sha256_json(contract)
    ):
        raise ValueError("result_contract_mismatch")
    supplied = result["sessions"]
    contract_sessions = contract["sessions"]
    if not isinstance(supplied, list) or not isinstance(
        contract_sessions, list
    ):
        raise ValueError("invalid_result_sessions")
    expected_refs = [
        str(session["session_ref"]) for session in contract_sessions
    ]
    supplied_refs = [
        item.get("session_ref") if isinstance(item, dict) else None
        for item in supplied
    ]
    if (
        len(supplied_refs) != len(expected_refs)
        or any(not isinstance(ref, str) for ref in supplied_refs)
        or len(set(supplied_refs)) != len(supplied_refs)
        or set(supplied_refs) != set(expected_refs)
    ):
        raise ValueError("invalid_result_session_coverage")
    supplied_by_ref = {
        str(item["session_ref"]): item for item in supplied
    }
    contract_by_ref = {
        str(item["session_ref"]): item for item in contract_sessions
    }
    normalized_sessions: list[dict[str, object]] = []
    for session_ref in expected_refs:
        item = supplied_by_ref[session_ref]
        decision = item.get("decision")
        if decision == "excluded":
            excluded = require_exact_object(
                item,
                EXCLUDED_RESULT_KEYS,
                "invalid_excluded_result_fields",
            )
            reason = excluded["excluded_reason"]
            if (
                not isinstance(reason, str)
                or reason not in EXCLUDED_REASONS
            ):
                raise ValueError("invalid_excluded_reason")
            normalized_sessions.append(
                {
                    "session_ref": session_ref,
                    "decision": "excluded",
                    "excluded_reason": reason,
                }
            )
            continue
        if decision != "candidate":
            raise ValueError("invalid_result_decision")
        candidate = require_exact_object(
            item,
            CANDIDATE_RESULT_KEYS,
            "invalid_candidate_result_fields",
        )
        target_identity = candidate["target_identity"]
        if (
            not isinstance(target_identity, str)
            or target_identity not in allowed_target_identities
        ):
            raise ValueError("unsupported_target")
        classification = require_exact_object(
            candidate["classification"],
            CLASSIFICATION_KEYS,
            "invalid_classification_fields",
        )
        category = classification["problem_category"]
        risk = candidate["risk_level"]
        if (
            not isinstance(category, str)
            or category not in PROBLEM_CATEGORIES
        ):
            raise ValueError("invalid_problem_category")
        if not isinstance(risk, str) or risk not in RISK_LEVELS:
            raise ValueError("invalid_risk_level")
        evidence_items = candidate["evidence"]
        if (
            not isinstance(evidence_items, list)
            or not evidence_items
            or len(evidence_items) > EVIDENCE_PER_CANDIDATE_MAX
        ):
            raise ValueError("invalid_candidate_evidence_count")
        records = {
            str(record["record_ref"]): record
            for record in contract_by_ref[session_ref]["records"]
        }
        normalized_evidence: list[dict[str, object]] = []
        used_record_refs: set[str] = set()
        for evidence_value in evidence_items:
            evidence = require_exact_object(
                evidence_value,
                EVIDENCE_RESULT_KEYS,
                "invalid_evidence_fields",
            )
            record_ref = evidence["record_ref"]
            signal_type = evidence["signal_type"]
            if (
                not isinstance(record_ref, str)
                or record_ref in used_record_refs
                or record_ref not in records
            ):
                raise ValueError("invalid_evidence_record_ref")
            if not isinstance(signal_type, str):
                raise ValueError("invalid_evidence_signal_type")
            record = records[record_ref]
            source_kind = record["source_kind"]
            if (
                record["evidence_eligible"] is not True
                or (signal_type, source_kind) not in SIGNAL_SOURCE_PAIRS
            ):
                raise ValueError("ineligible_evidence")
            used_record_refs.add(record_ref)
            normalized_evidence.append(
                {
                    "record_ref": record_ref,
                    "signal_type": signal_type,
                    "source_kind": source_kind,
                    "summary": normalize_candidate_text(
                        evidence["summary"], 280
                    ),
                }
            )
        normalized_sessions.append(
            {
                "session_ref": session_ref,
                "decision": "candidate",
                "target_identity": target_identity,
                "classification": {
                    "problem_category": category,
                    "target_locator": normalize_candidate_text(
                        classification["target_locator"], 160
                    ),
                    "proposal_intent": normalize_candidate_text(
                        classification["proposal_intent"], 160
                    ),
                },
                "problem_summary": normalize_candidate_text(
                    candidate["problem_summary"], 280
                ),
                "proposal_summary": normalize_candidate_text(
                    candidate["proposal_summary"], 280
                ),
                "validation_plan": normalize_candidate_text(
                    candidate["validation_plan"], 500
                ),
                "risk_level": risk,
                "evidence": normalized_evidence,
            }
        )
    normalized = {
        "schema_version": 1,
        "contract_digest": result["contract_digest"],
        "sessions": normalized_sessions,
    }
    if len(canonical_json_bytes(normalized)) > CANONICAL_RESULT_MAX_BYTES:
        raise ValueError("validated_result_too_large")
    return normalized
```

- [ ] **Step 5 (2–5 min): Run the strict validator tests and the Phase 3 capture suite**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -k ReviewCandidateValidationTests \
  -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: the four validator tests pass; all 80 Phase 3 production tests pass.

- [ ] **Step 6 (2–5 min): Commit strict declarative validation**

```bash
cd /Users/igyeongseob/Documents/오픈소스
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py
git diff --cached --check
git commit -m "feat(skill-evolver): validate review decisions"
```

Expected: one commit containing only `evolver.py` and `test_review.py`.

---

### Task 2: Fix Candidate Fingerprints and Recurrence Metadata

**Files:**
- Modify: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/test_review.py`

**Interfaces:**
- Consumes: `canonical_json_bytes`, `Installation.identity_key`, and the validated candidate fields from Task 1.
- Produces: `normalized_fingerprint_field`, `candidate_fingerprint`, `candidate_session_link_key`, `candidate_session_link_value`, and `candidate_evidence_aggregate_key`.

- [ ] **Step 1 (2–5 min): Add fingerprint and metadata-key tests**

Add this class before `unittest.main()` in `test_review.py`:

```python
class CandidateIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        sessions = root / "sessions"
        sessions.mkdir(mode=0o700)
        self.installation_path = self.runtime.initialize_runtime(
            root / "data",
            (sessions,),
            {"capture_paused": False, "exclude_roots": []},
        )
        self.installation = self.runtime.load_installation(
            self.installation_path
        )

    def test_fingerprint_is_nfkc_casefolded_and_field_bound(self) -> None:
        first = self.runtime.candidate_fingerprint(
            "user-skill:Example",
            "verification",
            " Completion   Claim ",
            "Require ＦＲＥＳＨ evidence",
        )
        second = self.runtime.candidate_fingerprint(
            "USER-SKILL:EXAMPLE",
            "verification",
            "completion claim",
            "require fresh evidence",
        )
        changed = self.runtime.candidate_fingerprint(
            "user-skill:example",
            "safety",
            "completion claim",
            "require fresh evidence",
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_recurrence_key_and_value_store_no_session_key(self) -> None:
        raw_session_key = "1" * 64
        key = self.runtime.candidate_session_link_key(
            self.installation, raw_session_key
        )
        value = self.runtime.candidate_session_link_value(
            candidate_id=17,
            dedupe_expires_at="2033-11-14T22:13:20Z",
        )
        self.assertRegex(key, r"^candidate-session\.[0-9a-f]{64}$")
        self.assertNotIn(raw_session_key, key)
        self.assertEqual(
            value,
            {
                "schema_version": 1,
                "candidate_id": 17,
                "dedupe_expires_at": "2033-11-14T22:13:20Z",
            },
        )
        self.assertNotIn(raw_session_key, json.dumps(value))
        self.assertEqual(
            self.runtime.candidate_evidence_aggregate_key(17),
            "candidate.17.evidence_aggregate",
        )
```

- [ ] **Step 2 (2–5 min): Run the identity tests and verify RED**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -k CandidateIdentityTests \
  -v
```

Expected: both tests error because the five candidate identity helpers are not defined.

- [ ] **Step 3 (2–5 min): Implement exact fingerprint and metadata helpers**

Add this block after the Task 1 validator:

```python
def normalized_fingerprint_field(value: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", value).casefold().split()
    )


def candidate_fingerprint(
    target_identity: str,
    problem_category: str,
    target_locator: str,
    proposal_intent: str,
) -> str:
    return sha256_json(
        {
            "schema_version": 1,
            "target_identity": normalized_fingerprint_field(
                target_identity
            ),
            "problem_category": normalized_fingerprint_field(
                problem_category
            ),
            "target_locator": normalized_fingerprint_field(
                target_locator
            ),
            "proposal_intent": normalized_fingerprint_field(
                proposal_intent
            ),
        }
    )


def candidate_session_link_key(
    installation: Installation,
    session_key_value: str,
) -> str:
    digest = hmac.new(
        installation.identity_key.read_bytes(),
        b"candidate-session\0" + session_key_value.encode("ascii"),
        "sha256",
    ).hexdigest()
    return f"candidate-session.{digest}"


def candidate_session_link_value(
    candidate_id: int,
    dedupe_expires_at: str,
) -> dict[str, object]:
    if candidate_id < 1:
        raise ValueError("invalid_candidate_id")
    parse_iso_utc(dedupe_expires_at)
    return {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "dedupe_expires_at": dedupe_expires_at,
    }


def candidate_evidence_aggregate_key(candidate_id: int) -> str:
    if candidate_id < 1:
        raise ValueError("invalid_candidate_id")
    return f"candidate.{candidate_id}.evidence_aggregate"
```

- [ ] **Step 4 (2–5 min): Run identity and strict-schema tests**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -k CandidateIdentityTests \
  -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -k ReviewCandidateValidationTests \
  -v
```

Expected: both identity tests and all four strict validator tests pass.

- [ ] **Step 5 (2–5 min): Commit candidate identity contracts**

```bash
cd /Users/igyeongseob/Documents/오픈소스
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py
git diff --cached --check
git commit -m "feat(skill-evolver): bind candidate recurrence"
```

Expected: one commit containing only `evolver.py` and `test_review.py`.

---

### Task 3: Commit Candidates, Evidence, Links, Generations, and Batch Atomically

**Files:**
- Modify: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/test_review.py`

**Interfaces:**
- Consumes: all exact Plan 4A catalog/digest interfaces; Plan 4B `require_bound_review_result_binding`, merged-exclusion `finalize_review_batch`, and the remaining exact result/batch interfaces; Task 1 validation; Task 2 fingerprint/link helpers; existing `record_candidate_evidence`; and `complete_batch_review_generation`.
- Produces: `commit_review_result(connection, installation, config, batch_id, owner_token, result_path, now) -> dict[str, object]`.
- Retry result: `{"schema_version": 1, "status": "retry", "batch_id": int, "error_code": "invalid_review_result", "result_path": str}`.
- Completed result: `{"schema_version": 1, "status": "completed", "batch_id": int, "new_candidates": list[str], "merged_candidates": list[str], "exclusion_counts": dict[str, int], "result_deleted": bool}`.

- [ ] **Step 1 (2–5 min): Add a reusable ready-batch candidate fixture**

Reuse Plan 4B's `BatchExportTestCase`, `insert_pending`, `make_export`,
`write_result_bytes`, and `claim_ready_batch` helpers. Add this subclass
before the Task 1 test class in `test_review.py`:

```python
class CandidateBatchFixture(BatchExportTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fixed_inputs = self.fixed_review_inputs()
        self.fixed_inputs.__enter__()
        self.addCleanup(
            self.fixed_inputs.__exit__, None, None, None
        )
        self.connection = self.runtime.open_database(self.installation)
        self.addCleanup(self.connection.close)

    def claim(self, count: int, now: float) -> dict[str, object]:
        for index in range(count):
            self.insert_pending(
                self.connection,
                index + 1,
                text=f"candidate-session-{index}\n",
                now=now,
            )
        exports = [
            self.make_export(
                "The user corrected a failed completion claim.",
                "The verification command failed.",
            )
            for _ in range(count)
        ]
        return self.claim_ready_batch(
            self.connection,
            exports,
            now=now,
        )

    def result_payload(
        self,
        claim: dict[str, object],
        locator_suffix: str = "",
    ) -> dict[str, object]:
        contract = self.runtime.load_review_contract(
            self.connection, int(claim["batch_id"]), "final"
        )
        sessions = []
        for index, session in enumerate(contract["sessions"]):
            record = session["records"][0]
            sessions.append(
                {
                    "session_ref": session["session_ref"],
                    "decision": "candidate",
                    "target_identity": self.catalog_entry.identity,
                    "classification": {
                        "problem_category": "verification",
                        "target_locator": (
                            f"completion claim {index}{locator_suffix}"
                        ),
                        "proposal_intent": (
                            "require successful verification"
                        ),
                    },
                    "problem_summary": (
                        "A completion claim survived a failed check."
                    ),
                    "proposal_summary": (
                        "Require fresh successful evidence."
                    ),
                    "validation_plan": (
                        "Reproduce the failure and add a focused regression."
                    ),
                    "risk_level": "low",
                    "evidence": [
                        {
                            "record_ref": record["record_ref"],
                            "signal_type": "explicit_correction",
                            "summary": (
                                "The user corrected a completion claim."
                            ),
                        }
                    ],
                }
            )
        return {
            "schema_version": 1,
            "contract_digest": claim["contract_digest"],
            "sessions": sessions,
        }

    def write_result(
        self,
        claim: dict[str, object],
        payload: dict[str, object],
    ) -> Path:
        path = Path(str(claim["result_path"]))
        self.write_result_bytes(
            path,
            json.dumps(
                payload,
                ensure_ascii=False,
            ).encode("utf-8"),
            self.runtime.parse_iso_utc(
                str(claim["lease_expires_at"])
            ),
        )
        return path
```

The exact Plan 4B helper contracts reused here are:

- `fixed_review_inputs(self)` context manager;
- `insert_pending(self, connection, number, *, text="record\n", error_code=None, now=2_000_000_000.0) -> sqlite3.Row`;
- `make_export(self, *texts, context=0) -> TranscriptExport`;
- `write_result_bytes(self, path: Path, value: bytes, now: float) -> None`;
- `claim_ready_batch(self, connection, exports, *, now=2_000_000_000.0) -> dict[str, object]`.

- [ ] **Step 2 (2–5 min): Add success, merged-exclusion, rollback, cap, recurrence, and retry-allocation tests**

Add this class after `CandidateBatchFixture`:

```python
class CandidateCommitTests(CandidateBatchFixture):
    def test_commit_writes_candidate_evidence_link_generation_and_audit(
        self,
    ) -> None:
        now = 2_000_000_000.0
        claim = self.claim(1, now)
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )
        committed = self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(claim["batch_id"]),
            str(claim["owner_token"]),
            result_path,
            now + 1,
        )
        candidate = self.connection.execute(
            "SELECT * FROM candidates"
        ).fetchone()
        evidence_count = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidate_evidence"
            ).fetchone()[0]
        )
        links = list(
            self.connection.execute(
                "SELECT key,value FROM metadata "
                "WHERE key LIKE 'candidate-session.%'"
            )
        )
        contract = self.connection.execute(
            "SELECT value FROM metadata WHERE key=?",
            (
                self.runtime.review_contract_key(
                    int(claim["batch_id"])
                ),
            ),
        ).fetchone()
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
        self.assertEqual(committed["status"], "completed")
        self.assertEqual(committed["new_candidates"], ["C-001"])
        self.assertEqual(candidate["occurrence_count"], 1)
        self.assertEqual(evidence_count, 1)
        self.assertEqual(len(links), 1)
        self.assertNotIn("candidate-session-0", links[0]["key"])
        self.assertIsNone(contract)
        self.assertEqual(audit["terminal_status"], "completed")
        self.assertEqual(audit["candidate_count"], 1)
        self.assertFalse(result_path.exists())

    def test_partial_export_commit_preserves_adapter_and_capacity_exclusions(
        self,
    ) -> None:
        now = 2_000_000_000.0
        for number in range(1, 4):
            self.insert_pending(
                self.connection,
                number,
                text=f"partial-{number}\n",
                now=now,
            )
        accepted = replace(
            self.make_export("accepted candidate evidence"),
            canonical_records_bytes=5_000_000,
        )
        terminal = self.runtime.TranscriptAdapterError(
            "unsupported_transcript",
            retryable=False,
        )
        capacity = replace(
            self.make_export("released by aggregate capacity"),
            canonical_records_bytes=5_000_000,
        )
        claim = self.claim_ready_batch(
            self.connection,
            [accepted, terminal, capacity],
            now=now,
        )
        batch_id = int(claim["batch_id"])
        before = json.loads(
            self.connection.execute(
                """
                SELECT exclusion_counts_json FROM review_batches
                WHERE id=?
                """,
                (batch_id,),
            ).fetchone()["exclusion_counts_json"]
        )
        result_path = self.write_result(
            claim, self.result_payload(claim)
        )
        committed = self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            batch_id,
            str(claim["owner_token"]),
            result_path,
            now + 1,
        )
        batch = self.connection.execute(
            """
            SELECT exclusion_counts_json FROM review_batches
            WHERE id=?
            """,
            (batch_id,),
        ).fetchone()
        audit = json.loads(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (self.runtime.review_audit_key(batch_id),),
            ).fetchone()["value"]
        )
        live_members = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM review_items WHERE batch_id=?",
                (batch_id,),
            ).fetchone()[0]
        )
        self.assertEqual(
            before,
            {
                "batch_capacity_released": 1,
                "unsupported_transcript": 1,
            },
        )
        self.assertEqual(committed["status"], "completed")
        self.assertEqual(committed["new_candidates"], ["C-001"])
        self.assertEqual(
            committed["exclusion_counts"],
            {"unsupported_transcript": 1},
        )
        self.assertEqual(
            audit["exclusion_counts"],
            {"unsupported_transcript": 1},
        )
        self.assertEqual(audit["batch_capacity_released"], 1)
        self.assertEqual(
            json.loads(batch["exclusion_counts_json"]),
            {
                "batch_capacity_released": 1,
                "unsupported_transcript": 1,
            },
        )
        self.assertEqual(live_members, 0)

    def test_residual_secret_rolls_back_and_allocates_fresh_retry(
        self,
    ) -> None:
        now = 2_000_000_000.0
        claim = self.claim(1, now)
        payload = self.result_payload(claim)
        payload["sessions"][0]["validation_plan"] = (
            "-----BEGIN PRIVATE KEY-----"
        )
        old_path = self.write_result(claim, payload)
        retried = self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(claim["batch_id"]),
            str(claim["owner_token"]),
            old_path,
            now + 1,
        )
        candidate_count = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0]
        )
        batch = self.connection.execute(
            "SELECT status FROM review_batches WHERE id=?",
            (int(claim["batch_id"]),),
        ).fetchone()
        review = self.connection.execute(
            "SELECT status,reviewed_boundary FROM review_items"
        ).fetchone()
        new_path = Path(str(retried["result_path"]))
        self.assertEqual(retried["status"], "retry")
        self.assertEqual(retried["error_code"], "invalid_review_result")
        self.assertEqual(candidate_count, 0)
        self.assertEqual(batch["status"], "ready")
        self.assertEqual(tuple(review), ("reviewing", 0))
        self.assertFalse(old_path.exists())
        self.assertTrue(new_path.exists())
        self.assertNotEqual(old_path, new_path)

    def test_foreign_result_is_preserved_and_never_rotated(self) -> None:
        now = 2_000_000_000.0
        claim = self.claim(1, now)
        foreign = Path(self.temp.name) / "foreign-result.json"
        foreign.write_text("{}", encoding="utf-8")
        with self.assertRaises(self.runtime.ReviewResultError) as caught:
            self.runtime.commit_review_result(
                self.connection,
                self.installation,
                self.config,
                int(claim["batch_id"]),
                str(claim["owner_token"]),
                foreign,
                now + 1,
            )
        self.assertIsNone(caught.exception.opened)
        self.assertTrue(foreign.exists())
        self.assertTrue(Path(str(claim["result_path"])).exists())
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0],
            0,
        )

    def test_bound_reader_failure_allocates_one_fresh_retry(self) -> None:
        now = 2_000_000_000.0
        claim = self.claim(1, now)
        old_path = Path(str(claim["result_path"]))
        old_path.write_bytes(b"x" * 262_145)
        retried = self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(claim["batch_id"]),
            str(claim["owner_token"]),
            old_path,
            now + 1,
        )
        new_path = Path(str(retried["result_path"]))
        self.assertEqual(retried["status"], "retry")
        self.assertEqual(retried["error_code"], "invalid_review_result")
        self.assertFalse(old_path.exists())
        self.assertTrue(new_path.exists())
        self.assertNotEqual(old_path, new_path)

    def test_four_new_fingerprints_roll_back_the_whole_batch(self) -> None:
        now = 2_000_000_000.0
        claim = self.claim(4, now)
        old_path = self.write_result(
            claim, self.result_payload(claim, "-distinct")
        )
        retried = self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(claim["batch_id"]),
            str(claim["owner_token"]),
            old_path,
            now + 1,
        )
        counts = self.connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM candidates),
              (SELECT COUNT(*) FROM candidate_evidence),
              (SELECT COUNT(*) FROM metadata
               WHERE key LIKE 'candidate-session.%')
            """
        ).fetchone()
        reviewing = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM review_items "
                "WHERE status='reviewing' AND reviewed_boundary=0"
            ).fetchone()[0]
        )
        self.assertEqual(retried["status"], "retry")
        self.assertEqual(tuple(counts), (0, 0, 0))
        self.assertEqual(reviewing, 4)

    def test_existing_session_link_prevents_a_second_candidate(self) -> None:
        now = 2_000_000_000.0
        first = self.claim(1, now)
        first_path = self.write_result(
            first, self.result_payload(first)
        )
        self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(first["batch_id"]),
            str(first["owner_token"]),
            first_path,
            now + 1,
        )
        row = self.connection.execute(
            "SELECT * FROM review_items"
        ).fetchone()
        self.connection.execute(
            """
            UPDATE review_items
            SET status='pending',generation=generation+1,
                observed_boundary=observed_boundary+1,pending_since=?
            WHERE id=?
            """,
            (self.runtime.iso_utc(now + 2), int(row["id"])),
        )
        second = self.runtime.claim_review_batch(
            self.connection,
            self.installation,
            self.config,
            now + 3,
        )
        payload = self.result_payload(second, "-different")
        second_path = self.write_result(second, payload)
        committed = self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(second["batch_id"]),
            str(second["owner_token"]),
            second_path,
            now + 4,
        )
        candidates = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0]
        )
        occurrence = int(
            self.connection.execute(
                "SELECT occurrence_count FROM candidates"
            ).fetchone()[0]
        )
        self.assertEqual(candidates, 1)
        self.assertEqual(occurrence, 1)
        self.assertEqual(
            committed["exclusion_counts"], {"candidate_limit": 1}
        )
```

- [ ] **Step 3 (2–5 min): Add binding, digest, target, and frozen-tuple revalidation tests**

Add these methods to `CandidateCommitTests`:

```python
    def test_every_static_and_dynamic_digest_drift_rolls_back(self) -> None:
        now = 2_000_000_000.0
        digest_functions = (
            "improvement_policy_digest",
            "transcript_adapter_digest",
            "catalog_adapter_digest",
        )
        for offset, function_name in enumerate(digest_functions):
            with self.subTest(function_name=function_name):
                claim = (
                    self.claim(1, now)
                    if offset == 0
                    else self.claim_ready_batch(
                        self.connection,
                        [self.make_export("new digest attempt")],
                        now=now + offset * 10,
                    )
                )
                path = self.write_result(
                    claim, self.result_payload(claim)
                )
                with mock.patch.object(
                    self.runtime,
                    function_name,
                    return_value="9" * 64,
                ):
                    retried = self.runtime.commit_review_result(
                        self.connection,
                        self.installation,
                        self.config,
                        int(claim["batch_id"]),
                        str(claim["owner_token"]),
                        path,
                        now + offset * 10 + 1,
                    )
                self.assertEqual(retried["status"], "retry")
                self.assertEqual(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM candidates"
                    ).fetchone()[0],
                    0,
                )
                self.runtime.abort_review_batch(
                    self.connection,
                    self.installation,
                    int(claim["batch_id"]),
                    str(claim["owner_token"]),
                    now + offset * 10 + 2,
                )

        claim = self.claim_ready_batch(
            self.connection,
            [self.make_export("snapshot drift")],
            now=now + 40,
        )
        path = self.write_result(claim, self.result_payload(claim))
        generation_before = tuple(
            self.connection.execute(
                """
                SELECT generation,status,reviewed_boundary,batch_id
                FROM review_items WHERE batch_id=?
                """,
                (int(claim["batch_id"]),),
            ).fetchone()
        )
        review_runtime = self.runtime.load_review_runtime()
        preflight_snapshot = self.runtime.build_catalog_snapshot(
            review_runtime
        )
        changed_snapshot = self.runtime.CatalogSnapshot(
            entries=preflight_snapshot.entries,
            export_bytes=preflight_snapshot.export_bytes,
            snapshot_digest="8" * 64,
            rejected_count=preflight_snapshot.rejected_count,
        )
        with mock.patch.object(
            self.runtime,
            "load_review_runtime",
            side_effect=[review_runtime, review_runtime],
        ) as load_runtime, mock.patch.object(
            self.runtime,
            "build_catalog_snapshot",
            side_effect=[preflight_snapshot, changed_snapshot],
        ) as build_snapshot:
            retried = self.runtime.commit_review_result(
                self.connection,
                self.installation,
                self.config,
                int(claim["batch_id"]),
                str(claim["owner_token"]),
                path,
                now + 41,
            )
        retry_path = Path(str(retried["result_path"]))
        counts = tuple(
            self.connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM candidates),
                  (SELECT COUNT(*) FROM candidate_evidence),
                  (SELECT COUNT(*) FROM metadata
                   WHERE key LIKE 'candidate-session.%'),
                  (SELECT COUNT(*) FROM metadata WHERE key=?)
                """,
                (
                    self.runtime.review_audit_key(
                        int(claim["batch_id"])
                    ),
                ),
            ).fetchone()
        )
        generation_after = tuple(
            self.connection.execute(
                """
                SELECT generation,status,reviewed_boundary,batch_id
                FROM review_items WHERE batch_id=?
                """,
                (int(claim["batch_id"]),),
            ).fetchone()
        )
        batch_status = self.connection.execute(
            "SELECT status FROM review_batches WHERE id=?",
            (int(claim["batch_id"]),),
        ).fetchone()["status"]
        self.assertEqual(retried["status"], "retry")
        self.assertEqual(load_runtime.call_count, 2)
        self.assertEqual(build_snapshot.call_count, 2)
        self.assertEqual(counts, (0, 0, 0, 0))
        self.assertEqual(generation_after, generation_before)
        self.assertEqual(batch_status, "ready")
        self.assertFalse(path.exists())
        self.assertNotEqual(retry_path, path)
        self.assertTrue(retry_path.exists())

    def test_result_binding_rotation_after_read_rolls_back_candidate_commit(
        self,
    ) -> None:
        now = 2_000_000_000.0
        claim = self.claim(1, now)
        batch_id = int(claim["batch_id"])
        owner_token = str(claim["owner_token"])
        old_path = self.write_result(
            claim, self.result_payload(claim)
        )
        generation_before = tuple(
            self.connection.execute(
                """
                SELECT generation,status,reviewed_boundary,batch_id
                FROM review_items
                """
            ).fetchone()
        )
        replacement_paths: list[Path] = []
        original_reader = self.runtime.read_bound_review_result

        def rotate_after_read(*args, **kwargs):
            opened = original_reader(*args, **kwargs)
            replacement_paths.append(
                self.runtime.replace_invalid_review_result(
                    self.connection,
                    self.installation,
                    batch_id,
                    owner_token,
                    opened,
                    now + 2,
                )
            )
            return opened

        with mock.patch.object(
            self.runtime,
            "read_bound_review_result",
            side_effect=rotate_after_read,
        ), self.assertRaisesRegex(
            ValueError, "review_result_binding_mismatch"
        ):
            self.runtime.commit_review_result(
                self.connection,
                self.installation,
                self.config,
                batch_id,
                owner_token,
                old_path,
                now + 2,
            )
        replacement = replacement_paths[0]
        binding = self.runtime.load_review_result_binding(
            self.connection, batch_id
        )
        counts = tuple(
            self.connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM candidates),
                  (SELECT COUNT(*) FROM candidate_evidence),
                  (SELECT COUNT(*) FROM metadata
                   WHERE key LIKE 'candidate-session.%'),
                  (SELECT COUNT(*) FROM metadata WHERE key=?)
                """,
                (self.runtime.review_audit_key(batch_id),),
            ).fetchone()
        )
        generation_after = tuple(
            self.connection.execute(
                """
                SELECT generation,status,reviewed_boundary,batch_id
                FROM review_items
                """
            ).fetchone()
        )
        batch_status = self.connection.execute(
            "SELECT status FROM review_batches WHERE id=?",
            (batch_id,),
        ).fetchone()["status"]
        self.assertEqual(counts, (0, 0, 0, 0))
        self.assertEqual(generation_after, generation_before)
        self.assertEqual(batch_status, "ready")
        self.assertEqual(binding["basename"], replacement.name)
        self.assertFalse(old_path.exists())
        self.assertTrue(replacement.exists())
        self.assertEqual(
            list(self.runtime.review_result_root().iterdir()),
            [replacement],
        )

    def test_frozen_to_change_rolls_back_whole_result(
        self,
    ) -> None:
        now = 2_000_000_000.0
        claim = self.claim(1, now)
        path = self.write_result(claim, self.result_payload(claim))
        self.connection.execute(
            """
            UPDATE review_items
            SET frozen_to=frozen_to+1
            WHERE batch_id=?
            """,
            (int(claim["batch_id"]),),
        )
        retried = self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(claim["batch_id"]),
            str(claim["owner_token"]),
            path,
            now + 1,
        )
        self.assertEqual(retried["status"], "retry")
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0],
            0,
        )
        self.assertTrue(Path(str(retried["result_path"])).exists())
```

The first test covers the current policy, transcript-adapter,
catalog-adapter, and dynamic catalog-snapshot digests. Its snapshot case returns
the original snapshot during preflight and a changed snapshot after
`BEGIN IMMEDIATE`, proving that a fresh runtime and catalog snapshot are loaded
inside the transaction before any candidate, evidence, link, generation, or
audit write survives; the invalid result is rotated for retry. The second
rotates the bound result after its single read and proves the candidate transaction
rejects the stale binding without candidate, evidence, link, generation, or
audit mutation while preserving the newly bound file. The third proves that
`complete_batch_review_generation` revalidates generation, epoch, frozen
from/to, locator digest, owner digest, batch membership, and lease before any
completion survives.

- [ ] **Step 4 (2–5 min): Run the atomic tests and verify RED**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -k CandidateCommitTests \
  -v
```

Expected: the ten tests error because `commit_review_result` is not defined.

- [ ] **Step 5 (2–5 min): Add recurrence-link loading, display IDs, digest checks, and retry rotation**

Add this block after the Task 2 helpers in `evolver.py`:

```python
def display_id(prefix: str, value: int) -> str:
    if value < 1:
        raise ValueError("invalid_display_id")
    return f"{prefix}-{value:03d}"


def load_candidate_session_link(
    connection: sqlite3.Connection,
    key: str,
) -> Optional[dict[str, object]]:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key=?", (key,)
    ).fetchone()
    if row is None:
        return None
    value = json.loads(str(row["value"]))
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schema_version", "candidate_id", "dedupe_expires_at"}
        or value["schema_version"] != 1
        or type(value["candidate_id"]) is not int
        or value["candidate_id"] < 1
        or not isinstance(value["dedupe_expires_at"], str)
    ):
        raise ValueError("invalid_candidate_session_link")
    parse_iso_utc(value["dedupe_expires_at"])
    return value


def current_review_digests(
    runtime: ReviewRuntime,
    snapshot: CatalogSnapshot,
) -> dict[str, str]:
    policy = load_improvement_policy(runtime)
    return {
        "policy_digest": improvement_policy_digest(policy),
        "transcript_adapter_digest": transcript_adapter_digest(runtime),
        "catalog_adapter_digest": catalog_adapter_digest(runtime),
        "catalog_snapshot_digest": snapshot.snapshot_digest,
    }


def rotate_invalid_review_result(
    connection: sqlite3.Connection,
    installation: Installation,
    batch_id: int,
    owner_token: str,
    opened: BoundReviewResult,
    now: float,
) -> dict[str, object]:
    replacement = replace_invalid_review_result(
        connection,
        installation,
        batch_id,
        owner_token,
        opened,
        now,
    )
    return {
        "schema_version": 1,
        "status": "retry",
        "batch_id": batch_id,
        "error_code": "invalid_review_result",
        "result_path": str(replacement),
    }
```

- [ ] **Step 6 (2–5 min): Implement the single candidate transaction**

Add the following functions immediately after `rotate_invalid_review_result`.
Do not call `cleanup_review_results` directly here:
`read_bound_review_result` already authenticates the live owner, persisted
binding, and exact allocated path before its bounded cleanup. Keeping that
single reader-owned call prevents unauthenticated cleanup and duplicate
namespace scans.

```python
def commit_review_result(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    batch_id: int,
    owner_token: str,
    result_path: Path,
    now: float,
) -> dict[str, object]:
    if connection.in_transaction:
        raise ValueError("active_transaction")
    try:
        opened = read_bound_review_result(
            connection,
            installation,
            batch_id,
            owner_token,
            result_path,
            now,
        )
    except ReviewResultError as error:
        if error.opened is None:
            raise
        return rotate_invalid_review_result(
            connection,
            installation,
            batch_id,
            owner_token,
            error.opened,
            now,
        )
    try:
        payload = json.loads(opened.encoded.decode("utf-8"))
    except (RecursionError, UnicodeError, json.JSONDecodeError):
        return rotate_invalid_review_result(
            connection,
            installation,
            batch_id,
            owner_token,
            opened,
            now,
        )
    runtime = load_review_runtime()
    snapshot = build_catalog_snapshot(runtime)
    try:
        _, contract, owner_digest = require_live_review_batch(
            connection,
            installation,
            batch_id,
            owner_token,
            now,
        )
        digests = current_review_digests(runtime, snapshot)
        if any(contract[name] != value for name, value in digests.items()):
            raise ValueError("review_contract_digest_drift")
        allowed_targets = frozenset(
            entry.identity for entry in snapshot.entries
        )
        validated = validate_declarative_result(
            payload, contract, allowed_targets
        )
        prepared: list[dict[str, object]] = []
        new_fingerprints: set[str] = set()
        connection.execute("BEGIN IMMEDIATE")
        try:
            _, live_contract, live_owner_digest = require_live_review_batch(
                connection,
                installation,
                batch_id,
                owner_token,
                now,
            )
            if (
                live_owner_digest != owner_digest
                or sha256_json(live_contract) != sha256_json(contract)
            ):
                raise ValueError("review_contract_changed")
            live_runtime = load_review_runtime()
            live_snapshot = build_catalog_snapshot(live_runtime)
            live_digests = current_review_digests(
                live_runtime, live_snapshot
            )
            if any(
                live_contract[name] != value
                for name, value in live_digests.items()
            ):
                raise ValueError("review_contract_digest_drift")
            require_bound_review_result_binding(
                connection,
                batch_id,
                opened,
            )
            contract_sessions = {
                str(item["session_ref"]): item
                for item in live_contract["sessions"]
            }
            for result in validated["sessions"]:
                session = contract_sessions[str(result["session_ref"])]
                row = connection.execute(
                    """
                    SELECT id,session_key,dedupe_expires_at
                    FROM review_items WHERE id=?
                    """,
                    (int(session["review_item_id"]),),
                ).fetchone()
                if row is None:
                    raise ValueError("review_item_missing")
                prepared_item: dict[str, object] = {
                    "result": result,
                    "session": session,
                    "row": row,
                }
                if result["decision"] == "candidate":
                    entry = resolve_catalog_target(
                        live_snapshot, str(result["target_identity"])
                    )
                    classification = result["classification"]
                    fingerprint = candidate_fingerprint(
                        entry.identity,
                        str(classification["problem_category"]),
                        str(classification["target_locator"]),
                        str(classification["proposal_intent"]),
                    )
                    existing = connection.execute(
                        "SELECT * FROM candidates WHERE fingerprint=?",
                        (fingerprint,),
                    ).fetchone()
                    link_key = candidate_session_link_key(
                        installation, str(row["session_key"])
                    )
                    link = load_candidate_session_link(
                        connection, link_key
                    )
                    candidate_limit = link is not None and (
                        existing is None
                        or int(link["candidate_id"]) != int(existing["id"])
                    )
                    if existing is None and not candidate_limit:
                        new_fingerprints.add(fingerprint)
                    prepared_item.update(
                        {
                            "entry": entry,
                            "fingerprint": fingerprint,
                            "existing": existing,
                            "link_key": link_key,
                            "link": link,
                            "candidate_limit": candidate_limit,
                        }
                    )
                prepared.append(prepared_item)
            if len(new_fingerprints) > config.max_candidates_per_batch:
                raise ValueError("max_candidates_per_batch")

            new_candidates: list[str] = []
            merged_candidates: list[str] = []
            semantic_exclusions: dict[str, int] = {}
            candidate_count = 0
            for item in prepared:
                result = item["result"]
                session = item["session"]
                row = item["row"]
                if result["decision"] == "excluded":
                    reason = str(result["excluded_reason"])
                    semantic_exclusions[reason] = (
                        semantic_exclusions.get(reason, 0) + 1
                    )
                    complete_batch_review_generation(
                        connection,
                        int(session["review_item_id"]),
                        batch_id,
                        owner_digest,
                        int(session["expected_generation"]),
                        int(session["frozen_epoch"]),
                        int(session["frozen_from"]),
                        int(session["frozen_to"]),
                        str(session["frozen_locator_digest"]),
                        "excluded",
                        reason,
                        now,
                    )
                    continue
                if item["candidate_limit"]:
                    semantic_exclusions["candidate_limit"] = (
                        semantic_exclusions.get("candidate_limit", 0) + 1
                    )
                    complete_batch_review_generation(
                        connection,
                        int(session["review_item_id"]),
                        batch_id,
                        owner_digest,
                        int(session["expected_generation"]),
                        int(session["frozen_epoch"]),
                        int(session["frozen_from"]),
                        int(session["frozen_to"]),
                        str(session["frozen_locator_digest"]),
                        "excluded",
                        "candidate_limit",
                        now,
                    )
                    continue
                candidate_id, created = upsert_validated_candidate(
                    connection,
                    item,
                    now,
                )
                inserted_evidence = 0
                for evidence in result["evidence"]:
                    inserted_evidence += int(
                        record_candidate_evidence(
                            connection,
                            candidate_id,
                            int(session["review_item_id"]),
                            owner_digest,
                            int(session["expected_generation"]),
                            str(evidence["signal_type"]),
                            str(evidence["source_kind"]),
                            str(evidence["summary"]),
                            now,
                        )
                    )
                if item["link"] is None:
                    if inserted_evidence < 1:
                        raise ValueError("candidate_evidence_required")
                    connection.execute(
                        "INSERT INTO metadata(key,value) VALUES(?,?)",
                        (
                            str(item["link_key"]),
                            canonical_json_bytes(
                                candidate_session_link_value(
                                    candidate_id,
                                    str(row["dedupe_expires_at"]),
                                )
                            ).decode("utf-8"),
                        ),
                    )
                candidate_count += 1
                target = (
                    new_candidates if created else merged_candidates
                )
                target.append(display_id("C", candidate_id))
                complete_batch_review_generation(
                    connection,
                    int(session["review_item_id"]),
                    batch_id,
                    owner_digest,
                    int(session["expected_generation"]),
                    int(session["frozen_epoch"]),
                    int(session["frozen_from"]),
                    int(session["frozen_to"]),
                    str(session["frozen_locator_digest"]),
                    "reviewed",
                    None,
                    now,
                )
            audit = finalize_review_batch(
                connection,
                batch_id,
                owner_digest,
                "completed",
                candidate_count,
                semantic_exclusions,
                now,
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    except (CatalogAdapterError, ValueError):
        return rotate_invalid_review_result(
            connection,
            installation,
            batch_id,
            owner_token,
            opened,
            now,
        )
    deleted = delete_bound_review_result(opened)
    return {
        "schema_version": 1,
        "status": "completed",
        "batch_id": batch_id,
        "new_candidates": new_candidates,
        "merged_candidates": sorted(set(merged_candidates)),
        "exclusion_counts": dict(audit["exclusion_counts"]),
        "result_deleted": deleted,
    }
```

- [ ] **Step 7 (2–5 min): Implement candidate insertion, distinct-session recurrence, tombstone, and stale revival**

Add this helper before `commit_review_result`:

```python
def upsert_validated_candidate(
    connection: sqlite3.Connection,
    prepared: dict[str, object],
    now: float,
) -> tuple[int, bool]:
    if not connection.in_transaction:
        raise ValueError("active_review_transaction_required")
    result = prepared["result"]
    classification = result["classification"]
    entry = prepared["entry"]
    existing = prepared["existing"]
    link = prepared["link"]
    now_text = iso_utc(now)
    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO candidates(
              fingerprint,target_identity,target_skill,target_path,
              problem_category,target_locator,proposal_intent,conflict_group,
              problem_summary,proposal_summary,validation_plan,risk_level,
              status,occurrence_count,first_seen_at,last_seen_at,updated_at,
              tombstone_until
            ) VALUES(?,?,?,?,?,?,?,NULL,?,?,?,?, 'proposed',1,?,?,?,NULL)
            """,
            (
                str(prepared["fingerprint"]),
                entry.identity,
                entry.skill_dir.name,
                str(entry.skill_dir),
                str(classification["problem_category"]),
                str(classification["target_locator"]),
                str(classification["proposal_intent"]),
                str(result["problem_summary"]),
                str(result["proposal_summary"]),
                str(result["validation_plan"]),
                str(result["risk_level"]),
                now_text,
                now_text,
                now_text,
            ),
        )
        return int(cursor.lastrowid), True
    candidate_id = int(existing["id"])
    if link is not None:
        return candidate_id, False
    status = str(existing["status"])
    tombstone = existing["tombstone_until"]
    tombstone_active = (
        status == "rejected"
        and tombstone is not None
        and parse_iso_utc(str(tombstone)) > now
    )
    revive = status == "stale" or (
        status == "rejected" and not tombstone_active
    )
    if revive:
        connection.execute(
            """
            UPDATE candidates
            SET occurrence_count=occurrence_count+1,
                last_seen_at=?,updated_at=?,status='proposed',
                tombstone_until=NULL,target_path=?,target_locator=?,
                proposal_intent=?,problem_summary=?,proposal_summary=?,
                validation_plan=?,risk_level=?
            WHERE id=?
            """,
            (
                now_text,
                now_text,
                str(entry.skill_dir),
                str(classification["target_locator"]),
                str(classification["proposal_intent"]),
                str(result["problem_summary"]),
                str(result["proposal_summary"]),
                str(result["validation_plan"]),
                str(result["risk_level"]),
                candidate_id,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE candidates
            SET occurrence_count=occurrence_count+1,
                last_seen_at=?,updated_at=?
            WHERE id=?
            """,
            (now_text, now_text, candidate_id),
        )
    return candidate_id, False
```

This helper deliberately increments occurrence only when the recurrence link is
absent. An active rejected tombstone accepts new distinct-session evidence and
last-seen recurrence but remains rejected. Expired rejected and stale rows
revive only on a new distinct-session link and restore the catalog target path,
classification locator and intent, validated summaries, validation plan, and
risk level from the new result.

- [ ] **Step 8 (2–5 min): Run atomic commit tests**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -k CandidateCommitTests \
  -v
```

Expected: all ten tests pass; foreign output is preserved, a post-read binding rotation rolls back every candidate-side database mutation while preserving the new bound file, batch-bound invalid output rotates to one new result file, persisted adapter and capacity exclusions survive completion, every static digest, a freshly loaded in-transaction runtime/catalog snapshot, and every frozen tuple are revalidated, candidate targets resolve only from that live snapshot, four new fingerprints roll back completely, and one session cannot create a second candidate.

- [ ] **Step 9 (2–5 min): Run all Review tests and Phase 3 generation regressions**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -k GenerationStateTests \
  -v
```

Expected: all Review tests and all generation-state tests pass.

- [ ] **Step 10 (2–5 min): Commit the atomic candidate transaction**

```bash
cd /Users/igyeongseob/Documents/오픈소스
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py
git diff --cached --check
git commit -m "feat(skill-evolver): commit review candidates atomically"
```

Expected: one commit containing only `evolver.py` and `test_review.py`.

---

### Task 4: Apply 30/90/180-Day Candidate Maintenance

**Files:**
- Modify: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/test_review.py`
- Modify: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/test_capture.py`

**Interfaces:**
- Consumes: existing `run_maintenance`, Plan 4B `cleanup_review_results(now) -> dict[str, int]`, Task 2 recurrence keys, and Task 3 candidate state.
- Produces: config values `deferred_to_stale_days=30`, `rejected_tombstone_days=90`, `terminal_candidate_retention_days=90`; aggregate metadata at `f"candidate.{candidate_id}.evidence_aggregate"`; strict `load_candidate_evidence_aggregate(connection, candidate_id) -> dict[str, object]` with a 4,096-byte bound; recurrence-link deletion at the existing 180-day session boundary.

- [ ] **Step 1 (2–5 min): Add the three candidate-retention defaults to config tests**

In `RuntimeStoreTests.test_init_creates_private_one_row_per_session_schema` in
`test_capture.py`, add these assertions after the current review limits:

```python
        self.assertEqual(config.deferred_to_stale_days, 30)
        self.assertEqual(config.rejected_tombstone_days, 90)
        self.assertEqual(config.terminal_candidate_retention_days, 90)
```

In the exact `DEFAULTS` key validation tests, use this complete expected subset:

```python
        self.assertEqual(
            {
                "deferred_to_stale_days": config.deferred_to_stale_days,
                "rejected_tombstone_days": config.rejected_tombstone_days,
                "terminal_candidate_retention_days": (
                    config.terminal_candidate_retention_days
                ),
            },
            {
                "deferred_to_stale_days": 30,
                "rejected_tombstone_days": 90,
                "terminal_candidate_retention_days": 90,
            },
        )
```

Add these two methods to `RuntimeStoreTests`:

```python
    def test_load_config_accepts_only_three_legacy_missing_keys(
        self,
    ) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "legacy-candidate-retention",
            (self.sessions,),
            self.config,
        )
        installation = self.runtime.load_installation(
            installation_path
        )
        payload = json.loads(
            installation.config_path.read_text(encoding="utf-8")
        )
        for key in self.runtime.LEGACY_OPTIONAL_CONFIG_KEYS:
            payload.pop(key)
        installation.config_path.write_text(
            json.dumps(payload), encoding="utf-8"
        )
        installation.config_path.chmod(0o600)
        loaded = self.runtime.load_config(installation)
        self.assertEqual(loaded.deferred_to_stale_days, 30)
        self.assertEqual(loaded.rejected_tombstone_days, 90)
        self.assertEqual(
            loaded.terminal_candidate_retention_days, 90
        )

    def test_load_config_rejects_old_missing_or_unknown_keys(
        self,
    ) -> None:
        mutations = (
            ("missing_old", "pending_retention_days", None),
            ("unknown", "unexpected_retention_days", 7),
        )
        for name, key, value in mutations:
            with self.subTest(name=name):
                installation_path = self.runtime.initialize_runtime(
                    self.base / name,
                    (self.sessions,),
                    self.config,
                )
                installation = self.runtime.load_installation(
                    installation_path
                )
                payload = json.loads(
                    installation.config_path.read_text(
                        encoding="utf-8"
                    )
                )
                if value is None:
                    payload.pop(key)
                else:
                    payload[key] = value
                installation.config_path.write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                installation.config_path.chmod(0o600)
                with self.assertRaisesRegex(
                    ValueError, "invalid_config_keys"
                ):
                    self.runtime.load_config(installation)
```

- [ ] **Step 2 (2–5 min): Add 30/90/180 retention, aggregate, link, and revival tests**

Add this class after `CandidateCommitTests`:

```python
class CandidateMaintenanceTests(CandidateBatchFixture):
    def committed_candidate(
        self, now: float
    ) -> tuple[int, dict[str, object]]:
        claim = self.claim(1, now)
        path = self.write_result(claim, self.result_payload(claim))
        self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(claim["batch_id"]),
            str(claim["owner_token"]),
            path,
            now + 1,
        )
        candidate_id = int(
            self.connection.execute(
                "SELECT id FROM candidates"
            ).fetchone()["id"]
        )
        return candidate_id, claim

    def test_30_90_180_maintenance_preserves_only_counts(self) -> None:
        now = 2_000_000_000.0
        claim = self.claim(1, now)
        payload = self.result_payload(claim)
        initial_summary = "redacted:valid-model-summary"
        payload["sessions"][0]["problem_summary"] = initial_summary
        path = self.write_result(claim, payload)
        self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(claim["batch_id"]),
            str(claim["owner_token"]),
            path,
            now + 1,
        )
        candidate_id = int(
            self.connection.execute(
                "SELECT id FROM candidates"
            ).fetchone()["id"]
        )
        self.assertEqual(
            self.runtime.load_candidate_evidence_aggregate(
                self.connection, candidate_id
            ),
            {
                "schema_version": 1,
                "counts": [],
                "updated_at": None,
            },
        )
        self.connection.execute(
            """
            UPDATE candidates
            SET status='deferred',updated_at=?
            WHERE id=?
            """,
            (self.runtime.iso_utc(now + 2), candidate_id),
        )
        at_30 = now + 2 + 30 * 86_400
        first = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            at_30,
        )
        stale = self.connection.execute(
            "SELECT status,updated_at FROM candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(first["candidates_staled"], 1)
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["updated_at"], self.runtime.iso_utc(at_30))

        at_90_terminal = at_30 + 90 * 86_400
        second = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            at_90_terminal,
        )
        redacted = self.connection.execute(
            """
            SELECT target_path,target_locator,proposal_intent,
              problem_summary,proposal_summary,validation_plan,risk_level,
              updated_at
            FROM candidates WHERE id=?
            """,
            (candidate_id,),
        ).fetchone()
        evidence = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidate_evidence"
            ).fetchone()[0]
        )
        links = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM metadata "
                "WHERE key LIKE 'candidate-session.%'"
            ).fetchone()[0]
        )
        aggregate_key = (
            self.runtime.candidate_evidence_aggregate_key(candidate_id)
        )
        aggregate_row = self.connection.execute(
            "SELECT value FROM metadata WHERE key=?",
            (aggregate_key,),
        ).fetchone()
        canonical_aggregate = aggregate_row["value"]
        aggregate = (
            self.runtime.load_candidate_evidence_aggregate(
                self.connection, candidate_id
            )
        )
        self.assertEqual(second["candidate_text_redacted"], 1)
        self.assertIsNone(redacted["target_path"])
        self.assertEqual(
            redacted["updated_at"], self.runtime.iso_utc(at_30)
        )
        self.assertEqual(
            redacted["problem_summary"],
            self.runtime.redacted_marker(initial_summary),
        )
        for name in (
            "target_locator",
            "proposal_intent",
            "proposal_summary",
            "validation_plan",
            "risk_level",
        ):
            self.assertRegex(
                redacted[name], r"^redacted:[0-9a-f]{64}$"
            )
        self.assertEqual(evidence, 0)
        self.assertEqual(links, 1)
        self.assertEqual(
            aggregate["counts"],
            [
                {
                    "signal_type": "explicit_correction",
                    "source_kind": "user_direct",
                    "count": 1,
                }
            ],
        )
        self.assertNotIn("session", json.dumps(aggregate))
        invalid_aggregate_values = (
            ("oversized", "{" + "x" * 4_096),
            ("noncanonical", json.dumps(aggregate)),
            (
                "duplicate-json-key",
                canonical_aggregate.replace(
                    '"schema_version":1',
                    '"schema_version":1,"schema_version":1',
                    1,
                ),
            ),
            (
                "private-field",
                self.runtime.canonical_json_bytes(
                    {
                        "schema_version": 1,
                        "counts": [
                            {
                                "signal_type": "explicit_correction",
                                "source_kind": "user_direct",
                                "count": 1,
                                "summary": "private",
                            }
                        ],
                        "updated_at": aggregate["updated_at"],
                    }
                ).decode("utf-8"),
            ),
            (
                "top-level-extra-key",
                self.runtime.canonical_json_bytes(
                    {
                        **aggregate,
                        "session_key": "private",
                    }
                ).decode("utf-8"),
            ),
            (
                "unsorted-pairs",
                self.runtime.canonical_json_bytes(
                    {
                        "schema_version": 1,
                        "counts": [
                            {
                                "signal_type": "unnecessary_rework",
                                "source_kind": "user_direct",
                                "count": 1,
                            },
                            {
                                "signal_type": "explicit_correction",
                                "source_kind": "user_direct",
                                "count": 1,
                            },
                        ],
                        "updated_at": aggregate["updated_at"],
                    }
                ).decode("utf-8"),
            ),
            (
                "duplicate-pair",
                self.runtime.canonical_json_bytes(
                    {
                        "schema_version": 1,
                        "counts": [
                            aggregate["counts"][0],
                            aggregate["counts"][0],
                        ],
                        "updated_at": aggregate["updated_at"],
                    }
                ).decode("utf-8"),
            ),
            (
                "unknown-pair",
                self.runtime.canonical_json_bytes(
                    {
                        "schema_version": 1,
                        "counts": [
                            {
                                "signal_type": "external_instruction",
                                "source_kind": "web",
                                "count": 1,
                            }
                        ],
                        "updated_at": aggregate["updated_at"],
                    }
                ).decode("utf-8"),
            ),
            (
                "aggregate-count-overflow",
                self.runtime.canonical_json_bytes(
                    {
                        "schema_version": 1,
                        "counts": [
                            {
                                "signal_type": "explicit_correction",
                                "source_kind": "user_direct",
                                "count": (
                                    self.runtime.SQLITE_INTEGER_MAX
                                ),
                            },
                            {
                                "signal_type": "unnecessary_rework",
                                "source_kind": "user_direct",
                                "count": 1,
                            },
                        ],
                        "updated_at": aggregate["updated_at"],
                    }
                ).decode("utf-8"),
            ),
            *(
                (
                    f"invalid-count-{label}",
                    self.runtime.canonical_json_bytes(
                        {
                            "schema_version": 1,
                            "counts": [
                                {
                                    "signal_type": (
                                        "explicit_correction"
                                    ),
                                    "source_kind": "user_direct",
                                    "count": count,
                                }
                            ],
                            "updated_at": aggregate["updated_at"],
                        }
                    ).decode("utf-8"),
                )
                for label, count in (
                    ("bool", True),
                    ("zero", 0),
                    (
                        "overflow",
                        self.runtime.SQLITE_INTEGER_MAX + 1,
                    ),
                )
            ),
            (
                "invalid-updated-at",
                self.runtime.canonical_json_bytes(
                    {
                        "schema_version": 1,
                        "counts": aggregate["counts"],
                        "updated_at": "2033-01-01T00:00:00.1Z",
                    }
                ).decode("utf-8"),
            ),
        )
        for label, invalid in invalid_aggregate_values:
            with self.subTest(invalid_aggregate=label):
                self.connection.execute(
                    "UPDATE metadata SET value=? WHERE key=?",
                    (invalid, aggregate_key),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "^invalid_candidate_evidence_aggregate$",
                ):
                    self.runtime.load_candidate_evidence_aggregate(
                        self.connection, candidate_id
                    )
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (json.dumps(aggregate), aggregate_key),
        )
        with self.assertRaisesRegex(
            ValueError, "^invalid_candidate_evidence_aggregate$"
        ):
            self.runtime.merge_candidate_evidence_aggregate(
                self.connection,
                candidate_id,
                {("explicit_correction", "user_direct"): 1},
                at_90_terminal + 1,
            )
        self.connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (canonical_aggregate, aggregate_key),
        )
        with self.assertRaisesRegex(
            ValueError, "^invalid_candidate_evidence_count$"
        ):
            self.runtime.merge_candidate_evidence_aggregate(
                self.connection,
                candidate_id,
                {
                    (
                        "unnecessary_rework",
                        "user_direct",
                    ): self.runtime.SQLITE_INTEGER_MAX
                },
                at_90_terminal + 1,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT value FROM metadata WHERE key=?",
                (aggregate_key,),
            ).fetchone()["value"],
            canonical_aggregate,
        )

        at_180 = now + self.config.session_dedupe_days * 86_400
        third = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            at_180,
        )
        remaining = self.connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM review_items),
              (SELECT COUNT(*) FROM candidate_evidence),
              (SELECT COUNT(*) FROM metadata
               WHERE key LIKE 'candidate-session.%')
            """
        ).fetchone()
        occurrence = int(
            self.connection.execute(
                "SELECT occurrence_count FROM candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()[0]
        )
        self.assertEqual(third["dedupe_deleted"], 1)
        self.assertEqual(third["candidate_session_links_deleted"], 1)
        self.assertEqual(tuple(remaining), (0, 0, 0))
        self.assertEqual(occurrence, 1)

    def test_stale_and_expired_tombstone_require_new_session_evidence(
        self,
    ) -> None:
        now = 2_000_000_000.0
        candidate_id, _ = self.committed_candidate(now)
        self.connection.execute(
            """
            UPDATE candidates
            SET status='deferred',updated_at=?
            WHERE id=?
            """,
            (self.runtime.iso_utc(now + 2), candidate_id),
        )
        at_30 = now + 2 + 30 * 86_400
        first = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            at_30,
        )
        self.assertEqual(first["candidates_staled"], 1)
        at_90_terminal = at_30 + 90 * 86_400
        second = self.runtime.run_maintenance(
            self.connection,
            self.installation,
            self.config,
            at_90_terminal,
        )
        redacted = self.connection.execute(
            """
            SELECT target_path,target_locator,proposal_intent,
              problem_summary,proposal_summary,validation_plan,risk_level,
              updated_at
            FROM candidates WHERE id=?
            """,
            (candidate_id,),
        ).fetchone()
        self.assertEqual(second["candidate_text_redacted"], 1)
        self.assertIsNone(redacted["target_path"])
        self.assertEqual(
            redacted["updated_at"], self.runtime.iso_utc(at_30)
        )
        for name in (
            "target_locator",
            "proposal_intent",
            "problem_summary",
            "proposal_summary",
            "validation_plan",
            "risk_level",
        ):
            self.assertRegex(
                redacted[name], r"^redacted:[0-9a-f]{64}$"
            )
        aggregate = self.runtime.load_candidate_evidence_aggregate(
            self.connection, candidate_id
        )
        self.assertEqual(
            set(aggregate), {"schema_version", "counts", "updated_at"}
        )
        self.assertEqual(
            aggregate["counts"],
            [
                {
                    "signal_type": "explicit_correction",
                    "source_kind": "user_direct",
                    "count": 1,
                }
            ],
        )

        revive_at = at_90_terminal + 1
        self.insert_pending(
            self.connection,
            99,
            text="new stale evidence\n",
            now=revive_at,
        )
        stale_claim = self.claim_ready_batch(
            self.connection,
            [self.make_export("new stale evidence")],
            now=revive_at,
        )
        stale_payload = self.result_payload(stale_claim)
        stale_result = stale_payload["sessions"][0]
        stale_result["problem_summary"] = "Fresh validated problem."
        stale_result["proposal_summary"] = "Fresh validated proposal."
        stale_result["validation_plan"] = "Run the fresh regression."
        stale_result["risk_level"] = "medium"
        stale_path = self.write_result(
            stale_claim, stale_payload
        )
        self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(stale_claim["batch_id"]),
            str(stale_claim["owner_token"]),
            stale_path,
            revive_at + 1,
        )
        revived = self.connection.execute(
            """
            SELECT status,occurrence_count,target_path,target_locator,
              proposal_intent,problem_summary,proposal_summary,
              validation_plan,risk_level
            FROM candidates WHERE id=?
            """,
            (candidate_id,),
        ).fetchone()
        classification = stale_result["classification"]
        self.assertEqual(
            tuple(revived),
            (
                "proposed",
                2,
                str(self.catalog_entry.skill_dir),
                classification["target_locator"],
                classification["proposal_intent"],
                stale_result["problem_summary"],
                stale_result["proposal_summary"],
                stale_result["validation_plan"],
                stale_result["risk_level"],
            ),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM metadata "
                "WHERE key LIKE 'candidate-session.%'"
            ).fetchone()[0],
            2,
        )

        tombstone = revive_at + 100
        self.connection.execute(
            """
            UPDATE candidates
            SET status='rejected',tombstone_until=?,updated_at=?
            WHERE id=?
            """,
            (
                self.runtime.iso_utc(
                    tombstone
                    + self.config.rejected_tombstone_days * 86_400
                ),
                self.runtime.iso_utc(tombstone),
                candidate_id,
            ),
        )
        self.insert_pending(
            self.connection,
            100,
            text="active tombstone evidence\n",
            now=tombstone + 1,
        )
        active_claim = self.claim_ready_batch(
            self.connection,
            [self.make_export("active tombstone evidence")],
            now=tombstone + 1,
        )
        active_path = self.write_result(
            active_claim, self.result_payload(active_claim)
        )
        self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(active_claim["batch_id"]),
            str(active_claim["owner_token"]),
            active_path,
            tombstone + 2,
        )
        active = self.connection.execute(
            "SELECT status,occurrence_count FROM candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(tuple(active), ("rejected", 3))

        expired_at = (
            tombstone
            + self.config.rejected_tombstone_days * 86_400
            + 1
        )
        self.insert_pending(
            self.connection,
            101,
            text="expired tombstone evidence\n",
            now=expired_at,
        )
        expired_claim = self.claim_ready_batch(
            self.connection,
            [self.make_export("expired tombstone evidence")],
            now=expired_at,
        )
        expired_payload = self.result_payload(expired_claim)
        expired_path = self.write_result(
            expired_claim, expired_payload
        )
        self.runtime.commit_review_result(
            self.connection,
            self.installation,
            self.config,
            int(expired_claim["batch_id"]),
            str(expired_claim["owner_token"]),
            expired_path,
            expired_at + 1,
        )
        expired = self.connection.execute(
            """
            SELECT status,occurrence_count,tombstone_until,target_path,
              target_locator,proposal_intent,problem_summary,
              proposal_summary,validation_plan,risk_level
            FROM candidates WHERE id=?
            """,
            (candidate_id,),
        ).fetchone()
        expired_result = expired_payload["sessions"][0]
        expired_classification = expired_result["classification"]
        self.assertEqual(
            tuple(expired),
            (
                "proposed",
                4,
                None,
                str(self.catalog_entry.skill_dir),
                expired_classification["target_locator"],
                expired_classification["proposal_intent"],
                expired_result["problem_summary"],
                expired_result["proposal_summary"],
                expired_result["validation_plan"],
                expired_result["risk_level"],
            ),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM metadata "
                "WHERE key LIKE 'candidate-session.%'"
            ).fetchone()[0],
            4,
        )
```

- [ ] **Step 3 (2–5 min): Run candidate maintenance tests and verify RED**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -k CandidateMaintenanceTests \
  -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -k test_load_config_accepts_only_three_legacy_missing_keys \
  -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -k test_load_config_rejects_old_missing_or_unknown_keys \
  -v
```

Expected: the same two candidate methods fail because the config fields,
strict aggregate loader/merge helpers, and candidate maintenance counts do not
exist; both config tests fail because the three-key legacy merge contract is
not implemented. Do not add a third `CandidateMaintenanceTests` method.

- [ ] **Step 4 (2–5 min): Add config fields without changing schema version**

Add these entries to `DEFAULTS`:

```python
    "deferred_to_stale_days": 30,
    "rejected_tombstone_days": 90,
    "terminal_candidate_retention_days": 90,
```

Define the only backward-compatible missing-key set beside `DEFAULTS`:

```python
LEGACY_OPTIONAL_CONFIG_KEYS = {
    "deferred_to_stale_days",
    "rejected_tombstone_days",
    "terminal_candidate_retention_days",
}
```

Add the matching fields to `Config` after `lease_heartbeat_seconds`:

```python
    deferred_to_stale_days: int
    rejected_tombstone_days: int
    terminal_candidate_retention_days: int
```

Replace only the key-shape check at the start of `load_config` with:

```python
    payload = json.loads(
        installation.config_path.read_text(encoding="utf-8")
    )
    allowed = set(DEFAULTS) | {"capture_paused", "exclude_roots"}
    keys = set(payload) if isinstance(payload, dict) else set()
    required = allowed - LEGACY_OPTIONAL_CONFIG_KEYS
    if (
        not isinstance(payload, dict)
        or not required.issubset(keys)
        or not keys.issubset(allowed)
    ):
        raise ValueError("invalid_config_keys")
    payload = {
        **{
            key: DEFAULTS[key]
            for key in LEGACY_OPTIONAL_CONFIG_KEYS
        },
        **payload,
    }
```

Keep the existing exact type/range validation loop unchanged after this merge. Only the three new retention keys may be absent. Any missing old key and every unknown key still raises `invalid_config_keys`. Do not change `SCHEMA_VERSION`, `SCHEMA_SQL`, `PRAGMA user_version`, Stop upsert semantics, or Phase 3 generation completion semantics.

- [ ] **Step 5 (2–5 min): Implement the strict aggregate loader, deterministic merging, and redaction**

Add these helpers before `run_maintenance`:

```python
def redacted_marker(value: object) -> str:
    encoded = str(value).encode("utf-8")
    return f"redacted:{hashlib.sha256(encoded).hexdigest()}"


CANDIDATE_EVIDENCE_AGGREGATE_MAX_BYTES = 4_096


def load_candidate_evidence_aggregate(
    connection: sqlite3.Connection,
    candidate_id: int,
) -> dict[str, object]:
    key = candidate_evidence_aggregate_key(candidate_id)
    row = connection.execute(
        "SELECT value FROM metadata WHERE key=?", (key,)
    ).fetchone()
    if row is None:
        return {
            "schema_version": 1,
            "counts": [],
            "updated_at": None,
        }
    raw = row["value"]
    if type(raw) is not str:
        raise ValueError("invalid_candidate_evidence_aggregate")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(
            "invalid_candidate_evidence_aggregate"
        ) from None
    if len(encoded) > CANDIDATE_EVIDENCE_AGGREGATE_MAX_BYTES:
        raise ValueError("invalid_candidate_evidence_aggregate")
    try:
        current = _load_declarative_result_json(encoded)
        canonical = canonical_json_bytes(current)
    except (UnicodeError, ValueError):
        raise ValueError(
            "invalid_candidate_evidence_aggregate"
        ) from None
    if (
        type(current) is not dict
        or set(current)
        != {"schema_version", "counts", "updated_at"}
        or type(current.get("schema_version")) is not int
        or current["schema_version"] != 1
        or type(current.get("counts")) is not list
        or not current["counts"]
        or len(current["counts"]) > len(SIGNAL_SOURCE_PAIRS)
        or type(current.get("updated_at")) is not str
        or canonical != encoded
    ):
        raise ValueError("invalid_candidate_evidence_aggregate")
    try:
        parse_iso_utc(current["updated_at"])
    except ValueError:
        raise ValueError(
            "invalid_candidate_evidence_aggregate"
        ) from None
    pairs: list[tuple[str, str]] = []
    total = 0
    for item in current["counts"]:
        if (
            type(item) is not dict
            or set(item) != {"signal_type", "source_kind", "count"}
            or type(item.get("signal_type")) is not str
            or type(item.get("source_kind")) is not str
            or type(item.get("count")) is not int
        ):
            raise ValueError("invalid_candidate_evidence_aggregate")
        pair = (item["signal_type"], item["source_kind"])
        count = item["count"]
        if (
            pair not in SIGNAL_SOURCE_PAIRS
            or not 1 <= count <= SQLITE_INTEGER_MAX
            or total > SQLITE_INTEGER_MAX - count
        ):
            raise ValueError("invalid_candidate_evidence_aggregate")
        pairs.append(pair)
        total += count
    if pairs != sorted(pairs) or len(pairs) != len(set(pairs)):
        raise ValueError("invalid_candidate_evidence_aggregate")
    return current


def merge_candidate_evidence_aggregate(
    connection: sqlite3.Connection,
    candidate_id: int,
    counts: dict[tuple[str, str], int],
    now: float,
) -> None:
    key = candidate_evidence_aggregate_key(candidate_id)
    current = load_candidate_evidence_aggregate(
        connection, candidate_id
    )
    merged: dict[tuple[str, str], int] = {}
    for item in current["counts"]:
        pair = (item["signal_type"], item["source_kind"])
        merged[pair] = item["count"]
    if type(counts) is not dict or not counts:
        raise ValueError("invalid_candidate_evidence_count")
    for pair, count in counts.items():
        if (
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(value) is not str for value in pair)
            or pair not in SIGNAL_SOURCE_PAIRS
            or type(count) is not int
            or not 1 <= count <= SQLITE_INTEGER_MAX
            or merged.get(pair, 0) > SQLITE_INTEGER_MAX - count
        ):
            raise ValueError("invalid_candidate_evidence_count")
        merged[pair] = merged.get(pair, 0) + count
    if sum(merged.values()) > SQLITE_INTEGER_MAX:
        raise ValueError("invalid_candidate_evidence_count")
    payload = {
        "schema_version": 1,
        "counts": [
            {
                "signal_type": signal_type,
                "source_kind": source_kind,
                "count": count,
            }
            for (signal_type, source_kind), count in sorted(
                merged.items()
            )
        ],
        "updated_at": iso_utc(now),
    }
    encoded = canonical_json_bytes(payload)
    if len(encoded) > CANDIDATE_EVIDENCE_AGGREGATE_MAX_BYTES:
        raise ValueError("invalid_candidate_evidence_aggregate")
    connection.execute(
        """
        INSERT INTO metadata(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, encoded.decode("utf-8")),
    )


def aggregate_candidate_evidence_rows(
    connection: sqlite3.Connection,
    candidate_ids: list[int],
    now: float,
) -> int:
    if not candidate_ids:
        return 0
    marks = ",".join("?" for _ in candidate_ids)
    rows = connection.execute(
        f"""
        SELECT candidate_id,signal_type,source_kind,COUNT(*) AS count
        FROM candidate_evidence
        WHERE candidate_id IN ({marks})
        GROUP BY candidate_id,signal_type,source_kind
        """,
        tuple(candidate_ids),
    )
    grouped: dict[int, dict[tuple[str, str], int]] = {}
    for row in rows:
        candidate_id = int(row["candidate_id"])
        grouped.setdefault(candidate_id, {})[
            (str(row["signal_type"]), str(row["source_kind"]))
        ] = int(row["count"])
    for candidate_id, counts in grouped.items():
        merge_candidate_evidence_aggregate(
            connection, candidate_id, counts, now
        )
    return sum(sum(counts.values()) for counts in grouped.values())
```

The loader is the only aggregate read path used by later maintenance and
inspection. It reads at most 4,096 UTF-8 bytes, uses the duplicate-rejecting
strict JSON decoder, requires byte-for-byte canonical JSON, and accepts only
the exact top-level and count-item keys shown above. Stored aggregates are
non-empty, their UTC timestamp is canonical, their allowed signal/source pairs
are unique and sorted, and each count and their sum fit SQLite's positive
integer range. Because the only string values are allowlisted enums and a UTC
timestamp, session identifiers, summaries, paths, record references, and other
private data are structurally impossible. Only an absent metadata row returns
the exact empty object with `updated_at: None`. Every merge calls this loader
before combining counts and fails closed without replacing malformed metadata.

- [ ] **Step 6 (2–5 min): Add the candidate transition and 90-day terminal cleanup transaction**

Inside the existing `BEGIN IMMEDIATE` section of `run_maintenance`, before
session dedupe deletion, add the following two independently bounded cohorts.
Both order by `updated_at,id` and limit by
`REVIEW_MAINTENANCE_BATCH_MAX`; that inherited Phase 4B constant is exactly
`200`. Each mutation is restricted to its selected ID set:

```python
        stale_cutoff = iso_utc(
            now - config.deferred_to_stale_days * 86_400
        )
        stale_ids = [
            int(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM candidates
                WHERE status='deferred' AND updated_at<=?
                ORDER BY updated_at,id
                LIMIT ?
                """,
                (
                    stale_cutoff,
                    REVIEW_MAINTENANCE_BATCH_MAX,
                ),
            )
        ]
        if stale_ids:
            marks = ",".join("?" for _ in stale_ids)
            candidates_staled = connection.execute(
                f"""
                UPDATE candidates
                SET status='stale',updated_at=?
                WHERE id IN ({marks})
                  AND status='deferred' AND updated_at<=?
                """,
                (
                    iso_utc(now),
                    *stale_ids,
                    stale_cutoff,
                ),
            ).rowcount
            if candidates_staled != len(stale_ids):
                raise sqlite3.IntegrityError(
                    "candidate_stale_cohort_race"
                )
        else:
            candidates_staled = 0
        terminal_cutoff = iso_utc(
            now - config.terminal_candidate_retention_days * 86_400
        )
        terminal_ids = [
            int(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM candidates
                WHERE status IN ('rejected','stale')
                  AND updated_at<=?
                  AND target_path IS NOT NULL
                ORDER BY updated_at,id
                LIMIT ?
                """,
                (
                    terminal_cutoff,
                    REVIEW_MAINTENANCE_BATCH_MAX,
                ),
            )
        ]
        terminal_evidence_aggregated = (
            aggregate_candidate_evidence_rows(
                connection, terminal_ids, now
            )
        )
        if terminal_ids:
            marks = ",".join("?" for _ in terminal_ids)
            connection.execute(
                f"""
                DELETE FROM candidate_evidence
                WHERE candidate_id IN ({marks})
                """,
                tuple(terminal_ids),
            )
            terminal_rows = list(
                connection.execute(
                    f"""
                    SELECT id,target_locator,proposal_intent,
                      problem_summary,proposal_summary,validation_plan,
                      risk_level
                    FROM candidates WHERE id IN ({marks})
                    ORDER BY id
                    """,
                    tuple(terminal_ids),
                )
            )
            candidate_text_redacted = 0
            for candidate in terminal_rows:
                changed = connection.execute(
                    """
                    UPDATE candidates
                    SET target_path=NULL,target_locator=?,proposal_intent=?,
                        problem_summary=?,proposal_summary=?,
                        validation_plan=?,risk_level=?
                    WHERE id=? AND target_path IS NOT NULL
                      AND status IN ('rejected','stale')
                      AND updated_at<=?
                    """,
                    (
                        redacted_marker(candidate["target_locator"]),
                        redacted_marker(candidate["proposal_intent"]),
                        redacted_marker(candidate["problem_summary"]),
                        redacted_marker(candidate["proposal_summary"]),
                        redacted_marker(candidate["validation_plan"]),
                        redacted_marker(candidate["risk_level"]),
                        int(candidate["id"]),
                        terminal_cutoff,
                    ),
                ).rowcount
                if changed != 1:
                    raise sqlite3.IntegrityError(
                        "candidate_redaction_cohort_race"
                    )
                candidate_text_redacted += changed
        else:
            candidate_text_redacted = 0
```

`target_path` is the system-owned redaction sentinel: a live candidate has its
catalog path, while this transaction sets it to `NULL` only after hashing every
retained free-text/classification field. Never infer redaction state from
model-controlled text such as a `problem_summary` prefix. Do not delete
`candidate-session.*` links in this 90-day block. Redaction deliberately does
not update `updated_at`: it remains the status-age clock used to select the
terminal cohort.

- [ ] **Step 7 (2–5 min): Aggregate and delete recurrence at the 180-day identity boundary**

Replace the current candidate-evidence/session dedupe block in
`run_maintenance` with one `dedupe_expires_at,id` ordered cohort limited by
the same exact 200-row `REVIEW_MAINTENANCE_BATCH_MAX`:

```python
        dedupe_cutoff = iso_utc(now)
        expiring_sessions = list(
            connection.execute(
                """
                SELECT id,session_key FROM review_items
                WHERE dedupe_expires_at<=?
                  AND status NOT IN ('pending','reviewing')
                ORDER BY dedupe_expires_at,id
                LIMIT ?
                """,
                (
                    dedupe_cutoff,
                    REVIEW_MAINTENANCE_BATCH_MAX,
                ),
            )
        )
        expiring_ids = [
            int(row["id"]) for row in expiring_sessions
        ]
        expiring_keys = [
            str(row["session_key"]) for row in expiring_sessions
        ]
        if expiring_ids:
            marks = ",".join("?" for _ in expiring_ids)
            evidence_rows = list(
                connection.execute(
                    f"""
                    SELECT candidate_id,signal_type,source_kind,
                      COUNT(*) AS count
                    FROM candidate_evidence
                    WHERE review_item_id IN ({marks})
                    GROUP BY candidate_id,signal_type,source_kind
                    """,
                    tuple(expiring_ids),
                )
            )
            grouped: dict[int, dict[tuple[str, str], int]] = {}
            for evidence in evidence_rows:
                grouped.setdefault(
                    int(evidence["candidate_id"]), {}
                )[
                    (
                        str(evidence["signal_type"]),
                        str(evidence["source_kind"]),
                    )
                ] = int(evidence["count"])
            for candidate_id, counts_by_pair in grouped.items():
                merge_candidate_evidence_aggregate(
                    connection, candidate_id, counts_by_pair, now
                )
            connection.execute(
                f"""
                DELETE FROM candidate_evidence
                WHERE review_item_id IN ({marks})
                """,
                tuple(expiring_ids),
            )
            candidate_session_links_deleted = 0
            for session_key_value in expiring_keys:
                candidate_session_links_deleted += (
                    connection.execute(
                        "DELETE FROM metadata WHERE key=?",
                        (
                            candidate_session_link_key(
                                installation, session_key_value
                            ),
                        ),
                    ).rowcount
                )
            dedupe_deleted = connection.execute(
                f"""
                DELETE FROM review_items
                WHERE id IN ({marks})
                  AND dedupe_expires_at<=?
                  AND status NOT IN ('pending','reviewing')
                """,
                (*expiring_ids, dedupe_cutoff),
            ).rowcount
            if dedupe_deleted != len(expiring_ids):
                raise sqlite3.IntegrityError(
                    "review_item_dedupe_cohort_race"
                )
        else:
            candidate_session_links_deleted = 0
            dedupe_deleted = 0
```

Only the selected review-item IDs and their exact evidence/link identities are
mutated. If more than 200 rows remain eligible, the next explicit maintenance
invocation processes the next `dedupe_expires_at,id` cohort.

- [ ] **Step 8 (2–5 min): Preserve Phase 4B maintenance finalization and extend only candidate counts**

Do not add a cleanup call before `import_spool`, and do not duplicate the
terminal batch/audit purge. Preserve Phase 4B's existing sequence unchanged:

1. perform the maintenance database transaction and commit it;
2. identity-safely delete captured bound result files after commit;
3. run bounded result cleanup as best effort and report its existing telemetry.

Also preserve Phase 4B's existing terminal batch/audit cohort exactly: it
orders eligible terminal batches, selects at most
`REVIEW_MAINTENANCE_BATCH_MAX` (`200`), deletes each exact audit key and the
same selected batch IDs atomically, and rolls back on an audit/batch row-count
mismatch.

Extend the existing returned maintenance dictionary with only these new
candidate fields:

```python
        "candidates_staled": candidates_staled,
        "terminal_evidence_aggregated": terminal_evidence_aggregated,
        "candidate_text_redacted": candidate_text_redacted,
        "candidate_session_links_deleted": (
            candidate_session_links_deleted
        ),
```

The pre-existing result-cleanup keys, `terminal_batches_deleted`, and
`result_cleanup_failed` remain where Phase 4B already returns them. Do not
rename, recompute, or add a second copy of any of those fields.

- [ ] **Step 9 (2–5 min): Run maintenance, capture, and Review tests**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -k CandidateMaintenanceTests \
  -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -k RuntimeStoreTests \
  -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -k MaintenanceStatusTests \
  -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -v
```

Expected: candidate maintenance tests pass, including model-prefix-independent
90-day redaction and complete stale/expired revival of catalog,
classification, summary, validation, and risk fields while recurrence links
remain distinct; all RuntimeStore config tests pass; existing
maintenance/status tests pass with the extended result keys; all Review tests
pass. At this completed Task 4 boundary Review discovery runs exactly 97
tests and capture discovery runs exactly 82 tests.

- [ ] **Step 10 (2–5 min): Commit candidate aging and privacy retention**

```bash
cd /Users/igyeongseob/Documents/오픈소스
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py
git diff --cached --check
git commit -m "feat(skill-evolver): retain candidate privacy aggregates"
```

Expected: one commit containing only `evolver.py`, `test_capture.py`, and `test_review.py`.

---

### Task 5: Freeze the Plan 4C Boundary

**Files:**
- Verify only: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Verify only: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/test_review.py`
- Verify unchanged: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/SKILL.md`
- Verify unchanged: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/README.md`
- Verify unchanged: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/hooks/hooks.json`
- Verify unchanged: `/Users/igyeongseob/Documents/오픈소스/skill-evolver/.codex-plugin/plugin.json`
- Verify unchanged: `/Users/igyeongseob/Documents/오픈소스/.agents/plugins/marketplace.json`

- [ ] **Step 1 (2–5 min): Prove Plan 4C has not exposed a CLI**

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -c 'import argparse,importlib.util,sys; from pathlib import Path; path=Path("skill-evolver/skills/skill-evolver/scripts/evolver.py"); spec=importlib.util.spec_from_file_location("evolver",path); module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module); parser=module.build_parser(); action=next(item for item in parser._actions if isinstance(item,argparse._SubParsersAction)); assert set(action.choices) == {"init","enqueue-stop","maintain","status"}; print("plan-4c-cli-boundary: PASS")'
git diff --exit-code -- \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/README.md \
  skill-evolver/hooks/hooks.json \
  skill-evolver/.codex-plugin/plugin.json \
  .agents/plugins/marketplace.json
```

Expected: `plan-4c-cli-boundary: PASS`; the diff command exits `0`. Do not add `review-claim`, `review-heartbeat`, `review-commit`, `review-abort`, `catalog-inspect`, `inspect`, `defer`, `resume`, or `reject` until Plan 4D.

- [ ] **Step 2 (2–5 min): Run the complete Plan 4C regression boundary**

```bash
cd /Users/igyeongseob/Documents/오픈소스
env PYTHONPYCACHEPREFIX=/private/tmp/skill-evolver-pycache \
  /usr/bin/python3 -m py_compile \
  skill-evolver/skills/skill-evolver/scripts/evolver.py
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_review.py' \
  -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py'
git diff --check
git status --short
```

Expected: compilation succeeds; `test_review.py` runs all 97 tests,
`test_capture.py` runs all 82 tests with no skip, and full discovery prints
`Ran 352 tests` and `OK (skipped=3)` with exactly the three historical
`test_skeleton.py` skips; `git diff --check` exits `0`; no generated result
file, installed-skill write, staging write, snapshot write, schema migration,
Hook edit, or CLI edit is present.

- [ ] **Step 3 (2–5 min): Record the Plan 4C implementation commit for Plan 4D**

```bash
cd /Users/igyeongseob/Documents/오픈소스
PLAN_4C_COMMIT="$(git rev-parse HEAD)"
test -n "$PLAN_4C_COMMIT"
git show --stat --oneline "$PLAN_4C_COMMIT"
```

Expected: the command identifies the last Plan 4C implementation commit. Pass this immutable commit to Plan 4D together with the enforced `require_bound_review_result_binding` call and merged-exclusion `finalize_review_batch` handoff; do not amend any Plan 4C commit after CLI work begins.
