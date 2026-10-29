"""Web app: JSON API plus applicant, staff and SPE pages."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from ehds.pipeline import ROOT, AccessBody, applicant_view
from ehds.spe import DownloadBlocked


def create_app(workdir: Path | str = ROOT, access_body: AccessBody | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.body = access_body or AccessBody(workdir=workdir)
        yield

    app = FastAPI(title="EHDS Health Data Access Body", lifespan=lifespan)

    def body() -> AccessBody:
        return app.state.body

    def result(request_id: str) -> dict:
        r = body().results.get(request_id)
        if r is None:
            raise HTTPException(404, f"unknown request {request_id}")
        return r

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
    def download(request_id: str, actor: str = "applicant"):
        result(request_id)
        try:
            body().spe.download(request_id, actor)
        except DownloadBlocked as exc:
            return JSONResponse({"blocked": True, "message": str(exc)}, status_code=403)

    return app


app = create_app()
