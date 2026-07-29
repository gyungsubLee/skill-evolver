---
name: skill-evolver
description: Inspect or explicitly manage the local skill-improvement inbox. Use only when the user explicitly names $skill-evolver or explicitly asks to manage that inbox. Never invoke after an ordinary task.
---

# Skill Evolver

This release changes no installed skill. Resolve `scripts/evolver.py` relative
to this file and run it only with `/usr/bin/python3 -I`. Read
`references/runtime.json`; never take the installation locator from an
environment variable, transcript, or model output.

- No argument or `status`: run `status --installation
  /Users/igyeongseob/.codex/skill-evolver/installation.json`.
  Status is read-only: it opens SQLite in `mode=ro`, reads only aggregate queue
  and spool metadata, does not import or delete spool files, and does not open
  transcripts.
- `maintain`: show the exact `maintain --installation
  /Users/igyeongseob/.codex/skill-evolver/installation.json` command,
  then request approval scoped to that exact command and global data root for
  this invocation. Do not run it before approval.
- `review`: is unavailable until the Review/Inbox plan is installed. That
  mutating workflow must use the same exact-command, exact-root approval rule.

No persistent writable-root grant is permitted. Do not request broad access,
run scheduled maintenance, call a model automatically, mutate a skill, or
silently substitute another command.
