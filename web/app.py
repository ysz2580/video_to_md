"""模块4：本地 Web 展示界面（FastAPI + 单页 HTML）。

每个视频一个独立目录 data/projects/{id}/，路由按项目 id 服务其内部文件。

路由：
  GET  /                            首页（index.html）
  POST /api/process                  提交 URL，启动后台 job，返回 job_id
  GET  /api/jobs/{job_id}            查询 job 进度
  GET  /api/projects                 列出所有已处理项目
  GET  /api/project/{pid}           单个项目详情（含每帧的图说）
  GET  /media/{pid}/video           服务该项目视频（带 Range，支持拖动条）
  GET  /media/{pid}/frames/{name}   服务该项目帧图片
  GET  /api/project/{pid}/markdown  下载生成的 .md
  GET  /api/open?path=...           用系统默认程序打开本地文件（便利）
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, UploadFile, Query
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

import config
from v2md import pipeline, markdown
from v2md.models import SubtitleSegment, Frame

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="video-to-md", version="0.1.0")
# 静态资源（CSS/JS 如需）
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")),
          name="static")


# ── 专栏（collection）：把多个视频组织成一个系列 ──────────
COLLECTIONS_DIR = config.DATA_DIR / "collections"
COLLECTIONS_DIR.mkdir(parents=True, exist_ok=True)


def _collection_path(cid: str) -> Path:
    return COLLECTIONS_DIR / f"{cid}.json"


def _load_collection(cid: str) -> Optional[dict]:
    p = _collection_path(cid)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _save_collection(coll: dict) -> Path:
    p = _collection_path(coll["id"])
    p.write_text(json.dumps(coll, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _project_meta(pid: str) -> Optional[dict]:
    """单个项目的轻量元信息（用于列表/专栏封面）。"""
    jp = _project_json_path(pid)
    if not jp.exists():
        return None
    d = json.loads(jp.read_text(encoding="utf-8"))
    ad = d.get("asset") or {}
    frames = d.get("frames", [])
    thumb = (f"/media/{pid}/frames/{Path(frames[0]['image_path']).name}"
             if frames else None)
    return {
        "id": pid,
        "title": ad.get("title") or "未命名视频",
        "duration_s": ad.get("duration_s", 0.0),
        "bvid": ad.get("bvid"),
        "frames": len(frames),
        "subtitles": len(d.get("subtitles", [])),
        "source_lang": d.get("source_lang"),
        "created_at": d.get("created_at", ""),
        "thumb_url": thumb,
    }


@app.get("/api/collections")
async def list_collections():
    out = []
    for cp in sorted(COLLECTIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            c = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            continue
        vids = [v for v in c.get("video_ids", []) if _project_meta(v)]
        c["count"] = len(vids)
        c["cover_url"] = (_project_meta(vids[0]) or {}).get("thumb_url") if vids else None
        out.append(c)
    return out


@app.post("/api/collections")
async def create_collection(body: dict):
    title = ((body or {}).get("title") or "新专栏").strip()[:80]
    desc = ((body or {}).get("desc") or "").strip()[:500]
    cid = uuid.uuid4().hex[:12]
    coll = {"id": cid, "title": title, "desc": desc, "video_ids": [],
            "created_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S")}
    _save_collection(coll)
    return coll


@app.get("/api/collection/{cid}")
async def get_collection(cid: str):
    c = _load_collection(cid)
    if c is None:
        raise HTTPException(404, "专栏不存在")
    c["videos"] = [m for m in (_project_meta(v) for v in c.get("video_ids", [])) if m]
    return c


@app.patch("/api/collection/{cid}")
async def update_collection(cid: str, body: dict):
    c = _load_collection(cid)
    if c is None:
        raise HTTPException(404, "专栏不存在")
    if "title" in (body or {}):
        c["title"] = (body["title"] or "").strip()[:80]
    if "desc" in (body or {}):
        c["desc"] = (body["desc"] or "").strip()[:500]
    if "video_ids" in (body or {}):
        # 保序、去重、仅保留已存在的项目
        seen, vids = set(), []
        for v in body["video_ids"]:
            if v not in seen and _project_meta(v):
                seen.add(v); vids.append(v)
        c["video_ids"] = vids
    _save_collection(c)
    return c


@app.delete("/api/collection/{cid}")
async def delete_collection(cid: str):
    p = _collection_path(cid)
    if not p.exists():
        raise HTTPException(404, "专栏不存在")
    p.unlink()
    return {"deleted": cid}


@app.get("/api/collection/{cid}/export")
async def export_collection(cid: str):
    """导出整个专栏为 zip：collection.json 清单 + projects/{pid}/ 各视频完整目录。"""
    c = _load_collection(cid)
    if c is None:
        raise HTTPException(404, "专栏不存在")
    fd, name = tempfile.mkstemp(suffix=".zip", dir=str(config.DATA_DIR))
    os.close(fd)
    tmp = Path(name)
    manifest = {"id": c["id"], "title": c.get("title", ""), "desc": c.get("desc", ""),
                "video_ids": c.get("video_ids", []), "created_at": c.get("created_at", "")}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("collection.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for pid in c.get("video_ids", []):
            wd = config.project_workdir(pid)
            if not wd.exists():
                continue
            for p in wd.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=f"projects/{pid}/{p.relative_to(wd)}")
    safe = "".join(ch for ch in c.get("title", "") if ch.isalnum() or ch in "-_") or cid
    return FileResponse(tmp, media_type="application/zip", filename=f"{safe}.zip",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


@app.post("/api/collection/import")
async def import_collection(file: UploadFile):
    """导入专栏 zip：重建各项目目录 + 新建本地专栏（新 cid，video_ids 引用已落地的项目）。"""
    data = await file.read()
    try:
        bio = io.BytesIO(data)
        zf = zipfile.ZipFile(bio)
    except Exception as e:
        raise HTTPException(400, f"无法读取 zip：{e}")
    names = zf.namelist()
    try:
        manifest = json.loads(zf.read("collection.json"))
    except KeyError:
        raise HTTPException(400, "zip 内缺少 collection.json，不是专栏导出包")
    new_pids = []
    for pid in manifest.get("video_ids", []):
        target = config.project_workdir(pid)
        prefix = f"projects/{pid}/"
        members = [n for n in names if n.startswith(prefix)]
        if target.exists() or not members:
            # 已存在则复用；zip 里没有该项目的目录则跳过
            if target.exists():
                new_pids.append(pid)
            continue
        target.mkdir(parents=True, exist_ok=True)
        for m in members:
            rel = m[len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            # 防 zip slip：拒绝绝对路径与 .. 上跳
            from pathlib import PurePath
            rp = PurePath(rel)
            if rp.is_absolute() or any(part == ".." for part in rp.parts):
                continue
            outp = target / rel
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_bytes(zf.read(m))
        new_pids.append(pid)
    new_cid = uuid.uuid4().hex[:12]
    coll = {"id": new_cid, "title": manifest.get("title", "导入的专栏"),
            "desc": manifest.get("desc", ""), "video_ids": new_pids,
            "created_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S")}
    _save_collection(coll)
    return coll


# ── 首页 ───────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, t: int = 0):
    # Starlette 1.x 签名：TemplateResponse(request, name, context)
    return TEMPLATES.TemplateResponse(request, "index.html", {"t": t})


# ── 处理任务 ───────────────────────────────────────────
@app.post("/api/process")
async def process(body: dict):
    url = (body or {}).get("url", "").strip()
    cookies = (body or {}).get("cookies")
    bilingual = bool((body or {}).get("bilingual", False))
    if not url:
        raise HTTPException(400, "url 不能为空")
    job_id = uuid.uuid4().hex[:12]
    th = threading.Thread(target=pipeline.run_async_job,
                          args=(job_id, url, cookies, bilingual), daemon=True)
    th.start()
    return {"job_id": job_id, "url": url, "bilingual": bilingual}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    st = pipeline.get_job(job_id)
    if not st:
        raise HTTPException(404, "job 不存在")
    return st.to_dict()


@app.get("/api/jobs")
async def list_jobs():
    """列出所有任务（进行中/已完成/出错/已暂停），按创建时间倒序。"""
    jobs = [j.to_dict() for j in pipeline.list_jobs()]
    jobs.sort(key=lambda j: j.get("created_at") or 0, reverse=True)
    return jobs


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """请求取消/暂停：whisper 循环在下一段前响应，已转部分存入 *.part.srt 可续传。"""
    if not pipeline.request_cancel(job_id):
        raise HTTPException(404, "job 不存在")
    return {"job_id": job_id, "cancel_requested": True}


# ── 设置面板（API Key / cookies / whisper 模型，写 data/settings.json，免改 config.py）──
def _settings_path() -> Path:
    return config.DATA_DIR / "settings.json"


def _read_settings() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_settings(d: dict) -> None:
    _settings_path().write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                encoding="utf-8")


def _apply_settings_live(d: dict) -> None:
    """把设置即时应用到 config 模块（下次下载/翻译/转写生效）。

    AI 供应商/模型由 ai.active_config() 每次现读 settings.json，无需预加载；
    此处额外把当前生效供应商同步到 config.TRANSLATE_*，供仍读 config 的遗留路径。
    """
    if d.get("whisper_model"):
        config.WHISPER_MODEL = d["whisper_model"]
    if "cookies_path" in d:
        config.COOKIES_PATH = d.get("cookies_path")
    try:
        from v2md import ai
        cfg = ai.active_config()
        if cfg:
            config.TRANSLATE_BASE_URL = cfg["base_url"]
            config.TRANSLATE_API_KEY = cfg["api_key"]
            config.TRANSLATE_MODEL = cfg["model"]
    except Exception:
        pass


@app.get("/api/settings")
async def get_settings():
    from v2md import ai
    d = _read_settings()
    cfg = ai.active_config()
    return {
        "providers": ai.masked_providers(),
        "active_provider_id": d.get("active_provider_id"),
        "active_model_id": d.get("active_model_id"),
        "presets": ai.PRESETS,
        # 兼容旧前端/CLI 的单供应商视图
        "has_translate_key": bool(cfg),
        "translate_api_key_masked": ai._mask(cfg["api_key"]) if cfg else None,
        "ai_enhance_enabled": bool(d.get("ai_enhance_enabled", False)),
        "whisper_model": d.get("whisper_model", config.WHISPER_MODEL),
        "cookies_path": d.get("cookies_path") or config.COOKIES_PATH,
        "has_cookies": bool(d.get("cookies_path") and Path(d["cookies_path"]).exists()),
    }


@app.put("/api/settings")
async def put_settings(body: dict):
    from v2md import ai
    d = _read_settings()
    b = body or {}
    # 多供应商：整体替换 providers，但保留未重输 key 的旧 key（前端不回传真实 key）
    if "providers" in b:
        incoming = b["providers"] or []
        old = {p.get("id"): p for p in d.get("providers", [])}
        merged = []
        for p in incoming:
            pid = p.get("id")
            if not pid:  # 新供应商，分配 id
                pid = ai.new_id()
                p["id"] = pid
            if (not p.get("api_key")) and pid in old:
                p["api_key"] = old[pid].get("api_key")  # 留空=保持原 key
            merged.append(p)
        d["providers"] = merged
    if "active_provider_id" in b:
        d["active_provider_id"] = b["active_provider_id"]
    if "active_model_id" in b:
        d["active_model_id"] = b["active_model_id"]
    if "whisper_model" in b:
        d["whisper_model"] = b["whisper_model"]
    if "ai_enhance_enabled" in b:
        d["ai_enhance_enabled"] = bool(b["ai_enhance_enabled"])
    if "cookies_path" in b:
        d["cookies_path"] = b["cookies_path"]
    # 兼容旧前端的单供应商字段（写入 legacy，供 CLI 回退）
    for k in ("translate_api_key", "translate_base_url", "translate_model"):
        if k in b:
            v = b[k]
            if k == "translate_api_key" and (v == "" or v is None):
                d[k] = None
            elif v is not None and v != "":
                d[k] = v
    _write_settings(d)
    _apply_settings_live(d)
    return {"ok": True}


@app.post("/api/ai/test")
async def ai_test(body: dict):
    """测试供应商连通性：body {provider:{...}} 测给定配置；{provider_id} 测已存供应商。"""
    from v2md import ai
    b = body or {}
    provider = b.get("provider")
    if not provider and b.get("provider_id"):
        provider = next((p for p in ai.providers() if p.get("id") == b["provider_id"]), None)
    ok, msg = ai.test_connection(provider)
    return {"ok": ok, "msg": msg}


# ── AI 增强字幕（清洗/章节/摘要，默认关，后续触发）──
@app.post("/api/project/{pid}/ai")
async def ai_enhance(pid: str, body: dict):
    from v2md import ai_enhance as AE
    tasks = (body or {}).get("tasks") or []
    tasks = [t for t in tasks if t in ("clean", "chapters", "summary")]
    if not tasks:
        raise HTTPException(400, "tasks 必须含 clean/chapters/summary 至少一项")
    if not AE.is_available():
        raise HTTPException(400, "未配置 AI 供应商（设置面板添加）")
    return AE.start_ai_job(pid, tasks)


@app.get("/api/project/{pid}/ai/status")
async def ai_enhance_status(pid: str):
    from v2md import ai_enhance as AE
    return AE.get_ai_job(pid)


@app.post("/api/settings/cookies")
async def upload_cookies(file: UploadFile):
    """上传 cookies.txt（B站/抖音登录视频用），存 data/cookies.txt 并设为 cookies_path。"""
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    p = config.DATA_DIR / "cookies.txt"
    p.write_bytes(data)
    d = _read_settings()
    d["cookies_path"] = str(p)
    _write_settings(d)
    _apply_settings_live(d)
    return {"cookies_path": str(p), "size": len(data)}


# ── 项目 ───────────────────────────────────────────────
def _project_json_path(pid: str) -> Path:
    return config.project_workdir(pid) / "project.json"


@app.get("/api/projects")
async def projects():
    out = []
    for jp in sorted(config.PROJECTS_DIR.glob("*/project.json"), reverse=True):
        try:
            d = json.loads(jp.read_text(encoding="utf-8"))
            out.append({
                "id": d.get("id", jp.parent.name),
                "title": (d.get("asset") or {}).get("title", ""),
                "created_at": d.get("created_at", ""),
                "frames": len(d.get("frames", [])),
                "subtitles": len(d.get("subtitles", [])),
                "bvid": (d.get("asset") or {}).get("bvid"),
            })
        except Exception:
            continue
    return out


def _dir_size(p: Path) -> int:
    """目录总字节数。"""
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


@app.get("/api/storage")
async def storage():
    """磁盘用量：每个项目的大小 + 所属专栏，供存储管理视图。"""
    projs = []
    for wd in config.PROJECTS_DIR.iterdir():
        if not wd.is_dir() or not (wd / "project.json").exists():
            continue
        try:
            d = json.loads((wd / "project.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        ad = d.get("asset") or {}
        projs.append({
            "id": d.get("id", wd.name),
            "title": ad.get("title", "未命名视频"),
            "size_bytes": _dir_size(wd),
            "frames": len(d.get("frames", [])),
            "subtitles": len(d.get("subtitles", [])),
            "duration_s": ad.get("duration_s", 0.0),
            "created_at": d.get("created_at", ""),
            "has_video": (wd / "video.mp4").exists(),
        })
    projs.sort(key=lambda x: x["size_bytes"], reverse=True)
    # 每个项目属于哪些专栏
    colls = []
    for cp in COLLECTIONS_DIR.glob("*.json"):
        try:
            c = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            continue
        colls.append({"id": c["id"], "title": c.get("title", ""), "video_ids": c.get("video_ids", [])})
    in_coll = {pid: [c["id"] for c in colls if pid in c["video_ids"]] for pid in [p["id"] for p in projs]}
    for p in projs:
        p["in_collections"] = in_coll.get(p["id"], [])
    total = sum(p["size_bytes"] for p in projs)
    return {"total_bytes": total, "project_count": len(projs),
            "collection_count": len(colls), "projects": projs}


@app.post("/api/storage/delete")
async def storage_delete(body: dict):
    """批量删项目：body{ids:[pid...]}，返回删掉的字节数。"""
    ids = (body or {}).get("ids") or []
    freed = 0
    deleted = []
    for pid in ids:
        wd = config.project_workdir(pid)
        if not wd.exists():
            continue
        sz = _dir_size(wd)
        shutil.rmtree(wd, ignore_errors=True)
        freed += sz
        deleted.append(pid)
        # 同步：从所有专栏移除该 pid
        for cp in list(COLLECTIONS_DIR.glob("*.json")):
            try:
                c = json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if pid in c.get("video_ids", []):
                c["video_ids"] = [v for v in c["video_ids"] if v != pid]
                _save_collection(c)
    return {"deleted": deleted, "freed_bytes": freed}


@app.get("/api/project/{pid}")
async def project_detail(pid: str):
    jp = _project_json_path(pid)
    if not jp.exists():
        raise HTTPException(404, "项目不存在")
    d = json.loads(jp.read_text(encoding="utf-8"))
    asset = d.get("asset") or {}
    subs = [SubtitleSegment(**s) for s in d.get("subtitles", [])]
    # 为每帧预计算图说字幕 + 媒体 URL（按项目 id 路由到其专属目录）
    cards = []
    for f in d.get("frames", []):
        t = f["timestamp_s"]
        cards.append({
            "timestamp_s": t,
            "image_url": f"/media/{pid}/frames/{Path(f['image_path']).name}",
            "caption": markdown._caption_for_time(subs, t),
        })
    return {
        "id": d.get("id", pid),
        "title": asset.get("title", "未命名视频"),
        "source_url": asset.get("source_url", ""),
        "bvid": asset.get("bvid"),
        "duration_s": asset.get("duration_s", 0.0),
        "video_url": f"/media/{pid}/video" if asset.get("video_path") else None,
        "frames": cards,
        "subtitles": d.get("subtitles", []),
        "subtitles_count": len(d.get("subtitles", [])),
        "frames_count": len(cards),
        "thumb_url": (cards[0]["image_url"] if cards else None),
        "source_lang": d.get("source_lang"),
        "md_url": f"/api/project/{pid}/markdown",
        "bilibili_url": (f"https://www.bilibili.com/video/{asset['bvid']}" if asset.get("bvid") else None),
    }


@app.get("/api/project/{pid}/markdown")
async def download_markdown(pid: str, embed: bool = False):
    """下载 .md。embed=1 时生成图片 base64 内嵌的单文件版（脱离服务可分享）。"""
    wd = config.project_workdir(pid)
    if embed:
        from v2md.models import Project
        proj = Project.load(wd)
        mp = markdown.build_embedded(proj)
        return FileResponse(mp, media_type="text/markdown",
                            filename=f"{pid}.single.md")
    mp = wd / "project.md"
    if not mp.exists():
        raise HTTPException(404, "Markdown 不存在")
    return FileResponse(mp, media_type="text/markdown", filename=f"{pid}.md")


@app.get("/api/project/{pid}/html")
async def download_html(pid: str):
    """下载自包含单文件 HTML（base64 图片 + 内联 CSS/JS，脱离服务双击可读）。"""
    from v2md.models import Project
    wd = config.project_workdir(pid)
    if not (wd / "project.json").exists():
        raise HTTPException(404, "项目不存在")
    proj = Project.load(wd)
    hp = markdown.build_html(proj)
    return FileResponse(hp, media_type="text/html",
                        filename=f"{pid}.html")


@app.delete("/api/project/{pid}")
async def delete_project(pid: str):
    """删除某个项目目录（视频/字幕/帧/json/md 全清）。"""
    wd = config.project_workdir(pid)
    if not wd.exists():
        raise HTTPException(404, "项目不存在")
    shutil.rmtree(wd, ignore_errors=True)
    return {"deleted": pid}


@app.post("/api/project/{pid}/frame")
async def capture_frame(pid: str, body: dict):
    """播放时手动截图：按当前时间 t 从视频抽帧，插入 project.frames（按时间排序），
    重建 project.json + project.md。用于补全机器抽帧遗漏的关键画面。"""
    import subprocess
    from v2md.models import Project, Frame
    from v2md import frames as F
    t = float((body or {}).get("t", 0) or 0)
    wd = config.project_workdir(pid)
    video = wd / "video.mp4"
    if not video.exists():
        raise HTTPException(404, "项目/视频不存在")
    frames_dir = wd / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    proj = Project.load(wd)
    # 同时刻（±0.3s）已有帧→覆盖其图片文件；否则按时间命名新建 frame_{ms}.jpg
    existing = next((f for f in proj.frames if abs(f.timestamp_s - t) < 0.3), None)
    if existing:
        out = Path(existing.image_path)
    else:
        name = F._time_name(t)
        n = 2
        ms = int(round(t * 1000))
        while (frames_dir / name).exists():
            name = f"frame_{ms:09d}_{n}.jpg"; n += 1
        out = frames_dir / name
    ff = F._find_ffmpeg()
    cmd = [ff, "-y", "-ss", f"{t:.3f}", "-i", str(video),
           "-frames:v", "1", "-q:v", "2", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if not out.exists():
        raise HTTPException(500, f"截图失败: {proc.stderr[-300:] if proc.stderr else 'unknown'}")
    try:
        ph = F._phash(out)
    except Exception:
        ph = ""
    if existing:
        existing.timestamp_s = t
        existing.image_path = out
        existing.phash = ph
    else:
        proj.frames.append(Frame(timestamp_s=t, image_path=out, phash=ph))
    proj.frames.sort(key=lambda f: f.timestamp_s)
    # 重建 frames.txt / json / md
    try:
        F._write_frames_manifest(proj.frames, wd)
    except Exception:
        pass
    markdown.build(proj)
    pipeline.save_project_json(proj)
    return {"timestamp_s": t, "image_url": f"/media/{pid}/frames/{out.name}",
            "frames": len(proj.frames)}


@app.delete("/api/project/{pid}/frame")
async def delete_frame(pid: str, t: float = Query(..., description="要删除帧的时间戳(秒)")):
    """删除某帧：从 project.frames 移除、删图片文件、重建 frames.txt/project.json/project.md。

    前端在 md 文档视图里对某区段点🗑即调此，同步本地「数据库」(json/md/frames.txt/图片)。
    """
    from v2md.models import Project
    from v2md import frames as F
    wd = config.project_workdir(pid)
    if not (wd / "project.json").exists():
        raise HTTPException(404, "项目不存在")
    proj = Project.load(wd)
    # 匹配时间最近(±0.5s)的帧；section.timestamp_s 来自帧时间戳，通常精确相等
    target = next((f for f in proj.frames if abs(f.timestamp_s - t) <= 0.5), None)
    if target is None:
        raise HTTPException(404, f"{t}s 附近无帧")
    proj.frames = [f for f in proj.frames if f is not target]
    try:
        Path(target.image_path).unlink(missing_ok=True)
    except Exception:
        pass
    proj.frames.sort(key=lambda f: f.timestamp_s)
    try:
        F._write_frames_manifest(proj.frames, wd)
    except Exception:
        pass
    markdown.build(proj)
    pipeline.save_project_json(proj)
    return {"removed": target.timestamp_s, "frames": len(proj.frames)}


# ── 字幕内联编辑（#1：修正 whisper 错字）──
@app.put("/api/project/{pid}/subtitle")
async def edit_subtitle(pid: str, body: dict):
    """修改某条字幕文本：按 (track, start_s) 定位 → 改 text → 回写 srt/json/md。

    body: {track: "zh"|"en", start_s: float, text: str}
    track=zh 改主轨(subtitle.srt)，en 改第二轨(subtitle.en.srt)。
    """
    from v2md.models import Project, SubtitleSegment
    from v2md import subtitle as S
    wd = config.project_workdir(pid)
    if not (wd / "project.json").exists():
        raise HTTPException(404, "项目不存在")
    track = (body or {}).get("track", "zh")
    start_s = float((body or {}).get("start_s", 0))
    new_text = ((body or {}).get("text") or "").strip()
    srt_path = wd / ("subtitle.srt" if track != "en" else "subtitle.en.srt")
    if not srt_path.exists():
        raise HTTPException(404, f"{track} 字幕轨不存在")
    segs = S.parse_srt(srt_path)
    # 按 start_s 精确匹配（容差 0.2s）
    target = next((s for s in segs if abs(s.start_s - start_s) <= 0.2), None)
    if target is None:
        raise HTTPException(404, f"{track} 轨 {start_s}s 附近无字幕段")
    target.text = new_text
    S.write_srt(segs, srt_path)
    # 回写 project.json + md
    proj = Project.load(wd)
    if track != "en":
        proj.subtitles = segs
    else:
        proj.subtitles_en = segs
    markdown.build(proj)
    pipeline.save_project_json(proj)
    return {"track": track, "start_s": start_s, "text": new_text}


@app.get("/api/project/{pid}/export")
async def export_project(pid: str):
    """导出整个项目为 zip（含项目目录名）。"""
    from starlette.background import BackgroundTask
    wd = config.project_workdir(pid)
    if not wd.exists():
        raise HTTPException(404, "项目不存在")
    fd, name = tempfile.mkstemp(suffix=".zip", dir=str(config.DATA_DIR))
    os.close(fd)
    tmp = Path(name)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in wd.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(wd.parent)))  # 含项目目录名
    return FileResponse(tmp, media_type="application/zip", filename=f"{pid}.zip",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


@app.get("/api/project/{pid}/doc")
async def project_doc(pid: str):
    """返回结构化文档区段（与 project.md 内容一致），供前端直接渲染阅读。"""
    from v2md.models import VideoAsset, Project
    jp = _project_json_path(pid)
    if not jp.exists():
        raise HTTPException(404, "项目不存在")
    d = json.loads(jp.read_text(encoding="utf-8"))
    asset_d = d.get("asset") or {}
    workdir = config.project_workdir(pid)
    subs = [SubtitleSegment(**s) for s in d.get("subtitles", [])]
    subs_en = [SubtitleSegment(**s) for s in d.get("subtitles_en", [])]
    frames = [Frame(timestamp_s=f["timestamp_s"],
                    image_path=Path(f["image_path"]),
                    phash=f.get("phash", ""))
              for f in d.get("frames", [])]
    asset = VideoAsset(
        video_path=workdir / (asset_d.get("video_path") or "video.mp4"),
        subtitle_path=workdir / (asset_d.get("subtitle_path") or "subtitle.srt")
        if asset_d.get("subtitle_path") else None,
        source_url=asset_d.get("source_url", ""),
        bvid=asset_d.get("bvid"),
        title=asset_d.get("title", "未命名视频"),
        duration_s=asset_d.get("duration_s", 0.0),
    )
    proj = Project(id=pid, workdir=workdir, asset=asset,
                    subtitles=subs, subtitles_en=subs_en, frames=frames,
                    source_lang=asset_d.get("source_lang"),
                    secondary_lang=d.get("secondary_lang"))
    tl = markdown.timeline(proj)
    # 给 frame 事件补 image_url / bili_url
    for ev in tl:
        if ev["type"] == "frame":
            ev["image_url"] = f"/media/{pid}/frames/{ev['image_name']}"
            if asset.bvid:
                ev["bili_url"] = (f"https://www.bilibili.com/video/{asset.bvid}"
                                  f"?t={int(round(ev['t']))}")
            else:
                ev["bili_url"] = None
    return {
        "id": pid,
        "title": asset.title,
        "duration_s": asset.duration_s,
        "bvid": asset.bvid,
        "video_url": f"/media/{pid}/video",
        "md_url": f"/api/project/{pid}/markdown",
        "md_embed_url": f"/api/project/{pid}/markdown?embed=1",
        "html_url": f"/api/project/{pid}/html",
        "bilibili_url": (f"https://www.bilibili.com/video/{asset.bvid}"
                         if asset.bvid else None),
        "bilingual": bool(subs_en),
        "source_lang": proj.source_lang,           # 原音语言 zh/en/…
        "secondary_lang": proj.secondary_lang,      # 第二轨语言（原音非英→en；原音英→zh）
        "summary": proj.summary,
        "tags": proj.tags,
        "subtitles": d.get("subtitles", []),        # 扁平主轨，供播放器覆盖
        "subtitles_en": d.get("subtitles_en", []),  # 扁平第二轨
        "timeline": tl,                             # 全局时间排序事件流（帧+字幕+章节）
    }


# ── 媒体服务（视频需支持 Range）────────────────────────
def _send_with_range(path: Path, media_type: str, request: Request):
    """支持 HTTP Range 的大文件流式响应（视频拖动条必需）。"""
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    size = path.stat().st_size
    range_hdr = request.headers.get("range")
    start, end = 0, size - 1
    if range_hdr:
        m = range_hdr.strip().lower()
        if m.startswith("bytes="):
            try:
                a, b = m[6:].split("-", 1)
                start = int(a) if a else 0
                end = int(b) if b else size - 1
            except ValueError:
                pass
    end = min(end, size - 1)
    length = end - start + 1

    def iterfile():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(1 << 20, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {"Content-Range": f"bytes {start}-{end}/{size}",
               "Accept-Ranges": "bytes",
               "Content-Length": str(length)}
    return StreamingResponse(iterfile(), media_type=media_type, status_code=206,
                             headers=headers)


@app.get("/media/{pid}/video")
async def media_video(pid: str, request: Request):
    return _send_with_range(config.project_workdir(pid) / "video.mp4", "video/mp4", request)


@app.get("/media/{pid}/frames/{name}")
async def media_frame(pid: str, name: str):
    p = config.project_workdir(pid) / "frames" / name
    if not p.exists():
        raise HTTPException(404, "图片不存在")
    return FileResponse(p, media_type="image/jpeg")


@app.get("/api/open")
async def open_local(path: str = Query(...)):
    """用系统默认程序打开本地文件/目录（便利）。仅教学本地用。"""
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "路径不存在")
    os.startfile(str(p))  # Windows
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host=config.WEB_HOST, port=config.WEB_PORT,
                reload=True)
