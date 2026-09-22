"""FastAPI 入口：导入邮件、重建线程、提供 JSON API 与静态页面。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import database
from .parser import parse_rfc822
from .threading import break_cycles, build_forest, _merge_by_subject, _propose_parents

# 允许通过环境变量覆盖数据库路径（Docker 中为 /data/emails.db）
_env_db = os.environ.get("EMAIL_DB_PATH")
if _env_db:
    database.DB_PATH = Path(_env_db)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.init_db()
    yield


app = FastAPI(title="邮件线程重建台", version="1.0.0", lifespan=lifespan)


def rebuild_threads() -> dict:
    """依据当前数据库内容完整重建一次会话树并持久化图结果。"""
    # 第一轮：仅真实邮件，找出缺失引用以补建占位节点
    real_rows = database.fetch_all_rows()
    parents, missing = _propose_parents(real_rows)
    database.save_graph(parents, {}, missing)

    # 第二轮：占位节点已落库，重新在全量节点上计算（含占位链连边）
    rows = database.fetch_all_rows()
    parents, missing = _propose_parents(rows)
    broken = break_cycles(parents)
    _merge_by_subject(rows, parents, broken)
    database.save_graph(parents, broken, set())
    return build_forest(database.fetch_all_rows())


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/import")
async def import_messages(
    raw: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> JSONResponse:
    text = ""
    if file is not None:
        content = await file.read()
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - 解码几乎不会失败
            raise HTTPException(status_code=400, detail=f"文件读取失败: {exc}")
    if raw and raw.strip():
        text = (text + "\n\n" + raw) if text.strip() else raw
    if not text.strip():
        raise HTTPException(status_code=400, detail="未提供任何邮件内容（文件或原始文本均为空）")

    messages = parse_rfc822(text)
    if not messages:
        raise HTTPException(
            status_code=422,
            detail="未能从内容中解析出任何带有 Message-ID 的邮件，请检查 RFC822 格式",
        )

    inserted = database.insert_messages(messages)
    result = rebuild_threads()
    result["imported"] = inserted
    return JSONResponse(result)


@app.get("/api/threads")
def get_threads(filter: str = "all") -> JSONResponse:
    result = build_forest(database.fetch_all_rows())
    if filter == "orphans":
        orphan_set = set(result["orphan_ids"])
        result["threads"] = [
            t for t in result["threads"]
            if t["message_id"] in orphan_set
            or _tree_has_orphan(t, orphan_set)
        ]
    elif filter == "cycles":
        result["threads"] = [
            t for t in result["threads"] if _tree_has_flag(t, "is_cycle")
        ]
    elif filter == "placeholders":
        result["threads"] = [
            t for t in result["threads"] if t["placeholder"] or _tree_has_flag(t, "placeholder")
        ]
    return JSONResponse(result)


@app.delete("/api/messages")
def clear_messages() -> JSONResponse:
    database.reset_db()
    return JSONResponse({"ok": True, "threads": [], "stats": {
        "total": 0, "placeholders": 0, "cycles": 0,
        "thread_roots": 0, "orphans": 0,
    }, "orphan_ids": [], "missing_refs": []})


def _tree_has_orphan(node: dict, orphan_set: set[str]) -> bool:
    if node["message_id"] in orphan_set:
        return True
    return any(_tree_has_orphan(c, orphan_set) for c in node["children"])


def _tree_has_flag(node: dict, flag: str) -> bool:
    if node.get(flag):
        return True
    return any(_tree_has_flag(c, flag) for c in node["children"])


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
