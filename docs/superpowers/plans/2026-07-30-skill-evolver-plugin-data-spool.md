# Skill Evolver Plugin-Data Spool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore reliable automatic `Stop` capture by writing a coalescing,
bounded ingress spool under Codex's writable `$PLUGIN_DATA`, then importing it
into the canonical queue only from explicit commands.

**Architecture:** Keep the canonical SQLite database, identity key and review
state under `/Users/igyeongseob/.codex/skill-evolver`. Bind the Hook's
host-provided `$PLUGIN_DATA` argument to the exact path pinned in
`runtime.json`, replace only `Installation.spool`, and reuse the existing
HMAC/import pipeline. The automatic path is spool-only; `maintain` imports both
the primary ingress and any legacy canonical spool before running existing
cleanup.

**Tech Stack:** Python 3 standard library, SQLite, `unittest`, Codex plugin
hooks, JSON.

## Global Constraints

- Add no dependency and keep `SCHEMA_VERSION = 1`.
- The Hook reads at most 65,536 input bytes, emits no output, invokes no model
  or network, and exits `0`.
- Use only the dedicated
  `/Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev/stop-spool`
  child for automatic capture.
- Reject an unpinned, relative, noncanonical, symlinked, wrong-owner or
  non-private plugin-data path before scanning, writing or deleting.
- Keep the spool limits at 200 distinct session files or 10,485,760 bytes.
- A repeated Stop for one session must not consume another file-capacity slot;
  an older delivery must not replace a newer delivery.
- Status is read-only and does not add spool-file counts to
  `pending_sessions`.
- Only explicit maintenance imports ingress into the canonical SQLite queue.
- Never reconstruct the missed `0.1.0` session or count fixtures as quality
  evidence.
- Any `evolver.py` change invalidates empty `Q-001` by provenance; after
  rollout, terminalize it and open `Q-002` from the exact predecessor digest.
- Never invoke `quality-label`; only the user may run it in an external
  terminal.
- Stage exact paths only; never use `git add .`.

---

## File Map

| File | Responsibility |
| --- | --- |
| `skills/skill-evolver/scripts/evolver.py` | Pin and validate plugin data, coalesce Hook capture, import ingress, and report its status. |
| `skills/skill-evolver/tests/test_capture.py` | Exercise the public Hook, maintenance and status paths plus filesystem/privacy boundaries. |
| `skills/skill-evolver/tests/test_review.py` | Verify the exact runtime-reference contract includes the pinned plugin-data path. |
| `skills/skill-evolver/references/runtime.json` | Pin the canonical plugin-data locator and release version. |
| `hooks/hooks.json` | Pass quoted `$PLUGIN_DATA` to the trusted Stop command. |
| `.codex-plugin/plugin.json` | Publish patch version `0.1.1`. |
| `skills/skill-evolver/SKILL.md` | Require literal plugin-data arguments and approval for both mutation roots. |
| `README.md` | Document ingress, status, maintenance and activation. |
| `.planning/STATE.md` | Record the corrected capture boundary and quality-epoch rollover. |
| `.planning/ROADMAP.md` | Keep Phase 5 collecting without bypassing its quality gate. |

## Task 1: Pin and validate the plugin-data boundary

**Files:**

- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `skills/skill-evolver/references/runtime.json`
- Modify: `skills/skill-evolver/tests/test_review.py`
- Modify: `skills/skill-evolver/tests/test_capture.py`

**Interfaces:**

- Produces:
  `plugin_spool_installation(installation: Installation,
  plugin_data: Path, *, create: bool) -> Installation`
- Produces: `ReviewRuntime.plugin_data: Path`
- Consumes: existing `private_directory()`, `within()` and
  `dataclasses.replace()`

- [ ] **Step 1: Write failing runtime and path-boundary tests**

Add these assertions to
`ReviewRuntimeContractTests.test_fixed_runtime_reference_and_policy_are_bounded`:

```python
self.assertEqual(
    review.plugin_data,
    Path(
        "/Users/igyeongseob/.codex/plugins/data/"
        "skill-evolver-skill-evolver-dev"
    ),
)
```

Extend `test_static_runtime_reference_rejects_any_changed_limit` with:

```python
(
    "changed_plugin_data",
    ("plugin_data",),
    "/private/tmp/not-the-installed-plugin",
),
```

Add this test to `RuntimeStoreTests`:

```python
def test_plugin_spool_requires_exact_private_nonoverlapping_root(self) -> None:
    installation_path = self.runtime.initialize_runtime(
        self.base / "data", (self.sessions,), self.config
    )
    installation = self.runtime.load_installation(installation_path)
    expected = (
        installation.data_root.parent
        / "plugins/data/skill-evolver-skill-evolver-dev"
    )
    expected.mkdir(mode=0o700, parents=True)
    runtime = replace(
        self.runtime.load_review_runtime(),
        plugin_data=expected,
    )
    with mock.patch.object(
        self.runtime, "load_review_runtime", return_value=runtime
    ):
        bound = self.runtime.plugin_spool_installation(
            installation, expected, create=True
        )
        self.assertEqual(bound.spool, expected / "stop-spool")
        self.assertEqual(stat.S_IMODE(bound.spool.stat().st_mode), 0o700)
        for rejected in (
            Path("relative"),
            installation.data_root,
            self.sessions,
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaisesRegex(
                    ValueError, "invalid_plugin_data"
                ):
                    self.runtime.plugin_spool_installation(
                        installation, rejected, create=False
                    )
```

Add a second boundary test that creates a symlink alias to `expected`, passes
the alias, expects `invalid_plugin_data`, and asserts a sentinel
`unrelated.json` under the alias target remains byte-for-byte unchanged. In
the same test, change the real root to mode `0755`, expect
`invalid_plugin_data`, restore `0700`, replace `stop-spool` with a symlink,
expect `invalid_plugin_data`, and verify the sentinel after every rejection.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd /Users/igyeongseob/Documents/오픈소스/skill-evolver
/usr/bin/python3 skills/skill-evolver/tests/test_review.py \
  ReviewRuntimeContractTests -v
/usr/bin/python3 skills/skill-evolver/tests/test_capture.py \
  RuntimeStoreTests.test_plugin_spool_requires_exact_private_nonoverlapping_root \
  RuntimeStoreTests.test_plugin_spool_rejects_symlink_without_touching_sentinel \
  -v
```

Expected: failures for missing `ReviewRuntime.plugin_data`,
`plugin_spool_installation`, and `runtime.json.plugin_data`.

- [ ] **Step 3: Add the fixed runtime contract**

In `evolver.py`, import `replace` beside `dataclass` and add:

```python
from dataclasses import dataclass, replace

FIXED_PLUGIN_DATA_ROOT = Path(
    "/Users/igyeongseob/.codex/plugins/data/"
    "skill-evolver-skill-evolver-dev"
)
PLUGIN_STOP_SPOOL_NAME = "stop-spool"
```

Add `plugin_data: Path` to `ReviewRuntime`. Change
`load_review_runtime()` so its exact key set also requires `plugin_data`, its
version is `0.1.1`, its value exactly equals
`str(FIXED_PLUGIN_DATA_ROOT)`, and the returned object includes:

```python
return ReviewRuntime(
    plugin_data=FIXED_PLUGIN_DATA_ROOT,
    mutable_skill_roots=FIXED_MUTABLE_SKILL_ROOTS,
    **REVIEW_RUNTIME_FIXED,
)
```

Change `runtime.json` to:

```json
{
  "schema_version": 1,
  "version": "0.1.1",
  "installation": "/Users/igyeongseob/.codex/skill-evolver/installation.json",
  "plugin_data": "/Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev",
  "mutable_skill_roots": [
    "/Users/igyeongseob/.codex/skills"
  ],
  "review_limits": {
    "review_batch_sessions": 5,
    "max_transcript_bytes": 2097152,
    "max_transcript_records": 100,
    "max_review_batch_bytes": 8388608,
    "max_candidates_per_session": 1,
    "max_candidates_per_batch": 3,
    "model_envelope_max_bytes": 131072,
    "catalog_max_skills": 512,
    "catalog_frontmatter_max_bytes": 65536,
    "catalog_inspect_max_bytes": 65536,
    "catalog_export_max_bytes": 49152,
    "catalog_identity_max_bytes": 272,
    "catalog_display_name_max_bytes": 128,
    "catalog_description_max_bytes": 384,
    "policy_max_bytes": 8192,
    "result_schema_instructions_max_bytes": 8192,
    "claim_contract_overhead_max_bytes": 8192
  }
}
```

- [ ] **Step 4: Implement exact plugin-data binding**

Add this function after `load_review_runtime()`:

```python
def plugin_spool_installation(
    installation: Installation,
    plugin_data: Path,
    *,
    create: bool,
) -> Installation:
    if (
        type(installation) is not Installation
        or not isinstance(plugin_data, Path)
        or not plugin_data.is_absolute()
        or plugin_data.is_symlink()
    ):
        raise ValueError("invalid_plugin_data")
    runtime = load_review_runtime()
    expected = (
        installation.data_root.parent
        / "plugins/data/skill-evolver-skill-evolver-dev"
    )
    if (
        plugin_data != runtime.plugin_data
        or plugin_data != expected
        or plugin_data.resolve(strict=False) != plugin_data
        or within(
            plugin_data,
            (installation.data_root, *installation.transcript_roots),
        )
        or within(installation.data_root, (plugin_data,))
        or within(PLUGIN_ROOT, (plugin_data,))
        or within(plugin_data, (PLUGIN_ROOT,))
    ):
        raise ValueError("invalid_plugin_data")
    spool = plugin_data / PLUGIN_STOP_SPOOL_NAME
    if not plugin_data.exists():
        if create:
            raise ValueError("invalid_plugin_data")
        return replace(installation, spool=spool)
    try:
        root = private_directory(plugin_data)
    except (FileNotFoundError, OSError, ValueError):
        raise ValueError("invalid_plugin_data") from None
    if plugin_data != root:
        raise ValueError("invalid_plugin_data")
    if create:
        try:
            spool.mkdir(mode=0o700)
        except FileExistsError:
            pass
    if spool.exists() or spool.is_symlink():
        try:
            spool = private_directory(spool)
        except (OSError, ValueError):
            raise ValueError("invalid_plugin_data") from None
    elif create:
        raise ValueError("invalid_plugin_data")
    return replace(installation, spool=spool)
```

The implementation must not create `plugin_data` or any ancestor; Codex owns
that directory. Only the exact `stop-spool` child may be created.

- [ ] **Step 5: Run the focused and review suites**

Run:

```bash
/usr/bin/python3 skills/skill-evolver/tests/test_review.py \
  ReviewRuntimeContractTests -v
/usr/bin/python3 skills/skill-evolver/tests/test_capture.py \
  RuntimeStoreTests -v
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add \
  skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/references/runtime.json \
  skills/skill-evolver/tests/test_review.py \
  skills/skill-evolver/tests/test_capture.py
git commit -m "feat(skill-evolver): bind plugin data ingress"
```

## Task 2: Make the automatic Hook spool-only and session-coalescing

**Files:**

- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `skills/skill-evolver/tests/test_capture.py`

**Interfaces:**

- Modifies:
  `spool_session_stop(installation, config, event, key, now, *,
  coalesce=False) -> bool`
- Modifies:
  `enqueue_stop(installation, config, raw, *,
  spool_only=False) -> str`
- Consumes: `plugin_spool_installation()` from Task 1

- [ ] **Step 1: Write failing public Hook and coalescing tests**

Add to `SessionCaptureTests`:

```python
def plugin_runtime(self) -> tuple[Path, object]:
    plugin_data = (
        self.installation.data_root.parent
        / "plugins/data/skill-evolver-skill-evolver-dev"
    )
    plugin_data.mkdir(mode=0o700, parents=True)
    runtime = replace(
        self.runtime.load_review_runtime(),
        plugin_data=plugin_data,
    )
    return plugin_data, runtime

def test_hook_uses_plugin_data_when_canonical_store_is_read_only(self) -> None:
    plugin_data, runtime = self.plugin_runtime()
    before = self.installation.database.read_bytes()
    args = Namespace(
        installation=str(self.installation_path),
        plugin_data=str(plugin_data),
    )
    stdin = mock.Mock()
    stdin.buffer.read.return_value = json.dumps(self.payload).encode()
    with (
        mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ),
        mock.patch.object(self.runtime.sys, "stdin", stdin),
        mock.patch.object(
            self.runtime,
            "open_database",
            side_effect=AssertionError("Hook must not open SQLite"),
        ),
    ):
        self.assertEqual(self.runtime.cmd_enqueue_stop(args), 0)
    payloads = list((plugin_data / "stop-spool").glob("*.json"))
    self.assertEqual(len(payloads), 1)
    self.assertEqual(self.installation.database.read_bytes(), before)
    self.assertEqual(list(self.installation.spool.iterdir()), [])
    self.assertNotIn(
        self.transcript.read_bytes().strip(),
        payloads[0].read_bytes(),
    )

def test_plugin_ingress_coalesces_repeated_session_stops(self) -> None:
    plugin_data, runtime = self.plugin_runtime()
    bound = None
    with mock.patch.object(
        self.runtime, "load_review_runtime", return_value=runtime
    ):
        bound = self.runtime.plugin_spool_installation(
            self.installation, plugin_data, create=True
        )
    raw = json.dumps(self.payload).encode()
    self.assertEqual(
        self.runtime.enqueue_stop(
            bound, self.runtime_config, raw, spool_only=True
        ),
        "spooled",
    )
    first = next(bound.spool.glob("*.json")).read_bytes()
    with self.transcript.open("ab") as stream:
        stream.write(b"new-boundary-without-transcript-read\n")
    self.assertEqual(
        self.runtime.enqueue_stop(
            bound, self.runtime_config, raw, spool_only=True
        ),
        "spooled",
    )
    payloads = list(bound.spool.glob("*.json"))
    self.assertEqual(len(payloads), 1)
    self.assertNotEqual(payloads[0].read_bytes(), first)
```

Add a third test that directly supplies a captured event with a smaller
`observed_at_ns` after a newer event and asserts the one file's bytes do not
change.

Add to `RuntimeStoreTests`:

```python
def test_atomic_write_does_not_chmod_published_path_by_name(self) -> None:
    parent = self.base / "atomic-parent"
    parent.mkdir(mode=0o700)
    target = parent / "payload.json"
    with mock.patch.object(
        os,
        "chmod",
        side_effect=AssertionError("path chmod race"),
    ):
        self.runtime.atomic_write_bytes(target, b"payload\n")
    self.assertEqual(target.read_bytes(), b"payload\n")
    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
/usr/bin/python3 skills/skill-evolver/tests/test_capture.py \
  SessionCaptureTests.test_hook_uses_plugin_data_when_canonical_store_is_read_only \
  SessionCaptureTests.test_plugin_ingress_coalesces_repeated_session_stops \
  SessionCaptureTests.test_plugin_ingress_rejects_stale_replacement \
  RuntimeStoreTests.test_atomic_write_does_not_chmod_published_path_by_name \
  -v
```

Expected: missing arguments or unexpected keyword errors, and the public Hook
still attempts SQLite.

- [ ] **Step 3: Add a deterministic same-session destination**

Change `spool_session_stop()` to accept `coalesce: bool = False`. While holding
the existing spool lock, use:

```python
destination = installation.spool / (
    f"session-{key}.json"
    if coalesce
    else f"{time.time_ns()}-{os.getpid()}-{secrets.token_hex(4)}.json"
)
```

For a coalescing destination that already exists:

```python
state, current_encoded, _identity = read_spool_snapshot(destination)
if state != "verified" or current_encoded is None:
    raise ValueError("invalid_spool_payload")
current_payload = json.loads(current_encoded.decode("utf-8"))
current, current_key, _created, _expires = event_from_spool(
    current_payload, installation, config, now
)
if current_key != key:
    raise ValueError("invalid_spool_session_key")
same_identity = (
    current.transcript_device == event.transcript_device
    and current.transcript_inode == event.transcript_inode
)
newer = event.observed_at_ns > current.observed_at_ns or (
    event.observed_at_ns == current.observed_at_ns
    and same_identity
    and event.transcript_size > current.transcript_size
)
if not newer:
    return True
replaced_size = len(current_encoded)
```

Use projected capacity rather than rejecting a replacement at the file limit:

```python
adds_file = destination not in files
projected_files = len(files) + int(adds_file)
projected_bytes = total - replaced_size + len(encoded)
if projected_files > file_limit or projected_bytes > byte_limit:
    record_spool_overflow(installation)
    return False
```

Remove the old early `len(files) >= file_limit` rejection from the directory
scan. Keep `MAX_SPOOL_SCAN_ENTRIES` as the hard scan bound, then apply the
projected file and byte decision after the existing destination is known.

Keep the current unique-file fallback behavior when `coalesce=False`.

- [ ] **Step 4: Remove the redundant path-based chmod**

`atomic_write_bytes()` already applies the final mode with `os.fchmod()` to the
open temporary descriptor before publishing it. Delete only this post-replace
line:

```python
os.chmod(path, mode)
```

Keep the file fsync, `os.replace()` and parent-directory fsync. This closes the
published-path symlink race without changing the durable-write sequence.

- [ ] **Step 5: Add spool-only capture and wire the handler**

Change `enqueue_stop()` to accept `spool_only: bool = False` and add before the
SQLite path:

```python
if spool_only:
    return (
        "spooled"
        if spool_session_stop(
            installation,
            config,
            event,
            key,
            now,
            coalesce=True,
        )
        else "overflow"
    )
```

In `cmd_enqueue_stop()`:

```python
installation = load_installation(Path(args.installation))
config = load_config(installation)
capture = plugin_spool_installation(
    installation, Path(args.plugin_data), create=True
)
raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
enqueue_stop(capture, config, raw, spool_only=True)
```

Retain the command's existing catch-all and silent exit `0`.

- [ ] **Step 6: Run capture tests**

Run:

```bash
/usr/bin/python3 skills/skill-evolver/tests/test_capture.py \
  SessionCaptureTests -v
```

Expected: all `SessionCaptureTests` pass, including the existing contention,
overflow, hardlink, FIFO, silence and network-free cases.

- [ ] **Step 7: Commit Task 2**

```bash
git add \
  skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_capture.py
git commit -m "feat(skill-evolver): coalesce stop ingress"
```

## Task 3: Import ingress explicitly and expose read-only status

**Files:**

- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `skills/skill-evolver/tests/test_capture.py`

**Interfaces:**

- Modifies:
  `run_maintenance(connection, installation, config, now, *,
  capture_installation=None) -> dict[str, int]`
- Uses the same bound `Installation` for `queue_status()`
- Consumes: current `import_spool()` and inode-bound deletion

- [ ] **Step 1: Write failing import, replay, privacy and status tests**

Add to `MaintenanceStatusTests`:

```python
def capture_installation(self):
    plugin_data = (
        self.installation.data_root.parent
        / "plugins/data/skill-evolver-skill-evolver-dev"
    )
    plugin_data.mkdir(mode=0o700, parents=True)
    spool = plugin_data / "stop-spool"
    spool.mkdir(mode=0o700)
    return replace(self.installation, spool=spool)

def test_maintenance_imports_plugin_data_spool_idempotently(self) -> None:
    capture = self.capture_installation()
    event = self.event("plugin-session")
    key = self.runtime.session_key(
        self.installation, event.session_id
    )
    self.assertTrue(
        self.runtime.spool_session_stop(
            capture,
            self.runtime_config,
            event,
            key,
            2_000_000_000.0,
            coalesce=True,
        )
    )
    source = next(capture.spool.glob("*.json"))
    replay = source.read_bytes()
    connection = self.runtime.open_database(self.installation)
    first = self.runtime.run_maintenance(
        connection,
        self.installation,
        self.runtime_config,
        2_000_000_001.0,
        capture_installation=capture,
    )
    source.write_bytes(replay)
    source.chmod(0o600)
    second = self.runtime.run_maintenance(
        connection,
        self.installation,
        self.runtime_config,
        2_000_000_002.0,
        capture_installation=capture,
    )
    rows = connection.execute(
        "SELECT COUNT(*) FROM review_items"
    ).fetchone()[0]
    connection.close()
    self.assertEqual(first["spool_imported"], 1)
    self.assertEqual(second["spool_imported"], 0)
    self.assertEqual(second["spool_duplicates"], 1)
    self.assertEqual(rows, 1)
    self.assertEqual(list(capture.spool.glob("*.json")), [])
```

Add `test_status_reports_unimported_plugin_data_capture_read_only`. It must
snapshot the database and spool bytes, call `queue_status(connection, capture,
now)`, assert `pending_sessions == 0`, `spool.files == 1`,
`spool.available is True`, and assert both snapshots are unchanged while
`import_spool` and `run_maintenance` are patched to fail if called.

Add `test_raw_transcript_content_is_never_persisted_in_ingress_or_database`.
Put a unique sentinel only inside a transcript body, capture and import by the
public paths, then scan regular files under both roots and assert the sentinel
is absent.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
/usr/bin/python3 skills/skill-evolver/tests/test_capture.py \
  MaintenanceStatusTests.test_maintenance_imports_plugin_data_spool_idempotently \
  MaintenanceStatusTests.test_status_reports_unimported_plugin_data_capture_read_only \
  MaintenanceStatusTests.test_raw_transcript_content_is_never_persisted_in_ingress_or_database \
  -v
```

Expected: `run_maintenance` rejects `capture_installation`, and status lacks
the availability field.

- [ ] **Step 3: Import primary and legacy spools**

Add this optional keyword-only argument:

```python
def run_maintenance(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    now: float,
    *,
    capture_installation: Optional[Installation] = None,
) -> dict[str, int]:
```

Initialize counters with:

```python
sources = [capture_installation or installation]
if sources[0].spool != installation.spool:
    sources.append(installation)
counts = {
    "spool_imported": 0,
    "spool_duplicates": 0,
    "spool_invalid_deleted": 0,
    "spool_expired": 0,
    "spool_preserved": 0,
    "spool_scan_saturated": 0,
}
for source in sources:
    imported = import_spool(connection, source, config, now)
    for name, value in imported.items():
        if name == "spool_scan_saturated":
            counts[name] = max(counts[name], value)
        else:
            counts[name] += value
```

This drains an upgrade's old canonical spool once without changing the normal
test path.

- [ ] **Step 4: Make absent ingress status read-only and visible**

Change `spool_inventory()` so a missing directory returns `(0, 0, False)`
without creating it. Add:

```python
"available": installation.spool.is_dir(),
```

to the status spool object after validating an existing directory through the
current private-directory checks.

Wire `cmd_maintain()` with `create=True` and:

```python
result = run_maintenance(
    connection,
    installation,
    config,
    time.time(),
    capture_installation=capture,
)
```

Wire `cmd_status()` with `create=False` and pass the bound capture installation
to `queue_status()`. Status must still open the canonical database in
`mode=ro`.

- [ ] **Step 5: Run maintenance/status and full capture tests**

Run:

```bash
/usr/bin/python3 skills/skill-evolver/tests/test_capture.py \
  MaintenanceStatusTests -v
/usr/bin/python3 skills/skill-evolver/tests/test_capture.py -v
```

Expected: all capture tests pass; no test creates or changes a target skill.

- [ ] **Step 6: Commit Task 3**

```bash
git add \
  skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_capture.py
git commit -m "feat(skill-evolver): import plugin stop ingress"
```

## Task 4: Activate the patch release and verify the repository

**Files:**

- Modify: `hooks/hooks.json`
- Modify: `.codex-plugin/plugin.json`
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `skills/skill-evolver/tests/test_capture.py`
- Modify: `skills/skill-evolver/SKILL.md`
- Modify: `README.md`
- Modify: `.planning/STATE.md`
- Modify: `.planning/ROADMAP.md`

**Interfaces:**

- Public Hook:
  `enqueue-stop --installation PATH --plugin-data "$PLUGIN_DATA"`
- Public status/maintenance:
  `--plugin-data /Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev`
- Release version: `0.1.1`

- [ ] **Step 1: Write failing public-surface assertions**

In `ProductionSurfaceTests.test_only_main_stop_is_an_automatic_writer`, assert:

```python
self.assertEqual(manifest["version"], "0.1.1")
self.assertIn(' --plugin-data "$PLUGIN_DATA"', command)
self.assertEqual(
    runtime["plugin_data"],
    "/Users/igyeongseob/.codex/plugins/data/"
    "skill-evolver-skill-evolver-dev",
)
self.assertIn("stop-spool", skill)
self.assertIn("canonical and plugin-data roots", skill)
```

Extend
`MaintenanceStatusTests.test_parser_exposes_exact_status_and_maintain_commands`
to parse `--plugin-data` for `enqueue-stop`, `status`, and `maintain`; assert
the option is required for all three.

- [ ] **Step 2: Run the public-surface tests and verify RED**

Run:

```bash
/usr/bin/python3 skills/skill-evolver/tests/test_capture.py \
  ProductionSurfaceTests.test_only_main_stop_is_an_automatic_writer \
  MaintenanceStatusTests.test_parser_exposes_exact_status_and_maintain_commands \
  -v
```

Expected: manifest/version/command/parser assertions fail.

- [ ] **Step 3: Wire the exact commands and version**

Set:

```python
VERSION = "skill-evolver 0.1.1"
```

Add required `--plugin-data` arguments to the three parsers. Change the Hook
command to this exact JSON string:

```json
"/usr/bin/python3 -I \"$PLUGIN_ROOT/skills/skill-evolver/scripts/evolver.py\" enqueue-stop --installation \"/Users/igyeongseob/.codex/skill-evolver/installation.json\" --plugin-data \"$PLUGIN_DATA\""
```

Set `.codex-plugin/plugin.json` version to `0.1.1`.

- [ ] **Step 4: Update skill and operator documentation**

Update `SKILL.md` and `README.md` so:

- status includes the literal pinned `--plugin-data` path and remains
  read-only;
- maintenance includes the same literal path and its approval covers the
  canonical and plugin-data roots for that invocation;
- Hook capture is spool-only and coalesces one file per session;
- `$PLUGIN_DATA` is accepted only from the trusted Hook command, never from
  transcript, web, tool or model content;
- no automatic review, label, evaluation or apply is implied.

Update `.planning/STATE.md` and `.planning/ROADMAP.md` to record that the
`0.1.0` production capture assumption regressed, `0.1.1` routes capture through
plugin data, `Q-001` must terminalize as provenance drift, and Phase 5 remains
blocked from Phase 6 until a successor epoch passes.

- [ ] **Step 5: Run the full verification suite**

Run:

```bash
cd /Users/igyeongseob/Documents/오픈소스
PYTHONPYCACHEPREFIX=/private/tmp/skill-evolver-pycache \
  /usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests -p 'test_*.py'
/usr/bin/python3 -m json.tool \
  skill-evolver/hooks/hooks.json >/dev/null
/usr/bin/python3 -m json.tool \
  skill-evolver/.codex-plugin/plugin.json >/dev/null
/usr/bin/python3 -m json.tool \
  skill-evolver/skills/skill-evolver/references/runtime.json >/dev/null
git -C skill-evolver diff --check
git -C skill-evolver status --short
```

Expected: the full suite passes with only the three documented historical
skips; JSON validation and `diff --check` pass; only planned paths are changed.

- [ ] **Step 6: Commit Task 4**

```bash
cd /Users/igyeongseob/Documents/오픈소스/skill-evolver
git add \
  hooks/hooks.json \
  .codex-plugin/plugin.json \
  skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_capture.py \
  skills/skill-evolver/SKILL.md \
  README.md \
  .planning/STATE.md \
  .planning/ROADMAP.md
git commit -m "release(skill-evolver): activate plugin data capture"
```

## Task 5: Production activation and real Desktop proof

**Files:**

- No source-file edits unless the real proof exposes a new defect.
- External plugin cache and data roots change only through exact approved
  commands or the Codex Hook trust UI.

**Interfaces:**

- Installed plugin ID: `skill-evolver@skill-evolver-dev`
- Expected plugin-data root:
  `/Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev`
- Expected successor quality epoch: `Q-002`

- [ ] **Step 1: Refresh the installed plugin**

Run the exact local marketplace/plugin refresh command and verify `codex plugin
list --json` reports `0.1.1`. Do not edit the cache directly.

- [ ] **Step 2: Review the changed Hook trust**

Use Codex `/hooks` and trust only the matcher-free `Stop` command whose
`--plugin-data` value is `"$PLUGIN_DATA"`. Do not write a trusted hash directly
into `config.toml`.

- [ ] **Step 3: Run one new meaningful Desktop task**

The task must be a genuinely new Desktop session after `0.1.1` activation. Do
not substitute a fixture, subagent, CLI process, repeated generation, or the
missed `0.1.0` session.

- [ ] **Step 4: Verify ingress before import**

Run the read-only status command with both literal locators. Expected:

```json
{
  "pending_sessions": 0,
  "spool": {
    "available": true,
    "files": 1
  }
}
```

The exact byte count may vary. One file proves durable Hook capture; it is not
yet a canonical pending session.

- [ ] **Step 5: Import once under explicit approval**

Run one fully expanded `maintain` command approved for the canonical and
plugin-data roots. Expected: `spool_imported >= 1`, then read-only status shows
the ingress file removed and at least one canonical pending session.

- [ ] **Step 6: Roll the quality epoch forward**

Run separately approved `quality-seal` and `quality-gate Q-001` commands. The
first must invalidate empty `Q-001` with
`quality_provenance_drift`; the second must create its one canonical terminal
report and digest.

Read the literal `terminal_report_digest` returned by `quality-gate Q-001`,
construct `Q-001@` followed immediately by those exact 64 lowercase
hexadecimal characters in current-turn memory, and use that complete value in
one separately approved `quality-open --predecessor` command. Verify read-only
`quality-status` reports `Q-002` as `COLLECTING` with zero synthetic carryover.

- [ ] **Step 7: Record production evidence**

Update `.planning/STATE.md` and `.planning/ROADMAP.md` with the actual plugin
version, real capture/import counts, `Q-001` terminal digest and `Q-002`
collection state. Commit only those two files:

```bash
git add .planning/STATE.md .planning/ROADMAP.md
git commit -m "docs(skill-evolver): record plugin data capture proof"
```

Do not claim Phase 5 complete. The next work remains collection of ten distinct
meaningful sessions in `Q-002`, explicit bounded review, user-only labels and
the quality gate.
