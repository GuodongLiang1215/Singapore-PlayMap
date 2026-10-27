import pytest

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
