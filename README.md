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
two separate terminal processes; do not resume, fork, or reuse a session.

```bash
/usr/bin/python3 -I "$EVOLVER" probe-v2-mark-surface \
  --installation "$INSTALLATION" --surface cli
codex exec --sandbox workspace-write \
  -C /Users/igyeongseob/Documents/오픈소스/skill-evolver \
  "Use the shell to run pwd once, then reply with only probe-cli-session-one."
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

```bash
/usr/bin/python3 -I "$EVOLVER" probe-v2-gate \
  --fixture-root "$FIXTURE_ROOT" \
  --predecessor-json "$PLUGIN_ROOT/docs/feasibility-report.json" \
  --output-json "$PLUGIN_ROOT/docs/feasibility-report-v2.json" \
  --output-markdown "$PLUGIN_ROOT/docs/feasibility-report-v2.md"
```

Before raw cleanup, remove the plugin and marketplace entry. Finally run scrub
in a TTY and type exactly `DELETE-FEASIBILITY-RAW` at its prompt.

```bash
codex plugin remove skill-evolver@skill-evolver-dev --json
codex plugin marketplace remove skill-evolver-dev --json
/usr/bin/python3 -I "$EVOLVER" probe-scrub --installation "$INSTALLATION"
```
