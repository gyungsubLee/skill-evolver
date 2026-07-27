# Skill Evolver Feasibility Probe

This directory contains the pre-MVP probe defined by
`docs/superpowers/specs/2026-07-26-skill-evolver-design.md`.

It answers three questions:

1. Do Codex CLI and Desktop deliver the required `Stop` fields?
2. Can the Hook and an explicitly invoked skill use one fixed private data root?
3. Can the captured transcript prefix map a turn and source provenance without
   reading bytes appended after the Hook?

The probe stores raw metadata only under
`/Users/igyeongseob/.codex/skill-evolver-feasibility` with private permissions.
Only sanitized structural reports are committed. The probe is removed after
the gate report is generated.

## Commands

Initialize the private probe:

```bash
/usr/bin/python3 -I /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py probe-init \
  --data-root /Users/igyeongseob/.codex/skill-evolver-feasibility \
  --transcript-root /Users/igyeongseob/.codex/sessions \
  --transcript-root /Users/igyeongseob/.codex/archived_sessions
```

Install from the local marketplace:

```bash
codex plugin marketplace add /Users/igyeongseob/Documents/오픈소스 --json
codex plugin add skill-evolver@skill-evolver-dev --json
```

The exact surface-arm, capture, promotion, and gate commands are recorded in
`docs/superpowers/plans/2026-07-26-skill-evolver-feasibility-spike.md`.

Remove the probe before scrubbing private observations:

```bash
codex plugin remove skill-evolver@skill-evolver-dev --json
codex plugin marketplace remove skill-evolver-dev --json
/usr/bin/python3 -I /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py probe-scrub \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json
```
