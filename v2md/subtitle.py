"""模块2：字幕获取/生成。

优先解析模块1抓到的自带字幕（SRT）；若没有，则用本地 faster-whisper
对音频转写，生成带时间戳的字幕并落地为 SRT。输出 list[SubtitleSegment]。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional
from typing import Optional

import config
from v2md.models import VideoAsset, SubtitleSegment, fmt_time

import logging
log = logging.getLogger(__name__)

# HuggingFace 端点必须在 huggingface_hub 被 import 之前设置（faster-whisper 依赖它）。
# 国内网络下 huggingface.co 常被墙，改用 hf-mirror.com 镜像下模型。
if getattr(config, "HF_ENDPOINT", None) and not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = config.HF_ENDPOINT
if getattr(config, "HF_HUB_DISABLE_XET", False) and "HF_HUB_DISABLE_XET" not in os.environ:
    os.environ["HF_HUB_DISABLE_XET"] = "1"


# ── SRT 解析 ───────────────────────────────────────────
_TS = r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
_LINE_RE = re.compile(rf"{_TS}\s*-->\s*{_TS}")


def parse_srt(srt_path: Path) -> list[SubtitleSegment]:
    """解析 SRT 文件为 segments。容错：忽略空行/序号/非标准行。"""
    if not srt_path.exists():
        return []
    text = srt_path.read_text(encoding="utf-8", errors="ignore")
    # BOM 处理
    if text and text[0] == "﻿":
        text = text[1:]
    segs: list[SubtitleSegment] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for blk in blocks:
        lines = [ln.rstrip() for ln in blk.splitlines() if ln.strip()]
        if not lines:
            continue
        # 跳过纯序号行
        if lines[0].isdigit():
            lines = lines[1:]
        if not lines:
            continue
        m = _LINE_RE.search(lines[0])
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = m.groups()
        start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000
        end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000
        body = " ".join(l.strip() for l in lines[1:]).strip()
        if body:
            segs.append(SubtitleSegment(start_s=start, end_s=end, text=body))
    return segs


def _srt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segs: list[SubtitleSegment], srt_path: Path) -> None:
    """把 segments 写回 SRT。"""
    lines = []
    for i, seg in enumerate(segs, 1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(seg.start_s)} --> {_srt_ts(seg.end_s)}")
        lines.append(seg.text)
        lines.append("")
    srt_path.write_text("\n".join(lines), encoding=config.SRT_ENCODING)


def _append_srt(path: Path, seg: SubtitleSegment, idx: int) -> None:
    """追加一条 SRT 块到 path（用于增量落盘 part.srt，支持续传）。"""
    with open(path, "a", encoding=config.SRT_ENCODING) as f:
        f.write(f"{idx}\n{_srt_ts(seg.start_s)} --> {_srt_ts(seg.end_s)}\n{seg.text}\n\n")


def _whisper_model_cached() -> bool:
    """检测 whisper 模型是否已缓存本地（用于首次下载前提示）。"""
    from pathlib import Path as _P
    repo = f"models--Systran--faster-whisper-{config.WHISPER_MODEL}"
    cache = _P.home() / ".cache" / "huggingface" / "hub" / repo
    return cache.exists() and (cache / "snapshots").exists()


# ── Whisper 转写（模型单例 + 段进度回调 + 续传/取消）─────────────
_WHISPER_MODEL = None


def get_whisper_model():
    """模型单例：双语时中文+英文两次转写复用同一份加载，省一次加载耗时。"""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    # 模型已缓存时离线加载，避免 huggingface_hub 每次联网查 revision（国内常超时 30s）
    if _whisper_model_cached() and "HF_HUB_OFFLINE" not in os.environ:
        os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "未安装 faster-whisper。请在 3.12 虚拟环境里 uv sync 安装依赖。"
        ) from e
    _WHISPER_MODEL = WhisperModel(
        config.WHISPER_MODEL,
        device=config.WHISPER_DEVICE,
        compute_type=config.WHISPER_COMPUTE_TYPE,
    )
    log.info("已加载 whisper 模型 %s (%s/%s)", config.WHISPER_MODEL,
             config.WHISPER_DEVICE, config.WHISPER_COMPUTE_TYPE)
    return _WHISPER_MODEL


def _run_whisper(video_path: Path, task: Optional[str] = None,
                start_t: float = 0.0,
                on_seg: Optional[Callable[[SubtitleSegment, int], None]] = None,
                cancel: Optional[Callable[[], bool]] = None,
                ) -> tuple[list[SubtitleSegment], Optional[str], bool]:
    """通用转写。task=None 源语言转写(自动检测)，task='translate' 译为英文。

    start_t>0 时用 clip_timestamps 只转写 [start_t, 末尾]（断点续传）。
    on_seg(seg, count) 每产出一 segment 回调；cancel() 返回 True 时尽快停止。
    返回 (segments, detected_lang, cancelled)。
    """
    model = get_whisper_model()
    kw = dict(vad_filter=True, beam_size=5)
    if task:
        kw["task"] = task
    else:
        kw["language"] = config.WHISPER_LANGUAGE or None  # None=自动检测
    if start_t > 0:
        kw["clip_timestamps"] = f"{start_t:.3f}"  # str，从 start_t 转到末尾
    segments_gen, info = model.transcribe(str(video_path), **kw)
    segs: list[SubtitleSegment] = []
    n = 0
    cancelled = False
    for s in segments_gen:
        if cancel and cancel():
            cancelled = True
            break
        text = s.text.strip()
        if text:
            seg = SubtitleSegment(start_s=s.start, end_s=s.end, text=text)
            segs.append(seg)
            n += 1
            if on_seg:
                on_seg(seg, n)
    detected = getattr(info, "language", None)
    log.info("whisper task=%s start=%.1f 检测语言=%s 段数=%d 取消=%s",
             task or "transcribe", start_t, detected, len(segs), cancelled)
    return segs, detected, cancelled


def align_en_to_zh(zh: list[SubtitleSegment],
                   en: list[SubtitleSegment]) -> list[Optional[SubtitleSegment]]:
    """为每条中文字幕找时间重叠最大的英文段（逐句对齐双语）。

    返回与 zh 等长的列表，元素为对应的英文 SubtitleSegment（无重叠则为 None）。
    用于 md/前端按句显示中英对照，避免两套独立分段错位。
    """
    out: list[Optional[SubtitleSegment]] = []
    for z in zh:
        best, best_ov = None, 0.0
        for e in en:
            lo = max(z.start_s, e.start_s)
            hi = min(z.end_s, e.end_s)
            ov = max(0.0, hi - lo)
            if ov > best_ov:
                best_ov, best = ov, e
        out.append(best if best_ov > 0 else None)
    return out


# ── 对外入口 ──────────────────────────────────────────
def _make_progress_cb(on_status, duration, base, t0):
    """构造 on_seg(seg,count)：把段进度转成带 pct/ETA 的状态字符串。"""
    import time
    def _cb(seg, count):
        if not on_status:
            return
        cur = seg.end_s
        if duration and cur > 0:
            el = time.monotonic() - t0
            rate = cur / el if el > 0 else 0
            rem = (duration - cur) / rate if rate > 0 else 0
            on_status(f"转写 {cur/duration*100:.0f}%（{fmt_time(cur)}/{fmt_time(duration)}）"
                      f" 剩~{fmt_time(rem)} 已{base+count}段")
        else:
            on_status(f"转写中 已生成 {base+count} 段")
    return _cb


# ── 对外入口 ──────────────────────────────────────────
def ensure_subtitle(asset: VideoAsset, workdir: Path,
                    on_status: Optional[Callable[[str], None]] = None,
                    cancel: Optional[Callable[[], bool]] = None,
                    ) -> tuple[list[SubtitleSegment], bool]:
    """获取主轨字幕，返回 (segments, complete)。

    complete=False 表示被取消（已增量落盘 subtitle.part.srt，可续传）。
    - 已有 subtitle.srt → 完成，直接解析。
    - 有 subtitle.part.srt → 续传：从最后时间用 clip_timestamps 转尾部，增量追加 part。
    - 否则 → 全量转写，增量写 part.srt，完成 rename 成 subtitle.srt。
    on_status(str) 上报「下载模型/转写 pct+ETA」；cancel() 返回 True 时尽快停止。
    """
    import time
    workdir = Path(workdir)
    if asset.has_subtitle:
        segs = parse_srt(asset.subtitle_path)
        if segs:
            if asset.source_lang is None:
                asset.source_lang = "zh"
            return segs, True

    part = workdir / "subtitle.part.srt"
    partial = parse_srt(part) if part.exists() else []
    start_t = max((s.end_s for s in partial), default=0.0)
    base = len(partial)
    if on_status and start_t > 0:
        on_status(f"续传：已转写 {base} 段到 {fmt_time(start_t)}，从该处继续…")
    if on_status and not _whisper_model_cached():
        on_status("首次下载 whisper 模型 small (~470MB)，请稍候…")

    t0 = time.monotonic()
    on_seg = _make_progress_cb(on_status, asset.duration_s, base, t0)
    def _append(seg, count):
        _append_srt(part, seg, base + count)
        on_seg(seg, count)
    new, detected, cancelled = _run_whisper(
        asset.video_path, task=None, start_t=start_t, on_seg=_append, cancel=cancel)
    asset.source_lang = detected or asset.source_lang
    segs = partial + new
    if cancelled:
        log.info("主轨转写被取消，已存 %d 段于 %s（可续传）", len(segs), part.name)
        return segs, False
    if not segs:
        return [], True
    part.rename(workdir / "subtitle.srt")
    asset.subtitle_path = workdir / "subtitle.srt"
    return segs, True


def ensure_subtitle_secondary(asset: VideoAsset, workdir: Path,
                              on_status: Optional[Callable[[str], None]] = None,
                              cancel: Optional[Callable[[], bool]] = None,
                              ) -> tuple[list[SubtitleSegment], Optional[str], bool]:
    """获取双语第二轨，返回 (segments, secondary_lang, complete)。

    complete=False 表示被取消（whisper translate 路径已增量落盘 subtitle.en.part.srt，可续传）。
    - 原音非英文(如中文)→英文：whisper task=translate（离线，可续传）。
    - 原音为英文→中文：翻译器(整批 API，不支持续传，取消则丢弃重来)。
    """
    import time
    workdir = Path(workdir)
    sec_path = workdir / "subtitle.en.srt"
    if sec_path.exists():
        segs = parse_srt(sec_path)
        if segs:
            sec_lang = "zh" if (asset.source_lang == "en") else "en"
            return segs, sec_lang, True

    src = asset.source_lang
    if src == "en":
        from v2md import translator
        if not translator.is_available():
            log.warning("原音为英文，欲生成中文双语轨但未配置 TRANSLATE_API_KEY，跳过。")
            return [], None, True
        if on_status:
            on_status(f"翻译英→中（{config.TRANSLATE_MODEL}）…")
        primary = parse_srt(asset.subtitle_path) if asset.has_subtitle else []
        zh_texts = translator.translate_to_zh(primary)
        segs = [SubtitleSegment(start_s=s.start_s, end_s=s.end_s, text=zh_texts[i] or s.text)
                for i, s in enumerate(primary)]
        if not segs:
            return [], None, True
        write_srt(segs, sec_path)
        return segs, "zh", True
    else:
        part = workdir / "subtitle.en.part.srt"
        partial = parse_srt(part) if part.exists() else []
        start_t = max((s.end_s for s in partial), default=0.0)
        base = len(partial)
        if on_status and start_t > 0:
            on_status(f"续传英文轨：已 {base} 段到 {fmt_time(start_t)}…")
        t0 = time.monotonic()
        on_seg = _make_progress_cb(on_status, asset.duration_s, base, t0)
        def _append(seg, count):
            _append_srt(part, seg, base + count)
            on_seg(seg, count)
        new, _, cancelled = _run_whisper(
            asset.video_path, task="translate", start_t=start_t, on_seg=_append, cancel=cancel)
        segs = partial + new
        if cancelled:
            return segs, "en", False
        if not segs:
            return [], None, True
        part.rename(sec_path)
        return segs, "en", True


# ── CLI ───────────────────────────────────────────────
def _cli() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m v2md.subtitle <video_path> [subtitle.srt]")
        print("  - 给 video_path：若无 subtitle 参数则用 Whisper 转写")
        print("  - 给 subtitle.srt：仅解析，不转写")
        sys.exit(1)
    vp = Path(sys.argv[1])
    # CLI 演示：用视频所在目录作为 workdir
    workdir = vp.parent
    asset = VideoAsset(video_path=vp, subtitle_path=None, source_url="", title=vp.stem)
    if len(sys.argv) > 2:
        asset.subtitle_path = Path(sys.argv[2])
    segs, complete = ensure_subtitle(asset, workdir=workdir)
    print(f"共 {len(segs)} 段字幕（原音语言: {asset.source_lang}，complete={complete}）：")
    for s in segs[:10]:
        print(f"  [{s.start_s:7.2f}-{s.end_s:7.2f}] {s.text}")


if __name__ == "__main__":
    _cli()
