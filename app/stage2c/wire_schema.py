"""Small provider-facing schemas, separate from strict LOCAL domain validation.

Do not send the full Action union grammar to the constrained decoder. All
operations remain available. F4 assigns a separate argument object to each operation.
command_contract.py compiles typed commands; operation_adapter.py resolves
explicit target_position values; models.py and engine.py still validate real
operations, bounds, fields, references and action evidence before adoption.

This is a compatibility change, NOT a claim that Gemini rejects all $ref/anyOf.
Google documents both; large combined constraints can still be rejected.
"""
from __future__ import annotations
import json
from typing import get_args
from .models import Topic, Category

WIRE_VERSION = '2C1-F5'
OPERATIONS = (
    'set_origin', 'set_finish', 'add_visit', 'replace_visit',
    'remove_visit', 'move_visit', 'set_stay', 'set_finish_policy',
    'set_mode', 'set_time', 'clear_visits',
)


def _object(properties: dict, required: list[str]) -> dict:
    return {'type': 'object', 'properties': properties,
            'required': required, 'additionalProperties': False}


def _list(item: dict) -> dict:
    return {'type': 'array', 'items': item}


def _text() -> dict:
    return {'type': 'string'}


def _nullable(kind: str) -> dict:
    return {'type': [kind, 'null']}


def interpretation_schema() -> dict:
    # F4 uses operation-scoped arrays instead of a shared argument envelope.
    from .command_contract import scoped_schema
    return scoped_schema()


def selection_schema() -> dict:
    return _object({'picks': _list(_object({
        'slot_id': _text(), 'candidate_key': _text(), 'reason': _text(),
    }, ['slot_id', 'candidate_key', 'reason']))}, ['picks'])


def schema_stats(schema: dict) -> dict:
    """Shape metadata ONLY, never contents of a user request or credentials."""
    counts = {'references': 0, 'union_nodes': 0, 'schema_nodes': 0, 'max_depth': 0}
    def visit(node, depth=0):
        if isinstance(node, dict):
            counts['schema_nodes'] += 1
            counts['max_depth'] = max(counts['max_depth'], depth)
            counts['references'] += int('$ref' in node)
            counts['union_nodes'] += int(any(k in node for k in ('anyOf', 'oneOf', 'allOf')))
            for value in node.values():
                if isinstance(value, (dict, list)): visit(value, depth+1)
        elif isinstance(node, list):
            for value in node:
                if isinstance(value, (dict, list)): visit(value, depth)
    visit(schema)
    return {'json_bytes': len(json.dumps(schema, ensure_ascii=False, separators=(',', ':')).encode('utf-8')), **counts}
