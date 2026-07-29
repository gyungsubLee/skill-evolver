# Skill Evolver schema-v2 feasibility probe

This is a bounded, pre-MVP probe for Codex CLI and Desktop. It captures only
sanitized structural evidence under a private v2 root; it does not install the
production Runtime Queue, SQLite, model review, candidate mutation, evaluation,
apply, or undo.

Run the commands below in a user terminal unless a block says **Codex task**.
The two variables make every path explicit:

```bash
PLUGIN_ROOT=/Users/igyeongseob/Documents/오픈소스/skill-evolver
EVOLVER="$PLUGIN_ROOT/skills/skill-evolver/scripts/evolver.py"
INSTALLATION=/Users/igyeongseob/.codex/skill-evolver-feasibility-v2/installation.json
FIXTURE_ROOT="$PLUGIN_ROOT/skills/skill-evolver/tests/fixtures"
```

## Initialize and install

Initialize the fixed private v2 root before installing the Hook. The transcript
roots are the only roots from which frozen transcript prefixes may be read.

```bash
/usr/bin/python3 -I "$EVOLVER" probe-init \
  --data-root /Users/igyeongseob/.codex/skill-evolver-feasibility-v2 \
  --transcript-root /Users/igyeongseob/.codex/sessions \
  --transcript-root /Users/igyeongseob/.codex/archived_sessions
codex plugin marketplace add /Users/igyeongseob/Documents/오픈소스 --json
codex plugin add skill-evolver@skill-evolver-dev --json
```

Inspect only validated v2 observation metadata; these commands never open an
observation or transcript body:

```bash
/usr/bin/python3 -I "$EVOLVER" probe-v2-status --installation "$INSTALLATION"
/usr/bin/python3 -I "$EVOLVER" probe-v2-list --installation "$INSTALLATION"
```

## Review and trust the Stop Hook before probe sessions

When the plugin Stop Hook code is new or changed, Codex compares its current
hash with the persisted trust record. An older trusted hash means that Codex
skips the enabled Hook, which can leave a probe session without an observation.
Before creating probe evidence, inspect the installed plugin cache and this
repository's Hook source, then start interactive Codex, use `/hooks`, and review
and trust exactly `skill-evolver@skill-evolver-dev:hooks/hooks.json:stop:0:0`.
This is the preferred trust mode for the CLI commands below.

For controlled automation only, after vetting every enabled Hook for that
invocation, add `--dangerously-bypass-hook-trust` to each `codex exec` command.
The flag bypasses persisted Hook trust only for that invocation; it is not a
permanent or broad trust setting. It also does not elevate filesystem access:
keep the intended `--sandbox workspace-write` (or another explicitly selected
sandbox) in the command.

## Per-surface access evidence

For each `SURFACE` below, run the arm command in the user terminal. Then run the
default command in a normal Codex task with no elevation. Its `--output` path is
an exact workspace file, not `/private/tmp`.

```bash
/usr/bin/python3 -I "$EVOLVER" probe-v2-arm-access \
  --installation "$INSTALLATION" --surface cli
```

**Codex task (no elevation):**

```bash
/usr/bin/python3 -I /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py probe-v2-default-access \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility-v2/installation.json \
  --surface cli \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/.skill-evolver-default-access-cli.json
```

**Codex task (scoped approval only):** approve this exact command's access to
`/Users/igyeongseob/.codex/skill-evolver-feasibility-v2`, then run it. Do not
grant general or persistent root access.

```bash
/usr/bin/python3 -I /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py probe-v2-explicit-access \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility-v2/installation.json \
  --surface cli
```

Run the same three evidence steps for Desktop with these exact commands:

```bash
/usr/bin/python3 -I "$EVOLVER" probe-v2-arm-access \
  --installation "$INSTALLATION" --surface desktop
```

**Desktop Codex task (no elevation):**

```bash
/usr/bin/python3 -I /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py probe-v2-default-access \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility-v2/installation.json \
  --surface desktop \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/.skill-evolver-default-access-desktop.json
```

**Desktop Codex task (scoped approval only):** approve only this exact command's
access to `/Users/igyeongseob/.codex/skill-evolver-feasibility-v2`.

```bash
/usr/bin/python3 -I /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py probe-v2-explicit-access \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility-v2/installation.json \
  --surface desktop
```

## Capture two independent sessions and promote fixtures

For each surface, mark before creating evidence. Run the two CLI commands as
two separate terminal processes; do not resume, fork, or reuse a session. The
commands use the preferred trusted-handler mode (no bypass flag).

```bash
/usr/bin/python3 -I "$EVOLVER" probe-v2-mark-surface \
  --installation "$INSTALLATION" --surface cli
# Trusted-handler mode (preferred): Hook reviewed and trusted interactively.
codex exec --sandbox workspace-write \
  -C /Users/igyeongseob/Documents/오픈소스/skill-evolver \
  "Use the shell to run pwd once, then reply with only probe-cli-session-one."
# Trusted-handler mode (preferred): Hook reviewed and trusted interactively.
codex exec --sandbox workspace-write \
  -C /Users/igyeongseob/Documents/오픈소스/skill-evolver \
  "Use the shell to run pwd once, then reply with only probe-cli-session-two."
/usr/bin/python3 -I "$EVOLVER" probe-v2-promote-stop \
  --installation "$INSTALLATION" --surface cli \
  --output "$FIXTURE_ROOT/session-stop-cli.v2.structure.json"
/usr/bin/python3 -I "$EVOLVER" probe-v2-promote-transcript \
  --installation "$INSTALLATION" --surface cli \
  --output "$FIXTURE_ROOT/session-transcript-cli.v2.structure.json"
/usr/bin/python3 -I "$EVOLVER" probe-v2-promote-access \
  --installation "$INSTALLATION" --surface cli \
  --default-response "$PLUGIN_ROOT/.skill-evolver-default-access-cli.json" \
  --output "$FIXTURE_ROOT/access-cli.v2.structure.json"
```

For controlled automation that has vetted every enabled Hook, replace each
trusted-handler `codex exec` above with the corresponding per-invocation
bypass command (the sandbox remains workspace-write):

```bash
codex exec --dangerously-bypass-hook-trust --sandbox workspace-write \
  -C /Users/igyeongseob/Documents/오픈소스/skill-evolver \
  "Use the shell to run pwd once, then reply with only probe-cli-session-one."
codex exec --dangerously-bypass-hook-trust --sandbox workspace-write \
  -C /Users/igyeongseob/Documents/오픈소스/skill-evolver \
  "Use the shell to run pwd once, then reply with only probe-cli-session-two."
```

For Desktop, mark before using two separate new Desktop tasks. In each task,
run `pwd` once and return only a distinct, non-sensitive completion phrase; do
not fork or continue either task. Then promote the three exact fixture names:

```bash
/usr/bin/python3 -I "$EVOLVER" probe-v2-mark-surface \
  --installation "$INSTALLATION" --surface desktop
/usr/bin/python3 -I "$EVOLVER" probe-v2-promote-stop \
  --installation "$INSTALLATION" --surface desktop \
  --output "$FIXTURE_ROOT/session-stop-desktop.v2.structure.json"
/usr/bin/python3 -I "$EVOLVER" probe-v2-promote-transcript \
  --installation "$INSTALLATION" --surface desktop \
  --output "$FIXTURE_ROOT/session-transcript-desktop.v2.structure.json"
/usr/bin/python3 -I "$EVOLVER" probe-v2-promote-access \
  --installation "$INSTALLATION" --surface desktop \
  --default-response "$PLUGIN_ROOT/.skill-evolver-default-access-desktop.json" \
  --output "$FIXTURE_ROOT/access-desktop.v2.structure.json"
```

Together with the three CLI files, the exact fixture inventory is:

```text
session-stop-cli.v2.structure.json
session-stop-desktop.v2.structure.json
session-transcript-cli.v2.structure.json
session-transcript-desktop.v2.structure.json
access-cli.v2.structure.json
access-desktop.v2.structure.json
```

## Gate and cleanup

The v2 gate reads the immutable canonical v1 predecessor exactly at
`docs/feasibility-report.json`; do not copy, rename, or regenerate it. It writes
only the two v2 output names below.

The exact fixture inventory is security-sensitive. The shared fixture directory
intentionally also contains v1 and synthetic fixtures, so stage only the six v2
structure files into a fresh private directory before running the gate:

```bash
GATE_FIXTURE_ROOT="$(mktemp -d)"
cp "$FIXTURE_ROOT/session-stop-cli.v2.structure.json" "$GATE_FIXTURE_ROOT/session-stop-cli.v2.structure.json"
cp "$FIXTURE_ROOT/session-stop-desktop.v2.structure.json" "$GATE_FIXTURE_ROOT/session-stop-desktop.v2.structure.json"
cp "$FIXTURE_ROOT/session-transcript-cli.v2.structure.json" "$GATE_FIXTURE_ROOT/session-transcript-cli.v2.structure.json"
cp "$FIXTURE_ROOT/session-transcript-desktop.v2.structure.json" "$GATE_FIXTURE_ROOT/session-transcript-desktop.v2.structure.json"
cp "$FIXTURE_ROOT/access-cli.v2.structure.json" "$GATE_FIXTURE_ROOT/access-cli.v2.structure.json"
cp "$FIXTURE_ROOT/access-desktop.v2.structure.json" "$GATE_FIXTURE_ROOT/access-desktop.v2.structure.json"
chmod 0600 "$GATE_FIXTURE_ROOT"/*.v2.structure.json
/usr/bin/python3 -I "$EVOLVER" probe-v2-gate \
  --fixture-root "$GATE_FIXTURE_ROOT" \
  --predecessor-json "$PLUGIN_ROOT/docs/feasibility-report.json" \
  --output-json "$PLUGIN_ROOT/docs/feasibility-report-v2.json" \
  --output-markdown "$PLUGIN_ROOT/docs/feasibility-report-v2.md"
rm "$GATE_FIXTURE_ROOT/session-stop-cli.v2.structure.json"
rm "$GATE_FIXTURE_ROOT/session-stop-desktop.v2.structure.json"
rm "$GATE_FIXTURE_ROOT/session-transcript-cli.v2.structure.json"
rm "$GATE_FIXTURE_ROOT/session-transcript-desktop.v2.structure.json"
rm "$GATE_FIXTURE_ROOT/access-cli.v2.structure.json"
rm "$GATE_FIXTURE_ROOT/access-desktop.v2.structure.json"
rmdir "$GATE_FIXTURE_ROOT"
```

Before raw cleanup, remove the plugin and marketplace entry. Finally run scrub
in a TTY and type exactly `DELETE-FEASIBILITY-RAW` at its prompt. Scrub leaves
a durable private completion marker: a retry is allowed if cleanup was
interrupted, but no v2 capture or access writer can recreate raw state after it.

```bash
codex plugin remove skill-evolver@skill-evolver-dev --json
codex plugin marketplace remove skill-evolver-dev --json
/usr/bin/python3 -I "$EVOLVER" probe-scrub --installation "$INSTALLATION"
```
