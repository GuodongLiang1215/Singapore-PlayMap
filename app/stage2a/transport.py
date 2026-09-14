"""Small bounded HTTPS client. No provider body, URL, credential or query in errors."""
from __future__ import annotations
import json
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

BASE = 'https://www.onemap.gov.sg'
PATHS = {'auth': '/api/auth/post/getToken', 'search': '/api/common/elastic/search',
         'route': '/api/public/routingsvc/route'}
MAX_RESPONSE = 4 * 1024 * 1024

class ProviderError(Exception):
    def __init__(self, code, message, status=502, upstream_status=None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.upstream_status = upstream_status

    def public(self):
        d = {'code': self.code, 'message': self.message}
        if self.upstream_status is not None: d['upstream_status'] = self.upstream_status
        return d


def status_error(status, operation='route'):
    if status in (401, 403):
        return ProviderError('AUTH_REJECTED', 'OneMap拒绝认证。请在本机重新运行 onemap_login.py；不要上传Token。', 503, status)
    if status == 429:
        return ProviderError('RATE_LIMITED', 'OneMap调用过于频繁。请稍后手动重试；本次没有自动循环请求。', 429, status)
    if operation == 'auth' and status == 404:
        return ProviderError('ACCOUNT_NOT_READY', 'OneMap账户未找到或尚未完成确认；请检查注册和邮箱确认。', 503, status)
    if status in (400, 404):
        return ProviderError('UPSTREAM_REQUEST_REJECTED', 'OneMap没有接受本次请求；请检查输入或接口状态。不能据此断言现实中没有路线。', 502, status)
    return ProviderError('UPSTREAM_HTTP_ERROR', 'OneMap返回服务错误；没有生成替代路线。', 502, status)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization or auth JSON to a redirected URL.
        raise ProviderError('REDIRECT_REFUSED', 'OneMap接口发生重定向，已停止请求以保护凭据。', 502, code)

class HTTPSJSON:
    def __init__(self, timeout=20, min_interval=0.35):
        self.timeout, self.min_interval = timeout, min_interval
        self._lock, self._last = threading.Lock(), 0.0

    def request(self, operation, *, token=None, params=None, body=None):
        if operation not in PATHS:
            raise ValueError('Unknown fixed provider operation')
        if operation == 'auth' and (params or token):
            raise ValueError('Authentication accepts a JSON body only')
        url = BASE + PATHS[operation]
        if params: url += '?' + urlencode(params)
        headers = {'Accept': 'application/json', 'User-Agent': 'SingaporePlayMap-Stage2A/1.0'}
        if token:
            if any(c.isspace() for c in token):
                raise ProviderError('TOKEN_FORMAT', 'Token格式无效，请重新配置。', 503)
            headers['Authorization'] = token  # OneMap documented raw-token header, not URL parameter.
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = Request(url, data=data, headers=headers, method='POST' if body is not None else 'GET')
        with self._lock:
            remaining = self.min_interval - (time.monotonic() - self._last)
            if remaining > 0: time.sleep(remaining)
            self._last = time.monotonic()
        try:
            with build_opener(NoRedirect()).open(req, timeout=self.timeout) as response:
                content = response.read(MAX_RESPONSE + 1)
                if len(content) > MAX_RESPONSE:
                    raise ProviderError('RESPONSE_TOO_LARGE', 'OneMap返回体超出本项目安全上限，已停止处理。')
        except ProviderError: raise
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise status_error(code, operation) from None
        except (TimeoutError, socket.timeout):
            raise ProviderError('NETWORK_TIMEOUT', '连接OneMap超时。请检查网络后重试；未生成估算路线。', 504) from None
        except (URLError, OSError):
            raise ProviderError('NETWORK_UNAVAILABLE', '无法连接OneMap，请检查网络、代理和域名解析。', 503) from None
        try:
            payload = json.loads(content.decode('utf-8-sig'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError):
            raise ProviderError('INVALID_JSON', 'OneMap返回的不是有效JSON；没有用网页文本代替空间数据。') from None
        if not isinstance(payload, dict):
            raise ProviderError('UNEXPECTED_SCHEMA', 'OneMap响应结构与本版接口契约不符。')
        return payload


def check_payload_error(payload):
    """Classify error JSON without reflecting untrusted provider text into UI/logs."""
    if payload.get('error') or payload.get('Error') or payload.get('errors'):
        text = str(payload.get('error', payload.get('Error', payload.get('errors', '')))).lower()
        if any(w in text for w in ('token', 'auth', 'expire', 'credential', 'unauthor')):
            raise status_error(401)
        raise ProviderError('UPSTREAM_ERROR', 'OneMap返回错误信息，未生成路线或搜索结果。')
