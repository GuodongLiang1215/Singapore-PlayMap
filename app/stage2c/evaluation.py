"""Offline interpretation scoring against independent, versioned annotations."""
from __future__ import annotations

import json
import hashlib
from collections import Counter

from app.llm_connect.errors import LLMConnectionError
from .command_contract import pending_to_commands
from .engine import ChatError, merge_preferences, model_context, validate_actions
from .models import ChatRequest, Draft, Preferences
from .operation_adapter import OperationFormatError, parse_interpretation
from .prompts import INTERPRET, PROMPT_VERSION
from .semantic import SemanticConsistencyError, validate_segment_command_consistency
from .wire_schema import WIRE_VERSION, interpretation_schema


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _segments(rows, *, hard_only=False):
    fields = ('type', 'name', 'value', 'scope', 'hardness', 'source')
    return Counter(_canonical({key: row.get(key) for key in fields}) for row in rows
                   if not hard_only or row.get('type') == 'constraint' and row.get('hardness') == 'hard')


def _commands(parsed, draft):
    envelope = pending_to_commands(parsed, draft)
    commands = envelope['commands']
    for rows in commands.values():
        for row in rows:
            row.pop('quote', None)
    return {**commands, 'max_walk_minutes': parsed.max_walk_minutes,
            **{name: getattr(parsed, name) for name in (
                'preferred_add', 'preferred_remove', 'excluded_add', 'excluded_remove',
                'notes_add', 'notes_remove')}}


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def score_predictions(cases, predictions):
    """Score recorded provider envelopes without making any network requests.

    Each multi-turn case carries controlled Draft snapshots; predicted Preferences
    are replayed across turns. This measures interpretation and ledger behaviour,
    not retrieval, routing or full application adoption. Missing predictions count
    against coverage and schema success; no output is never a passing experiment.
    """
    expected = {}
    for case in cases:
        for index, turn in enumerate(case['turns'], 1):
            key = (case['case_id'], index)
            if key in expected:
                raise ValueError('Duplicate case identifier')
            expected[key] = turn
    received = {}
    for prediction in predictions:
        key = (prediction['case_id'], prediction['turn_index'])
        if key not in expected:
            raise ValueError('Unknown prediction identifier')
        if key in received:
            raise ValueError('Duplicate prediction identifier')
        received[key] = prediction

    counts = Counter()
    results = []
    for case in cases:
        preferences = Preferences.model_validate(case.get('preferences', {}))
        for index, turn in enumerate(case['turns'], 1):
            key = (case['case_id'], index)
            draft = Draft.model_validate(turn.get('draft', case.get('draft', {})))
            gold, _ = parse_interpretation(turn['gold'], draft, turn_index=index)
            gold_rows = [s.model_dump() for s in gold.semantic_segments]
            gold_segments = _segments(gold_rows)
            gold_hard = _segments(gold_rows, hard_only=True)
            counts['gold_segments'] += gold_segments.total()
            counts['gold_hard'] += gold_hard.total()
            has_targets = any(getattr(action, 'target_id', None) for action in gold.actions)
            counts['target_turns'] += has_targets
            counts['ledger_turns'] += 'gold_active_constraints' in turn
            row = {'case_id': key[0], 'turn_index': index, 'scale': case['scale'],
                   'language': case['language'], 'cohort': case.get('cohort', 'standard'),
                   'errors': [], 'schema_pass': False,
                   'gold_segment_count': gold_segments.total(), 'predicted_segment_count': 0,
                   'segment_matches': 0, 'gold_hard_count': gold_hard.total(), 'hard_matches': 0}
            prediction = received.get(key)
            row['prediction_received'] = prediction is not None
            if prediction is None:
                row['errors'].append('missing_prediction')
                results.append(row)
                continue
            raw = prediction.get('output')
            try:
                if not isinstance(raw, dict) or not {'acknowledgement', 'semantic_segments', 'commands'} <= raw.keys():
                    raise ValueError('Missing provider envelope')
                parsed, _ = parse_interpretation(raw, draft, turn_index=index)
            except (OperationFormatError, ValueError):
                row['errors'].append('schema_invalid')
                results.append(row)
                continue
            counts['schema_pass'] += 1
            row['schema_pass'] = True
            predicted_rows = [s.model_dump() for s in parsed.semantic_segments]
            predicted_segments = _segments(predicted_rows)
            counts['predicted_segments'] += predicted_segments.total()
            counts['segment_matches'] += (gold_segments & predicted_segments).total()
            counts['hard_matches'] += (gold_hard & _segments(predicted_rows, hard_only=True)).total()
            row['predicted_segment_count'] = predicted_segments.total()
            row['segment_matches'] = (gold_segments & predicted_segments).total()
            row['hard_matches'] = (gold_hard & _segments(predicted_rows, hard_only=True)).total()
            row['segments_exact'] = predicted_segments == gold_segments
            row['commands_exact'] = _commands(parsed, draft) == _commands(gold, draft)
            counts['commands_exact'] += row['commands_exact']
            if has_targets:
                actual_targets = [(a.op, getattr(a, 'target_id', None)) for a in parsed.actions]
                gold_targets = [(a.op, getattr(a, 'target_id', None)) for a in gold.actions]
                counts['targets_exact'] += actual_targets == gold_targets
            row['clarification_correct'] = bool(parsed.questions) == bool(gold.questions)
            counts['clarification_correct'] += row['clarification_correct']
            claims = sum(s.name in {'must_be_open', 'opening_hours', 'last_admission'} and
                         s.status == 'satisfied' for s in parsed.semantic_segments)
            counts['unproven_opening_status_claims'] += claims
            if claims:
                row['errors'].append('unproven_opening_status_claim')
            body = ChatRequest(session_id='0' * 32, revision=index - 1, message=turn['message'],
                               draft=draft, preferences=preferences, consent=True)
            try:
                validate_actions(body, parsed)
                validate_segment_command_consistency(parsed.semantic_segments, parsed, draft=draft)
                counts['consistency_pass'] += 1
                preferences = merge_preferences(preferences, parsed, turn_index=index, draft=draft)
            except (ChatError, SemanticConsistencyError, ValueError):
                row['errors'].append('semantic_or_evidence_rejected')
            if 'gold_active_constraints' in turn:
                fields = ('name', 'value', 'scope', 'hardness', 'status')
                actual = Counter(_canonical({f: getattr(c, f) for f in fields})
                                 for c in preferences.active_constraints)
                wanted = Counter(_canonical({f: c.get(f) for f in fields})
                                 for c in turn['gold_active_constraints'])
                row['ledger_exact'] = actual == wanted
                counts['ledger_exact'] += row['ledger_exact']
            results.append(row)

    total = len(expected)
    metrics = {
        'schema_pass_rate': _ratio(counts['schema_pass'], total),
        'segment_micro_precision': _ratio(counts['segment_matches'], counts['predicted_segments']),
        'segment_micro_recall': _ratio(counts['segment_matches'], counts['gold_segments']) if received else None,
        'segment_micro_f1': _ratio(2 * counts['segment_matches'], counts['predicted_segments'] + counts['gold_segments']) if received else None,
        'hard_constraint_recall': _ratio(counts['hard_matches'], counts['gold_hard']) if received else None,
        'command_exact_rate': _ratio(counts['commands_exact'], total),
        'segment_command_consistency_rate': _ratio(counts['consistency_pass'], counts['schema_pass']),
        'clarification_decision_accuracy': _ratio(counts['clarification_correct'], total),
        'target_sequence_accuracy': _ratio(counts['targets_exact'], counts['target_turns']),
        'ledger_exact_rate': _ratio(counts['ledger_exact'], counts['ledger_turns']),
        'unproven_opening_status_claims': counts['unproven_opening_status_claims'],
    }
    eligible = all(c.get('annotation_status') == 'human_reviewed' for c in cases)
    acceptance = 'requires_manual_and_end_to_end_review'
    if len(received) < total:
        acceptance = 'incomplete_predictions'
    elif not eligible:
        acceptance = 'not_eligible_unreviewed_labels'
    groups = {}
    for scale in range(1, 6):
        groups[f'single_scale_{scale}'] = [r for r in results if r['cohort'] != 'multiturn' and r['scale'] == scale]
    for language in ('zh', 'en', 'mixed'):
        groups[f'single_standard_{language}'] = [r for r in results if r['cohort'] == 'standard' and r['language'] == language]
    groups['single_boundary'] = [r for r in results if r['cohort'] == 'boundary']
    groups['multiturn'] = [r for r in results if r['cohort'] == 'multiturn']
    return {
        'evaluation_scope': 'controlled_interpretation_and_ledger',
        'prompt_version': PROMPT_VERSION, 'wire_version': WIRE_VERSION,
        'expected_turns': total, 'received_turns': len(received),
        'missing_turns': total - len(received), 'acceptance': acceptance,
        'metrics': metrics, 'groups': {name: _summarize_group(rows) for name, rows in groups.items() if rows},
        'cases': results,
        'not_measured': ['retrieval_relevance', 'route_feasibility', 'silent_draft_mutation',
                         'takeaway_adoption', 'natural_language_false_claims'],
    }


def _summarize_group(rows):
    received = sum(row['prediction_received'] for row in rows)
    matches = sum(row['segment_matches'] for row in rows)
    segment_count = sum(row['gold_segment_count'] + row['predicted_segment_count'] for row in rows)
    return {'expected_turns': len(rows), 'received_turns': received,
            'schema_pass_rate': _ratio(sum(row['schema_pass'] for row in rows), len(rows)),
            'segment_micro_f1': _ratio(2 * matches, segment_count) if received else None,
            'hard_constraint_recall': _ratio(sum(row['hard_matches'] for row in rows),
                                             sum(row['gold_hard_count'] for row in rows)) if received else None}


def validate_fixtures(cases):
    """Check annotations against the public parser, evidence and ledger contracts.

    This is a dataset integrity check, never a model experiment or an accuracy
    result. Semantic correctness still requires independent human review.
    """
    seen = set()
    turns = 0
    for case in cases:
        if case['case_id'] in seen:
            raise ValueError('Duplicate case identifier')
        seen.add(case['case_id'])
        preferences = Preferences.model_validate(case.get('preferences', {}))
        for index, turn in enumerate(case['turns'], 1):
            turns += 1
            draft = Draft.model_validate(turn.get('draft', case.get('draft', {})))
            parsed, _ = parse_interpretation(turn['gold'], draft, turn_index=index)
            body = ChatRequest(session_id='0' * 32, revision=index - 1, message=turn['message'],
                               draft=draft, preferences=preferences, consent=True)
            try:
                validate_actions(body, parsed)
                validate_segment_command_consistency(parsed.semantic_segments, parsed, draft=draft)
                preferences = merge_preferences(preferences, parsed, turn_index=index, draft=draft)
            except (ChatError, SemanticConsistencyError, ValueError) as error:
                raise ValueError(f"Invalid annotation: {case['case_id']} turn {index}") from error
            if 'gold_active_constraints' in turn:
                fields = ('name', 'value', 'scope', 'hardness', 'status')
                actual = Counter(_canonical({f: getattr(c, f) for f in fields}) for c in preferences.active_constraints)
                wanted = Counter(_canonical({f: c.get(f) for f in fields}) for c in turn['gold_active_constraints'])
                if actual != wanted:
                    raise ValueError(f"Ledger annotation mismatch: {case['case_id']} turn {index}")
    return {'kind': 'fixture_contract_check', 'cases': len(cases), 'turns': turns,
            'model_calls': 0, 'human_review_required': any(c.get('annotation_status') != 'human_reviewed' for c in cases)}


def iter_predictions(cases, client, *, max_calls, pace=None):
    """Collect one interpreter output per controlled turn with no retries.

    Only an explicit caller supplies a provider. Yield each completed call so the
    CLI can flush it before the next request; interruptions cannot lose earlier
    outputs. Gold annotations are used for preflight only and never sent.
    """
    if type(max_calls) is not int or max_calls < 1:
        raise ValueError('max_calls must be a positive integer')
    validate_fixtures(cases)
    fixture_hash = hashlib.sha256(_canonical(cases).encode('utf-8')).hexdigest()
    prompt_hash = hashlib.sha256(INTERPRET.encode('utf-8')).hexdigest()
    calls = 0
    for case in cases:
        preferences = Preferences.model_validate(case.get('preferences', {}))
        for index, turn in enumerate(case['turns'], 1):
            if calls >= max_calls:
                return
            draft = Draft.model_validate(turn.get('draft', case.get('draft', {})))
            body = ChatRequest(session_id='0' * 32, revision=index - 1, message=turn['message'],
                               draft=draft, preferences=preferences, consent=True)
            payload = {'user_message': body.message, 'current_draft': model_context(body),
                       'preferences': preferences.model_dump(mode='json'), 'pending_request': None,
                       'singapore_now': case.get('singapore_now', '2026-10-27T09:00:00+08:00')}
            if pace is not None:
                pace.wait(lambda: False)
            calls += 1
            row = {'case_id': case['case_id'], 'turn_index': index,
                   'prompt_version': PROMPT_VERSION, 'wire_version': WIRE_VERSION,
                   'fixtures_sha256': fixture_hash, 'prompt_sha256': prompt_hash}
            try:
                result = client.generate_json(instruction=INTERPRET, payload=payload, schema=interpretation_schema())
                row['output'] = result['data']
                row['metadata'] = result.get('metadata', {})
            except LLMConnectionError as error:
                row['provider_error_code'] = error.code
                yield row
                if error.code in {'KEY_INVALID', 'ACCESS_DENIED', 'MODEL_NOT_AVAILABLE', 'BAD_REQUEST', 'RATE_LIMIT'}:
                    return
                continue
            try:
                parsed, _ = parse_interpretation(row['output'], draft, turn_index=index)
                validate_actions(body, parsed)
                validate_segment_command_consistency(parsed.semantic_segments, parsed, draft=draft)
                preferences = merge_preferences(preferences, parsed, turn_index=index, draft=draft)
            except (OperationFormatError, ChatError, SemanticConsistencyError, ValueError):
                pass
            yield row
