"""Incident Simulator and Postmortem Drafter: server-rendered pages behind one login."""
import hmac
import os
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import llm
from .charts import line_chart
from .models import Incident, SessionLocal, init_db
from .postmortem import NOTE_FIELDS, draft, severity
from .sim import ALERT_ERROR_PCT, ALERT_P95_MS, SCENARIOS, clock, run, validate_params

BASE = Path(__file__).parent
ENV = os.getenv("ENV", "development")
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD")
if ENV == "production" and (not APP_PASSWORD or len(APP_PASSWORD) < 12):
    raise RuntimeError("Set APP_PASSWORD (at least 12 characters) before running in production.")
APP_PASSWORD = APP_PASSWORD or "admin"  # local development only
POLISH_LIMIT_PER_HOUR = int(os.getenv("POLISH_LIMIT_PER_HOUR", "10"))
_polish_calls = deque()
DEFAULTS = {"scenario": "bad_deploy", "duration": 40, "fault_at": 8, "mitigate_after": 15, "seed": 7}

app = FastAPI(title="Incident Simulator")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
security = HTTPBasic()
init_db()


@app.middleware("http")
async def block_cross_site_posts(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method == "POST" and origin and urlparse(origin).netloc != request.headers.get("host", ""):
        return PlainTextResponse("Cross-site request blocked.", status_code=403)
    return await call_next(request)


def get_db():
    with SessionLocal() as db:
        yield db


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    user_ok = hmac.compare_digest(credentials.username.encode(), APP_USERNAME.encode())
    pass_ok = hmac.compare_digest(credentials.password.encode(), APP_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(401, "Incorrect username or password.", headers={"WWW-Authenticate": "Basic"})


async def form_data(request: Request) -> dict:
    return dict(await request.form())


def render(request: Request, name: str, status_code: int = 200, **context):
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


def polish_allowed() -> bool:
    now = time.time()
    while _polish_calls and now - _polish_calls[0] > 3600:
        _polish_calls.popleft()
    if len(_polish_calls) >= POLISH_LIMIT_PER_HOUR:
        return False
    _polish_calls.append(now)
    return True


def get_incident(db: Session, incident_id: int) -> Incident:
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found.")
    return incident


def incident_context(inc: Incident, message=None):
    s = inc.summary
    markers = [(s["fault_at"], "fault", "fault")]
    if s["alert_at"] is not None:
        markers.append((s["alert_at"], "alert", "alert"))
    markers.append((s["mitigated_at"], "mitigated", "action"))
    return dict(
        incident=inc, spec=SCENARIOS[inc.scenario], summary=s, severity=severity(s),
        events=[{**e, "clock": clock(e["minute"])} for e in inc.events],
        error_chart=line_chart([r["error_pct"] for r in inc.metrics], "Error rate", "%", markers, ALERT_ERROR_PCT),
        latency_chart=line_chart([r["p95_ms"] for r in inc.metrics], "p95 latency", "ms", markers, ALERT_P95_MS),
        llm_ready=llm.configured(), message=message)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "database unavailable"}, status_code=503)
    return {"status": "ok"}


private = APIRouter(dependencies=[Depends(require_auth)])


def index_page(request, db, status_code=200, values=None, errors=None):
    incidents = db.scalars(select(Incident).order_by(Incident.id.desc()).limit(50)).all()
    return render(request, "index.html", status_code=status_code, incidents=incidents, scenarios=SCENARIOS,
                  values=values or DEFAULTS, errors=errors or {})


@private.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    return index_page(request, db)


@private.post("/incidents")
def create_incident(request: Request, form: dict = Depends(form_data), db: Session = Depends(get_db)):
    params, errors = validate_params(form)
    if errors:
        return index_page(request, db, 422, form, errors)
    result = run(**params)
    inc = Incident(scenario=params["scenario"], params=params, metrics=result["metrics"],
                   events=result["events"], summary=result["summary"], notes={})
    inc.draft_md = draft(inc.scenario, inc.summary, inc.events, inc.notes)
    db.add(inc)
    db.commit()
    return redirect(f"/incidents/{inc.id}")


@private.get("/incidents/{incident_id}")
def view_incident(request: Request, incident_id: int, db: Session = Depends(get_db)):
    return render(request, "incident.html", **incident_context(get_incident(db, incident_id)))


@private.post("/incidents/{incident_id}/notes")
def save_notes(incident_id: int, form: dict = Depends(form_data), db: Session = Depends(get_db)):
    inc = get_incident(db, incident_id)
    inc.notes = {key: str(form.get(key, "")).strip()[:2000] for key in NOTE_FIELDS}
    inc.draft_md = draft(inc.scenario, inc.summary, inc.events, inc.notes)
    inc.polished_md = ""  # the notes changed, so the old polished version is stale
    db.commit()
    return redirect(f"/incidents/{inc.id}")


@private.post("/incidents/{incident_id}/polish")
def polish_incident(request: Request, incident_id: int, db: Session = Depends(get_db)):
    inc = get_incident(db, incident_id)
    error = None
    if not llm.configured():
        error = "AI polishing is off because no ANTHROPIC_API_KEY is set. The template draft is still complete."
    elif not polish_allowed():
        error = "The hourly limit for AI polishing was reached. Try again later."
    else:
        try:
            inc.polished_md = llm.polish(inc.draft_md, inc.notes)
            db.commit()
        except llm.LLMError as exc:
            error = str(exc)
    if error:
        return render(request, "incident.html", **incident_context(inc, message=error))
    return redirect(f"/incidents/{inc.id}")


@private.get("/incidents/{incident_id}/postmortem.md")
def download(incident_id: int, version: str = "best", db: Session = Depends(get_db)):
    inc = get_incident(db, incident_id)
    body = inc.polished_md if version != "draft" and inc.polished_md else inc.draft_md
    return Response(body, media_type="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="postmortem-{inc.id}.md"'})


@private.post("/incidents/{incident_id}/delete")
def delete_incident(incident_id: int, db: Session = Depends(get_db)):
    db.delete(get_incident(db, incident_id))
    db.commit()
    return redirect("/")


app.include_router(private)
