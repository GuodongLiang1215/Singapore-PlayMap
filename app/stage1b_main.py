"""Fresh app instance: installing this add-on never mutates the Stage1A entry point."""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.config import ROOT
from app.stage1.api import router as source_router
from app.stage1b.api import router

app=FastAPI(title='Singapore PlayMap — Stage1B',version='0.3.0',
            description='Nationwide catalogue interpretation, facilities and candidate entry associations. No live route planner yet.')
app.include_router(source_router)
app.include_router(router)
app.mount('/stage1-static',StaticFiles(directory=ROOT/'frontend'/'stage1'),name='stage1-static')
app.mount('/stage1b-static',StaticFiles(directory=ROOT/'frontend'/'stage1b'),name='stage1b-static')

@app.get('/',include_in_schema=False)
@app.get('/map',include_in_schema=False)
def catalogue_page():
    return FileResponse(ROOT/'frontend'/'stage1b'/'index.html')

@app.get('/map-source',include_in_schema=False)
def source_page():
    return FileResponse(ROOT/'frontend'/'stage1'/'index.html')

@app.get('/health')
def health():
    return {'status':'ok','stage':'1B','scope':'Singapore nationwide','live_routing':False,'live_llm':False,
            'data_readiness_checked_by':'/api/stage1b/status'}
