"""Bounded field vocabulary shared by memory, validation and model context."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RequirementField:
    cardinality: str
    capability: str
    reason: str


FIELDS = {
    'place_type': RequirementField('scalar', 'catalogue_type', 'Keep a requested venue type narrower than the five recall categories.'),
    'category': RequirementField('scalar', 'catalogue_type', 'Preserve an explicit broad recall category.'),
    'keyword': RequirementField('set', 'catalogue_text', 'Keep independent requested attributes during candidate filtering.'),
    'max_walk_minutes': RequirementField('scalar', 'route', 'Pass an explicit per-leg limit to the existing route checker.'),
    'budget_minutes': RequirementField('scalar', 'budget', 'Pass an explicit total-time limit to the existing budget checker.'),
    'departure_at': RequirementField('scalar', 'parameter', 'Retain the user-selected dated departure instant.'),
    'finish_by': RequirementField('scalar', 'parameter', 'Retain the user-selected dated deadline.'),
    'transport_mode': RequirementField('scalar', 'parameter', 'Select one supported route mode.'),
    'stay_minutes': RequirementField('scalar', 'parameter', 'Keep a visit-specific explicit stay override.'),
    'finish_policy': RequirementField('scalar', 'parameter', 'Keep the requested return or end policy.'),
    'takeaway': RequirementField('scalar', 'parameter', 'Select the data-provided takeaway duration profile.'),
    'excluded_topic': RequirementField('set', 'catalogue_filter', 'Apply exclusions with an explicit global or future-only scope.'),
    'theme': RequirementField('set', 'preference', 'Remember multiple independent outing interests.'),
    'drink': RequirementField('scalar', 'preference', 'Remember a drink request without inventing a global food preference.'),
    'pace': RequirementField('scalar', 'preference', 'Retain qualitative pace without manufacturing a time budget.'),
    'environment': RequirementField('set', 'unverified_attribute', 'Retain indoor or outdoor requests without claiming a verified venue property.'),
    'walking_effort': RequirementField('scalar', 'preference', 'Retain less-walking wishes without clearing a numeric cap.'),
    'duration': RequirementField('scalar', 'preference', 'Retain qualitative trip length without guessing minutes.'),
    'opening_hours': RequirementField('scalar', 'unverified_attribute', 'Keep opening requirements visible until a supported availability check exists.'),
    'wheelchair_access': RequirementField('scalar', 'unsupported', 'Expose the absence of verified step-free and accessibility data.'),
    'dietary': RequirementField('set', 'unverified_attribute', 'Keep dietary conditions separate from venue type evidence.'),
    'radius_m': RequirementField('scalar', 'spatial', 'Keep a straight-line radius separate from walking-time verification.'),
    'spatial_anchor': RequirementField('scalar', 'spatial', 'Name the centre of a proximity request instead of replacing the target query.'),
    'area': RequirementField('scalar', 'unsupported', 'Retain area requests when the planner has no authoritative boundary solver.'),
    'time_window': RequirementField('scalar', 'unsupported', 'Retain per-activity windows that the current route calculator cannot enforce.'),
}

ALIASES = {
    'travel_mode': 'transport_mode', 'must_be_open': 'opening_hours',
    'last_admission': 'opening_hours', 'accessibility': 'wheelchair_access',
    'step_free': 'wheelchair_access', 'dietary_requirement': 'dietary',
    'near': 'spatial_anchor', 'anchor': 'spatial_anchor',
}


def canonical_requirement_name(name):
    return ALIASES.get(name, name)


def capability_summary():
    return {name: {'capability': rule.capability, 'cardinality': rule.cardinality,
                   'reason': rule.reason}
            for name, rule in FIELDS.items()}
