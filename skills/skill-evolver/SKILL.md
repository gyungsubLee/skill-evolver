---
name: skill-evolver
description: Inspect the Skill Evolver schema-v2 feasibility probe. Use only when the user explicitly names $skill-evolver and asks for probe status, access evidence, fixture promotion, the v2 feasibility gate, or probe cleanup. Never invoke after an ordinary task.
---

# Skill Evolver Feasibility Probe

This pre-release skill does not review transcripts, create candidates, modify
skills, or install the production Runtime Queue.

Read `references/runtime.json` and pass its absolute `installation` value to the
self-contained `scripts/evolver.py` entrypoint.

Resolve `scripts/evolver.py` relative to this `SKILL.md` and always invoke it as
`/usr/bin/python3 -I` followed by that resolved absolute path. Resolve the plugin
root as this skill directory's second parent. The v2 installation is the absolute
locator in `references/runtime.json`.

Supported user-facing actions:

Only when the user explicitly names $skill-evolver, support these v2 probe
actions; status and list do not open transcripts.

- `$skill-evolver probe status` → run `probe-status`.
- `$skill-evolver probe list` → run `probe-list`.
- `$skill-evolver probe access default <surface>` → run
  `probe-v2-default-access --surface <surface> --output <caller-safe-output>`.
  The default preflight runs without elevation and writes its sanitized response
  only to an exact workspace path or a caller-created private temporary
  directory. It must not write the v2 root by default.
- `$skill-evolver probe access explicit <surface>` → first run
  `probe-v2-arm-access --surface <surface>`, then request approval for the
  exact `probe-v2-explicit-access` command and the exact v2 root. This is the
  only action that requests access; do not request broad or persistent access.
- `$skill-evolver probe promote stop|access|transcript <surface>` → use the
  corresponding `probe-v2-promote-*` command. Transcript promotion reads only
  the frozen prefixes selected by the operator; never open transcripts for any
  other action.
- `$skill-evolver probe gate` → run `probe-v2-gate` after exactly the six v2
  fixtures exist. It writes only `feasibility-report-v2.json` and
  `feasibility-report-v2.md`, while reading the immutable v1 predecessor.
- `$skill-evolver probe cleanup` → show the exact `probe-scrub` command, then
  require the user to run it externally in a TTY and type the exact
  `DELETE-FEASIBILITY-RAW` confirmation.

Never invoke after an ordinary task. Do not copy raw observations into chat.
Do not claim feasibility passed unless `probe-v2-gate` exits 0 and both v2
report files say `PASS`.

## Gate boundary

A `PASS` report authorizes writing a separate Read-only MVP implementation plan only when it is from the v2 gate.
It does not authorize SQLite queue implementation, model review, skill mutation,
evaluation, apply, or undo.

A `FAIL` report requires a design amendment for session-level capture. Do not
guess transcript fields or broaden filesystem access to force a pass.
