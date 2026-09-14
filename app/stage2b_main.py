"""New, additive entry point. Stage2A MapFix2 remains available and unmodified."""
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.config import ROOT
from app.stage1.api import router as sources
from app.stage1b.api import router as enriched
from app.stage1c.api import router as catalogue
from app.stage2a.api import router as geographic
from app.stage2b.api import router as itinerary

app = FastAPI(title='Singapore PlayMap — user-ordered itineraries',version='0.6.0')
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])

@app.middleware('http')
async def local_guard(request: Request,call_next):
    prefix = next((p for p in ('stage2a','stage2b') if request.url.path.startswith('/api/'+p+'/')),None)
    if prefix and request.method == 'POST':
        origin = request.headers.get('origin')
        if origin:
            parts = urlsplit(origin)
            if parts.scheme != request.url.scheme or parts.netloc != request.url.netloc:
                return JSONResponse({'detail':{'code':'ORIGIN_REJECTED','message':'只允许当前本地页面请求。'}},403)
        if request.headers.get('x-playmap-client') != prefix:
            return JSONResponse({'detail':{'code':'CLIENT_HEADER_REQUIRED','message':'请使用本项目页面调用。'}},403)
    response = await call_next(request)
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-PlayMap-UI-Version']='2B-1'
    if request.url.path.startswith(('/api/stage2a/','/api/stage2b/')):
        response.headers['Cache-Control']='no-store'
    elif request.url.path in ('/','/map','/map-2a','/map-1c','/map-1b','/map-source') or request.url.path.startswith(('/stage2b-static/','/stage2a-static/')):
        response.headers['Cache-Control']='no-cache'
    return response

@app.exception_handler(RequestValidationError)
async def bad_input(request,exc):
    return JSONResponse({'detail':{'code':'INVALID_ITINERARY_INPUT',
        'message':'请检查选点、有效数字、结束方式和时间字段；跨午夜必须显式选下一天，未给时间不能暗含预算。',
        'fields':['.'.join(str(x) for x in e['loc']) for e in exc.errors()]}},422)

for r in (sources,enriched,catalogue,geographic,itinerary): app.include_router(r)
for name in ('stage1','stage1b','stage1c','stage2a','stage2b'):
    app.mount('/'+name+'-static',StaticFiles(directory=ROOT/'frontend'/name),name=name+'-static')

@app.get('/',include_in_schema=False)
@app.get('/map',include_in_schema=False)
def page(): return FileResponse(ROOT/'frontend/stage2b/index.html')
@app.get('/map-2a',include_in_schema=False)
def two_points(): return FileResponse(ROOT/'frontend/stage2a/index.html')
@app.get('/map-1c',include_in_schema=False)
def reviewed(): return FileResponse(ROOT/'frontend/stage1c/index.html')
@app.get('/map-1b',include_in_schema=False)
def layers(): return FileResponse(ROOT/'frontend/stage1b/index.html')
@app.get('/map-source',include_in_schema=False)
def raw(): return FileResponse(ROOT/'frontend/stage1/index.html')
@app.get('/health')
def health():
    return {'status':'ok','stage':'2B','ui_patch':'2B-1','scope':'Singapore nationwide',
        'manual_itinerary':True,'automatic_selection':False,'order_optimisation':False,
        'live_llm':False,'provider_connectivity':'not established by this health check'}
