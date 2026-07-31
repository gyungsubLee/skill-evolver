# Skill Evolver Codex 0.146 Transcript Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the read-only review adapter accept the known text-bearing and
control records emitted by Codex `0.146.0`, while preserving fail-closed
handling for unknown or multimodal transcript shapes.

**Architecture:** Keep the existing frozen-transcript, redaction, bounded
export, SQLite, and quality-epoch architecture unchanged. Update only the
release-shaped response-item allowlist and tool-output text decoder, publish
the contract as `codex-rollout-jsonl-v2`, and release it as patch version
`0.1.3`. Treat `Q-002` as provenance-invalid after rollout and prove the new
adapter with a fresh, user-origin Desktop session before collecting `Q-003`.

**Tech Stack:** Python 3 standard library, SQLite, `unittest`, Codex plugin
runtime JSONL, JSON, SHA-256.

## Global Constraints

- Add no dependency, schema migration, command, table, or abstraction.
- Keep `SCHEMA_VERSION = 1` and SQLite `journal_mode=DELETE`.
- Support the exact known Codex `rust-v0.146.0` response-item vocabulary;
  unknown future response items remain terminal `unsupported_transcript`.
- Export only user/assistant message text and supported textual tool output.
- Ignore known call/control metadata only; never infer evidence from it.
- Structured tool output accepts `input_text` and omits
  `encrypted_content`; image, audio, malformed, or unknown items fail closed.
- `image_generation_call` remains unsupported because it combines generated
  media and result state.
- Preserve all owner-token redaction, ready-review-claim exclusion, bounds,
  transcript identity, HMAC, and privacy behavior.
- Do not import the two currently preserved `0.1.2` spool observations until
  the `0.1.3` adapter is installed.
- Any quality lifecycle mutation requires its own exact literal approval.
- Never invoke `quality-label`; only the user may label candidates in an
  external TTY.
- Stage exact paths only; never use `git add .`.

---

## File Map

| File | Responsibility |
| --- | --- |
| `skills/skill-evolver/tests/test_review.py` | Lock the Codex `0.146.0` rollout vocabulary, textual output behavior, and fail-closed boundaries. |
| `skills/skill-evolver/scripts/evolver.py` | Classify known response items and decode supported tool-output text. |
| `skills/skill-evolver/tests/test_capture.py` | Lock the `0.1.3` release metadata exposed through the installed runtime contract. |
| `skills/skill-evolver/references/runtime.json` | Publish the exact `0.1.3` runtime version. |
| `.codex-plugin/plugin.json` | Publish plugin version `0.1.3`. |
| `README.md` | State the supported Codex rollout baseline and fail-closed compatibility policy. |

## Task 1: Lock the Codex 0.146 transcript contract with failing tests

**Files:**

- Modify: `skills/skill-evolver/tests/test_review.py`

**Interfaces:**

- Consumes: `FrozenTranscriptTestCase.capture_and_claim(...)`
- Consumes: `read_frozen_transcript(...)`
- Consumes: `transcript_adapter_contract(...)`
- Produces no new production interface.

- [ ] **Step 1: Add a JSONL response-item helper**

Add this shared helper to `FrozenTranscriptTestCase`:

```python
def response_item(self, payload: dict[str, object]) -> bytes:
    return (
        json.dumps(
            {"type": "response_item", "payload": payload},
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
```

- [ ] **Step 2: Add a production-shaped textual tool session test**

Add the success test to `FrozenTranscriptLayoutTests`. Use
`owner_token = "a" * 64` and build one transcript containing, in order:

```python
records = [
    self.response_item(
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "inspect status"}],
        }
    ),
    self.response_item(
        {
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": "call-1",
            "name": "exec",
            "input": "{}",
        }
    ),
    self.response_item(
        {
            "type": "custom_tool_call_output",
            "call_id": "call-1",
            "output": [
                {
                    "type": "input_text",
                    "text": (
                        f"owner_token={owner_token} "
                        "pending_sessions=2"
                    ),
                },
                {
                    "type": "encrypted_content",
                    "encrypted_content": "opaque",
                },
            ],
        }
    ),
    self.response_item(
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "status checked"}],
        }
    ),
]
```

Freeze the header as context and all four response items as the evidence
delta. Assert the exported `(source_kind, text)` sequence is exactly:

```python
[
    ("user_direct", "inspect status"),
    (
        "tool_output",
        "owner_token=[REDACTED:owner-token] pending_sessions=2",
    ),
    ("assistant", "status checked"),
]
```

Also assert the raw `owner_token` does not occur in any exported record.

- [ ] **Step 3: Lock the exact ignored 0.146 item vocabulary**

In `FrozenTranscriptLayoutTests`, put each item type below between one user
and one assistant message and assert only those two message records are
exported:

```python
(
    "additional_tools",
    "agent_message",
    "reasoning",
    "function_call",
    "custom_tool_call",
    "local_shell_call",
    "tool_search_call",
    "tool_search_output",
    "web_search_call",
    "compaction",
    "context_compaction",
    "compaction_trigger",
)
```

Use a minimal payload of `{"type": item_type}`. These tests assert
classification only; they must not attempt to validate control-record fields
that are not evidence.

- [ ] **Step 4: Lock the structured-output failure boundary**

In `FrozenTranscriptFailureTests`, add subtests for each
`custom_tool_call_output.output` value:

```python
(
    [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}],
    [{"type": "input_audio", "audio_url": "data:audio/wav;base64,AA=="}],
    [{"type": "future_content", "text": "unknown"}],
    [{"type": "input_text", "text": 7}],
    [{"type": "encrypted_content", "encrypted_content": 7}],
    {"type": "input_text", "text": "not-a-list"},
)
```

Assert every evidence-eligible shape is terminal
`unsupported_transcript`, with `retryable=False`. Add a separate
`image_generation_call` case and keep the existing unknown-response-item
test unchanged.

- [ ] **Step 5: Lock the public adapter contract**

Extend
`FrozenTranscriptBoundedReadTests.test_transcript_adapter_digest_is_static`
with:

```python
contract = self.runtime.transcript_adapter_contract(self.review)
self.assertEqual(contract["format"], "codex-rollout-jsonl-v2")
self.assertEqual(
    contract["recognized"]["ignore"],
    [
        "agent_message",
        "compacted",
        "event_msg",
        "inter_agent_communication_metadata",
        "response_item/additional_tools",
        "response_item/agent_message",
        "response_item/compaction",
        "response_item/compaction_trigger",
        "response_item/context_compaction",
        "response_item/custom_tool_call",
        "response_item/function_call",
        "response_item/local_shell_call",
        "response_item/reasoning",
        "response_item/tool_search_call",
        "response_item/tool_search_output",
        "response_item/web_search_call",
        "tool_search_call",
        "tool_search_output",
        "turn_context",
        "world_state",
    ],
)
```

Keep the existing export list unchanged because the external source kinds do
not change.

- [ ] **Step 6: Run the focused tests and verify RED**

Run:

```bash
cd /Users/igyeongseob/Documents/오픈소스/skill-evolver
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
```

Expected: the new 0.146 success cases fail with
`unsupported_transcript`, structured-output boundary cases already fail
closed, and the contract-format assertion reports
`current-codex-jsonl-v1`.

- [ ] **Step 7: Commit the red tests**

```bash
git add skills/skill-evolver/tests/test_review.py
git commit -m "test(skill-evolver): cover Codex 0.146 rollouts"
```

## Task 2: Implement the smallest fail-closed adapter repair

**Files:**

- Modify: `skills/skill-evolver/tests/test_review.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`

`test_review.py` remains unchanged for the planned GREEN path. If task review
finds an uncovered compatibility boundary, add its failing regression here
before changing production behavior.

**Interfaces:**

- Extends: `RESPONSE_ITEM_IGNORED_TYPES`
- Produces:
  `_tool_output_texts(value: object) -> list[str]`
- Modifies: `_classify_transcript_object(...)`
- Modifies: `transcript_adapter_contract(...)`

- [ ] **Step 1: Extend only the exact known metadata allowlist**

Replace `RESPONSE_ITEM_IGNORED_TYPES` with:

```python
RESPONSE_ITEM_IGNORED_TYPES = frozenset(
    {
        "additional_tools",
        "agent_message",
        "compaction",
        "compaction_trigger",
        "context_compaction",
        "custom_tool_call",
        "function_call",
        "local_shell_call",
        "reasoning",
        "tool_search_call",
        "tool_search_output",
        "web_search_call",
    }
)
```

Do not add `image_generation_call` or a generic prefix rule.

- [ ] **Step 2: Decode supported textual tool output**

Add beside `_message_texts`:

```python
def _tool_output_texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [_validated_transcript_text(value)]
    if not isinstance(value, list):
        raise _transcript_error("unsupported_transcript")
    texts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise _transcript_error("unsupported_transcript")
        item_type = item.get("type")
        if item_type == "input_text":
            texts.append(
                _validated_transcript_text(item.get("text"))
            )
        elif item_type == "encrypted_content":
            _validated_transcript_text(
                item.get("encrypted_content")
            )
        else:
            raise _transcript_error("unsupported_transcript")
    return texts
```

This intentionally keeps one decoder instead of branching separately for
function and custom-tool output.

- [ ] **Step 3: Reuse the decoder in transcript classification**

In the `function_call_output` / `custom_tool_call_output` branch:

1. Call `_tool_output_texts(payload.get("output"))`.
2. Remove any raw decoded text that is a ready-review-claim output.
3. Apply `_redact_transcript_record_text(...)` to each remaining text.
4. Build one `TranscriptRecord(source_kind="tool_output", ...)` per remaining
   redacted text.
5. Preserve `evidence_eligible`, `scope`, and byte boundaries exactly.

Do not silently ignore an invalid context-only structured output: an
unsupported response item inside the bounded transcript is a compatibility
failure regardless of whether it can become evidence.

- [ ] **Step 4: Publish adapter contract v2**

Change only:

```python
"format": "codex-rollout-jsonl-v2",
```

and the exact sorted `recognized.ignore` list locked in Task 1. Keep schema
version, source kinds, limits, redaction, relocation, and exclusion contracts
unchanged.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
cd /Users/igyeongseob/Documents/오픈소스/skill-evolver
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
```

Expected: all selected tests pass.

- [ ] **Step 6: Run broader review tests**

```bash
cd /Users/igyeongseob/Documents/오픈소스/skill-evolver
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
```

Expected: all review tests pass.

- [ ] **Step 7: Commit the implementation**

```bash
git add skills/skill-evolver/scripts/evolver.py
git commit -m "fix(skill-evolver): support Codex 0.146 rollouts"
```

## Task 3: Publish patch version 0.1.3

**Files:**

- Modify: `skills/skill-evolver/tests/test_capture.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `skills/skill-evolver/references/runtime.json`
- Modify: `.codex-plugin/plugin.json`
- Modify: `README.md`

**Interfaces:**

- Changes release identity only: `0.1.2` → `0.1.3`
- Keeps schema, installation path, plugin-data path, and limits unchanged.

- [ ] **Step 1: Change release assertions first**

Change the exact manifest/runtime assertions in `test_capture.py` to
`0.1.3`. Add or update the CLI version assertion so:

```python
self.assertEqual(
    load_runtime().VERSION,
    "skill-evolver 0.1.3",
)
```

- [ ] **Step 2: Run the release assertions and verify RED**

```bash
cd /Users/igyeongseob/Documents/오픈소스/skill-evolver
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_capture.py' -v
```

Expected: assertions report `0.1.2`.

- [ ] **Step 3: Bump all runtime release identities**

Change:

- `evolver.py`: `VERSION = "skill-evolver 0.1.3"`
- `load_review_runtime()`: exact runtime version `0.1.3`
- `runtime.json`: `"version": "0.1.3"`
- `.codex-plugin/plugin.json`: `"version": "0.1.3"`

Do not change `SCHEMA_VERSION`.

- [ ] **Step 4: Document compatibility without promising stability**

Add a short README compatibility note:

```markdown
Version `0.1.3` recognizes the text and control response items emitted by
Codex `0.146.0`. Codex rollout JSONL is not a stable Hook interface, so
unknown or multimodal response items fail closed as `unsupported_transcript`
until explicitly reviewed.
```

- [ ] **Step 5: Run release and full verification**

```bash
cd /Users/igyeongseob/Documents/오픈소스/skill-evolver
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_capture.py' -v
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_quality_gate.py' -v
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_*.py' -v
/usr/bin/python3 -m py_compile \
  skills/skill-evolver/scripts/evolver.py
/usr/bin/python3 -m json.tool \
  .codex-plugin/plugin.json >/dev/null
/usr/bin/python3 -m json.tool \
  skills/skill-evolver/references/runtime.json >/dev/null
if rg -n \
  '"(raw_session_id|transcript_path|owner_token|session_key)"\s*:' \
  docs/release-reports; then
  exit 1
fi
git diff --check
```

Expected baseline: at least 466 passing tests, the existing three
environment-dependent skips only, successful compilation, valid JSON, no
private runtime keys in committed release reports, and no whitespace errors.

- [ ] **Step 6: Commit the release**

```bash
git add \
  .codex-plugin/plugin.json \
  README.md \
  skills/skill-evolver/references/runtime.json \
  skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_capture.py
git commit -m "chore(skill-evolver): release transcript adapter 0.1.3"
```

## Task 4: Review and install the exact tested source

**Files:**

- Review only: all Task 1–3 paths
- External mutation: Codex plugin cache and registry through
  `codex plugin add`

- [ ] **Step 1: Run independent specification and privacy reviews**

Dispatch two read-only reviews:

1. Compare the diff with
   `docs/superpowers/specs/2026-07-31-skill-evolver-codex-0146-transcript-adapter-design.md`.
2. Audit transcript bounds, unknown-item fail-closed behavior, redaction,
   ready-claim exclusion, and encrypted/multimodal handling.

Apply only verified findings, using a failing regression test first for every
behavioral correction.

- [ ] **Step 2: Re-run the full verification after review**

Run the exact Task 3 Step 5 commands. Record the passing counts and current
source commit.

- [ ] **Step 3: Install the local marketplace release**

Request approval for this exact external mutation:

```bash
codex plugin add skill-evolver@skill-evolver-dev --json
```

Expected: installed plugin version `0.1.3`.

- [ ] **Step 4: Verify installed identity and source equality**

Run:

```bash
codex plugin list --json
shasum -a 256 \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.3/skills/skill-evolver/scripts/evolver.py
```

Expected: both script digests are identical and the plugin list reports
`0.1.3`.

- [ ] **Step 5: Confirm the installed runtime is read-only healthy**

Run:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.3/skills/skill-evolver/scripts/evolver.py \
  status \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --plugin-data /Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev
```

Expected: `binding_failures=0`, two preserved verified spool files, and no
canonical pending session before explicit maintenance.

## Task 5: Roll quality provenance and prove the repaired Desktop path

**Files:**

- External runtime state:
  `/Users/igyeongseob/.codex/skill-evolver/evolver.db`
- External ingress:
  `/Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev/stop-spool`
- Modify after evidence:
  `.planning/STATE.md`
- Modify after evidence:
  `.planning/ROADMAP.md`

**Interfaces:**

- Consumes separately approved `quality-gate`, `quality-open`, `maintain`,
  `review-claim`, and `review-commit` lifecycle commands.
- Produces a fresh `Q-003` cohort under adapter contract
  `codex-rollout-jsonl-v2`.

- [ ] **Step 1: Terminalize stale Q-002**

After `0.1.3` is installed, request separate exact approval for:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.3/skills/skill-evolver/scripts/evolver.py \
  quality-gate \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  Q-002
```

Expected: stdout contains terminal `decision=INVALID` because its stored
provenance predates the installed adapter. Exit code `2` is the documented
normal result for this terminal non-PASS decision; do not retry it as a
technical failure. Retain the exact returned body and `report_digest`.

- [ ] **Step 2: Open Q-003 from the exact predecessor**

Construct the `quality-open` command only after Step 1 returns its immutable
predecessor body and digest. Present the fully literal command for a separate
approval; do not use shell substitution, an environment variable, a guessed
digest, or a placeholder. Assert the returned epoch is `Q-003` and records
the exact `Q-002` predecessor digest.

- [ ] **Step 3: Import the preserved observations under 0.1.3**

Request separate exact approval for:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.3/skills/skill-evolver/scripts/evolver.py \
  maintain \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --plugin-data /Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev
```

Expected: the two verified spool files import once and the ingress becomes
empty.

- [ ] **Step 4: Review the imported observations**

For each bounded batch:

1. Request exact approval for `review-claim`.
2. Read only the produced model export.
3. Generate exactly one strict JSON decision per session, with at most one
   reusable skill-improvement candidate per session. Exclude environment-only,
   one-off, injected, or unsupported evidence.
4. Write only that strict JSON to the exact bound `result_path`; do not choose
   another file or persist the owner token anywhere else.
5. Construct `review-commit` with only the exact installation, decimal
   `batch_id`, raw `owner_token`, and exact `result_path`. Request separate
   approval for that fully literal command and run it once.
6. Verify with read-only `status` and `quality-status`.

Expected: the sessions are no longer excluded as
`unsupported_transcript`. These pre-install captures are compatibility smoke
evidence, not the required fresh post-install proof.

- [ ] **Step 5: Hard checkpoint for a fresh Desktop session**

Ask the user to run one meaningful, direct Desktop task after `0.1.3` is
installed and `Q-003` is open. The task must use at least one tool and finish
normally so the real Stop Hook records it. Do not synthesize, copy, or relabel
a CLI, fixture, subagent, or pre-install observation as this proof.

- [ ] **Step 6: Prove the fresh session end to end**

After the user confirms completion:

1. Read-only `status` must show a new verified spool file.
2. Separately approve and run `maintain`.
3. Separately approve and run `review-claim`.
4. The claim must be ready, not `no_exportable_sessions`.
5. Separately approve and run `review-commit`.
6. Read-only `quality-status` must show at least one distinct `Q-003`
   session.

If any step fails, stop at the first failing boundary and diagnose it without
opening Phase 6.

- [ ] **Step 7: Record evidence without claiming Phase 5 PASS**

Update `STATE.md` and `ROADMAP.md` with the installed version, adapter
contract, source commit, fresh Desktop proof, and the remaining Phase 5
cohort counts. Commit only those two paths:

```bash
git add .planning/STATE.md .planning/ROADMAP.md
git commit -m "docs(skill-evolver): record transcript adapter proof"
```

Phase 5 remains in progress until ten distinct real sessions, at least one
candidate, sealing, all external-TTY user labels, and the content-addressed
quality report produce a real PASS.

---

## Final Verification Checklist

- [ ] New tests fail before implementation and pass afterward.
- [ ] Exact Codex `0.146.0` metadata items are ignored; unknown items are not.
- [ ] String and supported structured tool text export through existing
  redaction.
- [ ] Encrypted content is omitted only after shape validation.
- [ ] Image, audio, image-generation, malformed, and unknown content fail
  closed.
- [ ] Adapter contract is `codex-rollout-jsonl-v2`.
- [ ] Plugin/runtime/source release identities all equal `0.1.3`.
- [ ] Full suite and compilation pass after independent review.
- [ ] Installed script digest equals tested source.
- [ ] Q-002 is terminal and Q-003 binds to its exact digest.
- [ ] A fresh post-install Desktop tool-using session reaches a committed
  Q-003 review observation.
- [ ] Phase 6 remains closed until the complete Phase 5 quality gate passes.
