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

Run `python scripts/evaluate_person_b.py --check-fixtures` from the repository
root. This checks contracts without making model calls; it does not measure
model accuracy. See `docs/PERSON_B_EVALUATION.md` for collection and scoring.
