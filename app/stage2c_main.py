"""Stage 2C1 application with optional shared-team deployment protection."""
from __future__ import annotations

import os
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import ROOT
from app.deployment import (AccessGate, COOKIE_NAME, COOKIE_TTL_SECONDS,
                            allowed_hosts, deployment_mode, deployment_status,
                            same_host_origin)
from app.stage1.api import router as sources
from app.stage1b.api import router as enriched
from app.stage1c.api import router as catalogue
from app.stage2a.api import router as geographic
from app.stage2b.api import router as itinerary
from app.stage2c.api import router as dialogue
from app.stage2c.wire_schema import WIRE_VERSION

UI_PATCH = WIRE_VERSION
DEPLOYMENT_PATCH = 'TEAM1'
app = FastAPI(title='Singapore PlayMap — conversational itinerary proposals', version='0.8.0')
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
ACCESS = AccessGate.from_env()


def _protected_path(path: str) -> bool:
    return path in ('/', '/map', '/map-2b', '/map-2a', '/map-1c', '/map-1b', '/map-source', '/docs', '/redoc', '/openapi.json') or path.startswith('/api/stage')


@app.middleware('http')
async def security_and_cache(request: Request, call_next):
    path = request.url.path
    if ACCESS.enabled and _protected_path(path) and not ACCESS.request_authorized(request):
        if path.startswith('/api/') or path in ('/openapi.json',):
            return JSONResponse({'detail': {'code': 'TEAM_ACCESS_REQUIRED', 'message': '请输入小组访问码后再使用共享测试服务。'}}, 401)
        return RedirectResponse('/team-login', status_code=303)

    prefix = next((p for p in ('stage2a', 'stage2b', 'stage2c') if path.startswith('/api/' + p + '/')), None)
    if prefix and request.method == 'POST':
        if not same_host_origin(request):
            return JSONResponse({'detail': {'code': 'ORIGIN_REJECTED', 'message': '只允许当前PlayMap页面发起请求。'}}, 403)
        if request.headers.get('x-playmap-client') != prefix:
            return JSONResponse({'detail': {'code': 'CLIENT_HEADER_REQUIRED', 'message': '请使用本项目页面调用。'}}, 403)

    response = await call_next(request)
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-PlayMap-UI-Version'] = UI_PATCH
    if path.startswith(('/api/stage2a/', '/api/stage2b/', '/api/stage2c/', '/api/access/')):
        response.headers['Cache-Control'] = 'no-store'
    elif path in ('/', '/map', '/map-2a', '/map-1c', '/map-1b', '/map-source', '/team-login') or path.startswith(('/stage2b-static/', '/stage2a-static/', '/stage2c-static/')):
        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.exception_handler(RequestValidationError)
async def bad_input(request, exc):
    return JSONResponse({'detail': {'code': 'INVALID_ITINERARY_INPUT',
        'message': '请检查选点、有效数字、结束方式和时间字段；跨午夜必须显式选下一天，未给时间不能暗含预算。',
        'fields': ['.'.join(str(x) for x in e['loc']) for e in exc.errors()]}}, 422)


class AccessRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=1, max_length=256)


@app.get('/team-login', include_in_schema=False)
def team_login(request: Request):
    if not ACCESS.enabled or ACCESS.request_authorized(request):
        return RedirectResponse('/map', status_code=303)
    return FileResponse(ROOT / 'frontend' / 'team_login.html')


@app.get('/api/access/status')
def access_status(request: Request):
    return {**deployment_status(ACCESS), 'authorized': ACCESS.request_authorized(request)}


@app.post('/api/access/login')
def access_login(body: AccessRequest):
    if not ACCESS.enabled:
        return {'authorized': True, 'access_gate_enabled': False}
    if not ACCESS.verify_code(body.code):
        return JSONResponse({'detail': {'code': 'ACCESS_DENIED', 'message': '访问码不正确。'}}, 401)
    response = JSONResponse({'authorized': True, 'access_gate_enabled': True})
    secure = deployment_mode() != 'local' or os.environ.get('PLAYMAP_SECURE_COOKIE') == '1'
    response.set_cookie(COOKIE_NAME, ACCESS.issue_cookie(), max_age=COOKIE_TTL_SECONDS,
                        httponly=True, secure=secure, samesite='strict', path='/')
    return response


@app.post('/api/access/logout')
def access_logout():
    response = JSONResponse({'authorized': False})
    response.delete_cookie(COOKIE_NAME, path='/')
    return response


for router in (sources, enriched, catalogue, geographic, itinerary, dialogue):
    app.include_router(router)
for name in ('stage1', 'stage1b', 'stage1c', 'stage2a', 'stage2b', 'stage2c'):
    app.mount('/' + name + '-static', StaticFiles(directory=ROOT / 'frontend' / name), name=name + '-static')


@app.get('/', include_in_schema=False)
@app.get('/map', include_in_schema=False)
def page():
    return FileResponse(ROOT / 'frontend/stage2c/index.html')


@app.get('/map-2b', include_in_schema=False)
def manual():
    return FileResponse(ROOT / 'frontend/stage2b/index.html')


@app.get('/map-2a', include_in_schema=False)
def two_points():
    return FileResponse(ROOT / 'frontend/stage2a/index.html')


@app.get('/map-1c', include_in_schema=False)
def reviewed():
    return FileResponse(ROOT / 'frontend/stage1c/index.html')


@app.get('/map-1b', include_in_schema=False)
def layers():
    return FileResponse(ROOT / 'frontend/stage1b/index.html')


@app.get('/map-source', include_in_schema=False)
def raw():
    return FileResponse(ROOT / 'frontend/stage1/index.html')


@app.get('/health')
def health():
    return {'status': 'ok', 'stage': '2C1', 'ui_patch': UI_PATCH, 'scope': 'Singapore nationwide',
        'manual_itinerary': True, 'automatic_selection': False, 'order_optimisation': False,
        'map_chat_integrated': True, 'deployment': {**deployment_status(ACCESS), 'deployment_patch': DEPLOYMENT_PATCH},
        'llm_connection_probed_by_health': False,
        'provider_connectivity': 'not established by this health check'}
