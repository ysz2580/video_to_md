"""模块4a：把 asset + subtitles + frames 组装成图文 Markdown。

每个关键帧一个区块：时间戳跳转链接（默认跳本地播放器，带项目 id+时间自动定位；
另保留 B 站网页版入口）+ 图片 + 该帧时间窗内的全部字幕段（不丢失口述信息）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import config
from v2md.models import Project, Frame, SubtitleSegment, fmt_time

import logging
log = logging.getLogger(__name__)


def _caption_for_time(segs: list[SubtitleSegment], t: float,
                     window: float = 8.0) -> str:
    """取时间 t 附近的字幕文本（含该时间的段；否则 ±window 内最近的段）。"""
    # 1) 直接命中
    for s in segs:
        if s.contains(t):
            return s.text
    # 2) 最近的（在 ±window 内）
    best: Optional[SubtitleSegment] = None
    best_dt = float("inf")
    for s in segs:
        mid = (s.start_s + s.end_s) / 2
        dt = abs(mid - t)
        if dt < best_dt and dt <= window:
            best_dt = dt
            best = s
    return best.text if best else ""


def _subtitles_in_window(segs: list[SubtitleSegment], lo: float,
                         hi: float) -> list[SubtitleSegment]:
    """返回 start_s 落在 (lo, hi] 的字幕段（hi=inf 取 lo 之后全部）。

    用 hi=inf 处理最后一帧：把余下所有字幕都归入末帧区块，避免丢失结尾口述。
    """
    import math
    if math.isinf(hi):
        return [s for s in segs if s.start_s > lo]
    return [s for s in segs if lo < s.start_s <= hi]


def _local_link(project: Project, t: float) -> str:
    """本地 Web 播放器跳转链接（相对，端口无关）：/?p={id}&t={秒}。

    相对路径在任何端口的服务根都生效；脱离服务（单独打开 .md）时失效，
    单文件导出请用 build_embedded()。"""
    secs = int(round(t))
    return f"/?p={project.id}&t={secs}"


def _bilibili_link(project: Project, t: float) -> Optional[str]:
    """B 站网页版跳转（t= 参数定位）。无 bvid 时返回 None。"""
    asset = project.asset
    if not asset or not asset.bvid:
        return None
    secs = int(round(t))
    return f"https://www.bilibili.com/video/{asset.bvid}?t={secs}"


def sections(project: Project) -> list[dict]:
    """把 frames + subtitles 组成结构化文档区段，供前端渲染（与 .md 内容一致）。

    每条字幕归到**时间最近的帧**（nearest-frame），而非「上一帧到本帧」的向后窗。
    这样每条字幕都贴近其截图时间（误差 ≤ 半个帧间隔），删一帧后字幕自动重新
    分配到最近邻，不会出现「早字幕挂在晚截图下」的错位。字幕按 start_s 升序。
    每个区段：{timestamp_s, image_name, subs:[...], subs_en:[...]}
    subs 每条附 en（时间重叠最大的英文段，逐句对齐双语）。
    """
    from v2md.subtitle import align_en_to_zh
    frames_sorted = sorted(project.frames, key=lambda x: x.timestamp_s)
    if not frames_sorted:
        return []

    # 每条字幕归到最近的帧索引（中/英各自）
    def _assign(segs):
        buckets = [[] for _ in frames_sorted]
        for s in segs:
            best_i, best_d = 0, abs(s.start_s - frames_sorted[0].timestamp_s)
            for i, f in enumerate(frames_sorted):
                d = abs(s.start_s - f.timestamp_s)
                if d < best_d:
                    best_d, best_i = d, i
            buckets[best_i].append(s)
        return buckets
    zh_buckets = _assign(project.subtitles)
    en_buckets = _assign(project.subtitles_en)

    out: list[dict] = []
    for i, f in enumerate(frames_sorted):
        window = zh_buckets[i]
        window_en = en_buckets[i]
        aligned = align_en_to_zh(window, window_en)
        subs_out = []
        for j, s in enumerate(window):
            en = aligned[j] if j < len(aligned) else None
            subs_out.append({
                "start_s": s.start_s, "end_s": s.end_s, "text": s.text,
                "en": ({"start_s": en.start_s, "end_s": en.end_s, "text": en.text}
                       if en else None),
            })
        out.append({
            "timestamp_s": f.timestamp_s,
            "image_name": Path(f.image_path).name,
            "subs": subs_out,
            "subs_en": [{"start_s": s.start_s, "end_s": s.end_s, "text": s.text}
                        for s in window_en],
        })
    return out


def _image_src(project: Project, image_name: str, embed: bool) -> str:
    """图片源：embed=True 返回 base64 data URI（脱离服务可分享），否则相对路径。"""
    if not embed:
        return f"frames/{image_name}"
    import base64
    p = project.workdir / "frames" / image_name
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


def build(project: Project, embed: bool = False) -> Path:
    """组装并落地 Markdown 文件，返回其路径。

    embed=False 写 project.md（相对链接+相对图片，需服务/同目录）。
    embed=True  写 project.embedded.md（base64 内嵌图片，单文件可分享）。
    """
    asset = project.asset
    lines: list[str] = []

    title = (asset.title if asset else "未命名视频") or "未命名视频"
    lines.append(f"# {title}")
    lines.append("")

    if asset:
        bilibili_page = (f"https://www.bilibili.com/video/{asset.bvid}"
                         if asset.bvid else asset.source_url)
        lines.append(f"- 来源(网页版): [{bilibili_page}]({bilibili_page})")
        if asset.bvid:
            lines.append(f"- BV号: `{asset.bvid}`")
        if asset.duration_s:
            lines.append(f"- 时长: {fmt_time(asset.duration_s)}")
        lines.append(f"- 关键帧: {len(project.frames)} 张")
        if project.subtitles:
            lines.append(f"- 字幕: {len(project.subtitles)} 段")
        lines.append("")

    lines.append("> 每条字幕归到时间最近的帧；**早于帧的字幕在图片上方、晚于帧的在下方**，"
                 "全篇时间单调递增；字幕行时间戳可点击跳转本地播放器（需服务运行），🌐 跳 B站。")
    lines.append("")

    # 复用 sections()，保证 .md 与前端文档视图内容一致
    for sec in sections(project):
        t = sec["timestamp_s"]
        local = _local_link(project, t)
        bili = _bilibili_link(project, t)
        subs = sec["subs"] or []
        # 早于帧的字幕在图片上方
        before = [s for s in subs if s["start_s"] < t]
        after = [s for s in subs if s["start_s"] >= t]

        def _emit_sub(s):
            lines.append(f"> [{fmt_time(s['start_s'])}]({_local_link(project, s['start_s'])}) "
                         f"{s['text'].strip()}")
            en = s.get("en")
            if en:
                lines.append(f">   ↳ [{fmt_time(en['start_s'])}]({_local_link(project, en['start_s'])}) "
                             f"{en['text'].strip()}")

        for s in before:
            _emit_sub(s)
        head = f"## ⏱ [{fmt_time(t)}]({local})"
        if bili:
            head += f"  ·  [🌐 B站]({bili})"
        lines.append(head)
        lines.append("")
        lines.append(f"![frame @ {fmt_time(t)}]({_image_src(project, sec['image_name'], embed)})")
        lines.append("")
        for s in after:
            _emit_sub(s)
        lines.append("---")
        lines.append("")

    name = "project.embedded.md" if embed else "project.md"
    md_path = project.workdir / name
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    if not embed:
        project.md_path = md_path
    log.info("生成 %s（embed=%s）", md_path.name, embed)
    return md_path


def build_embedded(project: Project) -> Path:
    """生成图片 base64 内嵌的单文件 Markdown（脱离服务可分享）。"""
    return build(project, embed=True)


# ── CLI ───────────────────────────────────────────────
def _cli() -> None:
    import json
    import sys
    from v2md.models import VideoAsset, Frame
    if len(sys.argv) < 2:
        print("用法: python -m v2md.markdown <project.json>")
        print("  project.json 由 pipeline --json 产出")
        sys.exit(1)
    # 简易：直接从 json 重建 project（仅 CLI 演示用）
    json_path = Path(sys.argv[1])
    data = json.loads(json_path.read_text(encoding="utf-8"))
    workdir = json_path.parent  # project.json 所在目录即 workdir
    def _abs(rel: Optional[str]) -> Optional[Path]:
        if not rel:
            return None
        p = Path(rel)
        return p if p.is_absolute() else (workdir / p)
    asset = VideoAsset(
        video_path=_abs(data["asset"]["video_path"]),
        subtitle_path=_abs(data["asset"]["subtitle_path"]) if data["asset"].get("subtitle_path") else None,
        source_url=data["asset"]["source_url"],
        bvid=data["asset"].get("bvid"),
        title=data["asset"].get("title", ""),
        duration_s=data["asset"].get("duration_s", 0.0),
    )
    proj = Project(id=data["id"], workdir=workdir, asset=asset,
                   subtitles=[SubtitleSegment(**s) for s in data["subtitles"]],
                   frames=[Frame(timestamp_s=f["timestamp_s"],
                                 image_path=_abs(f["image_path"]),
                                 phash=f.get("phash", "")) for f in data["frames"]])
    out = build(proj)
    print(f"已生成: {out}")


if __name__ == "__main__":
    _cli()
