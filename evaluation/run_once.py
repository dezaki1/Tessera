"""One evaluation run: submit every request through the web API and save the answers.

Run in a fresh process by run_eval.py, so each of the three runs starts from
scratch (new store, new parser, different hash seed).
"""

from __future__ import annotations

import argparse
import json
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", default=str(ROOT / "requests"))
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    started = time.perf_counter()
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(workdir=Path(args.workdir))
    with TestClient(app) as client:
        startup = time.perf_counter() - started
        results = {}
        for path in sorted(Path(args.requests).glob("*.json")):
            if path.name == "expected.json":
                continue
            request = json.loads(path.read_text(encoding="utf-8"))
            t0 = time.perf_counter()
            response = client.post("/api/requests", json=request)  # what an applicant submits
            elapsed = time.perf_counter() - t0
            response.raise_for_status()
            applicant = response.json()
            staff = client.get(f"/api/staff/requests/{request['id']}").json()
            results[request["id"]] = {"seconds": elapsed, "applicant": applicant, "staff": staff}
    Path(args.out).write_text(json.dumps({"startup_seconds": startup, "results": results},
                                         ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
