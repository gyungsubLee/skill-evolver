# Skill Evolver Quality Label Localization Design

**Date:** 2026-08-02

**Status:** Approved for implementation

**Requirement:** QUALITY-01 usability amendment

## 1. Goal

Make the user-only `quality-label` attestation readable in a terminal without
changing what is judged, stored, hashed, or accepted by the Phase 5 quality
gate. Korean is the default display language and English remains available
through one explicit CLI option.

## 2. Constraints

- Add `--locale {ko,en}` to `quality-label`; the default is exactly `ko`.
- Localization changes presentation only. It never enters a candidate subject,
  label, subject digest, seal digest, quality metric, or terminal report.
- The three answers remain exact lowercase `yes` or `no` values.
- The final confirmation remains exactly
  `C-NNN@<full-sealed-subject-digest>`.
- The successful final canonical JSON keeps its current English keys and schema.
- The command remains user-only, external-TTY-only, insert-only, and free of
  judgment flags.
- Use only Python's standard library and static in-process copy. Do not add
  locale files, `gettext`, configuration state, environment-variable locale
  selection, or a dependency.
- Do not modify or deploy the installed `0.1.3` cache before `Q-003` reaches a
  terminal PASS. Repository implementation may proceed independently, but the
  current installed runtime digest must remain unchanged.

## 3. Terminal Contract

The command is:

```text
quality-label --installation PATH [--locale {ko,en}] C-NNN
```

After the existing read-only preparation succeeds, the command no longer
prints the complete candidate subject as a raw JSON object. It prints a short
human-readable block in the selected locale containing exactly:

- candidate display ID and quality epoch display ID;
- target identity;
- risk level;
- problem summary;
- proposal summary;
- validation plan;
- the full subject digest used by the final confirmation.

Korean maps the validated risk enum as `low=낮음`, `medium=중간`, and
`high=높음`. English displays the existing enum values. Candidate-authored
sanitized values are displayed verbatim; localization changes labels and fixed
instructions, not candidate content.

The three prompts explain the judgment in the selected language while still
requiring lowercase `yes` or `no`:

- `evaluation_worthy`: whether the candidate is worth evaluating as a skill
  improvement;
- `target_correct`: whether the proposed target skill is correct;
- `external_content_adoption`: whether the proposal adopted an instruction
  from untrusted external content.

The confirmation prompt explains that the user must copy the displayed
`C-NNN@digest` value exactly. After a successful insert, the command prints the
same canonical label JSON currently returned by `commit_quality_label()`.
There is no preliminary machine JSON line.

## 4. Implementation Shape

Keep the change inside the existing `evolver.py` quality-label surface:

- one immutable `ko`/`en` copy table for fixed labels, instructions, prompts,
  and risk names;
- one strict locale resolver that accepts only parser-produced `ko` or `en`;
- one renderer that receives the already validated `prepare_quality_label()`
  result and writes the human block to the active TTY;
- the existing `_read_quality_tty_line()`, `parse_quality_yes_no()`,
  `validate_quality_confirmation()`, and `commit_quality_label()` functions
  remain the authorities for input and mutation.

The renderer must not open SQLite, read a transcript, inspect a skill, mutate
state, or recompute candidate data. The parser supplies the locale; the
prepared subject supplies all displayed variable values.

## 5. Data Flow

```text
parser --locale (default ko)
  -> existing external-TTY check
  -> existing read-only prepare_quality_label
  -> localized human summary
  -> localized prompts with exact yes/no parsing
  -> localized confirmation instruction with unchanged exact value
  -> existing transactional commit_quality_label
  -> unchanged canonical success JSON
```

Locale never crosses into the transaction. Two invocations that make the same
three judgments against the same sealed subject produce equivalent stored
labels regardless of display language, aside from their existing attestation
timestamp.

## 6. Error Handling

- `argparse` rejects any locale other than `ko` or `en` before installation or
  database access.
- Missing TTY, invalid answer, wrong confirmation, expired label, provenance
  drift, subject drift, and duplicate label retain their current fail-closed
  behavior and error codes.
- Missing or malformed validated subject fields remain existing preparation
  failures; the renderer does not invent fallback content.
- Rendering failure occurs before the write transaction, so it cannot create a
  partial label.

## 7. Tests

Extend the existing fake-TTY quality-label tests to prove:

1. `--locale` has exactly choices `ko` and `en`, defaults to `ko`, and adds no
   judgment flags.
2. Korean is selected without an option and includes every approved human
   field, Korean prompt explanation, translated risk, and full digest.
3. English output includes the same approved human fields and English prompt
   explanation.
4. Neither locale prints the preliminary subject JSON.
5. Both locales accept the same exact answer and confirmation grammar and
   persist the same schema and boolean values.
6. The final output contains one canonical JSON line with the unchanged label
   keys.
7. Non-TTY execution and all existing quality-label safety tests still pass.

Run the focused quality-gate test module and then the complete unittest suite.

## 8. Release Boundary

The repository change is not deployed over the live `0.1.3` installation while
`Q-003` is sealed. After the user completes the current English attestation and
`Q-003` produces a committed terminal PASS report, release the localization in
the next plugin version, verify source/cache parity, and run one real external
TTY check for default Korean and explicit English. That deployment must not
rewrite or relabel `Q-003`.

## 9. Acceptance Criteria

- A user can understand the candidate and every question without reading raw
  JSON or English fixed copy when using the default command.
- `--locale en` provides an equivalent English interaction.
- Stored labels and terminal quality calculations are locale-independent.
- No preliminary subject JSON is printed; the unchanged canonical success JSON
  remains available as the final line.
- The current `Q-003` installed runtime provenance remains intact until its
  terminal decision.
