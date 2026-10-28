"""Check Person B annotations or score recorded outputs; no provider calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.stage2c.evaluation import EVALUATION_MODES, score_predictions, validate_fixtures


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def code_provenance():
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True)
    status = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True, check=True)
    return {'code_head': revision.stdout.strip(), 'working_tree_dirty': bool(status.stdout.strip())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, action='append', help='May be repeated; defaults to both Person B datasets.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-fixtures', action='store_true')
    mode.add_argument('--predictions', type=Path, help='JSONL: case_id, turn_index (1-based), output (provider JSON).')
    parser.add_argument('--output', type=Path, help='Write a new JSON report. An existing report will not be overwritten.')
    parser.add_argument('--mode', choices=sorted(EVALUATION_MODES), help='Explicit override; otherwise use each fixture mode (old files use f7_snapshot).')
    args = parser.parse_args(argv)
    paths = args.fixtures or [ROOT / 'tests/fixtures/person_b_semantic_cases.jsonl',
                              ROOT / 'tests/fixtures/person_b_multiturn_cases.jsonl']
    try:
        cases = [case for path in paths for case in read_jsonl(path)]
        fixture_report = validate_fixtures(cases, mode=args.mode)
        if args.check_fixtures:
            report = fixture_report
        else:
            report = score_predictions(cases, read_jsonl(args.predictions), mode=args.mode)
        report.update(code_provenance())
        report['fixtures_sha256'] = hashlib.sha256(
            json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        if args.predictions:
            report['predictions_sha256'] = hashlib.sha256(args.predictions.read_bytes()).hexdigest()
        serialized = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        if args.output:
            with args.output.open('x', encoding='utf-8') as handle:
                handle.write(serialized)
        else:
            print(serialized, end='')
        if args.predictions and report['missing_turns']:
            return 2
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f'Evaluation failed: {type(error).__name__}. Check file paths, identifiers and fixture contracts.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
