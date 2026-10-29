"""Web app: JSON API plus applicant, staff and SPE pages.

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ehds.pipeline import ROOT, AccessBody, applicant_view
from ehds.spe import DownloadBlocked

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
SAMPLES = ROOT / "requests"
REQUEST_TEMPLATE = {
    "id": "N01",
    "applicant": {"name": "Dr. Example", "organisation": "University hospital research group", "country": "AT"},
    "requested_as": "health data request",
    "requested_format": "anonymised statistics",
    "purpose": "We study hip fractures in older patients to plan rehabilitation capacity.",
    "question": "How many inpatient stays with a femur fracture started in 2025, by hospital?",
    "definitions": [
        "Inpatient stay = encounter with type 'inpatient'.",
        "Count stays (not patients) whose start_date is in 2025.",
        "Report per hospital: inpatient stays with a main diagnosis (primary_icd10) starting with S72.",
    ],
}


def load_samples() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SAMPLES.glob("R*.json"))]


def create_app(workdir: Path | str = ROOT, access_body: AccessBody | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.body = access_body or AccessBody(workdir=workdir)
        yield

    app = FastAPI(title="Tessera: EHDS Health Data Access Body", lifespan=lifespan)

    def body() -> AccessBody:
        return app.state.body

    def result(request_id: str) -> dict:
        r = body().results.get(request_id)
        if r is None:
            raise HTTPException(404, f"unknown request {request_id}")
        return r

    def page(request: Request, name: str, **context) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, context)

    # ---- JSON API -------------------------------------------------------------------
    @app.post("/api/requests")
    def submit(request: dict) -> dict:
        if not isinstance(request.get("id"), str) or not request.get("purpose"):
            raise HTTPException(422, "a request needs an 'id' and a 'purpose'")
        return applicant_view(body().process(request))

    @app.get("/api/requests/{request_id}")
    def get_request(request_id: str) -> dict:
        return applicant_view(result(request_id))

    @app.get("/api/staff/requests/{request_id}")
    def staff_request(request_id: str) -> dict:
        return result(request_id)

    @app.get("/api/spe/{request_id}/download")
    def api_download(request_id: str, actor: str = "applicant"):
        result(request_id)
        try:
            body().spe.download(request_id, actor)
        except DownloadBlocked as exc:
            return JSONResponse({"blocked": True, "message": str(exc)}, status_code=403)

    # ---- Applicant pages ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def portal(request: Request, error: str | None = None):
        return page(request, "portal.html", samples=load_samples(), error=error,
                    template=json.dumps(REQUEST_TEMPLATE, indent=2, ensure_ascii=False))

    @app.post("/submit")
    def submit_form(request: Request, sample: str | None = Form(None), request_json: str | None = Form(None)):
        if sample:
            matches = [s for s in load_samples() if s["id"] == sample]
            if not matches:
                raise HTTPException(404, sample)
            req = matches[0]
        else:
            try:
                req = json.loads(request_json or "")
                assert isinstance(req, dict) and isinstance(req.get("id"), str) and req.get("purpose")
            except (ValueError, AssertionError):
                return page(request, "portal.html", samples=load_samples(), template=request_json,
                            error="Please paste a JSON object with at least an 'id', a 'purpose', a 'question' and 'definitions'.")
        body().process(req)
        return RedirectResponse(f"/requests/{req['id']}", status_code=303)

    @app.get("/requests/{request_id}", response_class=HTMLResponse)
    def applicant_page(request: Request, request_id: str):
        return page(request, "applicant.html", r=applicant_view(result(request_id)))

    # ---- Staff pages ----------------------------------------------------------------
    @app.get("/staff", response_class=HTMLResponse)
    def staff_list(request: Request):
        results = sorted(body().results.values(), key=lambda r: r["request_id"])
        return page(request, "staff_list.html", results=results, audit=list(reversed(body().audit.entries()))[:30])

    @app.post("/staff/run-samples")
    def run_samples():
        for req in load_samples():
            body().process(req, actor="staff")
        return RedirectResponse("/staff", status_code=303)

    @app.get("/staff/requests/{request_id}", response_class=HTMLResponse)
    def staff_detail(request: Request, request_id: str):
        r = result(request_id)
        spec = r["staff"]["parse"]["statistics"] or r["staff"]["parse"]["cohort"]
        return page(request, "staff_detail.html", r=r, req=body().requests.get(request_id, {}),
                    spec_json=json.dumps(spec, indent=2, ensure_ascii=False),
                    audit=body().audit.entries(request_id))

    # ---- Secure processing environment -------------------------------------------------
    def spe_page(request: Request, request_id: str, **extra):
        r = result(request_id)
        manifest = body().spe.manifest(request_id)
        if not r.get("spe") or not manifest:
            raise HTTPException(404, "no secure processing environment for this request")
        return page(request, "spe.html", r=r, manifest=manifest,
                    audit=list(reversed(body().audit.entries(request_id))), **extra)

    @app.get("/spe/{request_id}", response_class=HTMLResponse)
    def spe_view(request: Request, request_id: str, table: str | None = None):
        preview = None
        if table:
            try:
                preview = body().spe.preview(request_id, table)
            except KeyError:
                raise HTTPException(404, table)
        return spe_page(request, request_id, preview=preview, table=table)

    @app.post("/spe/{request_id}/download", response_class=HTMLResponse)
    def spe_download(request: Request, request_id: str):
        result(request_id)
        try:
            body().spe.download(request_id, actor="applicant (web)")
        except DownloadBlocked as exc:
            response = spe_page(request, request_id, blocked=str(exc))
            response.status_code = 403
            return response

    @app.post("/spe/{request_id}/query", response_class=HTMLResponse)
    def spe_query(request: Request, request_id: str, sql: str = Form(...)):
        try:
            out = body().spe.query(request_id, sql, actor="applicant (web)")
            return spe_page(request, request_id, query=out, sql=sql)
        except (DownloadBlocked, ValueError, Exception) as exc:  # show DuckDB errors to the analyst
            return spe_page(request, request_id, query_error=str(exc), sql=sql)

    return app


app = create_app()
