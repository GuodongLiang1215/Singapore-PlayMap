import pytest
from copy import deepcopy

from app.stage2c.evaluation import iter_predictions, score_predictions, validate_fixtures


def make_case():
    return {
        'case_id': 'walk', 'scale': 3, 'language': 'zh',
        'annotation_status': 'synthetic_unreviewed',
        'turns': [{
            'message': '每段步行不超过10分钟',
            'gold': {
                'acknowledgement': '记录限制', 'max_walk_minutes': 10, 'commands': {},
                'semantic_segments': [{
                    'type': 'constraint', 'name': 'max_walk_minutes', 'value': 10,
                    'scope': 'global', 'hardness': 'hard', 'status': 'understood',
                    'source': 'user_explicit', 'quote': '每段步行不超过10分钟',
                }],
            },
        }],
    }


def test_scoring_detects_a_dropped_hard_requirement_even_when_commands_are_valid():
    report = score_predictions([make_case()], [{
        'case_id': 'walk', 'turn_index': 1,
        'output': {'acknowledgement': '记录限制', 'semantic_segments': [],
                   'commands': {}, 'max_walk_minutes': 10},
    }])

    assert report['metrics']['schema_pass_rate'] == 1
    assert report['metrics']['segment_micro_f1'] == 0
    assert report['metrics']['hard_constraint_recall'] == 0
    assert report['acceptance'] == 'not_eligible_unreviewed_labels'


def test_empty_predictions_cannot_be_reported_as_a_successful_model_experiment():
    report = score_predictions([make_case()], [])

    assert report['received_turns'] == 0
    assert report['missing_turns'] == 1
    assert report['metrics']['segment_micro_f1'] is None
    assert report['acceptance'] == 'incomplete_predictions'


def test_unknown_or_duplicate_predictions_are_rejected():
    prediction = {'case_id': 'walk', 'turn_index': 1, 'output': {}}
    with pytest.raises(ValueError, match='Duplicate'):
        score_predictions([make_case()], [prediction, prediction])
    with pytest.raises(ValueError, match='Unknown'):
        score_predictions([make_case()], [{'case_id': 'other', 'turn_index': 1, 'output': {}}])


def test_fixture_check_checks_annotations_without_reporting_model_accuracy():
    report = validate_fixtures([make_case()])

    assert report == {'kind': 'fixture_contract_check', 'cases': 1, 'turns': 1,
                      'model_calls': 0, 'human_review_required': True}


def test_missing_target_command_cannot_disappear_from_target_accuracy_denominator():
    case = make_case()
    case['draft'] = {'visits': [{'visit_id': 'first', 'point': {
        'label': 'Synthetic stop', 'latitude': 1.3, 'longitude': 103.8,
        'source': 'user_map', 'confirmed': True,
    }}]}
    case['turns'][0]['message'] = 'Remove the first stop.'
    case['turns'][0]['gold'] = {
        'acknowledgement': 'remove', 'semantic_segments': [],
        'commands': {'remove_visit': [{'order': 1, 'quote': 'Remove the first stop', 'target_position': 1}]},
    }
    report = score_predictions([case], [{
        'case_id': 'walk', 'turn_index': 1,
        'output': {'acknowledgement': 'no edits', 'semantic_segments': [], 'commands': {}},
    }])

    assert report['metrics']['target_sequence_accuracy'] == 0


def test_collection_respects_the_call_cap_and_never_sends_gold_labels():
    case = make_case()
    case['turns'].append(case['turns'][0].copy())

    class Provider:
        calls = 0

        def generate_json(self, *, instruction, payload, schema):
            self.calls += 1
            assert 'gold' not in payload
            assert 'semantic_segments' not in payload
            assert payload['user_message'] == '每段步行不超过10分钟'
            return {'data': make_case()['turns'][0]['gold'], 'metadata': {}}

    provider = Provider()
    rows = list(iter_predictions([case], provider, max_calls=1))

    assert provider.calls == 1
    assert len(rows) == 1
    assert rows[0]['case_id'] == 'walk'
    assert rows[0]['output']['max_walk_minutes'] == 10


def test_invalid_model_output_counts_as_failed_ledger_replay():
    case = make_case()
    case['turns'][0]['gold_active_constraints'] = [{
        'name': 'max_walk_minutes', 'value': 10, 'scope': 'global',
        'hardness': 'hard', 'status': 'understood',
    }]
    report = score_predictions([case], [{'case_id': 'walk', 'turn_index': 1, 'output': {}}])

    assert report['metrics']['ledger_exact_rate'] == 0


def test_collection_stops_after_an_unrecoverable_provider_error():
    from app.llm_connect.errors import LLMConnectionError
    case = make_case()
    case['turns'].append(case['turns'][0].copy())

    class Provider:
        def generate_json(self, **kwargs):
            raise LLMConnectionError('KEY_INVALID')

    rows = list(iter_predictions([case], Provider(), max_calls=2))

    assert len(rows) == 1
    assert rows[0]['provider_error_code'] == 'KEY_INVALID'


def test_normalized_content_excludes_derived_operations_and_reports_coverage():
    case = make_case()
    output = deepcopy(case['turns'][0]['gold'])
    output['semantic_segments'].append({'type': 'operation', 'name': 'set_walk', 'value': 10,
                                        'scope': 'global', 'hardness': 'unknown',
                                        'status': 'understood', 'source': 'user_explicit',
                                        'quote': '每段步行不超过10分钟'})

    report = score_predictions([case], [{'case_id': 'walk', 'turn_index': 1, 'output': output}])

    assert report['metrics']['normalized_content_micro_f1'] == 1
    assert report['metric_coverage']['content_gold_segments'] == 1
    assert report['metric_coverage']['takeaway_clear_turns'] == 0
    assert report['metrics']['takeaway_clear_accuracy'] is None


def test_f9_content_distinguishes_setting_false_from_clearing_a_value():
    case = make_case()
    case['evaluation_mode'] = 'f9_snapshot'
    case['draft'] = {'visits': [{'visit_id': 'coffee', 'point': {
        'label': 'Synthetic cafe', 'latitude': 1.3, 'longitude': 103.8,
        'source': 'user_map', 'confirmed': True,
    }}]}
    case['turns'][0]['message'] = '取消第一站外带偏好'
    case['turns'][0]['gold'] = {
        'acknowledgement': '取消', 'commands': {}, 'questions': ['需确认'],
        'semantic_segments': [{'type': 'preference', 'name': 'takeaway', 'value': None,
                               'effect': 'clear', 'scope': 'visit:1', 'hardness': 'soft',
                               'status': 'understood', 'source': 'user_explicit', 'quote': '取消第一站外带偏好'}],
    }
    output = deepcopy(case['turns'][0]['gold'])
    output['semantic_segments'][0]['effect'] = 'set'
    output['semantic_segments'][0]['value'] = False

    report = score_predictions([case], [{'case_id': 'walk', 'turn_index': 1,
                                        'prompt_version': '2C1-F7', 'output': output}])

    assert report['metrics']['normalized_content_micro_f1'] == 0
    assert report['metrics']['clear_effect_accuracy'] == 0
    assert report['prediction_prompt_versions'] == ['2C1-F7']
    assert report['scorer_version'] == 'person-b-f9-v3'
    assert report['evaluation_modes'] == ['f9_snapshot']


def test_unknown_evaluation_mode_is_rejected():
    case = make_case()
    case['evaluation_mode'] = 'automatic'
    with pytest.raises(ValueError, match='mode'):
        score_predictions([case], [])


def confirmed_state_case():
    case = make_case()
    case.update(evaluation_mode='f9_confirmed_state', cohort='multiturn')
    case['turns'] = []
    for minutes, event in [(10, 'continue_pending'), (5, 'cancel'), (15, 'confirm'), (20, 'continue_pending')]:
        message = f'每段步行不超过{minutes}分钟'
        gold = deepcopy(make_case()['turns'][0]['gold'])
        gold['max_walk_minutes'] = minutes
        gold['semantic_segments'][0].update(value=minutes, effect='set', quote=message)
        case['turns'].append({'message': message, 'gold': gold, 'event': event})
    return case


def test_f9_collection_retains_adopted_state_until_confirm_and_passes_pending():
    case = confirmed_state_case()

    class Provider:
        payloads = []

        def generate_json(self, *, payload, **kwargs):
            self.payloads.append(deepcopy(payload))
            return {'data': case['turns'][len(self.payloads) - 1]['gold']}

    provider = Provider()
    rows = list(iter_predictions([case], provider, max_calls=4))

    assert provider.payloads[1]['preferences']['active_constraints'] == []
    assert provider.payloads[1]['pending_request']['user_message'] == case['turns'][0]['message']
    assert provider.payloads[2]['pending_request'] is None
    assert provider.payloads[2]['preferences']['active_constraints'] == []
    assert provider.payloads[3]['current_draft']['max_walk_minutes'] == 15
    assert provider.payloads[3]['preferences']['active_constraints'][0]['value'] == 15
    assert [row['event_result'] for row in rows] == ['pending', 'cancelled', 'confirmed', 'pending']


def test_f9_replay_accumulates_pending_text_and_allows_nonblocking_questions():
    case = make_case()
    case.update(evaluation_mode='f9_confirmed_state', cohort='multiturn')
    first_message = 'Plan a relaxed afternoon.'
    second_message = 'Keep it near Bugis.'
    confirm_message = 'Add a cafe.'
    case['turns'] = [
        {'message': first_message, 'event': 'continue_pending', 'gold': {
            'acknowledgement': 'I can help plan that.', 'semantic_segments': [], 'commands': {},
        }},
        {'message': second_message, 'event': 'continue_pending', 'gold': {
            'acknowledgement': 'I will keep that in mind.', 'semantic_segments': [], 'commands': {},
        }},
        {'message': confirm_message, 'event': 'confirm', 'gold': {
            'acknowledgement': 'Here is a cafe option.', 'semantic_segments': [],
            'commands': {'add_visit': [{'order': 1, 'quote': confirm_message,
                                        'place': {'categories': ['food'], 'keywords': ['cafe']}}]},
            'questions': ['Would you like to add anything else?'],
        }, 'slots': [{'slot_id': 'slot-cafe', 'action_index': 0,
                     'explicit_location_confirmation': False,
                     'options': [{'candidate_key': 'cafe-1', 'point': {
                         'label': 'Synthetic Cafe', 'latitude': 1.30, 'longitude': 103.80,
                         'source': 'user_map', 'confirmed': True,
                     }}]}], 'choices': {'slot-cafe': 'cafe-1'}},
    ]

    class Provider:
        payloads = []

        def generate_json(self, *, payload, **kwargs):
            self.payloads.append(deepcopy(payload))
            return {'data': case['turns'][len(self.payloads) - 1]['gold']}

    provider = Provider()
    predictions = list(iter_predictions([case], provider, max_calls=3))
    report = score_predictions([case], predictions)

    assert provider.payloads[2]['pending_request']['user_message'] == first_message + '\n' + second_message
    assert report['cases'][2]['event_result'] == 'confirmed'
    assert report['metrics']['confirmation_success_rate'] == 1


def test_f9_failed_confirmation_keeps_actual_state_and_does_not_inject_gold_snapshot():
    case = confirmed_state_case()
    case['turns'][0]['event'] = 'confirm'
    case['turns'][0]['gold_adopted_draft'] = {'max_walk_minutes': 10}
    predictions = [{'case_id': 'walk', 'turn_index': 1, 'output': {}}]

    report = score_predictions([case], predictions)

    assert report['metrics']['confirmation_success_rate'] == 0
    assert report['cases'][0]['event_result'] == 'rejected'
    assert report['cases'][0]['adopted_revision'] == 0
    assert report['metrics']['adopted_draft_exact_rate'] == 0


def test_f9_fixtures_require_explicit_confirmation_events():
    case = confirmed_state_case()
    del case['turns'][0]['event']
    with pytest.raises(ValueError, match='event'):
        validate_fixtures([case])


def test_kind_ledger_does_not_treat_a_preference_as_the_same_constraint():
    case = make_case()
    case['evaluation_mode'] = 'f9_snapshot'
    case['turns'][0]['gold']['semantic_segments'][0]['effect'] = 'set'
    constraint = {'kind': 'constraint', 'name': 'max_walk_minutes', 'value': 10,
                  'scope': 'global', 'hardness': 'hard', 'status': 'understood',
                  'source': 'user_explicit', 'quote': case['turns'][0]['message'], 'turn_index': 0}
    case['preferences'] = {'active_constraints': [constraint]}
    case['turns'][0]['gold_active_constraints'] = [{**constraint, 'kind': 'preference'}]

    report = score_predictions([case], [{'case_id': 'walk', 'turn_index': 1,
                                        'output': case['turns'][0]['gold']}])

    assert report['metrics']['ledger_exact_rate'] == 1
    assert report['metrics']['kind_ledger_exact_rate'] == 0


def test_missing_f9_prediction_remains_in_content_and_confirmation_denominators():
    case = confirmed_state_case()
    predictions = [{'case_id': 'walk', 'turn_index': 1, 'output': case['turns'][0]['gold']}]

    report = score_predictions([case], predictions)

    assert report['metrics']['normalized_content_micro_recall'] == 0.25
    assert report['metric_coverage']['confirmation_events'] == 1
    assert report['metrics']['confirmation_success_rate'] == 0


def test_original_fixtures_still_pass_the_explicit_historical_ledger_policy():
    from pathlib import Path
    import json
    root = Path(__file__).resolve().parents[1]
    cases = [json.loads(line) for name in ('person_b_semantic_cases.jsonl', 'person_b_multiturn_cases.jsonl')
             for line in (root / 'tests' / 'fixtures' / name).read_text(encoding='utf-8').splitlines()]

    report = validate_fixtures(cases, mode='f7_snapshot')

    assert report['cases'] == 60
    assert report['turns'] == 71
    assert report['model_calls'] == 0


def test_f9_targets_follow_actual_confirmation_state_after_an_incorrect_move():
    case = make_case()
    case.update(evaluation_mode='f9_confirmed_state', cohort='multiturn', draft={'visits': [
        {'visit_id': f'stop-{index}', 'point': {'label': f'Synthetic {index}',
         'latitude': 1.3 + index / 1000, 'longitude': 103.8,
         'source': 'user_map', 'confirmed': True}} for index in (1, 2)]})
    case['turns'] = [
        {'message': 'Move second to first.', 'event': 'confirm', 'gold': {
            'acknowledgement': 'move', 'semantic_segments': [],
            'commands': {'move_visit': [{'order': 1, 'target_position': 2, 'position': 1,
                                         'quote': 'Move second to first'}]}}},
        {'message': 'Make first stay 45 minutes.', 'event': 'confirm', 'gold': {
            'acknowledgement': 'stay', 'semantic_segments': [],
            'commands': {'set_stay': [{'order': 1, 'target_position': 1, 'minutes': 45,
                                      'quote': 'Make first stay 45 minutes'}]}}}]
    first = deepcopy(case['turns'][0]['gold'])
    first['commands']['move_visit'][0]['target_position'] = 1

    report = score_predictions([case], [
        {'case_id': 'walk', 'turn_index': 1, 'output': first},
        {'case_id': 'walk', 'turn_index': 2, 'output': case['turns'][1]['gold']}])

    assert report['metrics']['target_sequence_accuracy'] == 0
    assert report['cases'][1]['commands_exact'] is False
    assert report['cases'][0]['event_result'] == 'unchanged'
    assert report['cases'][0]['adopted_revision'] == 0
    assert report['cases'][1]['adopted_revision'] == 1
    assert report['metric_coverage']['unchanged_confirmation_events'] == 1
    assert report['metrics']['confirmation_success_rate'] == 0.5


def test_f9_revision_fixtures_preserve_review_scope_and_are_not_human_gold():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures'
    cases = [json.loads(line) for name in ('person_b_f9_semantic_cases.jsonl', 'person_b_f9_multiturn_cases.jsonl')
             for line in (root / name).read_text(encoding='utf-8').splitlines()]

    report = validate_fixtures(cases)

    assert report['cases'] == 24
    assert report['turns'] == 28
    assert report['unchanged_confirmation_events'] == 1
    assert report['human_review_required'] is True
    assert sum(turn['text_review_confirmed'] for case in cases for turn in case['turns']) == 25
    assert all(case['annotation_status'] == 'synthetic_unreviewed' for case in cases)
    assert all(turn['annotation_status'] == 'synthetic_unreviewed' for case in cases for turn in case['turns'])


def test_rejected_provider_claim_still_counts_as_an_unproven_opening_claim():
    case = make_case()
    case['evaluation_mode'] = 'f9_snapshot'
    case['turns'][0]['gold']['semantic_segments'][0]['effect'] = 'set'
    output = deepcopy(case['turns'][0]['gold'])
    output['semantic_segments'][0].update(name='opening_hours', value='14:00', status='satisfied')

    report = score_predictions([case], [{'case_id': 'walk', 'turn_index': 1, 'output': output}])

    assert report['metrics']['schema_pass_rate'] == 0
    assert report['metrics']['unproven_opening_status_claims'] == 1
    assert 'unproven_opening_status_claim' in report['cases'][0]['errors']


def test_f9_equates_one_new_visit_alias_and_same_timezone_instant_only():
    case = make_case()
    case['evaluation_mode'] = 'f9_snapshot'
    case['turns'][0] = {'message': 'Add cafe, depart at 09:00 on 28 October 2026.', 'gold': {
        'acknowledgement': 'proposal', 'semantic_segments': [
            {'type': 'constraint', 'name': 'place_type', 'value': 'cafe', 'effect': 'set',
             'scope': 'visit:new', 'hardness': 'hard', 'status': 'understood',
             'source': 'user_explicit', 'quote': 'cafe'},
            {'type': 'constraint', 'name': 'departure_at', 'value': '2026-10-28T09:00:00+08:00', 'effect': 'set',
             'scope': 'global', 'hardness': 'hard', 'status': 'understood',
             'source': 'user_explicit', 'quote': 'depart at 09:00 on 28 October 2026'}],
        'commands': {'add_visit': [{'order': 1, 'quote': 'Add cafe', 'place': {'categories': ['food'], 'keywords': ['cafe']}}],
                     'set_time': [{'order': 2, 'quote': 'depart at 09:00 on 28 October 2026',
                                   'departure_at': '2026-10-28T09:00:00+08:00'}]}}}
    output = deepcopy(case['turns'][0]['gold'])
    output['semantic_segments'][0]['scope'] = 'visit:new:1'
    output['semantic_segments'][1]['value'] = '2026-10-28T01:00:00+00:00'
    output['commands']['set_time'][0]['departure_at'] = '2026-10-28T01:00:00+00:00'

    report = score_predictions([case], [{'case_id': 'walk', 'turn_index': 1, 'output': output}])

    assert report['metrics']['normalized_content_micro_f1'] == 1
    assert report['metrics']['command_exact_rate'] == 1
