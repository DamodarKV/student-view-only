"""
main.py
-------
FastAPI entrypoint. Keeps app wiring thin: the SPA shell is a single Jinja2
template, real data lives in data.py, and each dashboard ("Admin",
"Instructor", "Student") gets its own router module so the codebase stays
modular as it grows.
"""

from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from routers import admin, assignment, instructor, student
import insert_mongo

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Cohort Dashboard")


@app.on_event("shutdown")
def _close_mongo_client():
    # Release the pooled MongoDB connection cleanly on shutdown. The client
    # itself is created lazily once and reused for the app's lifetime
    # (see insert_mongo.get_mongo_client) rather than reconnected per request.
    insert_mongo.close_mongo_client()

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.include_router(admin.router)
app.include_router(assignment.router)
app.include_router(instructor.router)
app.include_router(student.router)



@app.get("/", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
@app.get("/instructor", response_class=HTMLResponse)
@app.get("/student", response_class=HTMLResponse)
@app.get("/student/{register_number}", response_class=HTMLResponse)
def index(request: Request, register_number: str = None):
    # Newer Starlette moved to a request-first signature —
    # TemplateResponse("index.html", {"request": request}) makes the
    # context dict get read as the template *name* on those versions,
    # which is what produced the "unhashable type: 'dict'" Jinja2 error.
    # Calling with `request` as the first positional arg works on both
    # the old and new Starlette signatures.
    return templates.TemplateResponse(request, "index.html")
