"""Application-authored spatial clarification, independent of model prose."""
from copy import deepcopy

import pytest

from app.stage2c.models import Interpretation
from app.stage2c.proposal_status import acknowledgement


def slot(operation, categories=(), *, options=(), spatial_status=None):
    return {'operation': operation, 'categories': list(categories),
            'options': list(options),
            'spatial_requirement': None if spatial_status is None else {'status': spatial_status}}


def message(slots):
    parsed = Interpretation(acknowledgement='The model claims everything is ready.')
    preview = {'can_apply': False, 'draft': {'visits': [
        {'visit_id': 'accepted-park', 'stay_minutes': 30},
        {'visit_id': 'accepted-cafe', 'stay_minutes': 45}]}}
    original = deepcopy((slots, preview, parsed.model_dump()))
    result = acknowledgement(parsed, slots, preview, 'Find a cafe and a park nearby.')
    assert (slots, preview, parsed.model_dump()) == original
    assert result != parsed.acknowledgement
    return result


@pytest.mark.parametrize('status', ['unresolved', 'conflicting'])
def test_unresolved_reference_requests_exact_station_or_exit_reply_not_a_failed_place_search(status):
    slots = [
        slot('set_origin', options=[{'label': 'DHOBY GHAUT MRT STATION (CC1)'},
                                    {'label': 'DHOBY GHAUT MRT STATION EXIT A'}]),
        slot('add_visit', ['food'], spatial_status=status),
        slot('add_visit', ['nature'], spatial_status=status),
    ]
    text = message(slots)
    assert 'Nearby search' in text and 'specific reference location' in text
    assert 'item 2 (dining)' in text and 'item 3 (parks and nature)' in text
    assert 'Reply in chat with the exact station or exit name from the starting-point candidates' in text
    assert 'No matching candidates' not in text
    assert 'Your itinerary is unchanged.' in text
    assert 'automatically' not in text and 'dropdown' not in text


def test_mixed_empty_slots_keep_unrelated_missing_place_information_separate():
    text = message([
        slot('set_origin', options=[{'label': 'DHOBY GHAUT MRT STATION EXIT A'}]),
        slot('add_visit', ['nature'], spatial_status='unresolved'),
        slot('add_visit', ['food']),
    ])
    assert 'Nearby search for item 2 (parks and nature)' in text
    assert 'No matching candidates were found for item 3 (dining).' in text
    assert 'No matching candidates were found for item 2' not in text
    assert 'Resolve all requested places before confirming.' in text


def test_no_starting_point_candidates_requests_a_reference_without_inventing_a_list():
    text = message([slot('add_visit', ['nature'], spatial_status='unresolved')])
    assert 'specific reference location' in text
    assert 'Reply in chat with the exact reference place name' in text
    assert 'starting-point candidates' not in text
    assert 'No matching candidates' not in text


@pytest.mark.parametrize('status', [None, 'applied'])
def test_nonspatial_missing_candidate_message_remains_compatible(status):
    text = message([slot('add_visit', ['food'], spatial_status=status)])
    assert text == ('The proposal is incomplete. No matching candidates were found for item 1 (dining). '
                    'Resolve all requested places before confirming. Your itinerary is unchanged.')


def test_spatial_metadata_does_not_override_available_candidates():
    text = message([slot('add_visit', ['nature'], options=[{'label': 'Park'}],
                         spatial_status='unresolved')])
    assert text == ('Place candidates are ready for review. Confirm the proposal to update your itinerary '
                    'and calculate routes and estimated times.')
