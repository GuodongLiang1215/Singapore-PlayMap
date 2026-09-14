"""Classify a provider error without returning its arbitrary text or identifiers.

All public strings are picked from local allowlists. No API key, project id,
request text, coordinates, response body, trace id or raw exception is exported.
An UNKNOWN classification remains unknown rather than blaming schema by default.
"""
from __future__ import annotations
import json

KINDS = {
    'SCHEMA_COMPLEXITY': '服务商提示输出结构过于复杂；请保留本次诊断。',
    'SCHEMA_FIELD': '服务商提示输出结构字段或引用不被接受；请保留本次诊断。',
    'SCHEMA_VALIDATION': '服务商提示输出结构校验未通过；请保留本次诊断。',
    'GENERATION_PARAMETER': '服务商提示生成参数不被接受；请保留本次诊断。',
    'BILLING_OR_REGION': '服务商提示计费或地区前提未满足；仅凭这条分类不能判断具体原因。',
    'UNKNOWN': '没有取得可安全识别的具体原因；请导出本地诊断，不要反复点击发送。',
}
STATUSES = frozenset({'INVALID_ARGUMENT', 'FAILED_PRECONDITION', 'UNAUTHENTICATED',
                     'PERMISSION_DENIED', 'NOT_FOUND', 'RESOURCE_EXHAUSTED',
                     'INTERNAL', 'UNAVAILABLE', 'DEADLINE_EXCEEDED', 'UNKNOWN'})
# Canonical names only; no provider supplied field/path is copied to a report.
FIELD_ALIASES = {
    'generationConfig.responseJsonSchema': ('responsejsonschema', 'response_json_schema'),
    'generationConfig.responseSchema': ('response_schema', 'responseschema'),
    'generationConfig.responseFormat': ('responseformat', 'response_format'),
    'generationConfig.responseMimeType': ('responsemimetype', 'response_mime_type'),
    'generationConfig.maxOutputTokens': ('maxoutputtokens', 'max_output_tokens'),
    'generationConfig.candidateCount': ('candidatecount', 'candidate_count'),
    'generationConfig.thinkingConfig': ('thinkingconfig', 'thinking_config'),
    'generationConfig.temperature': ('temperature',),
    'systemInstruction': ('systeminstruction', 'system_instruction'),
    'contents': ('contents',),
    '$defs': ('$defs',), '$ref': ('$ref',), 'anyOf': ('anyof', 'any_of'),
    'oneOf': ('oneof', 'one_of'), 'const': ('"const"', "'const'"),
    'exclusiveMinimum': ('exclusiveminimum', 'exclusive_minimum'),
    'maxLength': ('maxlength', 'max_length'), 'minLength': ('minlength', 'min_length'),
}


def safe_diagnostic(value: dict | None) -> dict | None:
    if not isinstance(value, dict): return None
    kind = value.get('kind')
    if not isinstance(kind, str) or kind not in KINDS: kind = 'UNKNOWN'
    status = value.get('provider_status')
    if not isinstance(status, str) or status not in STATUSES: status = None
    fields = value.get('field_hints')
    if not isinstance(fields, list): fields = []
    # Iterate local keys, not arbitrary external dictionary keys.
    fields = [name for name in FIELD_ALIASES if name in fields][:10]
    return {'kind': kind, 'provider_status': status, 'field_hints': fields,
            'raw_provider_body_included': False}


def classify_provider_error(raw: bytes) -> dict:
    # No decode exception can escape to the UI, even for malformed HTML errors.
    text = raw[:32768].decode('utf-8', errors='ignore')
    status = None
    try:
        data = json.loads(text)
        error = data.get('error', {}) if isinstance(data, dict) else {}
        if isinstance(error, dict):
            status = error.get('status')
            # Inspect message and descriptions only; ignore metadata IDs/URLs.
            pieces = [error.get('message', '')]
            details = error.get('details', [])
            for item in (details if isinstance(details, list) else [])[:20]:
                if not isinstance(item, dict): continue
                violations = item.get('fieldViolations', [])
                for v in (violations if isinstance(violations, list) else [])[:20]:
                    if isinstance(v, dict): pieces.extend([v.get('field', ''), v.get('description', '')])
            text = ' '.join(s for s in pieces if isinstance(s, str))
    except (ValueError, UnicodeError, RecursionError):
        pass
    s = text.lower()
    fields = [key for key, aliases in FIELD_ALIASES.items() if any(a in s for a in aliases)]
    if ('too many states' in s or 'schema is too complex' in s or
        ('schema' in s and any(x in s for x in ('too complex', 'too large', 'too deeply nested', 'nesting depth')))):
        kind = 'SCHEMA_COMPLEXITY'
    elif ('schema' in s or '$ref' in s) and any(x in s for x in ('unsupported', 'not supported', 'unknown name', 'unrecognized', 'unrecognised')):
        kind = 'SCHEMA_FIELD'
    elif 'schema' in s or '$ref' in s:
        kind = 'SCHEMA_VALIDATION'
    elif any(x in s for x in ('billing', 'user location', 'country is not supported', 'region is not supported')):
        kind = 'BILLING_OR_REGION'
    elif fields:
        kind = 'GENERATION_PARAMETER'
    else:
        kind = 'UNKNOWN'
    return safe_diagnostic({'kind': kind, 'provider_status': status, 'field_hints': fields})
