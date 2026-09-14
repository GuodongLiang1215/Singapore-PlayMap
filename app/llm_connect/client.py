"""Official HTTPS REST adapter. No SDK, proxy workaround, retries or model fallback."""
from __future__ import annotations
from dataclasses import dataclass
import json
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .config import GeminiSettings, clean_model
from .errors import LLMConnectionError
from .error_diagnostics import classify_provider_error

BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/'
MAX_RESPONSE_BYTES = 1024 * 1024
TOKEN_FIELDS = ('promptTokenCount', 'candidatesTokenCount', 'thoughtsTokenCount',
                'totalTokenCount', 'cachedContentTokenCount')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_error(status: int, raw: bytes = b'') -> LLMConnectionError:
    # Read only to classify known errors. Never return provider text or identifiers.
    text = raw[:32768].decode('utf-8', errors='ignore')
    if status == 400 and any(s in text for s in ('API_KEY_INVALID', 'API key not valid')):
        code = 'KEY_INVALID'
    elif status == 401: code = 'KEY_INVALID'
    elif status == 403: code = 'ACCESS_DENIED'
    elif status == 404: code = 'MODEL_NOT_AVAILABLE'
    elif status == 429: code = 'RATE_LIMIT'
    elif 300 <= status < 400: code = 'REDIRECT_BLOCKED'
    elif status == 400: code = 'BAD_REQUEST'
    else: code = 'PROVIDER_UNAVAILABLE'
    diagnostic = classify_provider_error(raw) if status == 400 and code == 'BAD_REQUEST' else None
    return LLMConnectionError(code, status, diagnostic=diagnostic)


class GeminiREST:
    """Credential value is kept in memory and only sent in x-goog-api-key."""
    def __init__(self, settings: GeminiSettings, *, opener=None, timeout: float = 60.0):
        self.settings = settings
        self.opener = opener if opener is not None else build_opener(NoRedirect())
        self.timeout = timeout
        self.metadata_requests = 0
        self.generation_requests = 0

    def _request(self, path: str, body: dict | None = None) -> dict:
        # All callers below construct relative paths using validated IDs or urlencode.
        if not path.startswith('models') or '://' in path or path.startswith('//'):
            raise LLMConnectionError('BAD_REQUEST')
        headers = {'x-goog-api-key': self.settings.key,
                   'Accept': 'application/json', 'User-Agent': 'Singapore-PlayMap-Stage2C0/1.0'}
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json; charset=utf-8'
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.generation_requests += 1
        else:
            self.metadata_requests += 1
        request = Request(BASE_URL + path, data=data, headers=headers,
                          method='POST' if data is not None else 'GET')
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = response.getcode()
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if status != 200:
                    raise http_error(status, raw)
        except HTTPError as error:
            try: raw = error.read(32768)
            except Exception: raw = b''
            finally: error.close()
            raise http_error(error.code, raw) from None
        except (TimeoutError, socket.timeout):
            raise LLMConnectionError('TIMEOUT') from None
        except (URLError, OSError):
            raise LLMConnectionError('NETWORK_ERROR') from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LLMConnectionError('BAD_RESPONSE')
        try:
            value = json.loads(raw)
            if not isinstance(value, dict): raise ValueError()
        except (ValueError, UnicodeError):
            raise LLMConnectionError('BAD_RESPONSE') from None
        if 'error' in value:
            error = value['error']
            status = error.get('code', 500) if isinstance(error, dict) else 500
            raise http_error(status if type(status) is int else 500, raw)
        return value

    def model_info(self) -> dict:
        expected = 'models/' + self.settings.model
        result = self._request(expected)
        methods = result.get('supportedGenerationMethods')
        if result.get('name') != expected or not isinstance(methods, list) or 'generateContent' not in methods:
            raise LLMConnectionError('MODEL_METADATA')
        return {'model': self.settings.model, 'generate_content_supported': True,
                'generation_permission_confirmed': False,
                'quota_or_free_tier_confirmed': False}

    def list_models(self) -> list[str]:
        names, token, seen_tokens = set(), None, set()
        for _ in range(5):
            query = {'pageSize': 1000}
            if token is not None: query['pageToken'] = token
            result = self._request('models?' + urlencode(query))
            models = result.get('models')
            if not isinstance(models, list):
                raise LLMConnectionError('NO_MODEL_LIST')
            for record in models:
                if not isinstance(record, dict): continue
                methods = record.get('supportedGenerationMethods', [])
                if not isinstance(methods, list) or 'generateContent' not in methods: continue
                name = record.get('name')
                if not isinstance(name, str) or not name.startswith('models/'): continue
                try: names.add(clean_model(name))
                except LLMConnectionError: continue
            token = result.get('nextPageToken')
            if not token: return sorted(names)
            if not isinstance(token, str) or len(token) > 8192 or token in seen_tokens:
                raise LLMConnectionError('NO_MODEL_LIST')
            seen_tokens.add(token)
        raise LLMConnectionError('NO_MODEL_LIST')

    def generate_json(self, *, instruction: str, payload: dict, schema: dict) -> dict:
        body = {
            'systemInstruction': {'parts': [{'text': instruction}]},
            'contents': [{'role': 'user', 'parts': [{'text': json.dumps(payload, ensure_ascii=False)}]}],
            'generationConfig': {
                'candidateCount': 1,
                'maxOutputTokens': 2048,
                'responseMimeType': 'application/json',
                'responseJsonSchema': schema,
            },
        }
        # Temperature and thinking use model defaults. No Search, Maps, tools or files.
        started = time.monotonic()
        result = self._request('models/' + self.settings.model + ':generateContent', body)
        elapsed = round(time.monotonic() - started, 3)
        feedback = result.get('promptFeedback')
        if isinstance(feedback, dict) and feedback.get('blockReason'):
            raise LLMConnectionError('GENERATION_BLOCKED')
        candidates = result.get('candidates')
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise LLMConnectionError('GENERATION_BLOCKED')
        candidate = candidates[0]
        if candidate.get('finishReason') != 'STOP':
            raise LLMConnectionError('INCOMPLETE_OUTPUT')
        content = candidate.get('content')
        parts = content.get('parts') if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise LLMConnectionError('BAD_RESPONSE')
        pieces = []
        for part in parts:
            if not isinstance(part, dict): raise LLMConnectionError('BAD_RESPONSE')
            if any(k in part for k in ('functionCall', 'executableCode', 'codeExecutionResult', 'inlineData')):
                raise LLMConnectionError('UNEXPECTED_TOOL')
            # Never print or export reasoning/thought blocks.
            if part.get('thought') is True: continue
            if isinstance(part.get('text'), str): pieces.append(part['text'])
        text = ''.join(pieces)
        if not text or len(text) > 65536:
            raise LLMConnectionError('SCHEMA_MISMATCH')
        try:
            structured = json.loads(text)
            if not isinstance(structured, dict): raise ValueError()
        except (ValueError, UnicodeError):
            raise LLMConnectionError('SCHEMA_MISMATCH') from None
        usage = result.get('usageMetadata')
        usage = usage if isinstance(usage, dict) else {}
        token_counts = {k: usage[k] for k in TOKEN_FIELDS
                        if type(usage.get(k)) is int and 0 <= usage[k] <= 1000000000}
        version = result.get('modelVersion')
        if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9._/-]{1,120}', version):
            version = None
        return {'data': structured, 'metadata': {'elapsed_s': elapsed, 'finish_reason': 'STOP',
                'reported_model_version': version, 'usage': token_counts}}
