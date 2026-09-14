"""OneMap token management for local and shared deployments.

Local development may keep a short-lived token in .env. Shared deployments can
store the OneMap account email/password as server-side environment variables;
the password is never persisted and refreshed tokens stay in process memory.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.stage2a.transport import ProviderError, check_payload_error

TOKEN_KEY = 'ONEMAP_ACCESS_TOKEN'
EXPIRY_KEY = 'ONEMAP_TOKEN_EXPIRES_AT'
EMAIL_KEYS = ('ONEMAP_EMAIL', 'ONEMAP_API_EMAIL')
PASSWORD_KEYS = ('ONEMAP_PASSWORD', 'ONEMAP_EMAIL_PASSWORD', 'ONEMAP_API_PASSWORD')


def env_values(root: Path) -> dict:
    path = Path(root) / '.env'
    if not path.exists():
        return {}
    if path.stat().st_size > 256 * 1024:
        raise ProviderError('ENV_TOO_LARGE', '本地.env文件异常，请检查配置。', 503)
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        s = line.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        k, v = s.split('=', 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        values[k] = v
    return values


def clean_token(token: str):
    token = token.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    if not token or token.lower() in ('your_token', 'your_access_token', 'replace_me'):
        raise ProviderError('TOKEN_REQUIRED', '尚未配置OneMap Token或自动刷新凭据。', 503)
    if len(token) > 8192 or len(token) < 8 or not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token):
        raise ProviderError('TOKEN_FORMAT', 'Token格式无效；应只输入access_token，不是整段JSON或账户密码。', 503)
    return token


def _first(values: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = os.environ.get(key, '').strip() or values.get(key, '').strip()
        if value:
            return value
    return ''


class TokenStore:
    """Prefer explicit tokens; refresh in memory when server-side credentials exist."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._runtime_token: str | None = None
        self._runtime_expiry: float | None = None
        self._refresh_lock = threading.Lock()

    def _local(self):
        try:
            return env_values(self.root)
        except (OSError, UnicodeError):
            raise ProviderError('ENV_UNREADABLE', '无法读取本机.env文件，请检查文件权限和UTF-8编码。', 503) from None

    def credentials(self) -> tuple[str, str]:
        local = self._local()
        email = _first(local, EMAIL_KEYS)
        password = _first(local, PASSWORD_KEYS)
        if bool(email) != bool(password):
            raise ProviderError('AUTO_AUTH_INCOMPLETE', 'OneMap自动刷新配置不完整；邮箱和密码必须同时配置。', 503)
        return email, password

    def can_refresh(self) -> bool:
        try:
            email, password = self.credentials()
            return bool(email and password)
        except ProviderError:
            return False

    def raw(self):
        now = time.time()
        if self._runtime_token and (self._runtime_expiry is None or self._runtime_expiry > now + 15):
            return self._runtime_token, self._runtime_expiry, 'runtime_refreshed'
        local = self._local()
        token = os.environ.get(TOKEN_KEY, '').strip()
        source = 'environment' if token else 'local_env_file'
        expiry = os.environ.get(EXPIRY_KEY, '') if token else local.get(EXPIRY_KEY, '')
        if not token:
            token = local.get(TOKEN_KEY, '')
        try:
            exp = float(expiry) if expiry else None
        except (ValueError, TypeError):
            exp = None
        if exp is not None and (exp != exp or abs(exp) == float('inf') or exp < 0 or exp > 4102444800):
            exp = None
        return token, exp, source

    def invalidate_runtime(self):
        with self._refresh_lock:
            self._runtime_token = None
            self._runtime_expiry = None

    def _refresh(self, transport, *, force=False):
        email, password = self.credentials()
        if not email or not password:
            raise ProviderError('TOKEN_REQUIRED', '尚未配置OneMap Token；共享部署可配置OneMap邮箱和密码用于自动刷新。', 503)
        with self._refresh_lock:
            if not force and self._runtime_token and (self._runtime_expiry is None or self._runtime_expiry > time.time() + 60):
                return self._runtime_token
            token, expiry = authenticate(transport, email, password)
            self._runtime_token = token
            self._runtime_expiry = float(expiry)
            return token

    def get(self, transport=None, *, force_refresh=False):
        token, exp, _ = self.raw()
        if not force_refresh:
            try:
                token = clean_token(token)
                if exp is None or exp > time.time() + 15:
                    return token
            except ProviderError as exc:
                if exc.code not in ('TOKEN_REQUIRED', 'TOKEN_FORMAT'):
                    raise
            else:
                # Configured token exists but is expired/near expiry.
                if exp is not None and exp <= time.time() + 15 and transport is None:
                    raise ProviderError('TOKEN_EXPIRED', '本地Token已过期或即将过期，请重新运行 scripts/onemap_login.py。', 503)
        if transport is not None and self.can_refresh():
            return self._refresh(transport, force=force_refresh)
        if token:
            clean_token(token)
            if exp is not None and exp <= time.time() + 15:
                raise ProviderError('TOKEN_EXPIRED', '本地Token已过期或即将过期，请重新运行 scripts/onemap_login.py。', 503)
        raise ProviderError('TOKEN_REQUIRED', '尚未配置OneMap Token。请在本机登录，或在共享服务器配置自动刷新凭据。', 503)

    def status(self):
        token, exp, source = self.raw()
        auto_refresh = self.can_refresh()
        try:
            clean_token(token)
            state = 'expired' if exp is not None and exp <= time.time() + 15 else ('configured' if exp else 'configured_expiry_unknown')
        except ProviderError as exc:
            state = 'auto_refresh_ready' if auto_refresh else ('missing' if exc.code == 'TOKEN_REQUIRED' else 'invalid_format')
        return {
            'state': state,
            'configured': state in ('configured', 'configured_expiry_unknown', 'expired', 'auto_refresh_ready'),
            'source': source if token else ('server_credentials' if auto_refresh else None),
            'expiry_utc': datetime.fromtimestamp(exp, timezone.utc).isoformat() if exp is not None else None,
            'auto_refresh_available': auto_refresh,
            'runtime_refresh_used': source == 'runtime_refreshed',
            'online_validity_verified': False,
            'token_value_exposed': False,
            'account_credentials_exposed': False,
        }


def persist_token(root: Path, token: str, expiry=None):
    token = clean_token(token)
    root = Path(root)
    path = root / '.env'
    if path.is_symlink():
        raise ValueError('Refuse to replace a symlinked .env file')
    original = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    newline = '\r\n' if '\r\n' in original else '\n'
    updates = {TOKEN_KEY: token, EXPIRY_KEY: str(int(expiry)) if expiry is not None else ''}
    lines, seen = [], set()
    for line in original.splitlines():
        k = line.strip().split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else ''
        if k in updates:
            if k not in seen:
                lines.append(k + '=' + updates[k]); seen.add(k)
        else:
            lines.append(line)
    for k, v in updates.items():
        if k not in seen:
            lines.append(k + '=' + v)
    fd, tmp = tempfile.mkstemp(prefix='.env.', suffix='.tmp', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(newline.join(lines) + newline)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        if os.name != 'nt':
            path.chmod(0o600)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def authenticate(transport, email, password):
    if not email.strip() or '@' not in email or not password:
        raise ProviderError('EMPTY_CREDENTIALS', '请输入已注册的OneMap邮箱和密码。', 400)
    payload = transport.request('auth', body={'email': email.strip(), 'password': password})
    check_payload_error(payload)
    try:
        token = clean_token(payload.get('access_token', ''))
        expiry = int(payload['expiry_timestamp'])
        if expiry <= time.time() or expiry > 4102444800:
            raise ValueError()
    except (ValueError, KeyError, TypeError, ProviderError):
        raise ProviderError('AUTH_RESPONSE_INVALID', '认证响应缺少有效Token或expiry_timestamp；没有保存凭据。', 503) from None
    return token, expiry
