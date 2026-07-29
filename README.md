# Skill Evolver Session Queue

This Read-only MVP records one row per session. It does not run a model,
analyze a transcript in the Hook, create a candidate automatically, or change
an installed skill.

## Gate

`docs/feasibility-report-v2.json` must contain:

```json
{
  "schema_version": 2,
  "decision": "PASS",
  "next_action": "write_session_runtime_queue_plan"
}
```

Do not initialize production from the older report or the superseded
turn-level plan.

## Initialize

The fixed production root is `/Users/igyeongseob/.codex/skill-evolver`.
Creating it is an explicit write outside ordinary workspaces. Run this from a
user-controlled terminal, or approve only this exact command and data root.

Save `/private/tmp/skill-evolver-config.json` as:

```json
{
  "capture_paused": false,
  "exclude_roots": []
}
```

Then run:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py init \
  --data-root /Users/igyeongseob/.codex/skill-evolver \
  --transcript-root /Users/igyeongseob/.codex/sessions \
  --transcript-root /Users/igyeongseob/.codex/archived_sessions \
  --config /private/tmp/skill-evolver-config.json
```

The private root is mode `0700`; `installation.json`, `config.json`,
`identity.key`, SQLite, and spool files are mode `0600`. Runtime environment
variables cannot redirect the installation.

## Install and inspect

```bash
codex plugin marketplace add \
  /Users/igyeongseob/Documents/오픈소스 --json
codex plugin add skill-evolver@skill-evolver-dev --json
```

Use `/hooks` in CLI and Desktop. Trust only the one matcher-free `Stop` command
shown in `hooks/hooks.json`. There is no `SubagentStop` registration. The Hook
validates a bounded envelope, stats the transcript, computes an HMAC
`session_key`, and upserts SQLite or the bounded spool. It reads no transcript
bytes, calls no model or network, writes no skill, prints nothing, and exits
`0`.

Defaults are 200 pending sessions, 14-day pending retention, 30-day raw
metadata cleanup, 180-day session-key dedupe, and 200 spool files or 10 MiB.
Repeated Stops remain one row per session.

## Read-only status

Status needs no write approval:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py status \
  --installation \
  /Users/igyeongseob/.codex/skill-evolver/installation.json
```

It reports pending sessions and generations, oldest age, active/expired
leases, binding failures, excluded/expired counts, spool files/bytes/overflow,
last successful Hook time, and raw-metadata cleanup health. It opens SQLite in
read-only mode, never imports the spool, and never opens a transcript.

## Explicit maintenance

Maintenance imports spool files, recovers expired leases without advancing a
cursor, enforces session capacity and retention, clears raw metadata, and
removes expired HMAC dedupe rows. Before running it, approve only this exact
command and data root for this invocation:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py maintain \
  --installation \
  /Users/igyeongseob/.codex/skill-evolver/installation.json
```

Do not grant later ordinary tasks permanent write access. Explicit review in
the next release uses the same exact-command, exact-root approval rule.

To start with capture disabled, set `"capture_paused": true` in the
initialization config. Changing `config.json` later is also an explicit
global-root mutation and must use a user-controlled terminal or separately
approved exact command.

## Uninstall

Uninstalling stops new Hook writes but preserves the private inbox:

```bash
codex plugin remove skill-evolver@skill-evolver-dev --json
codex plugin marketplace remove skill-evolver-dev --json
```
