"""邮件线程重建台 —— FastAPI 入口。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import database as db
from .parser import parse_eml
from .threading import build_threads, find_orphans

BASE_DIR = Path(__file__).resolve().parent
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_emails"

app = FastAPI(title="邮件线程重建台")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.on_event("startup")
def startup() -> None:
    db.init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, view: str = "threads"):
    messages = db.fetch_all()
    roots, stats = build_threads(messages)
    orphans = find_orphans(messages)
    stats["orphans"] = len(orphans)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "view": view,
            "roots": roots,
            "orphans": orphans,
            "stats": stats,
        },
    )


@app.post("/import")
async def import_files(files: list[UploadFile] = File(...)):
    """上传一个或多个 .eml 文件导入元数据（按 Message-ID 幂等）。"""
    imported = 0
    for f in files:
        data = await f.read()
        if not data.strip():
            continue
        db.upsert_message(parse_eml(data))
        imported += 1
    return RedirectResponse(f"/?imported={imported}", status_code=303)


@app.post("/import-sample")
def import_sample():
    """载入内置示例数据（含正常线程、缺失父节点、循环引用、孤立邮件）。"""
    imported = 0
    if SAMPLE_DIR.is_dir():
        for path in sorted(SAMPLE_DIR.glob("*.eml")):
            db.upsert_message(parse_eml(path.read_bytes()))
            imported += 1
    return RedirectResponse(f"/?imported={imported}", status_code=303)


@app.post("/clear")
def clear():
    db.clear_all()
    return RedirectResponse("/", status_code=303)


@app.get("/api/threads")
def api_threads():
    """JSON 接口：返回重建后的会话森林与孤立邮件列表。"""
    messages = db.fetch_all()
    roots, stats = build_threads(messages)
    orphans = find_orphans(messages)
    stats["orphans"] = len(orphans)
    return {
        "stats": stats,
        "threads": [r.to_dict() for r in roots],
        "orphan_ids": [m["message_id"] for m in orphans],
    }
