# Skill Evolver Quality Label Localization Design

**Date:** 2026-08-02

**Status:** Approved for implementation

> **Release-boundary amendment — 2026-08-04:** Q-003 terminalized `FAIL`, so
> the Q-003-PASS deployment precondition in Sections 2 and 8 cannot be met.
> `2026-08-04-skill-evolver-target-attribution-remediation-design.md`
> supersedes only that release boundary. The completed localization behavior,
> user-only label contract, and authoring-language rules remain unchanged.

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
- Future Review candidates write human-facing summaries in the language of the
  direct user request or correction that supplies the strong signal. If that
  language is ambiguous, the fallback is Korean.
- Fingerprint-bearing `target_locator` and `proposal_intent` remain concise
  canonical English so equivalent improvements do not split by display
  language.
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
instructions, not already sealed candidate content. Future candidates follow
the authoring-language contract in Section 4.1.

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

### 4.1 Future candidate authoring language

Update the Review policy and fixed result instructions without changing the
candidate schema:

- `problem_summary`, `proposal_summary`, `validation_plan`, and every evidence
  `summary` use the language of the final evidence-eligible `user_direct`
  record in envelope order, independently of the strong-evidence record;
- this includes `verification_failure`, whose strong evidence is
  `tool_output`;
- quoted or pasted content does not select the authoring language;
- when no eligible direct-user record exists or its language is ambiguous,
  use Korean;
- enum values, `target_identity`, `problem_category`, `risk_level`,
  `signal_type`, and `source_kind` retain their existing canonical values;
- `target_locator` and `proposal_intent` remain canonical English because they
  participate in `candidate_fingerprint()`.

This is a model-output policy, not runtime translation. Python still never
invokes a model or translation service. Existing candidates, including sealed
`C-001`, remain immutable and may retain English summaries.

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

Candidate authoring language is chosen earlier during explicit Review. It does
not depend on the later `quality-label --locale` value.

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
8. Review policy and result instructions require human-facing candidate text
   in the direct user's language, Korean fallback, and canonical-English
   fingerprint fields.
9. Candidate fingerprint tests prove that the language policy does not change
   the existing fingerprint input fields or hash algorithm.

Run the focused quality-gate test module and then the complete unittest suite.

## 8. Release Boundary

The repository change is not deployed over the live `0.1.3` installation while
`Q-003` is sealed. After the user completes the current English attestation and
`Q-003` produces a committed terminal PASS report, release the localization in
the next plugin version, verify source/cache parity, and run one real external
TTY check for default Korean and explicit English. That deployment must not
rewrite or relabel `Q-003`.

The policy change intentionally changes the policy and runtime digests for
future Review batches. It applies only after the terminal `Q-003` report is
committed; it does not reopen, rewrite, or reinterpret that epoch.

## 9. Acceptance Criteria

- A user can understand the candidate and every question without reading raw
  JSON or English fixed copy when using the default command.
- `--locale en` provides an equivalent English interaction.
- New candidates describe the problem, proposal, validation, and evidence in
  the direct user's language, with Korean as the ambiguous-language fallback.
- Fingerprint-bearing classification text remains canonical English, avoiding
  language-only duplicate fingerprints.
- Stored labels and terminal quality calculations are locale-independent.
- No preliminary subject JSON is printed; the unchanged canonical success JSON
  remains available as the final line.
- The current `Q-003` installed runtime provenance remains intact until its
  terminal decision.
