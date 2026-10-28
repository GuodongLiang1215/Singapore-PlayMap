"""Validate the reviewed CSV subset and prepare non-destructive revision candidates."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_CODE_HEAD = 'e516f3359f5e734b48394e1cb56887ab5b0a18c7'
DECISIONS = {'keep', 'revise', 'discuss'}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def _key(record):
    index = record.get('turn_index')
    if isinstance(index, str) and index.isdecimal():
        index = int(index)
    if not isinstance(record.get('case_id'), str) or type(index) is not int or index < 1:
        raise ValueError('Invalid review identifier')
    return record['case_id'], index


def _index_records(records):
    indexed = {}
    for record in records:
        key = _key(record)
        if key in indexed:
            raise ValueError('Duplicate review identifier')
        indexed[key] = record
    return indexed


def import_reviews(review_path, csv_path, fixture_paths, prediction_path, *, baseline_code_head):
    """Return candidates only; a confirmed textual review never creates new gold."""
    review_path, csv_path, prediction_path = map(Path, (review_path, csv_path, prediction_path))
    fixture_paths = [Path(path) for path in fixture_paths]
    with csv_path.open(encoding='utf-8-sig', newline='') as handle:
        csv_records = _index_records(csv.DictReader(handle))
    selected = {key: row for key, row in csv_records.items() if row.get('decision', '').strip()}
    for row in selected.values():
        if row['decision'] not in DECISIONS or not row.get('confirmed_at'):
            raise ValueError('Unconfirmed or unknown CSV decision')
    document = json.loads(review_path.read_text(encoding='utf-8-sig'))
    if document.get('schema') != 'ge5226-person-b-human-review-v1':
        raise ValueError('Unknown review schema')
    if document.get('code_head') != baseline_code_head:
        raise ValueError('Baseline code hash mismatch')
    cases = [case for path in fixture_paths for case in _read_jsonl(path)]
    fixture_hash = hashlib.sha256(_canonical(cases).encode('utf-8')).hexdigest()
    prediction_hash = hashlib.sha256(prediction_path.read_bytes()).hexdigest()
    if document.get('fixtures_sha256') != fixture_hash:
        raise ValueError('Fixture hash mismatch')
    if document.get('predictions_sha256') != prediction_hash:
        raise ValueError('Prediction hash mismatch')
    turns = {}
    case_ids = set()
    for case in cases:
        if case['case_id'] in case_ids:
            raise ValueError('Duplicate fixture case')
        case_ids.add(case['case_id'])
        for index, turn in enumerate(case['turns'], 1):
            turns[(case['case_id'], index)] = (case, turn)
    reviews = _index_records(document['reviews'])
    predictions = _index_records(_read_jsonl(prediction_path))
    if (set(csv_records) | set(reviews) | set(predictions)) - set(turns):
        raise ValueError('Unknown source identifier')
    if set(csv_records) != set(reviews):
        raise ValueError('CSV and JSON review identifiers disagree')
    candidates = []
    for key, csv_record in csv_records.items():
        record = reviews[key]
        review = record['review']
        if record.get('key', f'{key[0]}:{key[1]}') != f'{key[0]}:{key[1]}':
            raise ValueError('Review key mismatch')
        if review.get('decision', '') != csv_record.get('decision', ''):
            raise ValueError('CSV and JSON decisions disagree')
        if (review.get('confirmed_at') or '') != (csv_record.get('confirmed_at') or ''):
            raise ValueError('CSV and JSON confirmation disagree')
        case, turn = turns[key]
        if record.get('message') != turn['message'] or record.get('original_gold') != turn['gold']:
            raise ValueError('Review differs from original fixture')
        if 'message' in csv_record and csv_record['message'] != turn['message']:
            raise ValueError('CSV differs from original fixture')
        if 'prediction' in record and record['prediction'] != predictions.get(key):
            raise ValueError('Embedded prediction differs from recorded output')
        if key not in selected:
            continue
        proposed = review.get('proposed_gold_json')
        if isinstance(proposed, str) and proposed.strip():
            proposed = json.loads(proposed)
        if proposed and not isinstance(proposed, dict):
            raise ValueError('Proposed gold must be an object')
        candidates.append({
            'case_id': key[0], 'turn_index': key[1], 'decision': review['decision'],
            'language': case['language'], 'scale': case['scale'],
            'reviewer': review.get('confirmed_by', ''), 'confirmed_at': review['confirmed_at'],
            'model_revealed_at': review.get('model_revealed_at'),
            'rationale': review.get('rationale', ''), 'correction_notes': review.get('correction_notes', ''),
            'original_gold': deepcopy(turn['gold']), 'proposed_gold': proposed or None,
            'annotation_status': 'synthetic_unreviewed',
            'structured_annotation_status': 'proposed_requires_validation' if proposed else 'needs_structured_review',
        })
    summary = document.get('summary', {})
    if 'confirmed_turns' in summary and summary['confirmed_turns'] != len(candidates):
        raise ValueError('Review summary count mismatch')
    return {
        'schema': 'ge5226-person-b-f9-review-import-v1', 'dry_run': True,
        'baseline_code_head': baseline_code_head, 'fixtures_sha256': fixture_hash,
        'predictions_sha256': prediction_hash,
        'review_json_sha256': hashlib.sha256(review_path.read_bytes()).hexdigest(),
        'review_csv_sha256': hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        'expected_turns': len(turns), 'confirmed_review_turns': len(candidates),
        'unconfirmed_turns': len(turns) - len(candidates), 'automatic_gold_promotion': False,
        'evidence_scope': 'seen_development_review', 'candidates': candidates,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-json', type=Path, default=ROOT / '2026-10-27-person-b-human-review.json')
    parser.add_argument('--review-csv', type=Path, default=ROOT / '2026-10-27-person-b-review-summary.csv')
    parser.add_argument('--fixtures', type=Path, action='append')
    parser.add_argument('--predictions', type=Path, default=ROOT / 'reports/person_b_f7_20261027_predictions.jsonl')
    parser.add_argument('--baseline-code-head', default=BASELINE_CODE_HEAD)
    parser.add_argument('--output', type=Path, help='Explicitly write a new candidate manifest; never overwrite.')
    args = parser.parse_args(argv)
    paths = args.fixtures or [ROOT / 'tests/fixtures/person_b_semantic_cases.jsonl',
                              ROOT / 'tests/fixtures/person_b_multiturn_cases.jsonl']
    try:
        report = import_reviews(args.review_json, args.review_csv, paths, args.predictions,
                                baseline_code_head=args.baseline_code_head)
        if args.output:
            report['dry_run'] = False
            with args.output.open('x', encoding='utf-8') as handle:
                handle.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({'output': str(args.output), 'confirmed_review_turns': report['confirmed_review_turns'],
                              'automatic_gold_promotion': False}, ensure_ascii=False))
        else:
            summary = {key: value for key, value in report.items() if key != 'candidates'}
            summary['selected_ids'] = [f"{row['case_id']}:{row['turn_index']}" for row in report['candidates']]
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, TypeError, OSError):
        print('Import rejected: check hashes, baseline, identifiers and CSV/JSON decisions. No input was changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
