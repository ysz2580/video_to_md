"""AI 增强字幕：对已处理项目的字幕做清洗/纠错、自动章节、摘要标签。

复用 v2md/ai.py 的多供应商通道（ai.chat）。**默认不跑**——由
`POST /api/project/{pid}/ai` 触发，可对已处理项目后续启动（比较耗时，走 LLM）。

三项任务（可单选/多选）：
  clean     清洗字幕文本（加标点、纠错、去口语），时间戳不变，回写 subtitle.srt
  chapters  读全文字幕按语义切 3~8 章，输出 [{title, start_s}]，注入时间流
  summary   一句话摘要 + 3~5 标签，存 project.summary/tags
完成后重建 project.md + project.json。

任务状态存进程内 _AI_JOBS（教学项目够用）。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from v2md import ai
from v2md.models import SubtitleSegment

log = logging.getLogger(__name__)


def is_available() -> bool:
    return ai.is_available()


# ── 清洗：保持段序与时间戳，只改文本 ──
def clean_subtitles(segs: list[SubtitleSegment],
                    on_progress: Optional[Callable[[int, int], None]] = None
                    ) -> list[str]:
    """批量清洗字幕文本，返回与 segs 等长的清洗后文本列表。"""
    n = len(segs)
    out: list[str] = [""] * n
    if n == 0:
        return out
    chunk = 60
    for start in range(0, n, chunk):
        batch = segs[start:start + chunk]
        prompt = "\n".join(f"{i+1}. {s.text}" for i, s in enumerate(batch))
        messages = [
            {"role": "system", "content":
             "你是专业字幕校对。修正用户给的每行字幕：补标点、纠错字、"
             "去重复口语词（如『然后然后』），保持原意与口语风格，不翻译、不扩写。"
             "逐行输出『编号. 校对后文本』，编号与顺序严格对应，不要多余解释。"},
            {"role": "user", "content": prompt},
        ]
        text = ai.chat(messages)
        lines: dict[int, str] = {}
        for ln in text.splitlines():
            m = __import__("re").match(r"\s*(\d+)\.\s*(.+)", ln)
            if m:
                lines[int(m.group(1))] = m.group(2).strip()
        for i in range(len(batch)):
            out[start + i] = lines.get(i + 1, "") or batch[i].text
        if on_progress:
            on_progress(start + len(batch), n)
    return out


# ── 章节：按语义切分 ──
def generate_chapters(segs: list[SubtitleSegment],
                      duration_s: float = 0.0) -> list[dict]:
    """读全文字幕，让 LLM 切 3~8 章，返回 [{title, start_s}] 按 start_s 升序。"""
    if not segs:
        return []
    import re
    # 压缩成带时间戳的文本（每段一行 mm:ss 文本），过长则只取前后采样
    def _mmss(s: float) -> str:
        s = max(0, int(s))
        return f"{s//60:02d}:{s%60:02d}"
    lines = [f"{_mmss(s.start_s)} {s.text.strip()}" for s in segs]
    transcript = "\n".join(lines)
    if len(transcript) > 12000:  # 控制 token
        transcript = transcript[:6000] + "\n…（中段省略）…\n" + transcript[-6000:]
    messages = [
        {"role": "system", "content":
         "你是视频内容编辑。根据用户给的带时间戳字幕，把视频按语义切成 3~8 个章节。"
         "每行输出『mm:ss 章节标题』，mm:ss 为该章起始时间，标题 4~10 字。"
         "首行必为 00:00。只输出这些行，不要解释。"},
        {"role": "user", "content": f"时长约 {duration_s:.0f}s。字幕：\n{transcript}"},
    ]
    text = ai.chat(messages)
    chapters: list[dict] = []
    for ln in text.splitlines():
        m = re.match(r"\s*(\d{1,2}):(\d{2})\s+(.+)", ln)
        if m:
            mm, ss, title = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            chapters.append({"title": title[:40], "start_s": mm * 60 + ss})
    # 去重（同 start_s 只留首个）、排序
    seen, uniq = set(), []
    for c in sorted(chapters, key=lambda c: c["start_s"]):
        if c["start_s"] in seen:
            continue
        seen.add(c["start_s"])
        uniq.append(c)
    return uniq


# ── 摘要 + 标签 ──
def summarize(segs: list[SubtitleSegment]) -> tuple[str, list[str]]:
    """返回 (一句话摘要, 标签列表)。"""
    if not segs:
        return "", []
    import re
    transcript = " ".join(s.text.strip() for s in segs)
    if len(transcript) > 12000:
        transcript = transcript[:6000] + " … " + transcript[-6000:]
    messages = [
        {"role": "system", "content":
         "根据用户给的字幕，输出两行：第一行一句话摘要（30 字内，陈述视频讲什么）；"
         "第二行 3~5 个关键词标签，用逗号分隔。不要其它内容。"},
        {"role": "user", "content": transcript},
    ]
    text = ai.chat(messages)
    ls = [l.strip() for l in text.splitlines() if l.strip()]
    summary = ls[0] if ls else ""
    tags = [t.strip() for t in re.split(r"[，,、；;]", ls[1]) if t.strip()] if len(ls) > 1 else []
    return summary[:200], tags[:6]


# ── 任务注册表 + 后台执行 ──
_AI_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def get_ai_job(pid: str) -> dict:
    with _LOCK:
        st = dict(_AI_JOBS.get(pid) or {"pid": pid, "status": "idle", "step": "", "message": ""})
    return st


def start_ai_job(pid: str, tasks: list[str]) -> dict:
    """启动 AI 增强后台任务（每项目同时只跑一个）。"""
    with _LOCK:
        cur = _AI_JOBS.get(pid)
        if cur and cur.get("status") == "running":
            return cur
        st = {"pid": pid, "status": "running", "step": "", "message": "",
              "tasks": list(tasks), "done": []}
        _AI_JOBS[pid] = st
    th = threading.Thread(target=_run_ai_job, args=(pid, list(tasks)), daemon=True)
    th.start()
    return st


def _set(pid: str, **kw):
    with _LOCK:
        if pid in _AI_JOBS:
            _AI_JOBS[pid].update(kw)


def _run_ai_job(pid: str, tasks: list[str]):
    import config
    from v2md.models import Project
    from v2md import subtitle as S, markdown, pipeline
    wd = config.project_workdir(pid)
    try:
        if not (wd / "project.json").exists():
            _set(pid, status="error", message="项目不存在"); return
        proj = Project.load(wd)
        if not proj.subtitles:
            _set(pid, status="error", message="无字幕，无法 AI 增强"); return
        segs = proj.subtitles

        for t in tasks:
            if t == "clean":
                _set(pid, step="clean", message="清洗字幕中…")
                cleaned = clean_subtitles(segs)
                for i, s in enumerate(proj.subtitles):
                    s.text = cleaned[i]
                # 回写主轨 srt
                srt = wd / "subtitle.srt"
                if srt.exists() or proj.asset.subtitle_path:
                    S.write_srt(proj.subtitles, srt)
                # 第二轨也清洗
                if proj.subtitles_en:
                    cleaned_en = clean_subtitles(proj.subtitles_en)
                    for i, s in enumerate(proj.subtitles_en):
                        s.text = cleaned_en[i]
                    S.write_srt(proj.subtitles_en, wd / "subtitle.en.srt")
                _set(pid, message=f"清洗完成（{len(segs)} 段）", done=_append(pid, "clean"))
            elif t == "chapters":
                _set(pid, step="chapters", message="生成章节中…")
                dur = proj.asset.duration_s if proj.asset else 0
                proj.chapters = generate_chapters(proj.subtitles, dur)
                _set(pid, message=f"生成 {len(proj.chapters)} 个章节", done=_append(pid, "chapters"))
            elif t == "summary":
                _set(pid, step="summary", message="生成摘要中…")
                proj.summary, proj.tags = summarize(proj.subtitles)
                _set(pid, message="摘要完成", done=_append(pid, "summary"))

        # 重建 md + json
        markdown.build(proj)
        pipeline.save_project_json(proj)
        _set(pid, status="done", step="", message=f"AI 增强完成（{','.join(tasks)}）")
        log.info("AI 增强 done: %s tasks=%s", pid, tasks)
    except Exception as e:
        _set(pid, status="error", message=f"AI 增强出错: {e}")
        log.exception("AI 增强 失败 %s", pid)


def _append(pid: str, name: str) -> list:
    with _LOCK:
        st = _AI_JOBS.setdefault(pid, {})
        done = list(st.get("done") or [])
        if name not in done:
            done.append(name)
        st["done"] = done
        return done
