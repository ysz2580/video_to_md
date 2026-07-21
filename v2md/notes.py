"""时间点笔记/标注：独立 notes.json，与字幕/帧/章节完全解耦。

设计要求（用户）：笔记不能与原内容混在一起，要可拆解——
  - 数据：独立 notes.json（不进 project.json、不进 subtitle.srt）
  - 显示：前端「📝 笔记」开关，关则完全不渲染
  - 导出：md/html 默认不含笔记，?notes=1 才含

每条笔记：{id, t, text, created_at}，按 t 升序。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional


def _path(workdir) -> Path:
    return Path(workdir) / "notes.json"


def load(workdir) -> list[dict]:
    """读全部笔记（按 t 升序）。无则空列表。"""
    p = _path(workdir)
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    notes = d.get("notes") or []
    notes.sort(key=lambda n: float(n.get("t", 0)))
    return notes


def _save(workdir, notes: list[dict]) -> None:
    notes = sorted(notes, key=lambda n: float(n.get("t", 0)))
    _path(workdir).write_text(
        json.dumps({"notes": notes}, ensure_ascii=False, indent=2), encoding="utf-8")


def add(workdir, t: float, text: str) -> dict:
    """在时间 t 新增一条笔记，返回新笔记。"""
    notes = load(workdir)
    n = {"id": uuid.uuid4().hex[:10], "t": float(t),
         "text": (text or "").strip(), "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    notes.append(n)
    _save(workdir, notes)
    return n


def update(workdir, nid: str, text: str) -> Optional[dict]:
    notes = load(workdir)
    for n in notes:
        if n.get("id") == nid:
            n["text"] = (text or "").strip()
            _save(workdir, notes)
            return n
    return None


def delete(workdir, nid: str) -> bool:
    notes = load(workdir)
    new = [n for n in notes if n.get("id") != nid]
    if len(new) == len(notes):
        return False
    _save(workdir, new)
    return True
