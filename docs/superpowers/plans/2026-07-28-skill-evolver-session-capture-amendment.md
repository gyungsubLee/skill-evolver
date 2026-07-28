# Skill Evolver Session Capture Amendment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the failed turn-level feasibility assumptions with a session-level, global-inbox, asymmetric-access probe and prove the amended contract on real Codex CLI and Desktop sessions.

**Architecture:** Preserve every schema-v1 fixture and report as immutable evidence, then add schema-v2 Stop, access, transcript, and gate paths beside the existing probe. The Hook writes a fixed global private root, default skill execution proves read access and write denial, and an explicitly approved command proves scoped write access. Transcript inspection freezes a session prefix, binds it by file identity or an embedded session identifier, and never searches for a contiguous turn span.

**Tech Stack:** macOS, Codex CLI and Desktop, `/usr/bin/python3` 3.9+, Python standard library (`argparse`, `dataclasses`, `hashlib`, `hmac`, `json`, `os`, `pathlib`, `secrets`, `stat`, `tempfile`, `time`, `unittest`), Codex plugin manifest and matcher-free `Stop` command Hook.

## Global Constraints

- Source specification: `docs/superpowers/specs/2026-07-28-skill-evolver-session-capture-amendment-design.md`.
- Supported real surfaces are macOS Codex CLI and Desktop only.
- Runtime interpreter remains exactly `/usr/bin/python3`; runtime dependencies remain standard-library only.
- `evolver.py` stays self-contained because Python `-I` excludes sibling modules.
- The schema-v1 six fixtures and `docs/feasibility-report.{json,md}` are immutable.
- Schema-v2 uses a separate private root: `/Users/igyeongseob/.codex/skill-evolver-feasibility-v2`.
- The Hook remains silent, network-free, bounded to 64 KiB stdin, fail-open, and exit `0`.
- `turn_id` is optional diagnostic shape only; it is never a v2 identity or boundary.
- The v2 probe captures two distinct real sessions per surface.
- Default skill execution must read the global root and must not write it.
- Explicit scoped execution must read and write the exact v2 root without granting permanent access.
- Committed fixtures contain structural booleans, allowlisted error codes, counts, and sanitized pointer paths only.
- Phase 2 does not implement SQLite, model review, candidates, evaluation, apply, or undo.
- Phase 3 remains blocked until both v2 report files say `PASS`.
- Stage exact `skill-evolver` paths only; never stage the unrelated `n8n/` or `neo4j/` directories.
- Run test and probe commands from `/Users/igyeongseob/Documents/오픈소스/skill-evolver`; run the shown Git staging commands from `/Users/igyeongseob/Documents/오픈소스`.

## File Structure

| Path | Responsibility |
| --- | --- |
| `skills/skill-evolver/scripts/evolver.py` | Add schema-v2 capture, access, transcript binding, promotion, gate, and CLI commands while retaining v1 commands. |
| `skills/skill-evolver/tests/test_session_stop_v2.py` | Session Stop capture, optional turn, distinct-session promotion, privacy, and fail-open tests. |
| `skills/skill-evolver/tests/test_access_probe_v2.py` | Default read/write-denial and explicit scoped read/write access tests. |
| `skills/skill-evolver/tests/test_session_transcript_v2.py` | Frozen-prefix, identity relocation, epoch reset, provenance, and fail-closed tests. |
| `skills/skill-evolver/tests/test_gate_v2.py` | Exact schema-v2 fixture inventory, validation, predecessor digest, privacy, and PASS/FAIL tests. |
| `skills/skill-evolver/tests/fixtures/synthetic-session-v2.jsonl` | Synthetic session transcript containing stable session identity and user/assistant provenance. |
| `skills/skill-evolver/tests/fixtures/*.v2.structure.json` | Six real sanitized schema-v2 surface fixtures. |
| `.codex-plugin/plugin.json` | Bump the feasibility plugin to `0.0.2`. |
| `hooks/hooks.json` | Point `Stop` at the v2 capture command and separate v2 root. |
| `skills/skill-evolver/references/runtime.json` | Pin the v2 installation locator. |
| `skills/skill-evolver/SKILL.md` | Document default-read and explicitly approved write probe actions. |
| `README.md` | Record exact v2 initialization, installation, capture, gate, and cleanup runbook. |
| `docs/feasibility-report-v2.json` | Deterministic machine-readable amended decision and v1 predecessor digest. |
| `docs/feasibility-report-v2.md` | Human-readable amended decision. |
| `docs/superpowers/plans/2026-07-28-skill-evolver-session-runtime-queue.md` | Replacement Phase 3 plan using session upsert and cursor semantics. |
| `.planning/phases/02-session-level-capture-design-amendment/*` | Historical Phase 2 plan, summary, and verification evidence after PASS. |

---

### Task 1: Add Schema-v2 Session Stop Capture

**Files:**
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Create: `skills/skill-evolver/tests/test_session_stop_v2.py`

**Interfaces:**
- Consumes: existing `Installation`, `read_bounded_stdin`, `bounded_text`, `stat_transcript`, `atomic_write_json`, and private-root validators.
- Produces: `parse_session_stop_envelope(raw: bytes, installation: Installation) -> SessionStopEnvelope`, `capture_session_stop(installation: Installation, raw: bytes) -> Path`, `promote_session_stop_v2(installation: Installation, surface: str, output: Path) -> dict[str, object]`, and `probe-v2-stop`.

- [ ] **Step 1: Write failing optional-turn and metadata-only capture tests**

Create `test_session_stop_v2.py` with a temporary installation, one synthetic
transcript, and this core test:

```python
def test_v2_capture_accepts_missing_turn_and_stores_no_transcript_body(self) -> None:
    payload = {
        "hook_event_name": "Stop",
        "session_id": "session-secret",
        "transcript_path": str(self.transcript),
        "cwd": str(self.root),
    }
    observation = self.runtime.capture_session_stop(
        self.installation, json.dumps(payload).encode()
    )
    stored = json.loads(observation.read_text(encoding="utf-8"))
    self.assertEqual(stored["schema_version"], 2)
    self.assertEqual(stored["event"]["session_id"], "session-secret")
    self.assertIsNone(stored["event"]["turn_id"])
    self.assertNotIn("transcript-body-secret", observation.read_text())
    self.assertEqual(stat.S_IMODE(observation.stat().st_mode), 0o600)
```

Add tests that `turn_id` may be a bounded string when present, while missing
`session_id`, missing `transcript_path`, `SubagentStop`, a FIFO, a symlink, an
outside-root transcript, oversized stdin, and wrong-owner files produce only an
allowlisted sanitized error and never block the Hook command.

- [ ] **Step 2: Run the focused tests and verify the missing interfaces**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_session_stop_v2.py' -v
```

Expected: FAIL because `capture_session_stop` and the v2 command do not exist.

- [ ] **Step 3: Implement the v2 envelope and capture path**

Add an independent v2 required-field set and envelope so v1 behavior remains
unchanged:

```python
REQUIRED_SESSION_HOOK_FIELDS = {
    "hook_event_name": str,
    "session_id": str,
    "cwd": str,
}

@dataclass(frozen=True)
class SessionStopEnvelope:
    session_id: str
    turn_id: Optional[str]
    transcript_path: Path
    cwd: Path
    shape: dict[str, object]
```

`parse_session_stop_envelope` must require `Stop`, `session_id`, `cwd`, and
`transcript_path`; validate optional `turn_id` only when present; canonicalize
the paths; and require the transcript under a fixed transcript root.

`capture_session_stop` writes schema `2` observations beneath `incoming-v2/`.
The event contains only the four bounded metadata fields and optional turn. It
records the same stat fields as v1 and uses a separate v2 error allowlist.

Add a `cmd_probe_v2_stop` wrapper that catches every exception, writes no output,
and returns `0`.

- [ ] **Step 4: Add distinct-session promotion tests**

Add a test that marks a v2 surface boundary, captures two observations with
different `session_id` values, and asserts:

```python
report = self.runtime.promote_session_stop_v2(
    self.installation, "cli", self.root / "session-stop-cli.v2.structure.json"
)
self.assertEqual(report["schema_version"], 2)
self.assertEqual(report["observation_count"], 2)
self.assertTrue(report["distinct_sessions"])
self.assertTrue(report["capture_supported"])
self.assertTrue(report["turn_id_optional"])
self.assertNotIn("session-secret", json.dumps(report))
```

Add the inverse test: two Stops with the same session ID must produce
`distinct_sessions: false` and fail promotion.

- [ ] **Step 5: Implement v2 boundary and session promotion**

Add these exact interfaces:

- `mark_session_surface_boundary(installation: Installation, surface: str) -> dict[str, object]`
- `session_surface_observations(installation: Installation, surface: str) -> list[Path]`
- `promote_session_stop_v2(installation: Installation, surface: str, output: Path) -> dict[str, object]`

Promotion must accept exactly two post-marker schema-v2 observations with the
current installation nonce, stable payload shape, distinct raw session IDs,
valid transcript stat structure, and no capture error. The committed fixture
must contain only field names/types, booleans, counts, and allowlisted errors.

- [ ] **Step 6: Wire the parser and run v1 plus v2 Stop suites**

Add `probe-v2-stop`, `probe-v2-mark-surface`, and
`probe-v2-promote-stop` subcommands with exact installation, surface, and output
arguments.

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_stop_probe.py' -v
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_session_stop_v2.py' -v
```

Expected: all existing v1 and new v2 Stop tests pass.

- [ ] **Step 7: Commit the session Stop slice**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_session_stop_v2.py
git commit -m "feat(skill-evolver): probe session stop metadata"
```

---

### Task 2: Separate Default and Explicit Access Evidence

**Files:**
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Create: `skills/skill-evolver/tests/test_access_probe_v2.py`

**Interfaces:**
- Consumes: `Installation`, `atomic_write_json`, `validate_surface`, and v2 Stop observations.
- Produces: `arm_access_v2`, `load_access_challenge_v2`, `run_default_access_v2`, `run_explicit_access_v2`, `promote_access_v2`, and four `probe-v2-*access` commands.

- [ ] **Step 1: Write failing asymmetric access tests**

Use a temporary private installation and assert the exact sanitized response
shape:

```python
def test_default_access_reads_challenge_and_records_write_denial(self) -> None:
    self.runtime.arm_access_v2(self.installation, "cli")
    original_write = self.runtime.atomic_write_json

    def deny_global(path, payload, mode=0o600):
        if path.parent == self.installation.data_root / "reports":
            raise PermissionError("denied")
        return original_write(path, payload, mode)

    with mock.patch.object(
        self.runtime,
        "atomic_write_json",
        side_effect=deny_global,
    ):
        result = self.runtime.run_default_access_v2(
            self.installation, "cli", self.root / "default.json"
        )
    self.assertEqual(
        result,
        {
            "schema_version": 2,
            "surface": "cli",
            "challenge_read": True,
            "global_write": False,
            "write_denied": True,
            "challenge_digest": mock.ANY,
            "error_codes": [],
        },
    )
```

Add tests for:

- read failure reported separately from write failure;
- explicit mode writes and round-trips a current challenge;
- rearming invalidates every prior response;
- surface, schema, nonce, challenge, and digest mismatch fail closed;
- error output never includes a path, raw challenge, nonce, or exception text.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_access_probe_v2.py' -v
```

Expected: FAIL because v2 access functions are missing.

- [ ] **Step 3: Implement challenge and default-read/write-denial evidence**

Add SHA-256 challenge digests and a local response writer:

```python
def challenge_digest(challenge: str) -> str:
    return hashlib.sha256(challenge.encode("ascii")).hexdigest()

def run_default_access_v2(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    challenge = load_access_challenge_v2(installation, surface)
    digest = challenge_digest(str(challenge["challenge"]))
    result = {
        "schema_version": 2,
        "surface": surface,
        "challenge_read": True,
        "global_write": False,
        "write_denied": False,
        "challenge_digest": digest,
        "error_codes": [],
    }
    try:
        atomic_write_json(
            installation.data_root
            / "reports"
            / f"{surface}-v2-default-response.json",
            {"schema_version": 2, "surface": surface, "challenge_digest": digest},
        )
    except PermissionError:
        result["write_denied"] = True
    atomic_write_json(resolve_report_output(output), result)
    return result
```

Do not collapse read and write into one boolean. Catch only expected filesystem
errors and map them to fixed codes.

- [ ] **Step 4: Implement explicit access and deterministic promotion**

`run_explicit_access_v2` must validate the same challenge and write a private
response under `reports/`. `promote_access_v2` consumes the default sanitized
response, explicit global response, and two successful v2 Stop observations.
It writes exactly:

```json
{
  "schema_version": 2,
  "surface": "cli",
  "hook_global_read": true,
  "hook_global_write": true,
  "skill_default_read": true,
  "skill_default_write": false,
  "skill_default_write_denied": true,
  "skill_explicit_read": true,
  "skill_explicit_write": true,
  "error_codes": []
}
```

The `surface` value varies; the field inventory does not.

- [ ] **Step 5: Wire access commands and verify parser behavior**

Add:

```text
probe-v2-arm-access
probe-v2-default-access
probe-v2-explicit-access
probe-v2-promote-access
```

`probe-v2-default-access` accepts `--output` in a writable workspace or a
caller-created mode-`0700` temporary directory. It must not write directly into
the world-writable `/private/tmp` parent. `probe-v2-explicit-access` writes the
global response and is the only access command intended for scoped approval.
Promotion accepts the default-response path and committed fixture output.

Run the access suite and parser assertions. Expected: all pass.

- [ ] **Step 6: Commit the access slice**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_access_probe_v2.py
git commit -m "feat(skill-evolver): verify asymmetric root access"
```

---

### Task 3: Inspect Bounded Session Transcripts

**Files:**
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Create: `skills/skill-evolver/tests/test_session_transcript_v2.py`
- Create: `skills/skill-evolver/tests/fixtures/synthetic-session-v2.jsonl`

**Interfaces:**
- Consumes: schema-v2 observations and existing exact-prefix, pointer-redaction, path, owner, and permission validators.
- Produces: `resolve_session_transcript`, `discover_session_structure`, `inspect_session_structure_v2`, and `promote_session_transcript_v2`.

- [ ] **Step 1: Create a synthetic session fixture**

Create five complete JSONL records containing:

```json
{"payload":{"session_id":"session-one","role":"user","text":"private prompt"}}
{"payload":{"session_id":"session-one","role":"tool","text":"private output"}}
{"payload":{"session_id":"session-one","role":"assistant","text":"private answer"}}
{"payload":{"role":"internal","text":"record without a turn id"}}
{"payload":{"session_id":"session-one","role":"assistant","text":"later suffix"}}
```

Tests may read the text, but promoted output must never contain `session-one`,
prompt, output, answer, or absolute paths.

- [ ] **Step 2: Write failing frozen-prefix and no-turn tests**

Capture the first four records, append the fifth, and assert:

```python
report = self.runtime.inspect_session_structure_v2(
    self.observation, "cli"
)
self.assertTrue(report["supported"])
self.assertEqual(report["format"], "jsonl")
self.assertEqual(report["binding_mode"], "same_file_identity")
self.assertFalse(report["read_past_boundary"])
self.assertTrue(report["suffix_ignored"])
self.assertEqual(report["provenance_values"], ["assistant", "tool", "user"])
self.assertNotIn("turn", json.dumps(report))
```

Add tests for noncontiguous or absent turn IDs, partial records, oversized
prefixes, blank lines, changed size during read, symlinks, outside-root paths,
and privacy-safe failure output.

- [ ] **Step 3: Write failing relocation and epoch tests**

Add three cases:

1. same-inode rename beneath an allowed transcript root resolves without an
   epoch reset;
2. different-inode replacement with matching embedded session ID resolves as
   `embedded_session_id` with `epoch_reset: true`;
3. different-inode replacement without a matching embedded session ID fails as
   `session_binding_unavailable`.

The lookup must cap visited filesystem entries at an exact constant:

```python
MAX_TRANSCRIPT_LOOKUP_ENTRIES = 4096
```

- [ ] **Step 4: Run the session transcript suite and verify failure**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_session_transcript_v2.py' -v
```

Expected: FAIL because the session resolver and inspector do not exist.

- [ ] **Step 5: Implement bounded resolution and structure discovery**

Add the data type:

```python
@dataclass(frozen=True)
class ResolvedSessionTranscript:
    path: Path
    binding_mode: str
    epoch_reset: bool
```

Add these exact interfaces:

- `resolve_session_transcript(observation: dict[str, object], installation: Installation, max_entries: int = MAX_TRANSCRIPT_LOOKUP_ENTRIES) -> ResolvedSessionTranscript`
- `discover_session_structure(records: list[object], session_id: str, require_embedded_binding: bool, layout_identity: Optional[dict[str, object]] = None) -> dict[str, object]`

Resolution order is original path with same identity, bounded same-inode lookup
under fixed roots, then original-path different-inode acceptance only after an
embedded session-ID match. It never recursively reads unrelated transcript
contents to search for a session.

Allowlist the pointer segment `session_id`. Record only sanitized pointer paths,
binding mode, provenance values, counts, and booleans.

- [ ] **Step 6: Implement surface promotion**

`promote_session_transcript_v2` loads exactly the two v2 observations selected
for the surface, inspects each frozen prefix, and requires:

- two supported observations from distinct sessions;
- stable provenance and embedded-session pointer layouts when present;
- binding mode from the allowlisted enum;
- user and assistant provenance;
- no boundary overread;
- no turn-span field or condition.

It emits `session-transcript-<surface>.v2.structure.json`.

- [ ] **Step 7: Run v1 and v2 transcript suites**

```bash
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_transcript_probe.py' -v
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_session_transcript_v2.py' -v
```

Expected: all v1 and v2 transcript tests pass.

- [ ] **Step 8: Commit the transcript slice**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_session_transcript_v2.py \
  skill-evolver/skills/skill-evolver/tests/fixtures/synthetic-session-v2.jsonl
git commit -m "feat(skill-evolver): inspect bounded sessions"
```

---

### Task 4: Add the Immutable Schema-v2 Gate

**Files:**
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Create: `skills/skill-evolver/tests/test_gate_v2.py`

**Interfaces:**
- Consumes: six exact schema-v2 fixture validators and `docs/feasibility-report.json`.
- Produces: `evaluate_feasibility_gate_v2`, `write_gate_report_v2`, `render_gate_markdown_v2`, and `probe-v2-gate`.

- [ ] **Step 1: Write valid fixture builders and a failing PASS test**

`test_gate_v2.py` must build these exact files:

```python
V2_FIXTURE_NAMES = {
    "session-stop-cli.v2.structure.json",
    "session-stop-desktop.v2.structure.json",
    "session-transcript-cli.v2.structure.json",
    "session-transcript-desktop.v2.structure.json",
    "access-cli.v2.structure.json",
    "access-desktop.v2.structure.json",
}
```

Assert all surface checks pass and the report contains:

```python
self.assertEqual(report["schema_version"], 2)
self.assertEqual(report["decision"], "PASS")
self.assertEqual(
    report["predecessor"]["path"], "docs/feasibility-report.json"
)
self.assertEqual(len(report["predecessor"]["sha256"]), 64)
self.assertTrue(all(report["checks"].values()))
```

- [ ] **Step 2: Add fail-closed inventory, privacy, and output tests**

Cover:

- missing, seventh, duplicate, mislabeled, non-object, malformed, symlinked,
  non-private, oversized, and changed fixtures;
- boolean-as-integer aliases;
- default skill write unexpectedly succeeding;
- missing explicit write;
- unsupported session binding;
- unstable provenance;
- predecessor digest mismatch;
- output alias with a fixture or with the predecessor report;
- exception text containing secrets or paths never reaching the report.

Both output files must be produced for a valid fixture inventory even when the
decision is `FAIL`; invalid or unsafe fixture inventory exits nonzero without
leaking fixture values.

- [ ] **Step 3: Run the gate suite and verify missing v2 functions**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_gate_v2.py' -v
```

Expected: FAIL because schema-v2 gate interfaces are missing.

- [ ] **Step 4: Implement exact validators and decision checks**

The v2 checks are:

```python
checks = {
    "cli_session_stop_contract": session_stop_fixture_v2_passes(cli_stop, "cli"),
    "desktop_session_stop_contract": session_stop_fixture_v2_passes(desktop_stop, "desktop"),
    "cli_asymmetric_access": access_fixture_v2_passes(cli_access, "cli"),
    "desktop_asymmetric_access": access_fixture_v2_passes(desktop_access, "desktop"),
    "cli_session_transcript_supported": session_transcript_fixture_v2_passes(cli_transcript, "cli"),
    "desktop_session_transcript_supported": session_transcript_fixture_v2_passes(desktop_transcript, "desktop"),
}
```

Decision is `PASS` only when every value is exactly `True`. Surface summaries
contain only allowlisted stop keys, provenance/session pointer paths, and
binding modes.

- [ ] **Step 5: Implement predecessor-bound report writing**

`write_gate_report_v2` accepts:

```text
--fixture-root
--predecessor-json
--output-json
--output-markdown
```

It resolves and validates all paths, computes the predecessor SHA-256 from an
exact bounded read, refuses aliases, and writes `feasibility-report-v2.json`
plus Markdown atomically. It never edits the predecessor.

- [ ] **Step 6: Run both gate suites**

```bash
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_gate.py' -v
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_gate_v2.py' -v
```

Expected: all schema-v1 and schema-v2 gate tests pass.

- [ ] **Step 7: Commit the gate slice**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_gate_v2.py
git commit -m "feat(skill-evolver): gate session feasibility"
```

---

### Task 5: Wire the v2 Plugin and Runbook

**Files:**
- Modify: `.codex-plugin/plugin.json`
- Modify: `hooks/hooks.json`
- Modify: `skills/skill-evolver/references/runtime.json`
- Modify: `skills/skill-evolver/SKILL.md`
- Modify: `skills/skill-evolver/tests/test_skeleton.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: all v2 commands from Tasks 1–4.
- Produces: installed plugin version `0.0.2` and exact operator instructions.

- [ ] **Step 1: Update the failing skeleton expectations**

Require:

```python
self.assertEqual(manifest["version"], "0.0.2")
self.assertIn("probe-v2-stop", group["hooks"][0]["command"])
self.assertIn(
    "/Users/igyeongseob/.codex/skill-evolver-feasibility-v2/installation.json",
    group["hooks"][0]["command"],
)
self.assertEqual(
    run_isolated("--version").stdout.decode().strip(),
    "skill-evolver feasibility 0.0.2",
)
```

Also assert the skill names only explicit v2 probe actions and never claims
default write access.

- [ ] **Step 2: Run skeleton tests and verify the old metadata fails**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_skeleton.py' -v
```

Expected: FAIL on version, Hook command, runtime locator, and skill text.

- [ ] **Step 3: Update plugin metadata and Hook**

Bump the manifest and runtime version to `0.0.2`. Change only the Hook command:

```text
/usr/bin/python3 -I "$PLUGIN_ROOT/skills/skill-evolver/scripts/evolver.py" probe-v2-stop --installation "/Users/igyeongseob/.codex/skill-evolver-feasibility-v2/installation.json"
```

Keep one matcher-free `Stop`, timeout `2`, and no `SubagentStop`.

- [ ] **Step 4: Update the explicit-only skill**

Document these actions:

- status/list do not open transcripts;
- default access preflight runs without elevation and writes its sanitized
  response only to an exact workspace or caller-created private temporary
  directory;
- explicit access preflight is the only step that requests exact-command
  approval for the v2 root;
- transcript promotion reads only the frozen prefixes selected by the operator;
- v2 gate writes only the v2 reports;
- cleanup still requires a TTY and exact confirmation.

Retain “only when the user explicitly names `$skill-evolver`” and “Never invoke
after an ordinary task.”

- [ ] **Step 5: Replace the README runbook**

Record exact commands for:

1. initialize `/Users/igyeongseob/.codex/skill-evolver-feasibility-v2`;
2. install the local marketplace plugin;
3. arm default and explicit access per surface;
4. mark each surface before two independent sessions;
5. promote Stop, access, and transcript fixtures;
6. run the v2 gate with the v1 predecessor;
7. remove the plugin before raw cleanup.

State explicitly that the production Runtime Queue is not installed by this
probe.

- [ ] **Step 6: Run the entire deterministic suite**

```bash
/usr/bin/python3 -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all original 87 tests plus every new v2 test pass.

- [ ] **Step 7: Commit plugin wiring**

```bash
git add \
  skill-evolver/.codex-plugin/plugin.json \
  skill-evolver/hooks/hooks.json \
  skill-evolver/skills/skill-evolver/references/runtime.json \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/skills/skill-evolver/tests/test_skeleton.py \
  skill-evolver/README.md
git commit -m "docs(skill-evolver): wire session probe runbook"
```

---

### Task 6: Run Real CLI and Desktop Session Probes

**Files:**
- Create: six `skills/skill-evolver/tests/fixtures/*.v2.structure.json`
- Create: `docs/feasibility-report-v2.json`
- Create: `docs/feasibility-report-v2.md`

**Interfaces:**
- Consumes: installed plugin `0.0.2`, v2 private installation, two independent sessions per surface.
- Produces: authoritative schema-v2 cross-surface decision.

- [ ] **Step 1: Initialize and install from a user-controlled terminal**

Run the exact README commands. Verify `probe-status` reports probe version
`0.0.2`, an empty `incoming-v2`, and the canonical v2 root. Install the
marketplace and plugin, then inspect `/hooks` before trusting the command.

Initialize:

```bash
/usr/bin/python3 -I /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py probe-init \
  --data-root /Users/igyeongseob/.codex/skill-evolver-feasibility-v2 \
  --transcript-root /Users/igyeongseob/.codex/sessions \
  --transcript-root /Users/igyeongseob/.codex/archived_sessions
```

The installation locator may remain schema `1`; all new observation, fixture,
and gate artifacts are schema `2`. `probe-status` must report probe version
`0.0.2`, the canonical v2 root, and zero `incoming-v2` observations.

Install:

```bash
codex plugin marketplace add /Users/igyeongseob/Documents/오픈소스 --json
codex plugin add skill-evolver@skill-evolver-dev --json
```

Expected: one enabled matcher-free `Stop` command using `probe-v2-stop`.

- [ ] **Step 2: Capture CLI access evidence**

Arm `cli`, run default access from an ordinary `codex exec` task without
elevation, then run the explicit access command with approval limited to the
exact command and v2 root.

Expected default result:

```json
{"challenge_read":true,"global_write":false,"write_denied":true}
```

Expected explicit result: global read and write both `true`.

- [ ] **Step 3: Capture two independent CLI sessions**

Mark the CLI surface, then run two separate `codex exec` processes from this
workspace. Each process runs `pwd` once and returns a fixed non-sensitive probe
completion phrase. Do not reuse or resume a session.

```bash
codex exec --sandbox workspace-write \
  -C /Users/igyeongseob/Documents/오픈소스/skill-evolver \
  "Use the shell to run pwd once, then reply with only probe-cli-session-one."
codex exec --sandbox workspace-write \
  -C /Users/igyeongseob/Documents/오픈소스/skill-evolver \
  "Use the shell to run pwd once, then reply with only probe-cli-session-two."
```

Promote:

```text
session-stop-cli.v2.structure.json
session-transcript-cli.v2.structure.json
access-cli.v2.structure.json
```

Expected: all three fixtures are schema `2`, sanitized, and surface `cli`.

- [ ] **Step 4: Capture Desktop access evidence**

Arm `desktop`. In a Desktop task, invoke the explicit probe skill default-access
action without elevation and store only the sanitized response. Run the explicit
write action after exact-command approval.

Expected: the same asymmetric matrix as CLI.

- [ ] **Step 5: Capture two independent Desktop sessions**

Mark the Desktop surface, then use two separate new Desktop tasks. In each task,
run `pwd` once and return a fixed non-sensitive probe phrase. Do not fork or
continue one task as both observations.

Promote the three Desktop fixtures. Expected: schema `2`, sanitized, and surface
`desktop`.

- [ ] **Step 6: Run the amended gate**

Run `probe-v2-gate` with the six v2 fixtures, immutable
`docs/feasibility-report.json`, and v2 output paths.

Expected:

```text
exit 0
docs/feasibility-report-v2.json decision == PASS
docs/feasibility-report-v2.md contains Decision: **PASS**
predecessor SHA-256 matches docs/feasibility-report.json
```

If any surface fails, commit no fake PASS. Preserve the sanitized failure report,
fix only the proved contract defect, add a regression test, and rerun the same
surface.

- [ ] **Step 7: Remove the probe and scrub raw observations**

Remove the plugin and marketplace entry before cleanup. Run `probe-scrub` in a
TTY with the exact confirmation. Verify raw observations and ephemeral reports
are gone while committed fixtures and reports remain.

- [ ] **Step 8: Verify and commit real evidence**

Run the full deterministic suite, JSON validation on all six fixtures and both
reports, privacy scans for known raw marker strings and absolute transcript
paths, and `git diff --check`.

Commit exact evidence paths:

```bash
git commit -m "test(skill-evolver): prove session feasibility"
```

---

### Task 7: Replace the Blocked Runtime Queue Plan

**Files:**
- Create: `docs/superpowers/plans/2026-07-28-skill-evolver-session-runtime-queue.md`
- Modify: `docs/superpowers/plans/2026-07-26-skill-evolver-read-only-runtime-queue.md`
- Modify: `docs/superpowers/plans/2026-07-26-skill-evolver-implementation-roadmap.md`

**Interfaces:**
- Consumes: authoritative v2 PASS report and approved amendment spec.
- Produces: the executable Phase 3 plan; the old turn plan becomes explicitly superseded.

- [ ] **Step 1: Add a supersession notice to the old plan**

The first lines must identify the replacement path and state that no task in the
old turn-level plan may be executed.

- [ ] **Step 2: Write the session Runtime Queue plan**

Use `writing-plans` again. The replacement plan must define and test:

- HMAC `session_key`, no required `turn_id`;
- one SQLite row per session;
- `transcript_epoch`, generation, observed, reviewed, and frozen boundaries;
- Stop upsert and Stop-during-review behavior;
- lease recovery without cursor advancement;
- candidate evidence uniqueness by session;
- session-based capacity, retention, and status;
- Hook-only automatic writes;
- read-only status;
- explicit scoped approval for review and maintenance mutations;
- bounded spool and privacy cleanup;
- no model, transcript parsing, network, or skill mutation in the Hook.

- [ ] **Step 3: Update the implementation roadmap**

Replace turn-pointer and symmetric-root interface claims with the exact v2
report fields and point Runtime/Queue to the new plan.

- [ ] **Step 4: Self-review and commit planning changes**

Scan for placeholder tokens, stale required `turn_id`, `200 turns`, symmetric
default write, and execution links to the superseded plan. Run
`git diff --check`.

Commit:

```bash
git commit -m "docs(skill-evolver): plan session runtime queue"
```

---

### Task 8: Record Phase 2 Completion in GSD

**Files:**
- Create: `.planning/phases/02-session-level-capture-design-amendment/02-01-PLAN.md`
- Create: `.planning/phases/02-session-level-capture-design-amendment/02-01-SUMMARY.md`
- Create: `.planning/phases/02-session-level-capture-design-amendment/02-VERIFICATION.md`
- Modify: `.planning/REQUIREMENTS.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/STATE.md`

**Interfaces:**
- Consumes: committed amendment spec, implementation plan, v2 PASS report, full test output, and replacement Runtime Queue plan.
- Produces: `GATE-01` complete and Phase 3 selected as the next phase.

- [ ] **Step 1: Create the historical GSD plan artifact**

Map `02-01` to `GATE-01` and link the Superpowers spec, implementation plan,
commits, six fixtures, v2 reports, and replacement Runtime Queue plan.

- [ ] **Step 2: Write the completion summary**

Record:

- Phase 1 failure assumptions removed;
- global inbox retained;
- asymmetric access proved on CLI and Desktop;
- session binding and bounded-prefix behavior;
- exact test count and command;
- v2 report decision and predecessor digest;
- probe removal and raw cleanup result.

- [ ] **Step 3: Write verification evidence**

Set status `passed` only after fresh commands prove both reports say `PASS`, all
tests pass, fixture privacy checks pass, and the Runtime Queue replacement plan
contains the session contract.

- [ ] **Step 4: Advance GSD state**

Mark `GATE-01` complete, Phase 2 complete, Phase 3 current, and remove the Phase
2 product blocker. Keep later requirements pending.

- [ ] **Step 5: Query GSD and verify transition**

Run:

```bash
node /Users/igyeongseob/.codex/gsd-core/bin/gsd-tools.cjs query init.progress
node /Users/igyeongseob/.codex/gsd-core/bin/gsd-tools.cjs query verification.status \
  .planning/phases/02-session-level-capture-design-amendment
```

Expected: `completed_count: 2`, Phase 2 `complete/passed`, and Phase 3
`runtime-queue` as the next phase.

- [ ] **Step 6: Commit Phase 2 management evidence**

Stage only the exact `.planning` files above and commit:

```bash
git commit -m "docs(skill-evolver): complete phase 2 gate"
```
