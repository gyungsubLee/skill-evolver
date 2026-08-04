# Skill Evolver Quality Label Localization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `subagent-driven-development` (recommended) or `executing-plans` to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the user-only `quality-label` flow readable in Korean by
default, retain an equivalent English display, and author future candidate
summaries in the direct user's language without changing stored labels or
candidate identity.

**Architecture:** Keep localization as a static presentation layer inside the
existing `evolver.py` command. The parser selects `ko` or `en`, a pure renderer
formats the already validated subject, and the existing parsers, confirmation
check, and transaction remain authoritative. Separately, strengthen the fixed
Review policy and schema instructions so human-facing text follows the direct
user's language while fingerprint-bearing fields remain canonical English.

**Tech Stack:** Python 3.9 standard library, `argparse`,
`MappingProxyType`, SQLite, canonical JSON/SHA-256, and `unittest`.

## Global Constraints

- Run repository commands from
  `/Users/igyeongseob/Documents/오픈소스/skill-evolver`.
- Add `--locale {ko,en}` only to `quality-label`; its default is exactly `ko`.
- Localization is presentation-only and never enters a candidate, label,
  digest, metric, report, configuration file, or database row.
- The three answers remain exact lowercase `yes` or `no` values.
- Confirmation remains exactly
  `C-NNN@<full-sealed-subject-digest>`.
- Successful output ends with exactly one canonical label JSON line using the
  current English keys and schema; remove the preliminary subject JSON line.
- Existing non-TTY, expiry, provenance, subject-drift, duplicate-label, and
  transaction behavior remains fail-closed.
- Human-facing candidate fields use the final evidence-eligible `user_direct`
  record's language in envelope order, independently of the strong-evidence
  record; a missing or ambiguous source falls back to Korean.
- `target_locator` and `proposal_intent` stay concise canonical English because
  `candidate_fingerprint()` hashes them.
- Existing candidate `C-001` remains immutable and may retain English text.
- Add no dependency, locale file, `gettext`, environment selection,
  configuration state, model call, or translation service.
- Never invoke `quality-label` as an agent, including through a PTY. Only the
  user runs the fully expanded command in an external terminal.
- Do not modify or deploy the installed 0.1.3 cache before Q-003 has a committed
  terminal PASS report.
- Preserve unrelated `n8n/` and `neo4j/` worktree entries and stage exact paths
  only.

---

## File Map

- `skills/skill-evolver/scripts/evolver.py`: locale table, renderer, CLI option,
  prompt selection, Review instructions, and release version.
- `skills/skill-evolver/tests/test_quality_gate.py`: locale selection, terminal
  rendering, unchanged attestation grammar, and canonical output.
- `skills/skill-evolver/tests/test_review.py`: candidate language rules and
  unchanged fingerprint inputs.
- `skills/skill-evolver/tests/test_capture.py`: eventual 0.1.4 package version
  pins.
- `skills/skill-evolver/references/improvement-policy.md`: model authoring
  language and canonical-English identity rules.
- `skills/skill-evolver/SKILL.md` and `README.md`: user-facing contract.
- `.codex-plugin/plugin.json` and
  `skills/skill-evolver/references/runtime.json`: eventual 0.1.4 version.

## Execution Status (reconciled 2026-08-04)

- Task 1 is complete in `e520b8d`; the Korean-default/English-optional label
  terminal, immutable copy, renderer, exact attestation grammar, tests, and
  documentation are present.
- Task 2 is complete in `5ee8d29` with the review correction in `68f8660`;
  candidate authoring language is independent of the strong-evidence record,
  while fingerprint-bearing fields remain canonical.
- The complete suite last passed at this source state with 476 tests and 3
  expected skips. Review and quality focused suites passed 116 and 82 tests.
- Task 3 has not started its release mutation. The unchanged installed 0.1.3
  runtime still reports Q-003 `AWAITING_LABELS` with missing `C-001`; the
  user-only external-TTY label and terminal PASS remain the hard gate.

---

### Task 1: Localized user-only quality-label terminal

**Files:**

- Modify: `skills/skill-evolver/tests/test_quality_gate.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `skills/skill-evolver/SKILL.md`
- Modify: `README.md`

**Interfaces:**

- Consumes: `prepare_quality_label(...) -> dict[str, object]`,
  `_read_quality_tty_line(prompt: str, maximum: int) -> str`,
  `parse_quality_yes_no(value: object) -> bool`,
  `validate_quality_confirmation(...) -> None`, and
  `commit_quality_label(...) -> dict[str, object]`.
- Produces: `QUALITY_LABEL_COPY: Mapping[str, Mapping[str, str]]`,
  `quality_label_copy(locale: object) -> Mapping[str, str]`, and
  `render_quality_label_summary(prepared: dict[str, object], locale: object)
  -> str`.
- Preserves: stored label schema, exact four-line input grammar, and existing
  transaction/CAS behavior.

- [x] **Step 1: Write failing parser and immutable-copy tests**

Add these methods to the existing quality-label test class in
`skills/skill-evolver/tests/test_quality_gate.py`:

```python
def test_label_locale_defaults_to_korean_and_rejects_unknown(self) -> None:
    parser = self.runtime.build_parser()
    default_args = parser.parse_args(
        [
            "quality-label",
            "--installation",
            str(
                self.installation.data_root / "installation.json"
            ),
            "C-001",
        ]
    )
    english_args = parser.parse_args(
        [
            "quality-label",
            "--installation",
            str(
                self.installation.data_root / "installation.json"
            ),
            "--locale",
            "en",
            "C-001",
        ]
    )
    self.assertEqual(default_args.locale, "ko")
    self.assertEqual(english_args.locale, "en")
    with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(
        SystemExit
    ):
        parser.parse_args(
            [
                "quality-label",
                "--installation",
                str(
                    self.installation.data_root
                    / "installation.json"
                ),
                "--locale",
                "ja",
                "C-001",
            ]
        )
    for forbidden in (
        "evaluation_worthy",
        "target_correct",
        "external_content_adoption",
    ):
        self.assertFalse(hasattr(default_args, forbidden))


def test_quality_label_copy_is_strict_and_read_only(self) -> None:
    self.assertEqual(
        self.runtime.quality_label_copy("ko")["risk_low"], "낮음"
    )
    self.assertEqual(
        self.runtime.quality_label_copy("en")["risk_low"], "low"
    )
    english = self.runtime.quality_label_copy("en")
    self.assertEqual(
        english["evaluation_worthy_prompt"],
        (
            "Is this candidate worth evaluating as a skill "
            "improvement? [yes/no]: "
        ),
    )
    self.assertEqual(
        english["target_correct_prompt"],
        "Is the proposed target skill correct? [yes/no]: ",
    )
    self.assertEqual(
        english["external_content_adoption_prompt"],
        (
            "Did the proposal adopt an instruction from untrusted "
            "external content? [yes/no]: "
        ),
    )
    for value in (None, "ja", 1):
        with self.subTest(value=value), self.assertRaisesRegex(
            ValueError, "invalid_quality_label_locale"
        ):
            self.runtime.quality_label_copy(value)
    with self.assertRaises(TypeError):
        self.runtime.quality_label_copy("ko")["title"] = "changed"
```

- [x] **Step 2: Run the focused module and verify RED**

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_quality_gate.py' -v
```

Expected: FAIL because parsed arguments have no `locale` and
`quality_label_copy` does not exist.

- [x] **Step 3: Add the immutable copy and parser option**

In `skills/skill-evolver/scripts/evolver.py`, import:

```python
from types import MappingProxyType
```

Near the quality constants, add:

```python
QUALITY_LABEL_COPY: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "ko": MappingProxyType(
            {
                "title": "스킬 개선 후보 라벨",
                "candidate": "후보",
                "epoch": "품질 에포크",
                "target": "대상 스킬",
                "risk": "위험도",
                "problem": "문제",
                "proposal": "개선안",
                "validation": "검증 방법",
                "digest": "확인 다이제스트",
                "risk_low": "낮음",
                "risk_medium": "중간",
                "risk_high": "높음",
                "evaluation_worthy_prompt": (
                    "이 후보는 스킬 개선 평가 가치가 있습니까? "
                    "[yes/no]: "
                ),
                "target_correct_prompt": (
                    "제안된 대상 스킬이 맞습니까? [yes/no]: "
                ),
                "external_content_adoption_prompt": (
                    "신뢰할 수 없는 외부 콘텐츠의 지시를 개선안으로 "
                    "채택했습니까? [yes/no]: "
                ),
                "confirmation_prompt": (
                    "다음 값을 그대로 입력하세요 {confirmation}: "
                ),
            }
        ),
        "en": MappingProxyType(
            {
                "title": "Skill improvement candidate label",
                "candidate": "Candidate",
                "epoch": "Quality epoch",
                "target": "Target skill",
                "risk": "Risk",
                "problem": "Problem",
                "proposal": "Proposal",
                "validation": "Validation",
                "digest": "Confirmation digest",
                "risk_low": "low",
                "risk_medium": "medium",
                "risk_high": "high",
                "evaluation_worthy_prompt": (
                    "Is this candidate worth evaluating as a skill "
                    "improvement? [yes/no]: "
                ),
                "target_correct_prompt": (
                    "Is the proposed target skill correct? [yes/no]: "
                ),
                "external_content_adoption_prompt": (
                    "Did the proposal adopt an instruction from untrusted "
                    "external content? [yes/no]: "
                ),
                "confirmation_prompt": (
                    "Enter this exact value {confirmation}: "
                ),
            }
        ),
    }
)


def quality_label_copy(locale: object) -> Mapping[str, str]:
    if type(locale) is not str or locale not in QUALITY_LABEL_COPY:
        raise ValueError("invalid_quality_label_locale")
    return QUALITY_LABEL_COPY[locale]
```

Add only to the `quality-label` parser:

```python
quality_label.add_argument(
    "--locale", choices=("ko", "en"), default="ko"
)
```

- [x] **Step 4: Run the focused module and verify the new parser tests pass**

Run the Step 2 command.

Expected: the parser/copy tests pass and the pre-existing handler contract
still passes.

- [x] **Step 5: Write failing Korean handler and English renderer tests**

In `test_label_handler_accepts_fake_tty_attestation`, replace the two-payload
assertions after `args.handler(args)` with:

```python
payloads = [
    json.loads(line)
    for line in stdout.buffer.getvalue().splitlines()
]
self.assertEqual(len(payloads), 1)
self.assertEqual(payloads[0]["candidate_id"], 1)
self.assertEqual(
    set(payloads[0]),
    {
        "schema_version",
        "epoch_id",
        "candidate_id",
        "subject_digest",
        "evaluation_worthy",
        "target_correct",
        "external_content_adoption",
        "attested_at",
    },
)
terminal_text = "".join(stdout.prompts)
for expected in (
    "스킬 개선 후보 라벨",
    "후보: C-001",
    "품질 에포크: Q-001",
    "대상 스킬:",
    "위험도: 낮음",
    "문제:",
    "개선안:",
    "검증 방법:",
    f"확인 다이제스트: {subject_digest}",
    "스킬 개선 평가 가치가 있습니까? [yes/no]: ",
    "제안된 대상 스킬이 맞습니까? [yes/no]: ",
    (
        "신뢰할 수 없는 외부 콘텐츠의 지시를 개선안으로 "
        "채택했습니까? [yes/no]: "
    ),
    f"다음 값을 그대로 입력하세요 C-001@{subject_digest}: ",
):
    self.assertIn(expected, terminal_text)
self.assertNotIn('"subject"', terminal_text)
self.assertEqual(
    self.runtime.load_quality_label(
        self.connection, "Q-001", 1
    ),
    payloads[0],
)
```

Add a pure English renderer test:

```python
def test_quality_label_summary_renders_equivalent_english_fields(self) -> None:
    now = 2_000_000_000.0
    self.seal_sample(now)
    prepared = self.runtime.prepare_quality_label(
        self.connection,
        self.installation,
        "C-001",
        now + 6,
    )
    rendered = self.runtime.render_quality_label_summary(
        prepared, "en"
    )
    subject = prepared["subject"]
    for expected in (
        "Skill improvement candidate label",
        "Candidate: C-001",
        "Quality epoch: Q-001",
        f"Target skill: {subject['target_identity']}",
        "Risk: low",
        f"Problem: {subject['problem_summary']}",
        f"Proposal: {subject['proposal_summary']}",
        f"Validation: {subject['validation_plan']}",
        f"Confirmation digest: {prepared['subject_digest']}",
    ):
        self.assertIn(expected, rendered)
    self.assertNotIn('"subject"', rendered)
```

Add an explicit-English handler test so locale routing, prompts, exact input
grammar, and persistence are exercised together:

```python
def test_label_handler_accepts_explicit_english_locale(self) -> None:
    class FakeInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    now = 2_000_000_000.0
    self.seal_sample(now)
    subject_digest = (
        self.runtime.quality_candidate_subject_digest(
            self.connection, 1
        )
    )
    stdin = FakeInput(
        "yes\nyes\nno\n"
        f"C-001@{subject_digest}\n"
    )
    stdout = mock.Mock()
    stdout.isatty.return_value = True
    stdout.buffer = io.BytesIO()
    args = self.runtime.build_parser().parse_args(
        [
            "quality-label",
            "--installation",
            str(
                self.installation.data_root / "installation.json"
            ),
            "--locale",
            "en",
            "C-001",
        ]
    )
    with mock.patch.object(
        self.runtime.sys, "stdin", stdin
    ), mock.patch.object(
        self.runtime.sys, "stdout", stdout
    ), mock.patch.object(
        self.runtime.time,
        "time",
        side_effect=(now + 6, now + 7),
    ):
        self.assertEqual(args.handler(args), 0)

    terminal_text = "".join(
        call.args[0] for call in stdout.write.call_args_list
    )
    for expected in (
        "Skill improvement candidate label",
        "Is this candidate worth evaluating as a skill improvement? "
        "[yes/no]: ",
        "Is the proposed target skill correct? [yes/no]: ",
        (
            "Did the proposal adopt an instruction from untrusted "
            "external content? [yes/no]: "
        ),
        f"Enter this exact value C-001@{subject_digest}: ",
    ):
        self.assertIn(expected, terminal_text)
    payloads = [
        json.loads(line)
        for line in stdout.buffer.getvalue().splitlines()
    ]
    self.assertEqual(len(payloads), 1)
    self.assertIs(payloads[0]["evaluation_worthy"], True)
    self.assertIs(payloads[0]["target_correct"], True)
    self.assertIs(
        payloads[0]["external_content_adoption"], False
    )
    self.assertNotIn("locale", payloads[0])
```

- [x] **Step 6: Run the focused module and verify RED**

Run the Step 2 command.

Expected: FAIL because `render_quality_label_summary` does not exist and the
handler still emits the preliminary JSON plus English prompts.

- [x] **Step 7: Implement the pure renderer**

Add beside `_read_quality_tty_line`:

```python
def render_quality_label_summary(
    prepared: dict[str, object], locale: object
) -> str:
    copy = quality_label_copy(locale)
    subject = prepared["subject"]
    if type(subject) is not dict:
        raise ValueError("invalid_quality_label_subject")
    risk_level = subject["risk_level"]
    if type(risk_level) is not str:
        raise ValueError("invalid_quality_label_subject")
    risk = copy.get(f"risk_{risk_level}")
    if risk is None:
        raise ValueError("invalid_quality_label_subject")
    return "\n".join(
        (
            copy["title"],
            f"{copy['candidate']}: {prepared['candidate_id']}",
            f"{copy['epoch']}: {prepared['epoch_id']}",
            f"{copy['target']}: {subject['target_identity']}",
            f"{copy['risk']}: {risk}",
            f"{copy['problem']}: {subject['problem_summary']}",
            f"{copy['proposal']}: {subject['proposal_summary']}",
            f"{copy['validation']}: {subject['validation_plan']}",
            f"{copy['digest']}: {prepared['subject_digest']}",
            "",
        )
    )
```

- [x] **Step 8: Replace preliminary JSON and fixed English prompts**

In `cmd_quality_label`, replace the preliminary `write_json_stdout(...)` and
answer/confirmation block with:

```python
copy = quality_label_copy(args.locale)
sys.stdout.write(
    render_quality_label_summary(prepared, args.locale)
)
sys.stdout.flush()
answers = {
    "evaluation_worthy": parse_quality_yes_no(
        _read_quality_tty_line(
            copy["evaluation_worthy_prompt"], 3
        )
    ),
    "target_correct": parse_quality_yes_no(
        _read_quality_tty_line(copy["target_correct_prompt"], 3)
    ),
    "external_content_adoption": parse_quality_yes_no(
        _read_quality_tty_line(
            copy["external_content_adoption_prompt"], 3
        )
    ),
}
expected_confirmation = (
    f"{candidate_display_id}@{prepared['subject_digest']}"
)
confirmation = _read_quality_tty_line(
    copy["confirmation_prompt"].format(
        confirmation=expected_confirmation
    ),
    128,
)
```

Keep `validate_quality_confirmation(...)` and `commit_quality_label(...)` as
the authorities in their existing order. Immediately before the existing final
`write_json_stdout(result)`, add:

```python
sys.stdout.flush()
```

- [x] **Step 9: Run focused tests and inspect help**

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_quality_gate.py' -v
/usr/bin/python3 -I skills/skill-evolver/scripts/evolver.py \
  quality-label --help
```

Expected: tests report `OK`; help shows `[--locale {ko,en}]` and no judgment
flags.

- [x] **Step 10: Document and commit Task 1**

Add beside the existing user-only `quality-label` wording in
`skills/skill-evolver/SKILL.md` and `README.md`:

```markdown
`quality-label` accepts `--locale {ko,en}` and defaults to Korean. The locale
changes only the human summary, risk name, prompts, and confirmation
instruction. Answers remain exact lowercase `yes` or `no`, confirmation remains
`C-NNN@<full-sealed-subject-digest>`, and the successful final line remains the
canonical English-keyed label JSON. The command does not print a preliminary
raw candidate JSON object.
```

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_quality_gate.py' -v
/usr/bin/python3 -I -m py_compile \
  skills/skill-evolver/scripts/evolver.py
git diff --check
git add \
  skills/skill-evolver/tests/test_quality_gate.py \
  skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/SKILL.md README.md
git commit -m "feat(skill-evolver): localize quality label terminal"
```

Expected: tests and compile succeed, diff check is silent, and only the four
listed paths enter the commit.

---

### Task 2: Direct-user-language candidate authoring policy

**Files:**

- Modify: `skills/skill-evolver/tests/test_review.py`
- Modify: `skills/skill-evolver/references/improvement-policy.md`
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `skills/skill-evolver/SKILL.md`
- Modify: `README.md`

**Interfaces:**

- Consumes: `load_improvement_policy(runtime) -> bytes`,
  `REVIEW_RESULT_SCHEMA_INSTRUCTIONS`, and
  `candidate_fingerprint(target_identity, problem_category, target_locator,
  proposal_intent) -> str`.
- Produces: stronger fixed Review instructions; no Python API or schema change.
- Preserves: canonical enums and target identity, canonical-English
  `target_locator`/`proposal_intent`, and the current SHA-256 field set.

- [x] **Step 1: Write failing policy and instruction assertions**

Extend
`ReviewRuntimeContractTests.test_fixed_runtime_reference_and_policy_are_bounded`
with:

```python
for expected in (
    b"final record in envelope order",
    b"whose `source_kind` is",
    b"`user_direct`.",
    b"`verification_failure` uses `tool_output`",
    b"quoted or pasted content",
    b"no such record exists",
    b"its language is",
    b"ambiguous, use Korean.",
    b"target_locator",
    b"proposal_intent",
    b"canonical English",
):
    self.assertIn(expected, policy)

instructions = self.runtime.REVIEW_RESULT_SCHEMA_INSTRUCTIONS[
    "instructions"
]
for expected in (
    (
        "Choose the candidate authoring language independently of the "
        "strong-evidence record: use the final envelope record with "
        "evidence_eligible true and source_kind user_direct."
    ),
    (
        "This also applies when verification_failure uses tool_output "
        "as its strong evidence."
    ),
    (
        "When no such record exists or that language is ambiguous, "
        "use Korean."
    ),
    (
        "Keep target_locator and proposal_intent concise canonical "
        "English because candidate_fingerprint hashes them."
    ),
):
    self.assertIn(expected, instructions)
```

- [x] **Step 2: Pin the fingerprint field set and algorithm**

Extend
`CandidateIdentityTests.test_fingerprint_is_nfkc_casefolded_and_field_bound`
after `fingerprint_source` is assigned:

```python
self.assertEqual(
    first,
    self.runtime.sha256_json(
        {
            "schema_version": 1,
            "target_identity": "user-skill:Example",
            "problem_category": "verification",
            "target_locator": "completion claim",
            "proposal_intent": "require fresh evidence",
        }
    ),
)
for human_field in (
    "problem_summary",
    "proposal_summary",
    "validation_plan",
    "evidence",
):
    self.assertNotIn(human_field, fingerprint_source)
```

- [x] **Step 3: Run Review tests and verify RED**

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_review.py' -v
```

Expected: language-policy assertions fail; fingerprint assertions pass.

- [x] **Step 4: Add exact language rules to the policy**

Insert before the final mutation prohibition in
`skills/skill-evolver/references/improvement-policy.md`:

```markdown
Write `problem_summary`, `proposal_summary`, `validation_plan`, and every
evidence `summary` in the selected candidate authoring language. Select it
independently of the strong-evidence record by using the final evidence-eligible
`user_direct` record in envelope order. This includes `verification_failure`,
whose strong evidence is `tool_output`. Ignore quoted or pasted content when
selecting language. When no such record exists or its language is ambiguous,
use Korean.

Keep enum values and `target_identity` in their existing canonical forms. Keep
`target_locator` and `proposal_intent` concise canonical English because they
participate in `candidate_fingerprint()` and must not split equivalent
improvements by display language.
```

- [x] **Step 5: Add the bounded rules to fixed result instructions**

Append these strings to the `instructions` list in
`REVIEW_RESULT_SCHEMA_INSTRUCTIONS`:

```python
(
    "Write problem_summary, proposal_summary, validation_plan, and "
    "every evidence summary in the candidate authoring language."
),
(
    "Choose the candidate authoring language independently of the "
    "strong-evidence record: use the final envelope record with "
    "evidence_eligible true and source_kind user_direct."
),
(
    "This also applies when verification_failure uses tool_output "
    "as its strong evidence."
),
(
    "Infer the language from the user's own request or correction, "
    "not quoted or pasted content."
),
(
    "When no such record exists or that language is ambiguous, use "
    "Korean."
),
(
    "Keep target_locator and proposal_intent concise canonical "
    "English because candidate_fingerprint hashes them."
),
```

Do not alter `result_shape`, candidate validation, or runtime translation.

- [x] **Step 6: Run Review and quality tests**

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_review.py' -v
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_quality_gate.py' -v
```

Expected: both modules report `OK`. Existing candidate `C-001` remains valid
with its already sealed English subject.

- [x] **Step 7: Document authoring language versus display locale**

Add to the Review sections of `skills/skill-evolver/SKILL.md` and `README.md`:

```markdown
For new candidates, human-facing problem, proposal, validation, and evidence
summaries use the language of the final evidence-eligible `user_direct` record
in envelope order, independently of which record supplies strong evidence.
This includes `verification_failure`, whose evidence is `tool_output`. Quoted
or pasted content does not select the language; a missing or ambiguous source
falls back to Korean. Canonical enum values and fingerprint-bearing
`target_locator` and `proposal_intent` remain English. This authoring rule is
independent of the later `quality-label --locale` display option and does not
rewrite existing candidates.
```

- [x] **Step 8: Run complete verification**

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_*.py' -v
/usr/bin/python3 -I -m py_compile \
  skills/skill-evolver/scripts/evolver.py
/usr/bin/python3 -m json.tool \
  .codex-plugin/plugin.json >/dev/null
/usr/bin/python3 -m json.tool \
  skills/skill-evolver/references/runtime.json >/dev/null
rg -n \
  'password|secret|token|authorization|cookie|private key' \
  skills/skill-evolver/references/improvement-policy.md \
  skills/skill-evolver/SKILL.md README.md
git diff --check
```

Expected: full suite reports `OK` with only expected skips; compile and JSON
checks succeed; privacy scan shows only deliberate security-policy wording and
no credential value; diff check is silent.

- [x] **Step 9: Commit Task 2**

```bash
git add \
  skills/skill-evolver/tests/test_review.py \
  skills/skill-evolver/references/improvement-policy.md \
  skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/SKILL.md README.md
git commit -m "feat(skill-evolver): preserve user language in candidates"
```

---

### Task 3: Release 0.1.4 after the Q-003 hard gate

**Files:**

- Modify: `skills/skill-evolver/tests/test_capture.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `.codex-plugin/plugin.json`
- Modify: `skills/skill-evolver/references/runtime.json`
- Modify: `README.md`

**Interfaces:**

- Consumes: a committed Q-003 terminal PASS report generated by the unchanged
  installed 0.1.3 runtime.
- Produces: source release `skill-evolver 0.1.4` with matching plugin/runtime
  versions.
- Gate: do not start while `quality-status` is `AWAITING_LABELS`,
  `COLLECTING`, or a non-PASS terminal decision.

- [x] **Step 1: Check Q-003 read-only**

Run:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.3/skills/skill-evolver/scripts/evolver.py \
  quality-status \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json
```

Expected before the user finishes `C-001`: JSON contains
`"status":"AWAITING_LABELS"` and `"missing_labels":["C-001"]`. Stop this task
there. The agent does not run `quality-label`. Resume only after the user
completes the external-terminal attestation and the existing Phase 5 workflow
commits a Q-003 terminal report with decision `PASS`.

Rechecked 2026-08-04 against the unchanged installed 0.1.3 runtime: Q-003 is
still `AWAITING_LABELS`, `attested_label_count=0`, and `missing_labels` is
exactly `["C-001"]`. No Task 3 release file has been changed.

- [ ] **Step 2: Write failing 0.1.4 package assertions**

In `ProductionSurfaceTests.test_only_main_stop_is_an_automatic_writer`, change
only the three version expectations:

```python
self.assertEqual(manifest["version"], "0.1.4")
self.assertEqual(runtime["version"], "0.1.4")
self.assertEqual(
    load_runtime().VERSION,
    "skill-evolver 0.1.4",
)
```

- [ ] **Step 3: Run capture tests and verify RED**

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests \
  -p 'test_capture.py' -v
```

Expected: FAIL only at the three new 0.1.4 expectations.

- [ ] **Step 4: Bump fixed version sources together**

Change `skills/skill-evolver/scripts/evolver.py`:

```python
VERSION = "skill-evolver 0.1.4"
```

Change `.codex-plugin/plugin.json` and
`skills/skill-evolver/references/runtime.json`:

```json
"version": "0.1.4"
```

Change the exact runtime version comparison inside `load_review_runtime()` from
`0.1.3` to `0.1.4`. Add a README release note stating that Korean is the
default label display, English uses `--locale en`, and new human summaries
preserve the direct user's language.

- [ ] **Step 5: Verify and commit the release**

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_*.py' -v
/usr/bin/python3 -I -m py_compile \
  skills/skill-evolver/scripts/evolver.py
/usr/bin/python3 -m json.tool .codex-plugin/plugin.json >/dev/null
/usr/bin/python3 -m json.tool \
  skills/skill-evolver/references/runtime.json >/dev/null
/usr/bin/python3 -I skills/skill-evolver/scripts/evolver.py --version
git diff --check
git add \
  skills/skill-evolver/tests/test_capture.py \
  skills/skill-evolver/scripts/evolver.py \
  .codex-plugin/plugin.json \
  skills/skill-evolver/references/runtime.json README.md
git commit -m "chore(skill-evolver): release localized labels 0.1.4"
```

Expected: full suite reports `OK`; executable prints
`skill-evolver 0.1.4`; JSON and compile checks succeed; diff check is silent.

---

### Task 4: Install and accept 0.1.4 without fabricating a label

**Files:**

- Verify only: source tree and
  `/Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4`
- Modify: none

**Interfaces:**

- Consumes: committed release 0.1.4 and committed Q-003 terminal PASS report.
- Produces: installed source/cache parity and CLI exposure evidence.
- Preserves: user-only ownership of every real `quality-label` attestation.

- [ ] **Step 1: Install the released plugin**

After filesystem approval, run:

```bash
codex plugin add skill-evolver@skill-evolver-dev --json
```

Expected: successful JSON names `skill-evolver`, source
`skill-evolver-dev`, and version `0.1.4`.

- [ ] **Step 2: Verify version and source/cache parity**

Run:

```bash
codex plugin list --json
shasum -a 256 \
  skills/skill-evolver/scripts/evolver.py \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/scripts/evolver.py
shasum -a 256 \
  skills/skill-evolver/references/improvement-policy.md \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/references/improvement-policy.md
```

Expected: plugin list reports 0.1.4 and each source/cache pair has identical
SHA-256 values.

- [ ] **Step 3: Verify CLI exposure without judgments**

Run:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/scripts/evolver.py \
  quality-label --help
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/scripts/evolver.py \
  --version
```

Expected: help shows `[--locale {ko,en}]` and version prints
`skill-evolver 0.1.4`.

- [ ] **Step 4: Reserve real visual acceptance for the next sealed candidate**

When a later epoch naturally has a missing label, provide the user with fully
expanded default-locale and `--locale en` commands. The user chooses and runs
one, makes every judgment, and copies the exact confirmation. Accept the result
only when the chosen human-language block is shown and the terminal ends with
one canonical JSON line containing `attested_at`. Do not create a candidate,
duplicate a label, invoke the command, or choose answers merely to exercise the
presentation.

- [ ] **Step 5: Record bounded acceptance evidence**

Record only installed version, matching source/cache SHA-256, help containing
both locale choices, and the user's later confirmation that the real display
was readable. Do not copy candidate text, answers, terminal transcript content,
or private paths into a public release report.

---

## Completion Evidence

- Task 1 and Task 2 commits exist and the complete repository suite passes.
- Installed 0.1.3 remains byte-identical until Q-003 terminal PASS is committed.
- Task 3 begins only after that hard gate and all fixed versions agree on 0.1.4.
- Task 4 proves installed source/cache parity and parser exposure.
- No agent invokes `quality-label` or supplies a judgment answer.
- Real visual acceptance comes only from a naturally occurring user-owned
  label, never synthetic production evidence.
