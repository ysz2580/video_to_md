"""编排：串联 downloader → subtitle → frames → markdown。

各模块只通过 dataclass 传递，本模块负责顺序调用与进度上报。
也提供 job 状态机供 Web 层轮询。
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import config
from v2md import downloader, subtitle, frames, markdown
from v2md.models import Project, VideoAsset

import logging
log = logging.getLogger(__name__)


class Step(str, Enum):
    IDLE = "idle"
    DOWNLOADING = "downloading"
    SUBTITLE = "subtitle"
    FRAMES = "frames"
    MARKDOWN = "markdown"
    DONE = "done"
    PAUSED = "paused"
    ERROR = "error"


ProgressCb = Callable[[Step, str, Optional[Project]], None]


@dataclass
class JobState:
    job_id: str
    url: str
    step: Step = Step.IDLE
    message: str = ""
    project: Optional[Project] = None
    error: Optional[str] = None
    cancel_requested: bool = False

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "url": self.url,
            "step": self.step.value,
            "message": self.message,
            "error": self.error,
            "project_id": self.project.id if self.project else None,
        }


# 进程内 job 注册表（教学项目够用；多进程部署需换 Redis 等）
_JOBS: dict[str, JobState] = {}
_LOCK = threading.Lock()


def get_job(job_id: str) -> Optional[JobState]:
    return _JOBS.get(job_id)


def list_jobs() -> list[JobState]:
    return list(_JOBS.values())


def request_cancel(job_id: str) -> bool:
    """请求取消某个 job（whisper 循环会在下一段前响应并保存断点）。"""
    st = _JOBS.get(job_id)
    if st is None:
        return False
    st.cancel_requested = True
    return True


def _save_source(workdir: Path, asset) -> None:
    """写入 .source.json，用于断点续传时按输入匹配未完成的项目。"""
    import json as _j
    p = Path(workdir) / ".source.json"
    p.write_text(_j.dumps({
        "source_url": asset.source_url, "bvid": asset.bvid,
        "content_hash": getattr(asset, "content_hash", None),
        "title": getattr(asset, "title", ""),
    }, ensure_ascii=False), encoding="utf-8")


def _load_source(workdir: Path) -> dict:
    import json as _j
    p = Path(workdir) / ".source.json"
    if not p.exists():
        return {}
    try:
        return _j.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _input_key(url: str):
    """对输入做去重键：(类型, 值)。本地文件→sha256；B站→bvid；其余→URL 串。"""
    local = downloader._is_local_file(url)
    if local:
        return ("hash", downloader._file_sha256(local))
    bvid = downloader._extract_bvid(url)
    if bvid:
        return ("bvid", bvid)
    return ("url", (url or "").strip())


def find_reusable(url: str) -> Optional[Project]:
    """查已**完成**项目（有 project.json），命中相同输入则返回旧 Project。"""
    ktype, kval = _input_key(url)
    for jp in sorted(config.PROJECTS_DIR.glob("*/project.json")):
        try:
            import json as _json
            d = _json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        ad = d.get("asset") or {}
        hit = False
        if ktype == "bvid":
            hit = (ad.get("bvid") == kval)
        elif ktype == "url":
            hit = (ad.get("source_url") == kval)
        elif ktype == "hash":
            hit = (ad.get("content_hash") == kval) or (d.get("content_hash") == kval)
        if hit:
            log.info("命中已完成项目 %s（按 %s 复用）", jp.parent.name, ktype)
            try:
                return Project.load(jp.parent)
            except Exception as e:
                log.warning("加载 %s 失败: %s", jp.parent.name, e)
    return None


def find_inprogress(url: str) -> Optional[Path]:
    """查**未完成**项目：有 video.mp4 但无 project.json 且 .source.json 匹配输入。"""
    ktype, kval = _input_key(url)
    for wd in sorted(config.PROJECTS_DIR.iterdir()):
        if not wd.is_dir():
            continue
        if (wd / "project.json").exists():
            continue  # 已完成
        if not (wd / "video.mp4").exists():
            continue
        src = _load_source(wd)
        hit = False
        if ktype == "bvid":
            hit = (src.get("bvid") == kval)
        elif ktype == "url":
            hit = (src.get("source_url") == kval)
        elif ktype == "hash":
            hit = (src.get("content_hash") == kval)
        if hit:
            log.info("命中未完成项目 %s，断点续传", wd.name)
            return wd
    return None


def run(url: str, cookies_path: Optional[str] = None,
        bilingual: bool = False, force: bool = False,
        cancel: Optional[Callable[[], bool]] = None,
        on_progress: Optional[ProgressCb] = None) -> tuple[Project, bool]:
    """同步跑 pipeline，返回 (project, complete)。

    - bilingual=True 时额外用 whisper task=translate 生成英文字幕（双语第二轨）。
    - force=False（默认）命中已完成项目则直接复用；命中未完成项目则断点续传。
    - cancel() 返回 True 时在字幕步骤尽快停止，已转部分存入 *.part.srt，返回 complete=False。
    """
    def _emit(step: Step, msg: str, proj: Optional[Project] = None):
        if on_progress:
            on_progress(step, msg, proj)

    # 复用：已完成项目直接返回
    if not force:
        reuse = find_reusable(url)
        if reuse is not None:
            _emit(Step.DONE, f"复用已有项目 {reuse.id}", reuse)
            return reuse, True

    # 断点续传：命中未完成项目则接管其 workdir
    inprog = None if force else find_inprogress(url)
    if inprog is not None:
        project = Project()
        project.id = inprog.name
        project.workdir = inprog
        _emit(Step.DOWNLOADING, f"断点续传 {project.id}（视频已存在）...")
        src = _load_source(project.workdir)
        asset = VideoAsset(
            video_path=project.workdir / "video.mp4",
            subtitle_path=project.workdir / "subtitle.srt"
            if (project.workdir / "subtitle.srt").exists() else None,
            source_url=src.get("source_url", url),
            bvid=src.get("bvid"),
            title=src.get("title", ""),
            duration_s=downloader._probe_duration(project.workdir / "video.mp4"),
            content_hash=src.get("content_hash"),
        )
    else:
        project = Project()
        project.workdir = config.project_workdir(project.id)
        project.workdir.mkdir(parents=True, exist_ok=True)
        _emit(Step.DOWNLOADING, "下载视频并抓取字幕 ...")
        asset = downloader.download(url, workdir=project.workdir, cookies_path=cookies_path)
        _save_source(project.workdir, asset)  # 记录输入，供后续续传匹配

    project.asset = asset
    project.content_hash = getattr(asset, "content_hash", None)

    def _on_sub_status(msg: str):
        _emit(Step.SUBTITLE, msg)

    _emit(Step.SUBTITLE, "获取/生成字幕 ...")
    segs, complete = subtitle.ensure_subtitle(
        asset, workdir=project.workdir, on_status=_on_sub_status, cancel=cancel)
    project.subtitles = segs
    project.source_lang = asset.source_lang
    if not complete:
        # 被取消：已增量落盘 part.srt，记 PAUSED 并返回（下次重投同 URL 即续传）
        _emit(Step.PAUSED, f"已暂停，已转写 {len(segs)} 段（重投同 URL 可续传）", project)
        return project, False

    if bilingual:
        _emit(Step.SUBTITLE, "生成双语第二轨 ...")
        en_segs, sec_lang, en_complete = subtitle.ensure_subtitle_secondary(
            asset, project.workdir, on_status=_on_sub_status, cancel=cancel)
        project.subtitles_en = en_segs
        project.secondary_lang = sec_lang
        if not en_complete:
            _emit(Step.PAUSED, f"第二轨已暂停，已 {len(en_segs)} 段（重投可续传）", project)
            return project, False

    _emit(Step.FRAMES, "抽取关键帧 ...")
    project.frames = frames.extract_keyframes(asset, workdir=project.workdir)

    _emit(Step.MARKDOWN, "组装 Markdown ...")
    markdown.build(project)
    save_project_json(project)  # 持久化，供 Web /projects /doc 读取

    _emit(Step.DONE, "完成", project)
    return project, True


def run_async_job(job_id: str, url: str, cookies_path: Optional[str] = None,
                  bilingual: bool = False, force: bool = False) -> None:
    """在后台线程跑 pipeline，更新 _JOBS[job_id]。"""

    state = JobState(job_id=job_id, url=url)

    def _emit(step: Step, msg: str, proj: Optional[Project] = None):
        with _LOCK:
            state.step = step
            state.message = msg
            if proj is not None:
                state.project = proj
        _JOBS[job_id] = state

    def _cancel():
        with _LOCK:
            return state.cancel_requested

    try:
        _, complete = run(url, cookies_path=cookies_path, bilingual=bilingual,
                          force=force, cancel=_cancel, on_progress=_emit)
        if not complete:
            # 取消：state 已在 run 内置为 PAUSED，这里兜底
            with _LOCK:
                if state.step not in (Step.PAUSED, Step.ERROR):
                    state.step = Step.PAUSED
            _JOBS[job_id] = state
    except Exception as e:
        with _LOCK:
            state.step = Step.ERROR
            state.error = f"{type(e).__name__}: {e}"
        _JOBS[job_id] = state


def save_project_json(project: Project) -> "Path":
    """把 project 序列化到 workdir/project.json（路径相对 workdir，自包含可移植）。"""
    p = project.workdir / "project.json"
    p.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


# ── CLI ───────────────────────────────────────────────
def _cli() -> None:
    # 中文控制台编码已在 config.py 导入时统一重配为 UTF-8
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("用法: python -m v2md.pipeline <视频URL|本地路径> [cookies.txt] [--bilingual] [--force]")
        print("  --bilingual  额外生成英文字幕（双语第二轨）")
        print("  --force      不复用已有项目，强制重新处理")
        sys.exit(0 if args else 1)
    bilingual = "--bilingual" in args
    force = "--force" in args
    args = [a for a in args if a not in ("--bilingual", "--force")]
    url = args[0]
    cookies = args[1] if len(args) > 1 else None

    def cb(step: Step, msg: str, proj):
        print(f"[{step.value}] {msg}")

    proj, complete = run(url, cookies_path=cookies, bilingual=bilingual, force=force, on_progress=cb)
    if complete:
        save_project_json(proj)  # CLI 兜底再存一次（幂等）
        print(f"\n[OK] 完成 project_id={proj.id}")
        print(f"   目录:     {proj.workdir}")
        print(f"   Markdown: {proj.md_path}")
        print(f"   帧:       {len(proj.frames)}  字幕段: {len(proj.subtitles)}"
              + (f"  英文轨: {len(proj.subtitles_en)}" if bilingual else ""))
    else:
        print(f"\n[PAUSED] 已暂停 project_id={proj.id}，已转写 {len(proj.subtitles)} 段。")
        print(f"   重投同 URL 即可续传：python -m v2md.pipeline {url}")


if __name__ == "__main__":
    _cli()
