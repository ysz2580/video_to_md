"""模块1：视频下载器。

输入可以是：
- URL（B 站 / 抖音 / YouTube 等 yt-dlp 支持的站点）→ yt-dlp 下载，尽量顺带抓字幕。
- 本地文件路径 → 直接复制到项目 workdir，跳过 yt-dlp（无字幕则由模块2 兜底）。
输出 VideoAsset。

依赖：yt-dlp（作为库）。ffmpeg 由 yt-dlp 自动管理 / imageio-ffmpeg 自带。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import config
from v2md.models import VideoAsset

import logging
log = logging.getLogger(__name__)

# yt-dlp 是可选导入：仅本模块需要，便于在没装齐依赖时仍能 import 别的模块
from yt_dlp import YoutubeDL


def _file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    """计算文件内容 sha256（本地文件去重复用）。"""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def _find_ffmpeg_for_yt_dlp() -> Optional[str]:
    """定位 ffmpeg 给 yt-dlp 用（PATH 优先，否则 imageio-ffmpeg 自带二进制）。"""
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _probe_duration(video: Path) -> float:
    """用 ffmpeg 从 stderr 的 Duration: 行解析时长（本地文件用，yt-dlp 无此信息）。"""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        ff = get_ffmpeg_exe()
    except Exception:
        ff = shutil.which("ffmpeg")
    if not ff:
        return 0.0
    try:
        p = subprocess.run([ff, "-hide_banner", "-i", str(video)],
                           capture_output=True, text=True, encoding="utf-8", errors="ignore")
    except Exception:
        return 0.0
    m = re.search(r"Duration:\s*([0-9:.]+)", p.stderr or "")
    if not m:
        return 0.0
    h, mi, s = m.group(1).split(":")
    return int(h) * 3600 + int(mi) * 60 + float(s)


def _is_local_file(src: str) -> Optional[Path]:
    """输入若指向已存在的本地文件，返回其 Path，否则 None。"""
    if not src:
        return None
    p = Path(src.strip().strip('"').strip("'"))
    return p if p.is_file() else None


def _extract_bvid(url: str) -> Optional[str]:
    """从 B 站 URL 里抠出 BV 号（用于 .md 跳转链接）。"""
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    return m.group(1) if m else None


def _format_for(quality: str) -> str:
    """按清晰度质量生成 yt-dlp format 串。best=不限制。"""
    q = (quality or "720").strip().lower()
    if q in ("best", "max", "源"):
        return "best"
    h = q.lstrip("p")  # '720' / '480'
    if not h.isdigit():
        h = "720"
    return (f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
            f"/best[height<={h}][ext=mp4]/best")


def download(url: str, workdir: Path, cookies_path: Optional[str] = None,
             progress_hook=None, quality: str = "720") -> VideoAsset:
    """下载视频并尝试抓字幕，全部写入 workdir 目录。

    输入可以是 URL（B站/抖音/YouTube 等 yt-dlp 支持的站点）或本地文件路径。
    本地文件：直接复制到 workdir/video.mp4，跳过 yt-dlp，无字幕（由模块2 兜底）。
    quality：720/480/1080/best（默认 720，控体积）。
    产物：workdir/video.mp4（可能带 workdir/*.srt 字幕）。
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    # ── 本地文件分支：复制进项目目录 ──
    local = _is_local_file(url)
    if local:
        target = workdir / "video.mp4"
        if target.resolve() != local.resolve():
            shutil.copy2(local, target)
        log.info("本地文件: %s -> %s", local, target)
        return VideoAsset(
            video_path=target,
            subtitle_path=None,
            source_url=str(local.resolve()),
            bvid=None,
            title=local.stem,
            duration_s=_probe_duration(target),
            content_hash=_file_sha256(target),
        )

    # ── URL 分支：yt-dlp ──
    tmp_tmpl = str(workdir / "dl.%(ext)s")

    ydl_opts = {
        "outtmpl": tmp_tmpl,
        "format": _format_for(quality),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        # 字幕：尽量写自动字幕 + 普通字幕，优先中文
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "zh-Hans-HQ", "zh-HQ"],
        "subtitlesformat": "srt",
        "skip_download": False,
        # 让 yt-dlp 用本项目的 ffmpeg（PATH 或 imageio-ffmpeg 自带二进制）
        "ffmpeg_location": _find_ffmpeg_for_yt_dlp(),
        "paths": {"home": str(workdir)},  # 字幕等附属文件落地到 workdir
    }
    cookies = cookies_path or config.COOKIES_PATH
    if cookies:
        ydl_opts["cookiefile"] = cookies
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # 规范化视频文件名 -> workdir/video.mp4
    video_path = Path(ydl.prepare_filename(info))
    if not video_path.exists():
        # 合并后可能改名
        cand = list(workdir.glob("dl.*.mp4")) + list(workdir.glob("dl.mp4"))
        if cand:
            video_path = cand[0]
    target_video = workdir / "video.mp4"
    if video_path.exists() and video_path != target_video:
        video_path = video_path.rename(target_video)
    video_path = target_video

    # 字幕文件：yt-dlp 落地为 dl.<lang>.srt / dl.<lang>.vtt，规范成 subtitle.srt
    sub_path: Optional[Path] = None
    cand_subs = sorted(workdir.glob("dl.*.srt")) + sorted(workdir.glob("dl.*.vtt"))
    if cand_subs:
        src = cand_subs[0]
        target = workdir / "subtitle.srt"
        if src.suffix.lower() == ".vtt":
            _vtt_to_srt(src, target)
            src.unlink(missing_ok=True)
        elif src != target:
            src = src.rename(target)
        sub_path = target
    # 清理残留的 dl.* 临时文件
    for f in workdir.glob("dl.*"):
        if f != video_path and (sub_path is None or f != sub_path):
            try:
                f.unlink()
            except OSError:
                pass

    return VideoAsset(
        video_path=video_path,
        subtitle_path=sub_path,
        source_url=url,
        bvid=_extract_bvid(url),
        title=info.get("title", "") if isinstance(info, dict) else "",
        duration_s=float(info.get("duration", 0.0) or 0.0) if isinstance(info, dict) else 0.0,
    )


def _vtt_to_srt(vtt_path: Path, srt_path: Path) -> None:
    """简易 VTT -> SRT 转换（足够后续解析）。"""
    import re

    def _ts(m: str) -> str:
        # 00:00:01.234 -> 00:00:01,234
        return m.replace(".", ",").zfill(11)[:11]

    text = vtt_path.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"^WEBVTT.*?\n", "", text, flags=re.S)
    lines = text.splitlines()
    out, idx = [], 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            idx += 1
            a, b = line.split("-->")
            out.append(str(idx))
            out.append(f"{_ts(a.strip())} --> {_ts(b.strip())}")
            j = i + 1
            while j < len(lines) and lines[j].strip():
                out.append(lines[j].strip())
                j += 1
            out.append("")
            i = j
        else:
            i += 1
    srt_path.write_text("\n".join(out), encoding=config.SRT_ENCODING)


# ── CLI ───────────────────────────────────────────────
def _cli() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m v2md.downloader <视频URL> [cookies.txt]")
        sys.exit(1)
    url = sys.argv[1]
    cookies = sys.argv[2] if len(sys.argv) > 2 else None
    # CLI 演示：在 data/projects/_cli 下建临时 workdir
    workdir = config.PROJECTS_DIR / "_cli"
    workdir.mkdir(parents=True, exist_ok=True)

    def hook(d):
        if d.get("status") == "downloading":
            pct = d.get("_percent_str", "").strip()
            print(f"\r下载中 {pct}", end="", flush=True)
        elif d.get("status") == "finished":
            print("\n下载完成，正在合并/处理...")

    asset = download(url, workdir=workdir, cookies_path=cookies, progress_hook=hook)
    print(f"\n标题: {asset.title}")
    print(f"视频: {asset.video_path}")
    print(f"字幕: {asset.subtitle_path}")
    print(f"BV号: {asset.bvid}")
    print(f"时长: {asset.duration_s:.1f}s")


if __name__ == "__main__":
    _cli()
