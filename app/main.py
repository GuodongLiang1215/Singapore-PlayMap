"""Stage-0 local dashboard/API. Not a map, route engine, or chatbot yet."""
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from app.config import ROOT, project_config, source_registry
from app.models.contracts import PlanningRequest, Place, VisitOption, TravelLeg, SessionState, ItineraryPlan, ChangeSet
from app.storage import initialize_database, place_count

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield

app=FastAPI(title="Singapore PlayMap — Stage 0",version="0.1.0",lifespan=lifespan,
    description="Nationwide source registry and data contracts; planning is not implemented yet.")

@app.get("/")
def dashboard():
    return FileResponse(ROOT/"frontend"/"stage0_dashboard.html")

@app.get("/health")
def health():
    return {"status":"ok","stage":0,"scope":"Singapore nationwide","registered_sources":len(source_registry()),
            "normalised_place_count":place_count(),"live_routing":False,"live_llm":False}

@app.get("/api/project")
def project():
    return project_config()

@app.get("/api/sources")
def sources():
    output=[]
    for item in source_registry():
        local={"status":"not_downloaded","feature_count":None}
        path=ROOT/"data"/"manifests"/(item["key"]+".json")
        if path.exists():
            try:
                m=json.loads(path.read_text(encoding="utf-8"))
                actual=ROOT/m["relative_path"]
                local={"status":m["local_status"] if actual.exists() else "manifest_without_file",
                       "feature_count":m["summary"]["feature_count"] if actual.exists() else None,
                       "retrieved_at":m["retrieved_at"]}
            except (ValueError,KeyError,OSError):
                local={"status":"invalid_manifest","feature_count":None}
        output.append({**item,"local":local})
    return {"scope":"Singapore nationwide","sources":output,
            "warning":"Full source datasets are not a complete or currently verified attraction catalogue."}

@app.get("/api/contracts")
def contracts():
    models=[PlanningRequest,Place,VisitOption,TravelLeg,SessionState,ItineraryPlan,ChangeSet]
    return {m.__name__:m.model_json_schema() for m in models}

@app.post("/api/contracts/validate-request")
def validate_request(request: PlanningRequest):
    return {"valid_structure":True,"request":request.model_dump(mode="json"),
            "route_feasibility_checked":False,"plan_generated":False}

@app.post("/api/plan")
def unavailable_plan():
    raise HTTPException(status_code=501,detail="Planning is not implemented at Stage 0. No fabricated itinerary is returned.")
