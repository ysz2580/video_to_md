# 视频 → Markdown（图文）教学项目

输入一个视频 URL（如 B 站链接），自动 **下载视频 → 获取/生成带时间戳字幕 →
抽取内容变化的关键帧 → 在本地 Web 界面展示图文 → 生成可跳转视频的 Markdown**。

四个模块**解耦**，各自可独立运行/测试，由 pipeline 串联。

## 架构与模块

```
URL ─▶ downloader ─▶ VideoAsset ─▶ subtitle ─▶ [SubtitleSegment]
                                          │
                          VideoAsset ─▶ frames ─▶ [Frame]
                                          │
            asset + subtitles + frames ─▶ markdown ─▶ .md
                                          │
                                     pipeline 编排 + Web 展示
```

| 模块 | 文件 | 职责 | 输入 → 输出 |
|---|---|---|---|
| 1 下载 | [v2md/downloader.py](v2md/downloader.py) | URL（B站/抖音/YouTube，yt-dlp）或本地文件路径（复制进 data）+ 抓字幕 | URL/路径 → `VideoAsset` |
| 2 字幕 | [v2md/subtitle.py](v2md/subtitle.py) | 解析自带字幕，缺失则 faster-whisper 转写 | `VideoAsset` → `[SubtitleSegment]` |
| 3 关键帧 | [v2md/frames.py](v2md/frames.py) | ffmpeg 场景检测 + pHash 去重 + frames.txt 时间清单 | `VideoAsset` → `[Frame]` |
| 4a Markdown | [v2md/markdown.py](v2md/markdown.py) | 图文+时间戳跳转链接组装 | `Project` → `.md` |
| 编排 | [v2md/pipeline.py](v2md/pipeline.py) | 串联四模块 + job 状态 | URL → `Project` |
| Web | [web/app.py](web/app.py) + [index.html](web/templates/index.html) | 浏览器展示 | HTTP |

模块之间**只通过 dataclass 传递**（见 [v2md/models.py](v2md/models.py)）：
`VideoAsset` / `SubtitleSegment` / `Frame` / `Project`，互不 import 业务逻辑。

## 关键技术决策（教学要点）

- **为什么锁 Python 3.12？** 你的系统是 Python 3.14.6（非常新）。`faster-whisper`
  依赖的 `ctranslate2`、`opencv/numpy` 在 3.14 上尚无预编译 wheel，直接 `pip install`
  会失败。本项目用 [uv](https://github.com/astral-sh/uv) 自动拉取 3.12 建独立虚拟环境，
  wheel 齐全、互不污染系统 Python。
- **关键帧为什么用 ffmpeg `scene` 滤镜而不是 PySceneDetect？** 同属"场景检测"算法，
  但 ffmpeg 内置 `select=gt(scene,T)` 不需要 opencv/numpy，依赖更轻，在新 Python 上也稳。
  `showinfo` 滤镜在 stderr 打印每帧 `pts_time`，从而拿到精确时间戳。
- **pHash 去重**：相邻场景帧若视觉上几乎相同（如缓慢移动），用感知哈希汉明距离过滤，
  避免一堆相似图。见 `v2md/frames.py::_phash/_hamming`。
- **时间戳跳转**：生成的 .md 里每帧标题是 `[⏱ 00:42](https://www.bilibili.com/video/{bvid}?t=42)`，
  B 站支持 `t=` 参数直接跳转到对应秒数；非 B 站源则跳本地 Web 播放器 `/?t=42`。

## 快速开始

### 1. 环境引导（一次性）
```powershell
# 在项目根目录
.\scripts\bootstrap.ps1
```
脚本会：装 uv → 拉 Python 3.12 → 建虚拟环境装依赖 → 检查 ffmpeg。
若系统没有 ffmpeg，可 `winget install Gyan.FFmpeg`，或让 yt-dlp 首次下载时自动取。

### 2. 启动（一键）
首次环境就绪后，**双击 `start.bat`** 即可：自动 `uv sync` 确保依赖 → 启动 Web 服务 → 打开浏览器到 `http://127.0.0.1:8000`。关闭弹出的窗口或 Ctrl+C 即停止服务。

> 等价命令行：`pwsh -File start.ps1` 或 `uv run uvicorn web.app:app --reload`
>
> 停止服务：双击 `stop.bat`（杀掉占用 8000 端口的服务进程）。

### 3. 端到端跑通（CLI）
```powershell
# URL：B站 / 抖音 / YouTube 等（yt-dlp 支持的站点）
uv run python -m v2md.pipeline "https://www.bilibili.com/video/BVxxxxxxxx" --bilingual
# 本地文件：直接给路径，会复制进 data/projects/{id}/video.mp4
uv run python -m v2md.pipeline "D:\videos\demo.mp4"
```
`--bilingual` 额外用 whisper `task=translate` 生成英文字幕轨（中-英双语，写入 `subtitle.en.srt`）。给老项目补双语可用 `uv run python scripts/rebuild.py <项目目录> --en`。
产物在该视频的专属目录 `data/projects/{id}/`：video.mp4、subtitle.srt、subtitle.en.srt(双语时)、frames/、frames.txt、project.json、project.md。

### 各模块单独测试
```powershell
uv run python -m v2md.downloader  "<url>"            # 仅下载
uv run python -m v2md.subtitle   <video.mp4> [sub.srt]  # 解析/转写字幕
uv run python -m v2md.frames     <video.mp4> [0.4]      # 抽关键帧，第二参数=scene阈值
uv run python -m v2md.markdown   <project.json>         # 重新组装 md
```

## 配置

所有可调参数在 [config.py](config.py)：
- `DOWNLOAD_FORMAT`：限制最大 720p、mp4 优先，控体积。
- `COOKIES_PATH`：B 站需要登录的视频，放 `cookies.txt`（yt-dlp 格式）并指向它；
  导出方法见 yt-dlp 文档的 "cookies from browser"。
- `WHISPER_MODEL`：CPU 上 `small` 是质量/速度折中；要更快用 `base`/`tiny`，要更好用 `medium`。
- `SCENE_THRESHOLD`：ffmpeg scene 阈值（0.0~1.0），越高越严格，典型 0.3~0.5。
- `DEDUP_HAMMING`：pHash 汉明距离阈值，小于此值视为重复帧丢弃。

## 目录产物

**每个视频一个独立文件夹**，互不混放：

```
data/projects/{id}/
├── video.mp4        下载的视频
├── subtitle.srt     字幕（UTF-8-BOM，兼容记事本中文）
├── frames.txt       关键帧时间清单：每行「秒数<TAB>frames/图片名」
├── frames/          关键帧 .jpg
├── project.json     项目元数据（路径相对本目录，整个文件夹可整体搬走）
└── project.md       生成的图文 Markdown
```

`project.json` 存的是相对路径（如 `video.mp4`、`frames/xxx.jpg`），所以单个项目文件夹自包含、可移植。

## 编码规范（中文 Windows 防乱码）

中文 Windows 的系统区域设置是 GBK(936)，**编码不是一层而是四层**，每层默认都可能是 GBK——
本项目历史上每层都踩过坑（✅ emoji 崩溃、.ps1 中文乱码、`Get-Content` 把 UTF-8 文件读成乱码……）。
治理思路：**一次性把每层都钉死成 UTF-8，而不是每次记得加 `-Encoding utf8`**。

| 层 | 现状 | 兜底位置 |
|---|---|---|
| Python stdout | UTF-8 | [config.py](config.py) 启动即 `sys.stdout.reconfigure("utf-8")` |
| Python 文件/流 I/O | UTF-8 | 启动脚本注入 `PYTHONUTF8=1`（PEP 540，所有 `open()` 默认 UTF-8） |
| PS 控制台 + 管道 | UTF-8 | [start.ps1](start.ps1)/[bootstrap.ps1](scripts/bootstrap.ps1)/[stop.ps1](stop.ps1) 设 `chcp 65001` + `$OutputEncoding=[Console]::OutputEncoding=UTF8` |
| PS cmdlet 读写 | UTF-8 | 同上，`$PSDefaultParameterValues['*:Encoding']='utf8'` 让 `Get-Content/Set-Content/Out-File` 默认 UTF-8 |
| .ps1 源文件解析 | UTF-8 **带 BOM** | 三个 .ps1 已加 BOM；[.editorconfig](.editorconfig) 规定 `*.ps1 charset = utf-8-bom`，编辑器后续保存自动带 BOM |

**规则（写新代码请遵守）**：
1. **Python 里读写文件一律显式 `encoding="utf-8"`**（即便有 `PYTHONUTF8` 兜底也别省，CLI 直跑时该兜底不在）。SRT 用 `utf-8-sig`（带 BOM，兼容记事本/播放器，见 `config.SRT_ENCODING`）。
2. **PowerShell 里读项目文本文件**：用 `Get-Content xxx -Encoding utf8`，或经 `start.ps1` 注入的默认值（直接 `Get-Content` 也行）。
3. **新增 .ps1 含中文**：保存为 UTF-8 **带 BOM**（VSCode 右下角切到 "UTF-8 with BOM"，或靠 .editorconfig 自动）。
4. **控制台手动跑 Python**：推荐 `uv run python -X utf8 -m v2md.pipeline ...`（`-X utf8` 等同 PYTHONUTF8，CLI 直跑时同样免乱码）。

> 这是“能跑但中文是花的”这类隐性 bug 的根治方案：不是某处打补丁，而是把默认值整体改成 UTF-8。

## 备注 / 取舍

- **3.14 坚持党**：若不愿装 3.12，frames 模块仍可用（ffmpeg+imagehash 在 3.14 也能跑），
  但 faster-whisper 字幕兜底大概率装不上——届时把 `subtitle._transcribe_with_whisper`
  换成云端语音识别 API（需密钥）。
- **国内网络下 Whisper 模型下载**：huggingface.co 常被墙，且新版 `huggingface_hub` 默认
  走 Xet 存储（`cas-server.xethub.hf.co`）会 401。`config.py` 已设 `HF_ENDPOINT=https://hf-mirror.com`
  和 `HF_HUB_DISABLE_XET=True`（在 `subtitle.py` import faster-whisper 前注入环境变量），
  模型首次从镜像下载，之后缓存于 `~/.cache/huggingface`。能直连 HF 的环境把这两项置 None/False 即可。
- **静态视频的帧数**：讲解类画面变化少，纯场景检测（`select=gt(scene,T)`）可能只抓到 1~3 帧。
  本项目用「场景检测 + 每 20s 均匀采样」合并，并改用「时间相近且视觉相似才去重」的策略，
  保证静态视频也有足够时间覆盖；动态视频则场景帧会保留。调 `config.SCENE_THRESHOLD` /
  `UNIFORM_INTERVAL_S` 即可改变密度。
- B 站 AI 字幕质量一般，能拿到人工字幕优先用；都没有再跑 Whisper。
- 长视频（>30 分钟）CPU 跑 Whisper 较慢，可在 `config` 改 `WHISPER_DEVICE="cuda"`。
