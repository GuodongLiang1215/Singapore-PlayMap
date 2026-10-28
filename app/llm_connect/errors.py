"""Only app-authored messages reach the terminal/report; never provider bodies."""
from __future__ import annotations
from .error_diagnostics import safe_diagnostic, KINDS

MESSAGES = {'KEY_MISSING': 'Gemini is not configured. Run scripts/setup_gemini.py first.',
 'KEY_FORMAT': 'The key failed local input checks. Copy the complete key from AI Studio. Dots and long keys '
               'are allowed; this is not a Google validity check.',
 'KEY_LENGTH': 'The input is outside the local 20–16384 character guard. Check that you copied one complete '
               'key. This is not an official Google length specification.',
 'KEY_CONTROL': 'Terminal control characters were found. Run scripts/setup_gemini.py --gui and paste the key '
                'into the password field.',
 'KEY_MULTILINE': 'Multiple lines were found. Copy one complete key, not a code block. Internal line breaks '
                  'are not removed automatically.',
 'KEY_WHITESPACE': 'The key contains whitespace. Copy the complete key again; internal characters are not '
                   'removed automatically.',
 'KEY_INVISIBLE': 'Invisible characters were found. Copy the complete key again; key characters are not '
                  'rewritten.',
 'KEY_WRAPPER': 'The input includes an assignment, quotes, URL or code wrapper. Enter only the key itself.',
 'KEY_MASKED': 'The input appears to be a masked preview. Use the copy button on the key page to get the '
               'complete value.',
 'KEY_PLACEHOLDER': 'The input is an example placeholder. Copy the complete key from your own AI Studio '
                    'project.',
 'GUI_UNAVAILABLE': 'The local password window is unavailable. Use the interactive terminal or update '
                    'GEMINI_API_KEY, LLM_PROVIDER and LLM_MODEL in your local .env file. Do not share that '
                    'file.',
 'MODEL_FORMAT': 'Invalid model identifier. Use an API identifier such as gemini-3.5-flash-lite.',
 'PROVIDER_MISMATCH': 'LLM_PROVIDER is not gemini. The provider will not be changed automatically.',
 'ENV_FILE': 'Cannot safely read or write .env. Check UTF-8 encoding, file permissions and symbolic links.',
 'ENV_CONFLICT': '.env changed while saving and was not replaced. Close other configuration editors and try '
                 'again.',
 'HIDDEN_INPUT': 'Hidden input is unavailable. Run this in an interactive PowerShell terminal in VS Code.',
 'KEY_INVALID': 'The provider rejected this key. Check its status, project and API restrictions in AI '
                'Studio.',
 'ACCESS_DENIED': 'The provider denied access. Check key restrictions, project permissions and regional '
                  'service availability.',
 'MODEL_NOT_AVAILABLE': 'The specified model is unavailable with this key. No model was substituted. Use '
                        '--list-models to inspect the returned list.',
 'RATE_LIMIT': 'The provider returned HTTP 429, which may indicate a rate or quota limit. No automatic retry '
               'was made. Check your project rate limits.',
 'BAD_REQUEST': 'The provider rejected the request parameters. No automatic retry, model substitution or '
                'validation bypass was made.',
 'REDIRECT_BLOCKED': 'The API returned a redirect. It was blocked to avoid sending the key to another '
                     'address.',
 'PROVIDER_UNAVAILABLE': 'The provider is temporarily unavailable or returned an unexpected status. No '
                         'automatic retry was made.',
 'NETWORK_ERROR': 'Cannot connect to the official API. Check your network, proxy and certificates. Keep TLS '
                  'certificate verification enabled.',
 'TIMEOUT': 'The official API request timed out. The server may already have processed it; no automatic '
            'retry was made to avoid extra usage.',
 'BAD_RESPONSE': 'The provider response is invalid or exceeds the size limit. Its raw body was not saved.',
 'MODEL_METADATA': 'Model metadata did not match the request or did not declare generateContent support. No '
                   'generation call was made.',
 'GENERATION_BLOCKED': 'The provider blocked the response or returned no candidate. No model result was '
                       'fabricated.',
 'INCOMPLETE_OUTPUT': 'The model output was incomplete or truncated. It was not accepted as a successful '
                      'result.',
 'SCHEMA_MISMATCH': 'The model output failed local schema validation. Missing fields were not filled '
                    'automatically.',
 'SEMANTIC_MISMATCH': 'The response structure is valid but does not match the test request. The check did '
                      'not pass.',
 'UNEXPECTED_TOOL': 'The response included a non-text action although tools are disabled. The action was '
                    'rejected.',
 'UNEXPECTED_ERROR': 'An unexpected local error occurred. Raw exception text is withheld to protect '
                     'credentials.',
 'CANCELLED': 'The check was cancelled. Its cancellation status was recorded.',
 'NO_MODEL_LIST': 'The model list is invalid or pagination is incomplete. A complete list has not been '
                  'confirmed.'}

class LLMConnectionError(Exception):
    def __init__(self, code: str, http_status: int | None = None, *, diagnostic: dict | None = None):
        self.code = code if code in MESSAGES else 'UNEXPECTED_ERROR'
        self.message = MESSAGES[self.code]
        self.http_status = http_status
        self.diagnostic = safe_diagnostic(diagnostic)
        if self.code == 'BAD_REQUEST' and self.diagnostic:
            self.message += ' ' + KINDS[self.diagnostic['kind']]
        super().__init__(self.message)

    def public(self) -> dict:
        result = {'code': self.code, 'message': self.message, 'http_status': self.http_status}
        if self.diagnostic is not None: result['provider_diagnostic'] = self.diagnostic
        return result
