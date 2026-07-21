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


def timeline(project: Project) -> list[dict]:
    """把 frames + subtitles 组成**按时间全局排序的事件流**，供前端渲染（与 .md 一致）。

    每个帧、每条字幕都作为独立事件，按各自时间戳排进同一条时间轴——
    谁早就谁在前，天然单调，删帧/插帧/字幕重分配都不会出现错位。
    事件：{type:'frame', t, image_name} 或 {type:'sub', t, start_s, end_s, text, en}。
    en = 时间重叠最大的第二轨段（逐句对齐双语，可为 None）。
    同一时刻帧排在字幕前（帧先出，字幕随后）。
    """
    from v2md.subtitle import align_en_to_zh
    en_pairs = align_en_to_zh(project.subtitles, project.subtitles_en)
    events: list[dict] = []
    for f in project.frames:
        events.append({"type": "frame", "t": f.timestamp_s,
                       "image_name": Path(f.image_path).name})
    for j, s in enumerate(project.subtitles):
        en = en_pairs[j] if j < len(en_pairs) else None
        events.append({
            "type": "sub", "t": s.start_s, "start_s": s.start_s, "end_s": s.end_s,
            "text": s.text,
            "en": ({"start_s": en.start_s, "end_s": en.end_s, "text": en.text}
                   if en else None),
        })
    # AI 章节作为独立事件注入时间流（章节标题排在同时刻帧/字幕之前）
    for ch in (project.chapters or []):
        try:
            events.append({"type": "chapter", "t": float(ch["start_s"]),
                           "title": ch.get("title", "")})
        except Exception:
            continue
    # 按时间升序；同 t 时 chapter(-1)→frame(0)→sub(1)
    order = {"chapter": -1, "frame": 0, "sub": 1}
    events.sort(key=lambda e: (e["t"], order.get(e["type"], 2)))
    return events


# 兼容旧调用（已弃用，统一用 timeline）
def sections(project: Project) -> list[dict]:
    return timeline(project)


def _image_src(project: Project, image_name: str, embed: bool) -> str:
    """图片源：embed=True 返回 base64 data URI（脱离服务可分享），否则相对路径。"""
    if not embed:
        return f"frames/{image_name}"
    import base64
    p = project.workdir / "frames" / image_name
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


def build(project: Project, embed: bool = False,
          include_notes: bool = False, notes: list = None) -> Path:
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

    lines.append("> 帧与字幕按各自时间戳排成一条流，全篇时间单调递增；"
                 "字幕行时间戳可点击跳转本地播放器（需服务运行），🌐 跳 B站。")
    lines.append("")

    # AI 摘要 / 标签
    if project.summary:
        lines.append(f"> 📝 **摘要**：{project.summary.strip()}")
        lines.append("")
    if project.tags:
        lines.append("> 🏷 " + " ".join(f"`{t.strip()}`" for t in project.tags if str(t).strip()))
        lines.append("")

    # 全局时间排序的事件流：帧/字幕/章节/笔记谁早就谁在前，天然单调
    tl = timeline(project)
    if include_notes and notes:
        for n in notes:
            try:
                tl.append({"type": "note", "t": float(n.get("t", 0)),
                           "text": n.get("text", ""), "id": n.get("id", "")})
            except Exception:
                continue
        _ORDER = {"chapter": -1, "frame": 0, "sub": 1, "note": 2}
        tl.sort(key=lambda e: (e["t"], _ORDER.get(e["type"], 3)))
    for ev in tl:
        if ev["type"] == "frame":
            t = ev["t"]
            local = _local_link(project, t)
            bili = _bilibili_link(project, t)
            head = f"## ⏱ [{fmt_time(t)}]({local})"
            if bili:
                head += f"  ·  [🌐 B站]({bili})"
            lines.append(head)
            lines.append("")
            lines.append(f"![frame @ {fmt_time(t)}]({_image_src(project, ev['image_name'], embed)})")
            lines.append("")
        elif ev["type"] == "chapter":
            ch_t = ev["t"]
            lines.append(f"## 📑 [{fmt_time(ch_t)}]({_local_link(project, ch_t)}) {ev.get('title','').strip()}")
            lines.append("")
        elif ev["type"] == "note":
            nt = ev["t"]
            lines.append(f"> 📝 **笔记** [{fmt_time(nt)}]({_local_link(project, nt)}) {ev.get('text','').strip()}")
            lines.append("")
        else:  # sub
            s = ev
            lines.append(f"> [{fmt_time(s['start_s'])}]({_local_link(project, s['start_s'])}) "
                         f"{s['text'].strip()}")
            en = s.get("en")
            if en:
                lines.append(f">   ↳ [{fmt_time(en['start_s'])}]({_local_link(project, en['start_s'])}) "
                             f"{en['text'].strip()}")
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


def build_embedded(project: Project, include_notes: bool = False,
                   notes: list = None) -> Path:
    """生成图片 base64 内嵌的单文件 Markdown（脱离服务可分享）。"""
    return build(project, embed=True, include_notes=include_notes, notes=notes)


# ── 静态单文件 HTML 导出 ──────────────────────────────────
def _hesc(s: str) -> str:
    """HTML 文本转义。"""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(
        ">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def _b64_image(project: Project, image_name: str) -> str:
    """某帧图片的 base64 data URI。"""
    import base64
    p = project.workdir / "frames" / image_name
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


def build_html(project: Project, include_notes: bool = False,
               notes: list = None) -> Path:
    """生成**自包含单文件 HTML**：base64 内嵌图片 + 内联 CSS/JS，双击即可离线阅读。

    与 project.md 不同之处：
    - 脱离服务也能用（不依赖 /media/... 路由）；
    - 时间戳点击 = 页内锚点滚动到对应事件（无播放器可跳，故不内嵌体积庞大的视频）；
    - 带「中/英/双语」语言切换、大纲跳转、字幕搜索（与 Web 文档视图体验一致）。
    """
    asset = project.asset
    title = (asset.title if asset else "未命名视频") or "未命名视频"
    tl = timeline(project)
    if include_notes and notes:
        for n in notes:
            try:
                tl.append({"type": "note", "t": float(n.get("t", 0)),
                           "text": n.get("text", ""), "id": n.get("id", "")})
            except Exception:
                continue
        _ORDER = {"chapter": -1, "frame": 0, "sub": 1, "note": 2}
        tl.sort(key=lambda e: (e["t"], _ORDER.get(e["type"], 3)))
    has_en = any(ev.get("en") for ev in tl if ev["type"] == "sub")

    # 顶部信息
    meta_lines: list[str] = []
    if asset:
        page = (f"https://www.bilibili.com/video/{asset.bvid}"
                if asset.bvid else asset.source_url)
        if page:
            meta_lines.append(
                f'<li>来源(网页版): <a href="{_hesc(page)}" target="_blank" rel="noopener">{_hesc(page)}</a></li>')
        if asset.bvid:
            meta_lines.append(f"<li>BV号: <code>{_hesc(asset.bvid)}</code></li>")
        if asset.duration_s:
            meta_lines.append(f"<li>时长: {fmt_time(asset.duration_s)}</li>")
    meta_lines.append(f"<li>关键帧: {sum(1 for e in tl if e['type']=='frame')} 张</li>")
    nsub = sum(1 for e in tl if e['type'] == 'sub')
    if nsub:
        meta_lines.append(f"<li>字幕: {nsub} 段</li>")

    # 大纲（仅帧事件，含其后第一条字幕预览）
    frame_evs = [e for e in tl if e["type"] == "frame"]
    outline_opts = []
    for f in frame_evs:
        nxt = next((e for e in tl if e["type"] == "sub" and e["start_s"] >= f["t"]), None)
        preview = (nxt["text"] if nxt else "(无字幕)")[:18]
        outline_opts.append(
            f'<option value="ev-{int(round(f["t"]*1000))}">'
            f'{fmt_time(f["t"])} · {_hesc(preview)}</option>')

    # 事件流
    body_parts: list[str] = []
    for ev in tl:
        ev_id = f'ev-{int(round(ev["t"]*1000))}'
        if ev["type"] == "frame":
            t = ev["t"]
            bili = _bilibili_link(project, t)
            head = (f'<a class="ts" href="#{ev_id}">⏱ {fmt_time(t)}</a>')
            if bili:
                head += (f'  ·  <a class="bili" href="{_hesc(bili)}" '
                        f'target="_blank" rel="noopener">🌐 B站</a>')
            body_parts.append(
                f'<section class="frame" id="{ev_id}">'
                f'<div class="sec-h">{head}</div>'
                f'<img loading="lazy" src="{_b64_image(project, ev["image_name"])}" '
                f'alt="frame {fmt_time(t)}"></section>')
        elif ev["type"] == "chapter":
            ch_t = ev["t"]
            body_parts.append(
                f'<h2 class="chapter" id="{ev_id}">'
                f'<a class="ts" href="#{ev_id}">⏱ {fmt_time(ch_t)}</a> '
                f'{_hesc(ev.get("title", "").strip())}</h2>')
        elif ev["type"] == "note":
            nt = ev["t"]
            body_parts.append(
                f'<p class="note" id="{ev_id}">'
                f'<a class="ts" href="#{ev_id}">📝 {fmt_time(nt)}</a>'
                f'<span>{_hesc(ev.get("text", "").strip())}</span></p>')
        else:
            s = ev
            line = (f'<p class="subline" id="{ev_id}">'
                    f'<a class="ts" href="#{ev_id}">{fmt_time(s["start_s"])}</a>'
                    f'<span class="subtxt">{_hesc(s["text"].strip())}</span>')
            en = s.get("en")
            if en:
                line += (f'<span class="en">'
                         f'<a class="ts" href="#ev-{int(round(en["start_s"]*1000))}">'
                         f'{fmt_time(en["start_s"])}</a>'
                         f'{_hesc(en["text"].strip())}</span>')
            line += '</p>'
            body_parts.append(line)

    lang_btns = ""
    if has_en:
        lang_btns = ('<div class="langbar">显示：'
                     '<button data-lang="zh">中</button>'
                     '<button data-lang="en">英</button>'
                     '<button data-lang="bi" class="on">双语</button>'
                     '<input id="q" placeholder="搜字幕…"></div>')

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_hesc(title)} · 图文</title>
<style>
:root{{--bg:#fafafa;--fg:#222;--muted:#888;--line:#e3e3e3;--accent:#2b6cb0;--sub:#555}}
*{{box-sizing:border-box}}
body{{margin:0;font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--fg)}}
header{{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid var(--line);
  padding:10px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
header select,header input{{font-size:13px;padding:5px 8px;border:1px solid var(--line);
  border-radius:6px;background:#fff}}
header input{{flex:0 1 200px}}
main{{max-width:900px;margin:0 auto;padding:18px}}
h1{{font-size:22px;margin:0 0 6px}}
ul.meta{{list-style:none;padding:0;margin:6px 0 0;font-size:13px;color:var(--muted)}}
ul.meta li{{margin:2px 0}}
a{{color:var(--accent);text-decoration:none}}
a:hover{{text-decoration:underline}}
.frame{{background:#fff;border:1px solid var(--line);border-radius:10px;
  padding:10px 12px;margin:14px 0;scroll-margin-top:64px}}
.sec-h{{font-size:13px;color:var(--muted);margin-bottom:6px}}
.sec-h .bili{{margin-left:8px}}
img{{max-width:100%;height:auto;border-radius:6px;display:block}}
.subline{{margin:0;padding:6px 12px;border-left:3px solid transparent;
  scroll-margin-top:64px;font-size:14px;color:var(--sub)}}
.subline .ts{{display:inline-block;min-width:54px;color:var(--muted);font-size:12px}}
.subline .en{{display:block;margin-left:54px;color:#999;font-size:13px}}
.subline.match{{background:#fff8d8;border-left-color:#e6c200}}
.subline.hide{{display:none}}
hr{{border:none;border-top:1px dashed var(--line);margin:10px 0}}
.langbar{{margin-left:auto;display:flex;gap:6px;align-items:center;font-size:13px;color:var(--muted)}}
.langbar button{{padding:3px 9px;font-size:12px;border:1px solid var(--line);
  border-radius:6px;background:#fff;cursor:pointer}}
.langbar button.on{{background:var(--accent);color:#fff;border-color:var(--accent)}}
.chapter{{font-size:17px;margin:18px 0 4px;padding:6px 0 4px;border-bottom:2px solid var(--line)}}
.chapter .ts{{color:var(--accent);font-size:12px;margin-right:6px}}
.note{{margin:0;padding:6px 12px;border-left:3px solid #e6c200;background:#fff8d8;
  scroll-margin-top:64px;font-size:13px;color:#6b5800}}
.note .ts{{color:#b08800;font-size:12px;margin-right:6px}}
.summary{{background:#fff;border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:10px 0;font-size:14px}}
.summary .tags{{margin-top:4px;font-size:12px;color:var(--accent)}}
</style></head><body>
<header>
  <select id="outline"><option value="">大纲跳转…</option>{''.join(outline_opts)}</select>
  {lang_btns}
</header>
<main>
<h1>{_hesc(title)}</h1>
{('<div class="summary">📝 ' + _hesc(project.summary.strip()) + (''.join(f'<span class="tags">`{_hesc(str(t))}`</span> ' for t in project.tags if str(t).strip())) + '</div>') if project.summary else ''}
<ul class="meta">{''.join(meta_lines)}</ul>
<p style="font-size:13px;color:var(--muted);margin:8px 0 4px">
帧与字幕按各自时间戳排成一条流，全篇时间单调递增；点击时间戳在本页内定位到对应事件，
🌐 跳转 B站原视频。</p>
{''.join(body_parts)}
</main>
<script>
// 大纲跳转
document.getElementById('outline').onchange=e=>{{
  const id=e.target.value; if(!id) return;
  const el=document.getElementById(id); if(el) el.scrollIntoView({{behavior:'smooth',block:'start'}});
  e.target.value='';
}};
// 中/英/双语 切换
document.querySelectorAll('.langbar button[data-lang]').forEach(b=>b.onclick=()=>{{
  document.querySelectorAll('.langbar button[data-lang]').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');
  const mode=b.dataset.lang;
  document.querySelectorAll('.subline .en').forEach(en=>{{
    en.style.display = (mode==='en'||mode==='bi') ? '' : 'none';
  }});
  document.querySelectorAll('.subline .subtxt').forEach(z=>{{
    z.style.display = (mode==='zh'||mode==='bi') ? '' : 'none';
  }});
}};
// 搜索：高亮+滚动，不匹配隐藏
const q=document.getElementById('q');
if(q) q.oninput=()=>{{
  const v=q.value.trim().toLowerCase(); let first=null;
  document.querySelectorAll('.subline').forEach(p=>{{
    if(!v){{p.classList.remove('hide','match');return;}}
    const hit=(p.textContent||'').toLowerCase().includes(v);
    p.classList.toggle('hide',!hit); p.classList.toggle('match',hit);
    if(hit&&!first) first=p;
  }});
  if(first) first.scrollIntoView({{behavior:'smooth',block:'center'}});
}};
</script>
</body></html>"""
    out = project.workdir / "project.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    log.info("生成 %s（自包含 HTML）", out.name)
    return out


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
