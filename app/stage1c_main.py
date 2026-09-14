"""Independent entry point; older source maps stay available."""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.config import ROOT
from app.stage1.api import router as source_router
from app.stage1b.api import router as catalogue_router
from app.stage1c.api import router

app = FastAPI(title='Singapore PlayMap — reviewed catalogue', version='0.4.0')
app.include_router(source_router)
app.include_router(catalogue_router)
app.include_router(router)
for name in ('stage1','stage1b','stage1c'):
    app.mount('/'+name+'-static',StaticFiles(directory=ROOT/'frontend'/name),name=name+'-static')

@app.get('/',include_in_schema=False)
@app.get('/map',include_in_schema=False)
def map_page(): return FileResponse(ROOT/'frontend'/'stage1c'/'index.html')

@app.get('/map-1b',include_in_schema=False)
def previous_page(): return FileResponse(ROOT/'frontend'/'stage1b'/'index.html')

@app.get('/map-source',include_in_schema=False)
def source_page(): return FileResponse(ROOT/'frontend'/'stage1'/'index.html')

@app.get('/health')
def health(): return {'status':'ok','stage':'1C','scope':'Singapore nationwide','live_routing':False,'live_llm':False}
