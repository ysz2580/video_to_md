"""模块3：关键帧抽取。

策略（场景检测 + pHash 去重）：
  1) ffmpeg 的 select=gt(scene,T) 滤镜做场景变化检测，showinfo 滤镜
     在 stderr 打印每个被选中帧的 pts_time（精确时间戳）。
  2) imagehash 对每张候选帧算 pHash，与上一保留帧汉明距离 < 阈值则丢弃，
     实现相邻相似帧去重。
  3) 输出 list[Frame]（按时间升序）。

不依赖 opencv/PySceneDetect，在新 Python 上也能跑（只需 ffmpeg + Pillow）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import config
from v2md.models import VideoAsset, Frame, new_project_id

import logging
log = logging.getLogger(__name__)

_SHOWINFO_RE = re.compile(r"pts_time:\s*([0-9.]+)")


def _find_ffmpeg() -> str:
    """定位 ffmpeg，优先级：PATH > imageio-ffmpeg 自带二进制 > yt-dlp 缓存。"""
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    # imageio-ffmpeg 自带静态二进制（pip 装的，免系统安装）
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    # 兜底：yt-dlp 自动下载的 ffmpeg（Windows 常见位置）
    home = Path.home()
    for cand in (
        home / "AppData/Roaming/yt-dlp/ffmpeg/ffmpeg.exe",
        home / "AppData/Roaming/yt-dlp/ffmpeg.exe",
        home / "ffmpeg/ffmpeg.exe",
        Path("ffmpeg.exe"),
    ):
        if cand.exists():
            return str(cand)
    raise FileNotFoundError(
        "未找到 ffmpeg。已尝试 PATH / imageio-ffmpeg / yt-dlp 缓存。"
        "请 winget install Gyan.FFmpeg，或本项目已含 imageio-ffmpeg（uv sync）。"
    )


def _time_name(t: float) -> str:
    """按时间位置命名：frame_{ms:09d}.jpg（ms=round(t*1000)，定长可字典序排序）。"""
    return f"frame_{int(round(t * 1000)):09d}.jpg"


def _rename_to_time(files: list[Path], times: list[float],
                    out_dir: Path) -> list[Path]:
    """把 ffmpeg 临时序号文件改名为 frame_{ms}.jpg（按各自时间），冲突加 _2/_3 后缀。

    自动帧与手动帧共用此命名，中间插入天然落在时间邻居之间（不再用 1..n 序号）。
    """
    used = {p.name for p in out_dir.glob("frame_*.jpg")}
    renamed: list[Path] = []
    for fp, t in zip(files, times):
        base = _time_name(t)
        name = base
        n = 2
        while name in used or (out_dir / name).exists():
            name = f"frame_{int(round(t * 1000)):09d}_{n}.jpg"
            n += 1
        used.add(name)
        target = out_dir / name
        if fp.resolve() != target.resolve():
            fp.rename(target)
        renamed.append(target)
    return renamed


def _run_scene_extract(video: Path, out_dir: Path, threshold: float,
                       width: int) -> list[Path]:
    """跑 ffmpeg 场景检测，输出候选帧图片，返回按序生成的文件列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    pid = new_project_id()
    pattern = str(out_dir / f"{pid}_%06d.jpg")

    # select 选场景变化帧；scale 可选缩放；showinfo 输出 pts_time
    parts = [f"select='gt(scene,{threshold})'"]
    if width and width > 0:
        parts.append(f"scale='min(iw,{width})':-2")
    parts.append("showinfo")
    vf = ",".join(parts)

    cmd = [
        _find_ffmpeg(), "-nostats", "-hide_banner", "-i", str(video),
        "-filter:v", vf, "-vsync", "vfr",
        "-frame_pts", "0",          # 顺序命名（随后 _rename_to_time 改为按时间）
        "-q:v", "2",                # JPEG 质量
        pattern,
    ]
    # ffmpeg 正常结束码 0；若无帧被选中会返回非 0，需容错
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="ignore")
    stderr = proc.stderr or ""
    # 解析 pts_time 列表（顺序与生成的文件一一对应）
    times = [float(m) for m in _SHOWINFO_RE.findall(stderr)]

    files = sorted(out_dir.glob(f"{pid}_*.jpg"))
    files = _rename_to_time(files, times, out_dir)
    return files, times


def _phash(path: Path) -> str:
    from PIL import Image
    import imagehash
    with Image.open(path) as im:
        return str(imagehash.phash(im))


def _hamming(a: str, b: str) -> int:
    """两个十六进制 pHash 字符串的汉明距离。"""
    if len(a) != len(b):
        return 64  # 长度不等视为差异大
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def extract_keyframes(asset: VideoAsset, workdir: Path,
                      scene_threshold: Optional[float] = None,
                      dedup_hamming: Optional[int] = None) -> list[Frame]:
    """抽取关键帧，图片写入 workdir/frames/，并生成 workdir/frames.txt 时间清单。

    策略：场景检测帧 + 均匀采样帧 合并；去重时只在「时间相近 且 视觉相似」时
    丢弃——这样动态视频保留内容变化点，静态教学视频靠均匀采样保证时间覆盖。

    Args:
        asset: 模块1产物
        workdir: 该视频的专属目录；帧图片落地到 workdir/frames/
        scene_threshold: ffmpeg scene 阈值，None 取 config.SCENE_THRESHOLD
        dedup_hamming: 去重汉明阈值，None 取 config.DEDUP_HAMMING
    Returns:
        list[Frame]，按时间升序
    """
    if scene_threshold is None:
        scene_threshold = config.SCENE_THRESHOLD
    if dedup_hamming is None:
        dedup_hamming = config.DEDUP_HAMMING

    workdir = Path(workdir)
    frames_dir = workdir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    duration = asset.duration_s or _duration(asset.video_path)

    # 1) 场景检测候选
    sc_files, sc_times = _run_scene_extract(
        asset.video_path, frames_dir, scene_threshold, config.FRAME_WIDTH,
    )
    # 2) 均匀采样候选（每 uniform_interval_s 秒一帧，保证时间覆盖）
    un_files, un_times = _run_uniform_extract(
        asset.video_path, frames_dir, config.UNIFORM_INTERVAL_S, config.FRAME_WIDTH,
    )

    # 3) 合并去重
    candidates: list[tuple[float, Path]] = []
    for fp, t in zip(sc_files, sc_times):
        candidates.append((t, fp))
    for fp, t in zip(un_files, un_times):
        candidates.append((t, fp))
    # 按时间排序；时间戳缺失的(0.0)排最前（理论上不会出现，showinfo 都会给出）
    candidates.sort(key=lambda x: x[0])

    frames: list[Frame] = []
    last_t = -1e9
    last_hash: Optional[str] = None
    dropped = 0
    for t, fp in candidates:
        h = _phash(fp)
        # 仅当与上一保留帧「时间相近 且 视觉相似」才丢弃——避免合并远时间点的相似帧
        close_in_time = (t - last_t) < config.MIN_GAP_S
        close_in_visual = last_hash is not None and _hamming(h, last_hash) < dedup_hamming
        if close_in_time and close_in_visual:
            dropped += 1
            # 丢弃的候选文件直接删除，避免 frames/ 里残留无用图片
            try:
                fp.unlink()
            except OSError:
                pass
            continue
        frames.append(Frame(timestamp_s=t, image_path=fp, phash=h))
        last_t = t
        last_hash = h

    frames.sort(key=lambda f: f.timestamp_s)
    # 写帧时间清单 frames.txt（类比 SRT 记录每帧时间）
    _write_frames_manifest(frames, workdir)
    log.info("场景 %d + 均匀 %d = %d 候选，去重丢 %d，保留 %d 帧（时长 %.0fs）",
             len(sc_files), len(un_files), len(candidates), dropped, len(frames), duration)
    return frames


def _write_frames_manifest(frames: list[Frame], workdir: Path) -> None:
    """写 frames.txt：每帧一行「秒数 <TAB> 图片相对路径」，类比 SRT 的时间记录。"""
    lines = [
        "# 关键帧时间清单（每行：秒数<TAB>frames/图片名）",
        f"# count={len(frames)}",
    ]
    for f in frames:
        rel = f"frames/{Path(f.image_path).name}"
        lines.append(f"{f.timestamp_s:.3f}\t{rel}")
    (workdir / "frames.txt").write_text("\n".join(lines), encoding="utf-8")


def _run_uniform_extract(video: Path, out_dir: Path, interval_s: float,
                         width: int) -> tuple[list[Path], list[float]]:
    """每 interval_s 秒抽一帧（fps=1/interval），返回文件列表与 pts_time 列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    pid = new_project_id()
    pattern = str(out_dir / f"{pid}_u%06d.jpg")
    parts = [f"fps=1/{max(1, int(round(interval_s)))}"]
    if width and width > 0:
        parts.append(f"scale='min(iw,{width})':-2")
    parts.append("showinfo")
    vf = ",".join(parts)
    cmd = [
        _find_ffmpeg(), "-nostats", "-hide_banner", "-i", str(video),
        "-vf", vf, "-vsync", "vfr", "-q:v", "2", pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="ignore")
    times = [float(m) for m in _SHOWINFO_RE.findall(proc.stderr or "")]
    files = sorted(out_dir.glob(f"{pid}_u*.jpg"))
    files = _rename_to_time(files, times, out_dir)
    return files, times


def _duration(video: Path) -> float:
    """用 ffmpeg（非 ffprobe）从 stderr 的 Duration: 行解析时长。"""
    cmd = [_find_ffmpeg(), "-hide_banner", "-i", str(video)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="ignore")
    except Exception:
        return 0.0
    m = re.search(r"Duration:\s*([0-9:.]+)", proc.stderr or "")
    if not m:
        return 0.0
    h, mi, s = m.group(1).split(":")
    return int(h) * 3600 + int(mi) * 60 + float(s)


# ── CLI ───────────────────────────────────────────────
def _cli() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m v2md.frames <video_path> [scene_threshold]")
        sys.exit(1)
    vp = Path(sys.argv[1])
    th = float(sys.argv[2]) if len(sys.argv) > 2 else None
    asset = VideoAsset(video_path=vp, subtitle_path=None, source_url="", title=vp.stem)
    frames = extract_keyframes(asset, workdir=vp.parent, scene_threshold=th)
    for f in frames:
        print(f"  [{f.timestamp_s:7.2f}s] {f.image_path.name}  phash={f.phash}")


if __name__ == "__main__":
    _cli()
