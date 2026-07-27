---
name: skill-evolver
description: Inspect the Skill Evolver feasibility probe. Use only when the user explicitly names $skill-evolver and asks for probe status, fixture promotion, the feasibility gate, or probe cleanup. Never invoke after an ordinary task.
---

# Skill Evolver Feasibility Probe

This pre-release skill does not review transcripts, create candidates, or modify skills.

Read `references/runtime.json` and pass its absolute `installation` value to the
self-contained `scripts/evolver.py` entrypoint.

Resolve `scripts/evolver.py` relative to this `SKILL.md` and always invoke it as
`/usr/bin/python3 -I` followed by that resolved absolute path. Resolve the plugin
root as this skill directory's second parent. For `probe-gate`, use
`tests/fixtures` under this skill as `--fixture-root` and `docs/feasibility-report.json`
plus `docs/feasibility-report.md` under the plugin root as the two outputs.

Supported user-facing actions:

- `$skill-evolver probe status` → run `probe-status`.
- `$skill-evolver probe list` → run `probe-list`.
- `$skill-evolver probe preflight cli` → run `probe-skill-preflight --surface cli`
  without requesting elevated filesystem access.
- `$skill-evolver probe preflight desktop` → run
  `probe-skill-preflight --surface desktop` without requesting elevated filesystem access.
- `$skill-evolver probe gate` → run `probe-gate` after the six sanitized fixtures exist.
- `$skill-evolver probe cleanup` → show the exact `probe-scrub` command and require the user to run it in a TTY.

Do not open a transcript for status or list. Do not copy raw observations into chat.
Do not claim feasibility passed unless `probe-gate` exits 0 and both report files say `PASS`.

## Gate boundary

A `PASS` report authorizes writing a separate Read-only MVP implementation plan.
It does not authorize SQLite queue implementation, model review, skill mutation,
evaluation, apply, or undo.

A `FAIL` report requires a design amendment for session-level capture. Do not
guess transcript fields or broaden filesystem access to force a pass.
