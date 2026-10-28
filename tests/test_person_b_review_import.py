import copy
import csv
import hashlib
import json

import pytest

from scripts.import_person_b_review import import_reviews, main


def write_inputs(tmp_path):
    fixtures = [{'case_id': 'm1', 'scale': 2, 'language': 'zh',
                 'annotation_status': 'synthetic_unreviewed', 'turns': [
                     {'message': '先找咖啡馆', 'gold': {'commands': {}}},
                     {'message': '再找公园', 'gold': {'commands': {}}},
                     {'message': '取消限制', 'gold': {'commands': {}}}]}]
    fixture_path = tmp_path / 'fixtures.jsonl'
    fixture_path.write_text(json.dumps(fixtures[0]), encoding='utf-8')
    prediction_path = tmp_path / 'predictions.jsonl'
    prediction_path.write_text('{"case_id":"m1","turn_index":3,"output":{}}\n', encoding='utf-8')
    fixture_hash = hashlib.sha256(json.dumps(fixtures, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
    review = {'schema': 'ge5226-person-b-human-review-v1', 'code_head': 'baseline',
              'fixtures_sha256': fixture_hash,
              'predictions_sha256': hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
              'reviews': [{'key': 'm1:3', 'case_id': 'm1', 'turn_index': 3,
                           'message': '取消限制', 'original_gold': {'commands': {}},
                           'review': {'decision': 'discuss', 'confirmed_at': '2026-10-27',
                                      'confirmed_by': 'B', 'rationale': '三轮都很好',
                                      'correction_notes': '', 'proposed_gold_json': ''}}]}
    review_path = tmp_path / 'review.json'
    review_path.write_text(json.dumps(review), encoding='utf-8')
    csv_path = tmp_path / 'summary.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=['case_id', 'turn_index', 'decision', 'confirmed_at'])
        writer.writeheader()
        writer.writerow({'case_id': 'm1', 'turn_index': 3, 'decision': 'discuss', 'confirmed_at': '2026-10-27'})
    return review_path, csv_path, [fixture_path], prediction_path


def test_text_only_review_creates_one_candidate_without_promoting_other_turns(tmp_path):
    paths = write_inputs(tmp_path)
    before = [path.read_bytes() for path in [paths[0], paths[1], *paths[2], paths[3]]]

    report = import_reviews(*paths, baseline_code_head='baseline')

    assert report['confirmed_review_turns'] == 1
    candidate = report['candidates'][0]
    assert (candidate['case_id'], candidate['turn_index']) == ('m1', 3)
    assert candidate['structured_annotation_status'] == 'needs_structured_review'
    assert candidate['annotation_status'] == 'synthetic_unreviewed'
    assert report['automatic_gold_promotion'] is False
    assert before == [path.read_bytes() for path in [paths[0], paths[1], *paths[2], paths[3]]]


@pytest.mark.parametrize('mutation', ['fixture_hash', 'prediction_hash', 'decision', 'duplicate', 'unknown_id', 'baseline'])
def test_import_rejects_mismatched_evidence_without_writing(tmp_path, mutation):
    paths = write_inputs(tmp_path)
    data = json.loads(paths[0].read_text())
    if mutation == 'fixture_hash':
        data['fixtures_sha256'] = '0' * 64
    elif mutation == 'prediction_hash':
        data['predictions_sha256'] = '0' * 64
    elif mutation == 'decision':
        data['reviews'][0]['review']['decision'] = 'keep'
    elif mutation == 'duplicate':
        data['reviews'].append(copy.deepcopy(data['reviews'][0]))
    elif mutation == 'unknown_id':
        data['reviews'][0].update(key='other:3', case_id='other')
    else:
        data['code_head'] = 'other'
    paths[0].write_text(json.dumps(data), encoding='utf-8')

    with pytest.raises(ValueError):
        import_reviews(*paths, baseline_code_head='baseline')

    assert sorted(path.name for path in tmp_path.iterdir()) == ['fixtures.jsonl', 'predictions.jsonl', 'review.json', 'summary.csv']


def test_cli_defaults_to_dry_run_and_existing_output_is_never_overwritten(tmp_path, capsys):
    paths = write_inputs(tmp_path)
    arguments = ['--review-json', str(paths[0]), '--review-csv', str(paths[1]),
                 '--fixtures', str(paths[2][0]), '--predictions', str(paths[3]),
                 '--baseline-code-head', 'baseline']
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)['dry_run'] is True
    output = tmp_path / 'manifest.json'
    output.write_text('original', encoding='utf-8')
    assert main([*arguments, '--output', str(output)]) == 1
    assert output.read_text() == 'original'


def test_unconfirmed_null_timestamp_is_not_a_review(tmp_path):
    paths = write_inputs(tmp_path)
    document = json.loads(paths[0].read_text())
    document['reviews'][0]['review'].update(decision='', confirmed_at=None)
    paths[0].write_text(json.dumps(document), encoding='utf-8')
    text = paths[1].read_text().replace('discuss,2026-10-27', ',')
    paths[1].write_text(text, encoding='utf-8')

    report = import_reviews(*paths, baseline_code_head='baseline')

    assert report['confirmed_review_turns'] == 0
    assert report['candidates'] == []
