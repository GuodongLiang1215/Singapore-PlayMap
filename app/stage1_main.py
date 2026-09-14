"""Additive application entry point. Stage0 source files and data remain intact."""
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.main import app
from app.config import ROOT
from app.stage1.api import router

app.title='Singapore PlayMap — Stage 1A'
app.version='0.2.0'
app.description='Nationwide source map and data inspection. Not a route planner or chatbot yet.'
app.include_router(router)
app.mount('/stage1-static',StaticFiles(directory=ROOT/'frontend'/'stage1'),name='stage1-static')

@app.get('/map',include_in_schema=False)
def map_page():
    return FileResponse(ROOT/'frontend'/'stage1'/'index.html')
