"""Append-only audit log (JSON lines) for decisions, SPE builds and blocked downloads."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: str, request_id: str, actor: str = "system", **detail) -> dict:
        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "request_id": request_id,
            "actor": actor,
            **detail,
        }
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self, request_id: str | None = None, event: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if (request_id is None or e.get("request_id") == request_id) and (event is None or e.get("event") == event):
                out.append(e)
        return out
