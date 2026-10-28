# Person B evaluation fixtures

These are synthetic development annotations, not human-reviewed gold labels or
production chat logs. All map coordinates belong to synthetic public test
snapshots. Never replace these files with real users' conversations.

- `person_b_semantic_cases.jsonl`: 50 single-turn cases, 10 at each of five scales.
  The standard cohort has 20 Chinese, 20 English and 5 mixed-language cases.
  Five additional boundary cases have their actual language recorded separately;
  boundary is a test cohort, not a fourth language.
- `person_b_multiturn_cases.jsonl`: 10 conversations, 21 turns; includes fixed
  snapshot reordering to check whether constraints follow visit identity.

Every case is `synthetic_unreviewed`. An independent reviewer must inspect the
message, expected segments, commands and ledger state before changing a case to
`human_reviewed`. Do not mark cases reviewed merely because contract checks pass.

F9 adds separate development files and preserves both original F7 files:

- `person_b_f9_semantic_cases.jsonl`: 21 single-turn revision candidates,
  explicitly using `f9_snapshot`; interpretation success does not adopt state.
- `person_b_f9_multiturn_cases.jsonl`: 3 groups, 7 turns, using
  `f9_confirmed_state`; includes fixed catalogue facts, candidate choices and
  `confirm` events. These test the shared local reducer without model selection
  or map calls. Additional cancellation/Pending cases are in the public API tests.
- `person_b_f9_review_manifest.json`: links the 25 CSV-confirmed textual reviews
  to original and candidate structured annotations, source hashes and rationale.
  The other 3 turns in the selected multi-turn groups are unreviewed context.
  All 28 candidate turns remain `synthetic_unreviewed`; accepting a product rule
  does not imply that the reviewer checked its future exact JSON representation.
- `person_b_f9_holdout_design.json`: the planned independent 50-single/10-group,
  71-turn coverage. It is an unfrozen design with zero authorized model calls;
  no held-out gold labels or model results are claimed.

Case fields and why they exist:

| Field | Purpose |
|---|---|
| `case_id` | Joins predictions to annotations; duplicates are errors. |
| `scale` | Keeps each of the five problem scales distinguishable in analysis. |
| `language` | Records the actual utterance language for language comparisons. |
| `cohort` | Separates standard, boundary and multi-turn cases. |
| `annotation_status` | Prevents synthetic labels from being presented as validated gold. |
| `draft` | Defines the adopted snapshot required to resolve station ordinals. |
| `preferences` | Optional initial memory, when a case needs a starting constraint. |
| `turns[].message` | The only new user instruction sent to the model. |
| `turns[].gold` | Independent expected provider envelope; never sent to Gemini. |
| `turns[].draft` | Optional controlled snapshot after an adopted move; prevents old positions from being reused. |
| `turns[].gold_active_constraints` | Expected reduced memory after a turn. |
| `evaluation_mode` | Separates historical replay, current parsing snapshots and actual confirmation-state replay. |
| `turns[].event` | Fixes `confirm`, `cancel` or `continue_pending` before predictions; only confirm advances adopted state. |
| `catalogue`, `turns[].slots`, `turns[].choices` | Fixed public test facts and user choices; no gold Draft is injected as a retrieval or adoption result. |
| `turns[].new_visit_ids` | Optional deterministic IDs keyed by action index, for explicit expected identity checks. |
| `turns[].gold_adopted_draft` | Optional comparison target after the local event; never used to advance actual state. |
| `turns[].annotation_status`, `text_review_confirmed`, `structured_annotation_status` | Distinguish a reviewed textual comment from an independently reviewed exact F9 annotation. |

Run `python scripts/evaluate_person_b.py --check-fixtures` from the repository
root. This checks contracts without making model calls; it does not measure
model accuracy. See `docs/PERSON_B_EVALUATION.md` for collection and scoring.

Check the current revision candidates without a provider:

```powershell
.venv/Scripts/python.exe scripts/evaluate_person_b.py --check-fixtures --fixtures tests/fixtures/person_b_f9_semantic_cases.jsonl --fixtures tests/fixtures/person_b_f9_multiturn_cases.jsonl
```
