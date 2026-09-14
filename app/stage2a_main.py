"""Independent entry point. Existing Stage1A/B/C code and data remain unchanged."""
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.config import ROOT
from app.stage1.api import router as source_router
from app.stage1b.api import router as enrichment_router
from app.stage1c.api import router as catalogue_router
from app.stage2a.api import router

app = FastAPI(title='Singapore PlayMap — geocoding and routes',version='0.5.2')
# This milestone is deliberately localhost-only. Before deployment, explicitly
# configure allowed hosts, authentication, provider budgets and retention policies.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])

@app.middleware('http')
async def local_client_guard(request:Request,call_next):
    if request.url.path.startswith('/api/stage2a/') and request.method == 'POST':
        origin = request.headers.get('origin')
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
                return JSONResponse({'detail':{'code':'ORIGIN_REJECTED','message':'只允许当前本地页面发起请求。'}},403)
        if request.headers.get('x-playmap-client') != 'stage2a':
            return JSONResponse({'detail':{'code':'CLIENT_HEADER_REQUIRED','message':'请从本项目页面调用；本地API需要X-PlayMap-Client: stage2a。'}},403)
    response = await call_next(request)
    if request.url.path.startswith('/api/stage2a/'):
        response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    # A browser tile request must identify the real origin. The previous
    # no-referrer policy conflicted with OSM's published tile usage policy.
    # Cross-origin requests disclose the origin, not paths or query strings.
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    if request.url.path in ('/', '/map', '/map-1c', '/map-1b', '/map-source') or request.url.path.startswith('/stage2a-static/'):
        # Revalidate OUR application files, not third-party map tiles.
        response.headers['Cache-Control']='no-cache'
    response.headers['X-PlayMap-UI-Version']='2A-M2'
    return response

@app.exception_handler(RequestValidationError)
async def invalid_input(request,exc):
    # Do not reflect the full request (labels, coordinates, accidentally pasted
    # secrets) into validation errors or reports.
    return JSONResponse({'detail':{'code':'INVALID_INPUT',
        'message':'输入未通过检查，请确认已选择起终点、经纬度顺序和交通方式。',
        'fields':['.'.join(str(x) for x in e['loc']) for e in exc.errors()]}},422)

for r in (source_router,enrichment_router,catalogue_router,router): app.include_router(r)
for name in ('stage1','stage1b','stage1c','stage2a'):
    app.mount('/'+name+'-static',StaticFiles(directory=ROOT/'frontend'/name),name=name+'-static')

@app.get('/',include_in_schema=False)
@app.get('/map',include_in_schema=False)
def page(): return FileResponse(ROOT/'frontend'/'stage2a'/'index.html')

@app.get('/map-1c',include_in_schema=False)
def old_c(): return FileResponse(ROOT/'frontend'/'stage1c'/'index.html')

@app.get('/map-1b',include_in_schema=False)
def old_b(): return FileResponse(ROOT/'frontend'/'stage1b'/'index.html')

@app.get('/map-source',include_in_schema=False)
def old_a(): return FileResponse(ROOT/'frontend'/'stage1'/'index.html')

@app.get('/health')
def health():
    return {'status':'ok','stage':'2A','ui_patch':'2A-M2','scope':'Singapore nationwide',
            'routing_adapter_implemented':True,'provider_connectivity':'check /api/stage2a/status or run the live check',
            'live_llm':False,'itinerary_planning':False}
