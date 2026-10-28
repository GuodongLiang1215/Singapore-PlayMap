"""Bounded activity aliases and fine place types for catalogue retrieval.

Exact, deliberately bounded aliases only. A restaurant, cafe, cuisine, halal or
vegetarian condition is NOT an alias for all food records. No fallback drops
such conditions, no provider is called here, and source data is never changed.
"""
from __future__ import annotations
import re
import unicodedata
from .models import PlaceSpec


def normalise(value: str) -> str:
    return ' '.join(re.sub(r'[^\w\s]', ' ', unicodedata.normalize('NFKC', value).casefold()).split())


# These describe activities/classes, not verified amenities or specific venues.
CATEGORY_INTENT_ALIASES = {
    'food': {
        'food', 'foods', 'dining', 'dine', 'eat', 'eating', 'meal', 'meals',
        'lunch', 'dinner', 'breakfast', 'food place', 'food places', 'place to eat',
        'places to eat', 'eating place', 'eating places', 'dining place', 'dining places',
        '吃饭', '用餐', '餐饮', '吃东西', '吃点东西', '找吃的', '美食',
        '吃饭的地方', '吃饭地点', '用餐地点', '用餐场所', '餐饮地点', '餐饮场所',
        '午餐', '晚餐', '早餐',
    },
    'nature': {'nature', '自然', '自然景观'},
}
ALIAS_CATEGORY = {normalise(term): cat for cat, terms in CATEGORY_INTENT_ALIASES.items() for term in terms}

PLACE_TYPE_ALIASES = {
    'park': {'park', 'parks', '公园'},
    'cafe': {'cafe', 'cafes', 'café', 'cafés', 'coffee shop', 'coffee shops', '咖啡馆', '咖啡店'},
    'restaurant': {'restaurant', 'restaurants', '餐厅', '餐馆'},
    'museum': {'museum', 'museums', '博物馆'},
}
SUPPORTED_PLACE_TYPES = frozenset(PLACE_TYPE_ALIASES)
PLACE_TYPE_CATEGORY = {'park': 'nature', 'cafe': 'food', 'restaurant': 'food', 'museum': 'tourism'}
PLACE_TYPE_BY_ALIAS = {normalise(term): place_type for place_type, terms in PLACE_TYPE_ALIASES.items()
                      for term in terms}


def canonical_place_type(term: str) -> str | None:
    return PLACE_TYPE_BY_ALIAS.get(normalise(term))


def canonical_spec(spec: PlaceSpec, operation: str | None = None):
    """Return a new spec and bounded normalization facts without mutating input.

    Generic queries are converted only for visit discovery/replacement, not
    endpoint geocoding. Category synonyms in keywords are not extra must-match
    name filters. More specific keywords keep the existing OR-within-list rule.
    """
    categories = list(spec.categories)
    query = spec.query.strip()
    normalized_count = 0
    query_normalized = False
    query_type = canonical_place_type(query)
    query_category = PLACE_TYPE_CATEGORY.get(query_type) or ALIAS_CATEGORY.get(normalise(query))
    keywords = list(spec.keywords)
    if operation in ('add_visit', 'replace_visit') and query_category and (
        not categories or query_category in categories
    ):
        if not categories:
            categories.append(query_category)
        if query_type and query_type not in keywords and len(keywords)<6:
            keywords.append(query_type)
        query = ''
        query_normalized = True
        normalized_count += 1

    # When only generic activity keywords are present, they can identify a class.
    # Explicit place names retain their search semantics and are never generalized.
    if not categories and not query:
        categories = list(dict.fromkeys(category for k in keywords
                         if (category := PLACE_TYPE_CATEGORY.get(canonical_place_type(k))
                             or ALIAS_CATEGORY.get(normalise(k)))))
    specific = []
    for keyword in keywords:
        term = keyword.strip()
        if not term:
            continue
        category = ALIAS_CATEGORY.get(normalise(term))
        if category and category in categories:
            normalized_count += 1
        elif term not in specific:
            specific.append(term)
    effective = PlaceSpec(query=query, categories=categories, keywords=specific)
    return effective, {
        'generic_intent_terms_normalized': normalized_count,
        'generic_query_normalized': query_normalized,
        # Keep the fine type even when all six keyword positions are already occupied.
        'query_place_type':query_type if query_normalized else None,
        'specific_keyword_count': len(specific),
        'input_keyword_count': len(spec.keywords),
        'explicit_query_present': bool(query),
        'effective_categories': categories,
        'specific_requirements_relaxed': False,
    }
