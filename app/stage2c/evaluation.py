"""Offline interpretation scoring against independent, versioned annotations."""
from __future__ import annotations

import json
import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path

from app.llm_connect.errors import LLMConnectionError
from .command_contract import pending_to_commands
from .engine import ChatError, assemble, merge_preferences, model_context, validate_actions
from .models import ActiveConstraint, ChatRequest, Draft, Pending, Preferences
from .operation_adapter import OperationFormatError, parse_interpretation
from .prompts import INTERPRET, PROMPT_VERSION
from .semantic import SemanticConsistencyError, bind_constraint_scopes, validate_segment_command_consistency
from .wire_schema import WIRE_VERSION, interpretation_schema

SCORER_VERSION = 'person-b-f9-v2'
EVALUATION_MODES = {'f7_snapshot', 'f9_snapshot', 'f9_confirmed_state'}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _normalize_instant(value):
    if isinstance(value, str):
        try:
            instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if instant.tzinfo is not None:
                return instant.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return value


def _segments(rows, *, hard_only=False):
    fields = ('type', 'name', 'value', 'scope', 'hardness', 'source')
    return Counter(_canonical({key: row.get(key) for key in fields}) for row in rows
                   if not hard_only or row.get('type') == 'constraint' and row.get('hardness') == 'hard')


def _evaluation_mode(case, override=None):
    mode = override or case.get('evaluation_mode', 'f7_snapshot')
    if mode not in EVALUATION_MODES:
        raise ValueError('Unknown evaluation mode')
    return mode


def _parse(raw, draft, index, mode):
    if mode == 'f7_snapshot':
        return parse_interpretation(raw, draft, turn_index=index)
    from .operation_adapter import parse_provider_interpretation
    return parse_provider_interpretation(raw, draft, turn_index=index)


def _content_segments(rows, *, single_add=False, hard_only=False, clears_only=False):
    fields = ('type', 'name', 'value', 'effect', 'scope', 'hardness', 'source')
    content = Counter()
    for row in rows:
        if row.get('type') not in {'constraint', 'preference', 'preserve', 'conflict'}:
            continue
        item = {key: row.get(key) for key in fields}
        if item['effect'] is None:
            item['effect'] = ('clear' if item['value'] is None else 'set') if item['type'] in {'constraint', 'preference'} else 'none'
        if hard_only and not (item['type'] == 'constraint' and item['hardness'] == 'hard' and item['effect'] == 'set'):
            continue
        if clears_only and item['effect'] != 'clear':
            continue
        if single_add and item['scope'] == 'visit:new':
            item['scope'] = 'visit:new:1'
        if item['name'] in {'departure_at', 'finish_by'} and isinstance(item['value'], str):
            item['value'] = _normalize_instant(item['value'])
        content[_canonical(item)] += 1
    return content


def _ledger(rows, *, include_kind=False):
    fields = ('name', 'value', 'scope', 'hardness', 'status')
    return Counter(_canonical({**{key: row.get(key) for key in fields},
                               **({'kind': row.get('kind', 'constraint')} if include_kind else {})})
                   for row in rows)


def _merge_legacy_preferences(old, parsed, draft, index):
    """Freeze F8 ledger policy so F7 reports survive the F9 reducer change.

    Historical fixtures intentionally contain null-as-clear and constraints-only
    memory. Reusing the live reducer would change the meaning of the old experiment.
    This policy is reachable only through the explicit historical evaluation mode.
    """
    data = old.model_dump()
    for field in ('preferred', 'excluded', 'notes'):
        remove, add = getattr(parsed, field + '_remove'), getattr(parsed, field + '_add')
        data[field] = list(dict.fromkeys([item for item in data[field] if item not in remove] + add))
    active = bind_constraint_scopes(old.active_constraints, draft)
    segments = bind_constraint_scopes(parsed.semantic_segments, draft)
    grouped = defaultdict(list)
    for segment in segments:
        if segment.type == 'constraint':
            grouped[(segment.name, segment.scope)].append(segment)
    for key, rows in grouped.items():
        if all(row.value is None for row in rows):
            active = [item for item in active if (item.name, item.scope) != key]
            continue
        incoming_hard = any(row.hardness == 'hard' for row in rows)
        active = [item for item in active if (item.name, item.scope) != key or item.hardness == 'hard' and not incoming_hard]
        hard, other = [row for row in rows if row.hardness == 'hard'], [row for row in rows if row.hardness != 'hard']
        hard_values = {_canonical(row.value.casefold().strip() if isinstance(row.value, str) else row.value) for row in hard}
        conflict = len(hard_values) > 1
        for row in (hard if conflict else hard[-1:]) + other[-1:]:
            status = 'conflicting' if conflict and row.hardness == 'hard' else row.status
            if status != 'conflicting' and row.name in {'opening_hours', 'must_be_open', 'last_admission'}:
                status = 'unresolved'
            elif status == 'satisfied':
                status = 'understood'
            active.append(ActiveConstraint(name=row.name, value=deepcopy(row.value), scope=row.scope,
                          hardness=row.hardness, status=status, source=row.source, quote=row.quote, turn_index=index))
    removed = {'visit_id:' + action.target_id for action in parsed.actions if action.op == 'remove_visit'}
    if any(action.op == 'clear_visits' for action in parsed.actions):
        removed.update('visit_id:' + visit.visit_id for visit in draft.visits)
    data['active_constraints'] = [row.model_dump() for row in active if row.scope not in removed]
    if set(data['preferred']) & set(data['excluded']):
        raise ValueError('Historical preference conflict')
    return Preferences.model_validate(data)


class _ReplayState:
    def __init__(self, case, mode):
        self.case, self.mode = case, mode
        self.draft = Draft.model_validate(case.get('draft', {}))
        self.preferences = Preferences.model_validate(case.get('preferences', {}))
        self.pending = None
        self.revision = 0
        self.catalogue = SimpleNamespace(by_id={row['entity_id']: row for row in case.get('catalogue', [])})

    def body(self, turn, index):
        draft = self.draft if self.mode == 'f9_confirmed_state' else Draft.model_validate(turn.get('draft', self.case.get('draft', {})))
        return ChatRequest(session_id='0' * 32,
                           revision=self.revision if self.mode == 'f9_confirmed_state' else index - 1,
                           message=turn['message'], draft=draft, preferences=self.preferences,
                           pending=self.pending, consent=True)

    def event(self, turn, index, parsed, body):
        if self.mode == 'f7_snapshot':
            if parsed is not None:
                self.preferences = _merge_legacy_preferences(self.preferences, parsed, body.draft, index)
            return 'legacy_snapshot'
        if self.mode == 'f9_snapshot':
            return 'snapshot_only'
        event = turn.get('event')
        if event not in {'confirm', 'cancel', 'continue_pending'}:
            raise ValueError('F9 state replay requires an explicit event')
        if event == 'cancel':
            self.pending = None
            return 'cancelled'
        if parsed is None:
            return 'rejected'
        previous = self.pending.user_message + '\n' if self.pending else ''
        self.pending = Pending(user_message=(previous + turn['message'])[-3000:], interpretation=parsed)
        if event == 'continue_pending':
            return 'pending'
        new_ids = {i: f"{self.case['case_id']}_{index}_{i}" for i, action in enumerate(parsed.actions) if action.op == 'add_visit'}
        fixed_ids = turn.get('new_visit_ids', {})
        for action_index in new_ids:
            if str(action_index) in fixed_ids:
                new_ids[action_index] = fixed_ids[str(action_index)]
        preferences = merge_preferences(self.preferences, parsed, turn_index=index, draft=body.draft,
                                        new_visit_ids=list(new_ids.values()))
        record = {'body': body, 'parsed': parsed, 'preferences': preferences, 'merged_preferences': preferences,
                  'slots': deepcopy(turn.get('slots', [])), 'warnings': [], 'new_ids': new_ids,
                  'new_visit_ids': new_ids, 'build_id': 'fixed-evaluation-catalogue'}
        preview = assemble(record, turn.get('choices', {}), strict=True, cat=self.catalogue)
        if not preview['can_apply']:
            return 'rejected'
        draft = Draft.model_validate(preview['draft'])
        preferences = Preferences.model_validate(preview['preferences'])
        self.draft, self.preferences = draft, preferences
        self.pending = None
        self.revision += 1
        return 'confirmed'


def _record_event(row, state, turn, index, parsed, body, counts=None):
    try:
        result = state.event(turn, index, parsed, body)
    except (ChatError, SemanticConsistencyError, ValueError, KeyError):
        result = 'rejected'
    row.update(event_result=result, adopted_revision=state.revision)
    if counts is None or state.mode != 'f9_confirmed_state':
        return
    if turn['event'] == 'confirm':
        counts['confirmations'] += 1
        counts['confirmation_success'] += result == 'confirmed'
    if 'gold_adopted_draft' in turn:
        counts['adopted_draft_turns'] += 1
        row['adopted_draft_exact'] = state.draft == Draft.model_validate(turn['gold_adopted_draft'])
        counts['adopted_draft_exact'] += row['adopted_draft_exact']


def _commands(parsed, draft, *, normalize=False):
    envelope = pending_to_commands(parsed, draft)
    commands = envelope['commands']
    for rows in commands.values():
        for row in rows:
            row.pop('quote', None)
            if normalize:
                for key in ('departure_at', 'finish_by'):
                    if key in row:
                        row[key] = _normalize_instant(row[key])
    return {**commands, 'max_walk_minutes': parsed.max_walk_minutes,
            'resolved_targets': [(action.op, action.target_id) for action in parsed.actions
                                 if getattr(action, 'target_id', None)],
            **{name: getattr(parsed, name) for name in (
                'preferred_add', 'preferred_remove', 'excluded_add', 'excluded_remove',
                'notes_add', 'notes_remove')}}


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


@dataclass
class _TurnScoreContext:
    case: dict
    turn: dict
    index: int
    mode: str
    body: ChatRequest
    state: _ReplayState
    gold: object
    gold_draft: Draft
    gold_segments: Counter
    gold_content: Counter
    gold_hard: Counter
    gold_clears: Counter
    takeaway_segments: list
    takeaway_groups: set[str]
    has_targets: bool


def _index_predictions(cases, predictions, mode):
    expected = {}
    for case in cases:
        case_mode = _evaluation_mode(case, mode)
        for index, turn in enumerate(case['turns'], 1):
            if case_mode == 'f9_confirmed_state' and turn.get('event') not in {'confirm', 'cancel', 'continue_pending'}:
                raise ValueError('F9 state replay requires an explicit event')
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
    return expected, received


def _make_turn_score_context(case, turn, index, mode, state, gold_state, counts):
    body = state.body(turn, index)
    gold_body = gold_state.body(turn, index)
    gold_draft = gold_body.draft
    gold, _ = _parse(turn['gold'], gold_draft, index, mode)
    gold_state.event(turn, index, gold, gold_body)
    gold_rows = [segment.model_dump() for segment in gold.semantic_segments]
    single_add = sum(action.op == 'add_visit' for action in gold.actions) == 1
    content = _content_segments(gold_rows, single_add=single_add)
    segments = _segments(gold_rows) if mode == 'f7_snapshot' else content
    hard = (_segments(gold_rows, hard_only=True) if mode == 'f7_snapshot'
            else _content_segments(gold_rows, single_add=single_add, hard_only=True))
    clears = _content_segments(gold_rows, single_add=single_add, clears_only=True)
    takeaway = [segment for segment in gold_rows if segment['name'] == 'takeaway']
    takeaway_groups = set()
    if any(segment.get('effect') == 'clear' or segment['value'] is None for segment in takeaway):
        takeaway_groups.add('clear')
    if takeaway:
        takeaway_groups.update(action.op.removesuffix('_visit') for action in gold.actions
                               if action.op in {'add_visit', 'replace_visit'})
    _count_gold_turn(gold, turn, content, segments, hard, clears, takeaway_groups, counts)
    return _TurnScoreContext(
        case=case, turn=turn, index=index, mode=mode, body=body, state=state, gold=gold,
        gold_draft=gold_draft, gold_segments=segments, gold_content=content, gold_hard=hard,
        gold_clears=clears, takeaway_segments=takeaway, takeaway_groups=takeaway_groups,
        has_targets=any(getattr(action, 'target_id', None) for action in gold.actions))


def _count_gold_turn(gold, turn, content, segments, hard, clears, takeaway_groups, counts):
    counts['gold_content'] += content.total()
    counts['clear_turns'] += bool(clears)
    for effect in ('set', 'clear', 'none'):
        counts[f'gold_effect_{effect}'] += sum(
            json.loads(item)['effect'] == effect for item in content.elements())
    for group in takeaway_groups:
        counts[f'takeaway_{group}_turns'] += 1
    counts['gold_segments'] += segments.total()
    counts['gold_hard'] += hard.total()
    counts['target_turns'] += any(getattr(action, 'target_id', None) for action in gold.actions)
    counts['ledger_turns'] += 'gold_active_constraints' in turn


def _new_turn_result(context):
    case = context.case
    row = {'case_id': case['case_id'], 'turn_index': context.index, 'scale': case['scale'],
           'language': case['language'], 'cohort': case.get('cohort', 'standard'),
           'errors': [], 'schema_pass': False,
           'gold_segment_count': context.gold_segments.total(), 'predicted_segment_count': 0,
           'segment_matches': 0, 'gold_hard_count': context.gold_hard.total(), 'hard_matches': 0,
           'evaluation_mode': context.mode, 'gold_content_count': context.gold_content.total(),
           'predicted_content_count': 0, 'content_matches': 0}
    return row


def _parse_prediction(prediction, context, row, counts):
    if prediction is None:
        row['errors'].append('missing_prediction')
        return None
    raw = prediction.get('output')
    raw_segments = raw.get('semantic_segments', []) if isinstance(raw, dict) else []
    claims = sum(isinstance(segment, dict) and
                 segment.get('name') in {'must_be_open', 'opening_hours', 'last_admission'} and
                 segment.get('status') == 'satisfied'
                 for segment in raw_segments) if isinstance(raw_segments, list) else 0
    counts['unproven_opening_status_claims'] += claims
    if claims:
        row['errors'].append('unproven_opening_status_claim')
    try:
        if not isinstance(raw, dict) or not {'acknowledgement', 'semantic_segments', 'commands'} <= raw.keys():
            raise ValueError('Missing provider envelope')
        parsed, _ = _parse(raw, context.body.draft, context.index, context.mode)
    except (OperationFormatError, ValueError):
        row['errors'].append('schema_invalid')
        return None
    counts['schema_pass'] += 1
    row['schema_pass'] = True
    return parsed


def _score_parsed_turn(parsed, context, row, counts):
    predicted_rows = [segment.model_dump() for segment in parsed.semantic_segments]
    single_add = sum(action.op == 'add_visit' for action in parsed.actions) == 1
    content = _content_segments(predicted_rows, single_add=single_add)
    segments = _segments(predicted_rows) if context.mode == 'f7_snapshot' else content
    hard = (_segments(predicted_rows, hard_only=True) if context.mode == 'f7_snapshot'
            else _content_segments(predicted_rows, single_add=single_add, hard_only=True))
    _record_segment_scores(parsed, context, predicted_rows, content, segments, hard, row, counts)
    _record_command_scores(parsed, context, row, counts)
    valid = _validate_scored_prediction(parsed, context, row, counts)
    _record_event(row, context.state, context.turn, context.index,
                  parsed if valid else None, context.body, counts)
    _record_ledger_scores(context, row, counts)


def _record_segment_scores(parsed, context, rows, content, segments, hard, row, counts):
    single_add = sum(action.op == 'add_visit' for action in context.gold.actions) == 1
    counts['predicted_content'] += content.total()
    counts['content_matches'] += (context.gold_content & content).total()
    counts['predicted_segments'] += segments.total()
    counts['segment_matches'] += (context.gold_segments & segments).total()
    counts['hard_matches'] += (context.gold_hard & hard).total()
    row.update(predicted_content_count=content.total(),
               content_matches=(context.gold_content & content).total(),
               predicted_segment_count=segments.total(),
               segment_matches=(context.gold_segments & segments).total(),
               hard_matches=(context.gold_hard & hard).total(),
               segments_exact=segments == context.gold_segments)
    if context.gold_clears:
        predicted_clears = _content_segments(rows, single_add=single_add, clears_only=True)
        counts['clears_exact'] += context.gold_clears == predicted_clears
    for group in context.takeaway_groups:
        predicted_takeaway = _content_segments([row for row in rows if row['name'] == 'takeaway'],
                                               single_add=single_add)
        expected_takeaway = _content_segments(context.takeaway_segments, single_add=single_add)
        commands_match = _commands(parsed, context.body.draft, normalize=context.mode != 'f7_snapshot') == _commands(
            context.gold, context.gold_draft, normalize=context.mode != 'f7_snapshot')
        counts[f'takeaway_{group}_exact'] += predicted_takeaway == expected_takeaway and commands_match


def _record_command_scores(parsed, context, row, counts):
    normalize = context.mode != 'f7_snapshot'
    commands_match = _commands(parsed, context.body.draft, normalize=normalize) == _commands(
        context.gold, context.gold_draft, normalize=normalize)
    row['commands_exact'] = commands_match
    counts['commands_exact'] += commands_match
    if context.has_targets:
        actual = [(action.op, getattr(action, 'target_id', None)) for action in parsed.actions]
        expected = [(action.op, getattr(action, 'target_id', None)) for action in context.gold.actions]
        counts['targets_exact'] += actual == expected
    row['clarification_correct'] = bool(parsed.questions) == bool(context.gold.questions)
    counts['clarification_correct'] += row['clarification_correct']


def _validate_scored_prediction(parsed, context, row, counts):
    try:
        validate_actions(context.body, parsed)
        validate_segment_command_consistency(parsed.semantic_segments, parsed, draft=context.body.draft,
            historical=context.mode == 'f7_snapshot')
    except (ChatError, SemanticConsistencyError, ValueError):
        row['errors'].append('semantic_or_evidence_rejected')
        return False
    counts['consistency_pass'] += 1
    return True


def _record_ledger_scores(context, row, counts):
    turn = context.turn
    if 'gold_active_constraints' not in turn:
        return
    actual_rows = [constraint.model_dump() for constraint in context.state.preferences.active_constraints]
    expected_rows = turn['gold_active_constraints']
    row['ledger_exact'] = _ledger(actual_rows) == _ledger(expected_rows)
    row['kind_ledger_exact'] = _ledger(actual_rows, include_kind=True) == _ledger(
        expected_rows, include_kind=True)
    counts['ledger_exact'] += row['ledger_exact']
    counts['kind_ledger_exact'] += row['kind_ledger_exact']


def _score_turn(case, turn, index, mode, state, gold_state, prediction, counts):
    context = _make_turn_score_context(case, turn, index, mode, state, gold_state, counts)
    row = _new_turn_result(context)
    row['prediction_received'] = prediction is not None
    parsed = _parse_prediction(prediction, context, row, counts)
    if parsed is None:
        _record_event(row, state, turn, index, None, context.body, counts)
    else:
        _score_parsed_turn(parsed, context, row, counts)
    return row


def _score_cases(cases, received, mode):
    counts = Counter()
    results = []
    for case in cases:
        case_mode = _evaluation_mode(case, mode)
        state, gold_state = _ReplayState(case, case_mode), _ReplayState(case, case_mode)
        for index, turn in enumerate(case['turns'], 1):
            prediction = received.get((case['case_id'], index))
            results.append(_score_turn(case, turn, index, case_mode, state, gold_state,
                                       prediction, counts))
    return results, counts


def _score_metrics(counts, total, received):
    return {
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
        'normalized_content_micro_precision': _ratio(counts['content_matches'], counts['predicted_content']),
        'normalized_content_micro_recall': _ratio(counts['content_matches'], counts['gold_content']) if received else None,
        'normalized_content_micro_f1': _ratio(2 * counts['content_matches'], counts['predicted_content'] + counts['gold_content']) if received else None,
        'clear_effect_accuracy': _ratio(counts['clears_exact'], counts['clear_turns']),
        'kind_ledger_exact_rate': _ratio(counts['kind_ledger_exact'], counts['ledger_turns']),
        'confirmation_success_rate': _ratio(counts['confirmation_success'], counts['confirmations']),
        'adopted_draft_exact_rate': _ratio(counts['adopted_draft_exact'], counts['adopted_draft_turns']),
        **{f'takeaway_{group}_accuracy': _ratio(counts[f'takeaway_{group}_exact'], counts[f'takeaway_{group}_turns'])
           for group in ('add', 'replace', 'clear')},
    }


def _score_groups(results):
    groups = {}
    for scale in range(1, 6):
        groups[f'single_scale_{scale}'] = [row for row in results
                                            if row['cohort'] != 'multiturn' and row['scale'] == scale]
    for language in ('zh', 'en', 'mixed'):
        groups[f'single_standard_{language}'] = [row for row in results
                                                  if row['cohort'] == 'standard' and row['language'] == language]
    groups['single_boundary'] = [row for row in results if row['cohort'] == 'boundary']
    groups['multiturn'] = [row for row in results if row['cohort'] == 'multiturn']
    return {name: _summarize_group(rows) for name, rows in groups.items() if rows}


def _score_acceptance(cases, mode, received, total):
    eligible = all(case.get('annotation_status') == 'human_reviewed' and
                   (_evaluation_mode(case, mode) == 'f7_snapshot' or
                    all(turn.get('annotation_status') == 'human_reviewed' for turn in case['turns']))
                   for case in cases)
    if len(received) < total:
        return 'incomplete_predictions'
    if not eligible:
        return 'not_eligible_unreviewed_labels'
    return 'requires_manual_and_end_to_end_review'


def _score_report(cases, predictions, mode, received, results, counts, total):
    prompt_versions = sorted({prediction['prompt_version'] for prediction in predictions
                              if isinstance(prediction.get('prompt_version'), str) and
                              re.fullmatch(r'2C1-F[0-9]+(?:\.[0-9]+)?', prediction['prompt_version'])})
    wire_versions = sorted({prediction['wire_version'] for prediction in predictions
                            if isinstance(prediction.get('wire_version'), str) and
                            re.fullmatch(r'2C1-F[0-9]+(?:\.[0-9]+)?', prediction['wire_version'])})
    modes = sorted({_evaluation_mode(case, mode) for case in cases})
    return {
        'evaluation_scope': 'controlled_interpretation_and_ledger',
        'prompt_version': prompt_versions[0] if len(prompt_versions) == 1 else None,
        'wire_version': wire_versions[0] if len(wire_versions) == 1 else None,
        'prediction_prompt_versions': prompt_versions, 'prediction_wire_versions': wire_versions,
        'local_scoring_contract': WIRE_VERSION, 'scorer_version': SCORER_VERSION,
        'scorer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'ledger_policies': {name: '2C1-F8-historical' if name == 'f7_snapshot' else '2C1-F9.1-confirmation'
                            for name in modes},
        'evaluation_modes': modes, 'expected_turns': total, 'received_turns': len(received),
        'missing_turns': total - len(received),
        'acceptance': _score_acceptance(cases, mode, received, total),
        'metrics': _score_metrics(counts, total, received), 'groups': _score_groups(results),
        'metric_coverage': {'content_gold_segments': counts['gold_content'],
                            'clear_effect_turns': counts['clear_turns'],
                            'confirmation_events': counts['confirmations'],
                            'adopted_draft_turns': counts['adopted_draft_turns'],
                            'target_turns': counts['target_turns'], 'ledger_turns': counts['ledger_turns'],
                            **{f'gold_effect_{effect}': counts[f'gold_effect_{effect}']
                               for effect in ('set', 'clear', 'none')},
                            **{f'takeaway_{group}_turns': counts[f'takeaway_{group}_turns']
                               for group in ('add', 'replace', 'clear')}},
        'cases': results,
        'not_measured': ['retrieval_relevance', 'route_feasibility', 'browser_confirmation',
                         'natural_language_false_claims'],
    }


def score_predictions(cases, predictions, *, mode=None):
    """Score stored provider responses and replay state without network requests."""
    cases, predictions = list(cases), list(predictions)
    expected, received = _index_predictions(cases, predictions, mode)
    results, counts = _score_cases(cases, received, mode)
    return _score_report(cases, predictions, mode, received, results, counts, len(expected))


def _summarize_group(rows):
    received = sum(row['prediction_received'] for row in rows)
    matches = sum(row['segment_matches'] for row in rows)
    segment_count = sum(row['gold_segment_count'] + row['predicted_segment_count'] for row in rows)
    content_matches = sum(row['content_matches'] for row in rows)
    content_count = sum(row['gold_content_count'] + row['predicted_content_count'] for row in rows)
    return {'expected_turns': len(rows), 'received_turns': received,
            'schema_pass_rate': _ratio(sum(row['schema_pass'] for row in rows), len(rows)),
            'segment_micro_f1': _ratio(2 * matches, segment_count) if received else None,
            'normalized_content_micro_f1': _ratio(2 * content_matches, content_count) if received else None,
            'hard_constraint_recall': _ratio(sum(row['hard_matches'] for row in rows),
                                             sum(row['gold_hard_count'] for row in rows)) if received else None}


def validate_fixtures(cases, *, mode=None):
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
        case_mode = _evaluation_mode(case, mode)
        state = _ReplayState(case, case_mode)
        for index, turn in enumerate(case['turns'], 1):
            turns += 1
            if case_mode == 'f9_confirmed_state' and turn.get('event') not in {'confirm', 'cancel', 'continue_pending'}:
                raise ValueError('F9 state replay requires an explicit event')
            body = state.body(turn, index)
            draft = body.draft
            parsed, _ = _parse(turn['gold'], draft, index, case_mode)
            try:
                validate_actions(body, parsed)
                validate_segment_command_consistency(parsed.semantic_segments, parsed, draft=draft,
                    historical=case_mode=='f7_snapshot')
                outcome = state.event(turn, index, parsed, body)
                if case_mode == 'f9_confirmed_state' and turn['event'] == 'confirm' and outcome != 'confirmed':
                    raise ValueError('Gold confirmation could not be applied')
            except (ChatError, SemanticConsistencyError, ValueError) as error:
                raise ValueError(f"Invalid annotation: {case['case_id']} turn {index}") from error
            if 'gold_active_constraints' in turn:
                actual = _ledger([c.model_dump() for c in state.preferences.active_constraints], include_kind=case_mode != 'f7_snapshot')
                wanted = _ledger(turn['gold_active_constraints'], include_kind=case_mode != 'f7_snapshot')
                if actual != wanted:
                    raise ValueError(f"Ledger annotation mismatch: {case['case_id']} turn {index}")
            if 'gold_adopted_draft' in turn and state.draft != Draft.model_validate(turn['gold_adopted_draft']):
                raise ValueError(f"Draft annotation mismatch: {case['case_id']} turn {index}")
    return {'kind': 'fixture_contract_check', 'cases': len(cases), 'turns': turns,
            'model_calls': 0, 'human_review_required': any(c.get('annotation_status') != 'human_reviewed' for c in cases)}


def iter_predictions(cases, client, *, max_calls, pace=None, mode=None):
    """Collect one interpreter output per controlled turn with no retries.

    Only an explicit caller supplies a provider. Yield each completed call so the
    CLI can flush it before the next request; interruptions cannot lose earlier
    outputs. Gold annotations are used for preflight only and never sent.
    """
    if type(max_calls) is not int or max_calls < 1:
        raise ValueError('max_calls must be a positive integer')
    validate_fixtures(cases, mode=mode)
    fixture_hash = hashlib.sha256(_canonical(cases).encode('utf-8')).hexdigest()
    prompt_hash = hashlib.sha256(INTERPRET.encode('utf-8')).hexdigest()
    calls = 0
    for case in cases:
        case_mode = _evaluation_mode(case, mode)
        state = _ReplayState(case, case_mode)
        for index, turn in enumerate(case['turns'], 1):
            if calls >= max_calls:
                return
            body = state.body(turn, index)
            draft = body.draft
            payload = {'user_message': body.message, 'current_draft': model_context(body),
                       'preferences': state.preferences.model_dump(mode='json'),
                       'pending_request': ({'user_message': state.pending.user_message,
                                            'interpretation': pending_to_commands(state.pending.interpretation, draft)}
                                           if state.pending else None),
                       'singapore_now': case.get('singapore_now', '2026-10-27T09:00:00+08:00')}
            if pace is not None:
                pace.wait(lambda: False)
            calls += 1
            row = {'case_id': case['case_id'], 'turn_index': index,
                   'prompt_version': PROMPT_VERSION, 'wire_version': WIRE_VERSION,
                   'fixtures_sha256': fixture_hash, 'prompt_sha256': prompt_hash,
                   'evaluation_mode': case_mode, 'scorer_version': SCORER_VERSION,
                   'scorer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
            try:
                result = client.generate_json(instruction=INTERPRET, payload=payload, schema=interpretation_schema())
                row['output'] = result['data']
                row['metadata'] = result.get('metadata', {})
            except LLMConnectionError as error:
                row['provider_error_code'] = error.code
                _record_event(row, state, turn, index, None, body)
                yield row
                if error.code in {'KEY_INVALID', 'ACCESS_DENIED', 'MODEL_NOT_AVAILABLE', 'BAD_REQUEST', 'RATE_LIMIT'}:
                    return
                continue
            try:
                parsed, _ = _parse(row['output'], draft, index, case_mode)
                validate_actions(body, parsed)
                validate_segment_command_consistency(parsed.semantic_segments, parsed, draft=draft,
                    historical=case_mode=='f7_snapshot')
            except (OperationFormatError, ChatError, SemanticConsistencyError, ValueError):
                parsed = None
            _record_event(row, state, turn, index, parsed, body)
            yield row
