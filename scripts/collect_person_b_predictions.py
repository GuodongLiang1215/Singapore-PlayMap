"""Explicit, capped Gemini experiment on synthetic fixtures; may incur provider charges."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm_connect.config import load_settings
from app.llm_connect.errors import LLMConnectionError
from app.stage2c.engine import CallPacer, ChatGeminiREST
from app.stage2c.evaluation import iter_predictions, validate_fixtures
from scripts.evaluate_person_b import read_jsonl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, action='append')
    parser.add_argument('--max-calls', type=int, required=True, help='Hard upper bound on billable attempts; no retries.')
    parser.add_argument('--output', type=Path, required=True, help='New JSONL file; will never overwrite an existing run.')
    args = parser.parse_args()
    if args.max_calls < 1:
        parser.error('--max-calls must be positive')
    paths = args.fixtures or [ROOT / 'tests/fixtures/person_b_semantic_cases.jsonl',
                              ROOT / 'tests/fixtures/person_b_multiturn_cases.jsonl']
    cases = [case for path in paths for case in read_jsonl(path)]
    validate_fixtures(cases)
    settings = load_settings(ROOT)
    count = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        for row in iter_predictions(cases, ChatGeminiREST(settings), max_calls=args.max_calls, pace=CallPacer()):
            row['model'] = settings.model
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
            handle.flush()
            count += 1
            print(f'Completed {count}/{args.max_calls} attempts', flush=True)
    print('Saved recorded outputs. Run evaluate_person_b.py to score them.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Interrupted. Completed outputs remain saved; no retries were scheduled.', file=sys.stderr)
        raise SystemExit(130)
    except LLMConnectionError as error:
        print(f'Collection stopped before generation: {error.code}. Configure this project locally; never paste credentials into chat.', file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f'Collection stopped: {type(error).__name__}. Completed outputs remain saved; check paths and local provider configuration.', file=sys.stderr)
        raise SystemExit(1)
