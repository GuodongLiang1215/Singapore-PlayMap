"""Scoped .env updates; all non-LLM lines are retained without reinterpretation."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import tempfile
from .errors import LLMConnectionError

DEFAULT_MODEL = 'gemini-3.5-flash-lite'
OWNED_KEYS = ('GEMINI_API_KEY', 'LLM_PROVIDER', 'LLM_MODEL')
MAX_ENV_BYTES = 256 * 1024
ASSIGNMENT = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=')


# Local transport/input guards, NOT a specification of Google's key formats.
# Do not assume a prefix, a 39-character length, or an alphanumeric-only token.
MAX_KEY_CHARS = 16384
KEY_TOKEN = re.compile(r'[A-Za-z0-9._~+/=-]+', re.ASCII)
PLACEHOLDER_KEYS = {
    'your_gemini_api_key_here', 'replace_with_your_api_key',
    'paste_your_api_key_here', 'your_api_key_here', 'your_api_key',
}


def clean_key(value: str) -> str:
    """Accept opaque single-line key tokens; preserve every internal character.

    Only surrounding whitespace is removed. Never decode, truncate or strip a
    prefix/punctuation to make a credential pass. Authenticity is checked by the
    provider, not by this local guard. Errors contain no credential fragments.
    """
    if not isinstance(value, str):
        raise LLMConnectionError('KEY_FORMAT')
    value = value.strip()
    if not value:
        raise LLMConnectionError('KEY_MISSING')
    if '\r' in value or '\n' in value:
        raise LLMConnectionError('KEY_MULTILINE')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise LLMConnectionError('KEY_CONTROL')
    if any(c.isspace() for c in value):
        raise LLMConnectionError('KEY_WHITESPACE')
    if any(c in value for c in ('\u200b', '\u200c', '\u200d', '\u2060', '\ufeff')):
        raise LLMConnectionError('KEY_INVISIBLE')
    if value.lower() in PLACEHOLDER_KEYS:
        raise LLMConnectionError('KEY_PLACEHOLDER')
    if value.startswith(('GEMINI_API_KEY=', 'GOOGLE_API_KEY=', '$env:', 'http:', 'https:')) or any(c in value for c in ('"', "'", '{', '}', '<', '>', '`')):
        raise LLMConnectionError('KEY_WRAPPER')
    if '...' in value or any(c in value for c in ('*', '\u2026', '\u2022', '\u25cf')):
        raise LLMConnectionError('KEY_MASKED')
    if not 20 <= len(value) <= MAX_KEY_CHARS:
        raise LLMConnectionError('KEY_LENGTH')
    if not KEY_TOKEN.fullmatch(value):
        raise LLMConnectionError('KEY_FORMAT')
    return value


def clean_model(value: str) -> str:
    value = value.strip()
    if value.startswith('models/'):
        value = value[len('models/'):]
    if not re.fullmatch(r'gemini-[a-z0-9][a-z0-9._-]{0,95}', value):
        raise LLMConnectionError('MODEL_FORMAT')
    return value


def _env_bytes(path: Path) -> bytes:
    try:
        if path.is_symlink():
            raise LLMConnectionError('ENV_FILE')
        if not path.exists():
            return b''
        if not path.is_file() or path.stat().st_size > MAX_ENV_BYTES:
            raise LLMConnectionError('ENV_FILE')
        data = path.read_bytes()
        if len(data) > MAX_ENV_BYTES:
            raise LLMConnectionError('ENV_FILE')
        data.decode('utf-8-sig')
        return data
    except (OSError, UnicodeError):
        raise LLMConnectionError('ENV_FILE') from None


def _parse_value(text: str) -> str:
    text = text.strip()
    if text[:1] in ('"', "'"):
        quote = text[0]
        end = text.find(quote, 1)
        if end < 0 or (text[end + 1:].strip() and not text[end + 1:].lstrip().startswith('#')):
            raise LLMConnectionError('ENV_FILE')
        return text[1:end]
    return re.split(r'\s+#', text, maxsplit=1)[0].strip()


def read_owned(root: Path) -> dict[str, str]:
    text = _env_bytes(Path(root) / '.env').decode('utf-8-sig')
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        if match and match.group(1) in OWNED_KEYS:
            values[match.group(1)] = _parse_value(line[match.end():])
    return values


@dataclass(frozen=True)
class GeminiSettings:
    key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    key_source: str = 'local_env_file'
    model_source: str = 'default'

    def public(self) -> dict:
        return {'provider': 'gemini', 'configured': bool(self.key), 'model': self.model,
                'key_source': self.key_source, 'model_source': self.model_source,
                'key_value_exposed': False}


def load_settings(root: Path, environ: dict | None = None) -> GeminiSettings:
    env = os.environ if environ is None else environ
    local = read_owned(root)
    def selected(name: str, default: str = '') -> tuple[str, str]:
        external = env.get(name, '').strip()
        if external:
            return external, 'process_environment'
        file_value = local.get(name, '').strip()
        return (file_value, 'local_env_file') if file_value else (default, 'default')
    provider, _ = selected('LLM_PROVIDER', 'gemini')
    if provider.lower() != 'gemini':
        raise LLMConnectionError('PROVIDER_MISMATCH')
    key, key_source = selected('GEMINI_API_KEY')
    model, model_source = selected('LLM_MODEL', DEFAULT_MODEL)
    return GeminiSettings(clean_key(key), clean_model(model), key_source, model_source)


def persist_settings(root: Path, key: str, model: str = DEFAULT_MODEL) -> None:
    """Update only owned keys. No .env backup containing credentials is produced."""
    key, model = clean_key(key), clean_model(model)
    root = Path(root)
    path = root / '.env'
    original = _env_bytes(path)
    text = original.decode('utf-8-sig')
    bom = b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b''
    newline = '\r\n' if '\r\n' in text else '\n'
    replacements = {'GEMINI_API_KEY': key, 'LLM_PROVIDER': 'gemini', 'LLM_MODEL': model}
    result: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines(keepends=True):
        match = ASSIGNMENT.match(line)
        name = match.group(1) if match else None
        if name in replacements:
            if name not in seen:
                result.append(f'{name}={replacements[name]}{newline}')
                seen.add(name)
        else:
            result.append(line)
    if result and not result[-1].endswith(('\n', '\r')):
        result[-1] += newline
    for name in OWNED_KEYS:
        if name not in seen:
            result.append(f'{name}={replacements[name]}{newline}')
    output = bom + ''.join(result).encode('utf-8')
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix='.env.', suffix='.tmp', dir=root)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        if _env_bytes(path) != original:
            raise LLMConnectionError('ENV_CONFLICT')
        os.replace(tmp, path)
        tmp = None
        if os.name != 'nt':
            path.chmod(0o600)
    except OSError:
        raise LLMConnectionError('ENV_FILE') from None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
