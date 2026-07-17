"""项目配置：路径、阈值、模型大小等。所有可调参数集中在此。"""
from __future__ import annotations

import sys
from pathlib import Path

# ── 路径 ───────────────────────────────────────────────
# 每个视频一个独立文件夹：data/projects/{id}/
#   video.mp4  subtitle.srt  frames.txt  frames/*.jpg  project.json  project.md
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
PROJECTS_DIR = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def project_workdir(pid: str) -> Path:
    """某个视频项目的专属目录。"""
    return PROJECTS_DIR / pid


# ── Windows 控制台中文：强制 stdout/stderr 为 UTF-8，避免 GBK 乱码/崩溃 ──
if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ── 统一日志（替代各模块散落的 print）── stdout 已重配为 UTF-8
import logging as _logging
_h = _logging.StreamHandler(sys.stdout)
_h.setFormatter(_logging.Formatter("%(asctime)s [%(name)s] %(message)s", "%H:%M:%S"))
_logging.basicConfig(level=_logging.INFO, handlers=[_h], force=True)

# ── 下载 ───────────────────────────────────────────────
# 限制最大分辨率，控制体积（教学场景够用）
DOWNLOAD_FORMAT = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best"
# 可选 cookies 文件路径（B 站需要登录的视频在此放 cookies.txt）
COOKIES_PATH: str | None = None

# ── 字幕 ───────────────────────────────────────────────
WHISPER_MODEL = "small"        # tiny/base/small/medium/large3，CPU 上 small 是质量/速度折中
WHISPER_LANGUAGE = None       # None=自动检测原音语言（中/英皆可）；填 "zh"/"en" 则强制
WHISPER_DEVICE = "cpu"        # 有 GPU 可改 "cuda"
WHISPER_COMPUTE_TYPE = "int8" # CPU 用 int8 最快；cuda 可用 "float16"
# 字幕 SRT 写成 UTF-8 with BOM，兼容 Windows 记事本/播放器中文显示
SRT_ENCODING = "utf-8-sig"

# ── 翻译器（英文原音→中文双语用，OpenAI 兼容 chat API）──
# 仅当原音为英文(或非中文)且要双语时启用；中文原音的中→英用 whisper translate，不需要这里。
# 国内可用 DeepSeek：BASE_URL="https://api.deepseek.com/v1", MODEL="deepseek-chat"
# 不填 KEY 则英文原音的双语自动跳过（仅生成英文主轨）。
TRANSLATE_BASE_URL = "https://api.deepseek.com/v1"
TRANSLATE_API_KEY: str | None = None
TRANSLATE_MODEL = "deepseek-chat"
# HuggingFace 模型下载端点：国内网络连不上 huggingface.co 时改用镜像。
# 设为 None 则用官方端点。必须在 import faster_whisper 之前生效（见 subtitle.py）。
HF_ENDPOINT = "https://hf-mirror.com"
# 新版 huggingface_hub 对部分仓库走 Xet 存储(cas-server.xethub.hf.co)，
# 该域名不走镜像且常 401；禁用它，强制走经典 HTTP(尊重 HF_ENDPOINT)。
HF_HUB_DISABLE_XET = True

# ── 关键帧 ──────────────────────────────────────────────
# ffmpeg select=gt(scene,T) 的阈值：越高越严格（0.0~1.0，典型 0.1~0.5）
# 教学类静态画面建议 0.1~0.2；影视类可 0.3~0.5
SCENE_THRESHOLD = 0.2
# 均匀采样间隔（秒）：每 N 秒强制抽一帧，保证时间覆盖（静态视频靠它兜底）
UNIFORM_INTERVAL_S = 20
# 去重最小时间间隔（秒）：仅当与上一保留帧「时间相近(<此值) 且 视觉相似」才丢弃，
# 避免把远时间点的相似帧错误合并
MIN_GAP_S = 3
# pHash 去重汉明距离阈值，小于此值视为视觉相似
DEDUP_HAMMING = 8
# 抽帧后的图片宽度（等比缩放，0=原尺寸）
FRAME_WIDTH = 960

# ── Markdown / Web ──────────────────────────────────────
WEB_HOST = "127.0.0.1"
WEB_PORT = 8000


# ── 用户设置（data/settings.json 覆盖上面的默认值，供 Web 设置面板写）──
def _apply_user_settings():
    import json
    p = DATA_DIR / "settings.json"
    if not p.exists():
        return
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    g = globals()
    if d.get("translate_api_key") is not None:
        g["TRANSLATE_API_KEY"] = d["translate_api_key"]
    if d.get("translate_base_url"):
        g["TRANSLATE_BASE_URL"] = d["translate_base_url"]
    if d.get("translate_model"):
        g["TRANSLATE_MODEL"] = d["translate_model"]
    if d.get("whisper_model"):
        g["WHISPER_MODEL"] = d["whisper_model"]
    if d.get("cookies_path"):
        g["COOKIES_PATH"] = d["cookies_path"]


_apply_user_settings()
