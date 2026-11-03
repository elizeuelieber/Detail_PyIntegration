from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("DATA_DIR", "/data"))
DOWNLOADS = DATA / "downloads"
LOGS = DATA / "logs"
CONFIG_FILE = Path(os.getenv("APP_CONFIG", "/config/config.json"))
API_TOKEN = os.getenv("API_TOKEN", "")

DOWNLOADS.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Quallity Data Integration", docs_url="/api/docs")
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "running": False,
    "job_id": None,
    "stage": "idle",
    "started_at": None,
    "finished_at": None,
    "return_code": None,
    "output": "",
    "error": None,
}


class RunRequest(BaseModel):
    stage: str = "all"


def authorized(token: str | None) -> None:
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="API_TOKEN não configurado no ambiente")
    if token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")


def check_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        raise RuntimeError(f"Configuração ausente: {CONFIG_FILE}")
    with CONFIG_FILE.open(encoding="utf-8") as handle:
        config = json.load(handle)
    for section, key in (("amwSystem", "username"), ("amwSystem", "password"), ("mysql", "host"), ("mysql", "user"), ("mysql", "database")):
        if not config.get(section, {}).get(key):
            raise RuntimeError(f"Campo obrigatório ausente em config.json: {section}.{key}")
    return config


def run_job(stage: str, job_id: str) -> None:
    scripts = []
    if stage in ("all", "crawl"):
        scripts.append(("crawl", ROOT / "crawling.py"))
    if stage in ("all", "import"):
        scripts.append(("import", ROOT / "saveBackupSQL.py"))
    try:
        check_config()
        for label, script in scripts:
            with _LOCK:
                _STATE["stage"] = label
            if not script.exists():
                raise RuntimeError(f"Script não encontrado: {script.name}")
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                with _LOCK:
                    _STATE["output"] = (_STATE["output"] + line)[-24000:]
            return_code = proc.wait()
            if return_code != 0:
                raise RuntimeError(f"Etapa {label} terminou com código {return_code}")
        with _LOCK:
            _STATE["return_code"] = 0
    except Exception as exc:  # state must always be finalized for the UI
        with _LOCK:
            _STATE["error"] = str(exc)
            _STATE["return_code"] = 1
    finally:
        with _LOCK:
            _STATE["running"] = False
            _STATE["finished_at"] = datetime.now(timezone.utc).isoformat()
            _STATE["job_id"] = job_id


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
def status(x_api_token: str | None = Header(default=None)) -> dict[str, Any]:
    authorized(x_api_token)
    with _LOCK:
        return dict(_STATE)


@app.post("/api/run")
def run(request: RunRequest, x_api_token: str | None = Header(default=None)) -> dict[str, str]:
    authorized(x_api_token)
    if request.stage not in ("all", "crawl", "import"):
        raise HTTPException(status_code=422, detail="stage deve ser all, crawl ou import")
    with _LOCK:
        if _STATE["running"]:
            raise HTTPException(status_code=409, detail="Já existe uma execução em andamento")
        if not CONFIG_FILE.exists():
            raise HTTPException(status_code=400, detail="Monte config/config.json antes de executar")
        job_id = str(uuid.uuid4())
        _STATE.update({
            "running": True,
            "job_id": job_id,
            "stage": "starting",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "return_code": None,
            "output": "",
            "error": None,
        })
    threading.Thread(target=run_job, args=(request.stage, job_id), daemon=True).start()
    return {"job_id": job_id, "status": "started", "stage": request.stage}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (ROOT / "static" / "index.html").read_text(encoding="utf-8")
