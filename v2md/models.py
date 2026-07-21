"""模块间数据契约（dataclass）。

四个解耦模块之间只通过这些 dataclass 传递数据，互不 import 业务逻辑。
所有路径用 Path 绝对路径，便于序列化与跨平台。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def new_project_id() -> str:
    """短 id 作为项目目录/文件名。"""
    return uuid.uuid4().hex[:12]


@dataclass
class VideoAsset:
    """模块1的输出：一个已落地的视频（可能带字幕文件）。"""
    video_path: Path
    subtitle_path: Optional[Path]        # 可能为 None（无字幕，需模块2兜底）
    source_url: str
    bvid: Optional[str] = None           # B 站 BV 号，用于 .md 跳转链接
    title: str = ""
    duration_s: float = 0.0
    content_hash: Optional[str] = None   # 本地文件内容的 sha256，用于去重复用
    source_lang: Optional[str] = None    # 原音语言（whisper 自动检测，如 zh/en）

    @property
    def has_subtitle(self) -> bool:
        return self.subtitle_path is not None and self.subtitle_path.exists()


@dataclass
class SubtitleSegment:
    """一条带时间戳的字幕片段。"""
    start_s: float
    end_s: float
    text: str

    def contains(self, t: float) -> bool:
        return self.start_s <= t <= self.end_s


@dataclass
class Frame:
    """一个抽取出的关键帧（图片 + 时间戳）。"""
    timestamp_s: float
    image_path: Path
    phash: str = ""


@dataclass
class Project:
    """整条 pipeline 的产物：聚合 asset / subtitles / frames / md。

    每个视频一个独立工作目录 workdir，所有产物都在其中：
      video.mp4 / subtitle.srt / frames.txt / frames/*.jpg / project.json / project.md
    """
    id: str = field(default_factory=new_project_id)
    workdir: Optional[Path] = None
    asset: Optional[VideoAsset] = None
    subtitles: list[SubtitleSegment] = field(default_factory=list)
    subtitles_en: list[SubtitleSegment] = field(default_factory=list)  # 双语第二轨（语言见 secondary_lang）
    frames: list[Frame] = field(default_factory=list)
    md_path: Optional[Path] = None
    content_hash: Optional[str] = None   # 顶层冗余存一份，便于复用查找
    source_lang: Optional[str] = None   # 原音语言（zh/en/…）
    secondary_lang: Optional[str] = None  # 双语第二轨语言（原音非英文→"en"；原音英文→"zh"）
    # AI 增强（默认不跑，由 /api/project/{pid}/ai 后续触发；见 v2md/ai_enhance.py）
    chapters: list = field(default_factory=list)    # [{title, start_s}]，按 start_s 升序
    summary: Optional[str] = None                   # 一句话摘要
    tags: list = field(default_factory=list)        # 关键词标签
    created_at: str = field(default_factory=_ts)

    def to_dict(self, rel_to: Optional[Path] = None) -> dict:
        """序列化为 JSON 友好的 dict。rel_to 非空时把路径转成相对它（默认相对 workdir）。"""
        if rel_to is None:
            rel_to = self.workdir
        def _p(p: Optional[Path]) -> Optional[str]:
            if p is None:
                return None
            p = Path(p)
            try:
                return str(p.resolve().relative_to(rel_to.resolve())) if rel_to else str(p)
            except ValueError:
                return str(p)
        d = asdict(self)
        d["workdir"] = _p(self.workdir)
        if self.asset:
            d["asset"] = {
                "video_path": _p(self.asset.video_path),
                "subtitle_path": _p(self.asset.subtitle_path),
                "source_url": self.asset.source_url,
                "bvid": self.asset.bvid,
                "title": self.asset.title,
                "duration_s": self.asset.duration_s,
                "has_subtitle": self.asset.has_subtitle,
                "content_hash": self.asset.content_hash,
                "source_lang": self.asset.source_lang,
            }
        d["md_path"] = _p(self.md_path)
        d["content_hash"] = self.content_hash
        d["source_lang"] = self.source_lang
        d["secondary_lang"] = self.secondary_lang
        d["chapters"] = self.chapters
        d["summary"] = self.summary
        d["tags"] = self.tags
        for f in d["frames"]:
            f["image_path"] = _p(Path(f["image_path"])) if f["image_path"] else None
        d["created_at"] = self.created_at
        return d

    @classmethod
    def load(cls, workdir) -> "Project":
        """从 workdir/project.json 重建 Project（复用已有项目用）。"""
        import json
        workdir = Path(workdir)
        d = json.loads((workdir / "project.json").read_text(encoding="utf-8"))
        ad = d.get("asset") or {}
        asset = VideoAsset(
            video_path=workdir / (ad.get("video_path") or "video.mp4"),
            subtitle_path=workdir / ad["subtitle_path"] if ad.get("subtitle_path") else None,
            source_url=ad.get("source_url", ""),
            bvid=ad.get("bvid"),
            title=ad.get("title", ""),
            duration_s=ad.get("duration_s", 0.0),
            content_hash=ad.get("content_hash"),
            source_lang=ad.get("source_lang"),
        )
        md_rel = d.get("md_path") or "project.md"
        return cls(
            id=d.get("id", workdir.name),
            workdir=workdir,
            asset=asset,
            subtitles=[SubtitleSegment(**s) for s in d.get("subtitles", [])],
            subtitles_en=[SubtitleSegment(**s) for s in d.get("subtitles_en", [])],
            frames=[Frame(timestamp_s=f["timestamp_s"],
                          image_path=workdir / f["image_path"],
                          phash=f.get("phash", ""))
                    for f in d.get("frames", [])],
            md_path=workdir / md_rel,
            content_hash=d.get("content_hash"),
            source_lang=d.get("source_lang"),
            secondary_lang=d.get("secondary_lang"),
            chapters=d.get("chapters", []),
            summary=d.get("summary"),
            tags=d.get("tags", []),
            created_at=d.get("created_at", _ts()),
        )


def fmt_time(total_s: float) -> str:
    """秒 -> mm:ss 或 h:mm:ss。"""
    total_s = max(0, int(round(total_s)))
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
