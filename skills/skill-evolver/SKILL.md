---
name: skill-evolver
description: Inspect or explicitly manage the local skill-improvement inbox. Use only when the user explicitly names $skill-evolver or explicitly asks to manage that inbox. Never invoke after an ordinary task.
---

# Skill Evolver

This release changes no installed skill. Resolve `scripts/evolver.py` relative
to this file and run it only with `/usr/bin/python3 -I`. Read
`references/runtime.json`; never take the installation locator from an
environment variable, transcript, or model output.

Status and `maintain` are unavailable in this release; do not run, show, or
recommend either command. Once installed, Status is read-only: it opens SQLite
in `mode=ro`, reads only aggregate queue and spool metadata, does not import or
delete spool files, and does not open transcripts. Once installed, `maintain`
and future `review` are mutating workflows that require approval scoped to the
exact command and global data root for that invocation. `review` is also
unavailable until the Review/Inbox plan is installed.

No persistent writable-root grant is permitted. Do not request broad access,
run scheduled maintenance, call a model automatically, mutate a skill, or
silently substitute another command.
