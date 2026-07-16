# IMPROVEMENT — 视频转 Markdown 项目演进日志

> 本文件记录项目现状与历次更新。**后续每次更新都按下方格式在「更新日志」末尾尾加一条**：
>
> ```
> ## YYYY-MM-DD #N — 标题
> - 场景：……（在什么情境下/做什么）
> - 问题：……（遇到了什么问题/不足）
> - 解决（优化）方法：……（怎么改的，涉及哪些文件）
> - 结果：……（改完后的效果/验证）
> ```

---

## 一、项目现状总览（截至 2026-07-15）

### 目标
智能视频转 Markdown（图文）教学项目，功能解耦为四模块，由 pipeline 串联：
**下载 → 字幕 → 关键帧 → Web 展示（+ 导出 .md）**。

### 输入支持
- URL：B 站 / 抖音 / YouTube 等 yt-dlp 支持的站点。
- 本地文件路径：复制进项目 `data/`。

### 目录结构
```
video_to_md/
├── start.bat / start.ps1     # 一键启动（自动 uv sync + uvicorn + 开浏览器）
├── stop.bat / stop.ps1       # 一键关闭（杀 8000 端口进程）
├── prototype.html            # 离线前端原型（可双击预览布局）
├── pyproject.toml            # 依赖（uv 管理）
├── .python-version           # 3.12
├── config.py                 # 集中配置：路径/阈值/Whisper/HF镜像/编码
├── README.md / IMPROVEMENT.md
├── v2md/
│   ├── models.py            # dataclass 契约：VideoAsset/SubtitleSegment/Frame/Project
│   ├── downloader.py        # 模块1：URL(yt-dlp)/本地文件 → VideoAsset
│   ├── subtitle.py          # 模块2：自带字幕/Whisper转写/Whisper translate双语
│   ├── frames.py            # 模块3：ffmpeg scene+均匀采样+pHash去重 + frames.txt
│   ├── markdown.py          # 模块4a：组装 .md + sections() 结构化文档
│   └── pipeline.py          # 编排 + 异步 job（Step 进度）
├── web/
│   ├── app.py               # FastAPI：process/jobs/projects/project/doc/media
│   └── templates/index.html  # 单页前端
├── scripts/
│   ├── bootstrap.ps1        # 装环境（uv+3.12+依赖+ffmpeg）
│   └── rebuild.py           # 重建某项目 json+md（可选 --en 补双语）
└── data/projects/{id}/      # 每视频独立目录
    ├── video.mp4  subtitle.srt  subtitle.en.srt(双语)
    ├── frames.txt  frames/*.jpg
    └── project.json  project.md
```

### 关键设计
- **解耦**：模块间只通过 dataclass（`VideoAsset`/`list[SubtitleSegment]`/`list[Frame]`/`Project`）传递，互不 import 业务逻辑；各模块可单独 CLI 跑。
- **每视频独立目录**：`data/projects/{id}/` 自包含（视频/字幕/帧/json/md）。
- **字幕**：优先 B 站自带，缺失则 Whisper 转写；双语用 Whisper `task=translate` 出英文轨。
- **关键帧**：ffmpeg `select=gt(scene,T)` 场景检测 + 每 20s 均匀采样合并 + pHash 去重（仅「时间相近且视觉相似」才丢），`frames.txt` 记时间清单。
- **md**：每区块含「上一帧到本帧」全部字幕（不丢口述信息）；时间戳默认跳本地 `http://127.0.0.1:8000/?p={id}&t={秒}`，每帧附 `🌐 B站` 网页版入口。
- **Web 前端**：视频左 / md 右，可拖拽调宽；历史项目下拉；显示/隐藏视频；字幕覆盖（中/英/双语分段选择）；倍速/音量/全屏；跟随视频高亮当前段。
- **启停**：`start.bat`（ASCII 横幅 + chcp65001 + 自动开浏览器）/ `stop.bat`（杀端口）。

### 怎么跑
1. 首次：`pwsh -File scripts/bootstrap.ps1`（你这台已装好可跳过）。
2. 启动：双击 `start.bat` → 浏览器开 `http://127.0.0.1:8000`。
3. 处理：输入框粘 URL（B站/抖音/YouTube）或本地路径 → 处理（可勾「双语」）；或从「历史项目」下拉选已处理的。
4. 关闭：双击 `stop.bat`。

---

## 二、更新日志

## 2026-07-15 #1 — 项目骨架与环境规避
- 场景：从零搭建四模块解耦的教学项目。
- 问题：本机 Python 3.14.6 太新，`PySceneDetect`(依赖 opencv/numpy) 与 `faster-whisper`(依赖 ctranslate2) 在 3.14 上无预编译 wheel；ffmpeg、yt-dlp 均未安装。
- 解决（优化）方法：用 `uv` 拉取 Python 3.12 建虚拟环境（`pyproject.toml` 锁 `requires-python>=3.12,<3.14`）；关键帧改用 ffmpeg `scene` 滤镜 + `imagehash`(pHash) 去重，绕开 opencv；yt-dlp 复用 `imageio-ffmpeg` 自带 ffmpeg 二进制（已显式列入依赖）。建 `config.py` 集中配置，`v2md/models.py` 用 dataclass 固定模块契约。
- 结果：四模块骨架 + CLI 入口 + 配置就绪；环境可复现安装。

## 2026-07-15 #2 — B站视频端到端跑通（踩坑修复）
- 场景：用 `BV1kx4y1x7bu`（VLLM 讲解，12 分钟）实测完整 pipeline。
- 问题：① yt-dlp 合并多格式时找不到 ffmpeg 报错；② 连不上 huggingface.co 下 Whisper 模型（WinError 10060）；③ 新版 `huggingface_hub` 走 Xet（cas-server.xethub.hf.co）返 401；④ 静态讲解视频场景检测只抽到 1 帧；⑤ pipeline 末尾 ✅ emoji 在 GBK 控制台 `UnicodeEncodeError` 崩溃。
- 解决（优化）方法：① [downloader.py](v2md/downloader.py) 加 `_find_ffmpeg_for_yt_dlp()` 复用 imageio-ffmpeg；②③ [config.py](config.py) 设 `HF_ENDPOINT=https://hf-mirror.com` + `HF_HUB_DISABLE_XET=True`，在 [subtitle.py](v2md/subtitle.py) import faster-whisper 前注入环境变量；④ [frames.py](v2md/frames.py) 改「场景检测 + 每 20s 均匀采样」合并 + 「时间相近且视觉相似才去重」+ 默认阈值 0.2；⑤ [config.py](config.py) 对 Windows 统一 `sys.stdout.reconfigure(utf-8)`、横幅改 ASCII。
- 结果：pipeline 跑通，exit 0；38 帧 / 254 字幕段；中文正常显示；Whisper 模型走镜像缓存。

## 2026-07-15 #3 — 数据按视频独立存储 + 帧时间清单 + 中文编码
- 场景：数据混在一起、图片无时间记录文件、中文显示异常。
- 问题：flat 的 videos/subtitles/frames/projects 目录混杂，难管理；关键帧只有 project.json 里有时间，没有像 SRT 那样的独立时间清单；SRT/控制台中文乱码。
- 解决（优化）方法：改为每视频独立 workdir `data/projects/{id}/`（video.mp4/subtitle.srt/frames.txt/frames/*.jpg/project.json/project.md）；[frames.py](v2md/frames.py) 写 `frames.txt`（每行 秒数↔图片名）；[config.py](config.py) 设 `SRT_ENCODING=utf-8-sig`（带 BOM，兼容记事本/播放器）；Windows 下 stdout/stderr 重配 UTF-8。
- 结果：每个视频自包含一文件夹；`frames.txt` 记录帧时间；SRT/控制台中文正常。

## 2026-07-15 #4 — md 不丢字幕 + 跳转默认本地 + 前端播放器
- 场景：md 每帧只显示一条字幕丢信息；时间戳跳转跳 B站；播放器功能少。
- 问题：① md 每区块只取该时刻一条字幕，帧间口述信息全丢；② 时间戳默认跳 B站而非本地；③ 播放器无倍速等常用功能。
- 解决（优化）方法：① [markdown.py](v2md/markdown.py) `_subtitles_in_window()`，每区块带「上一帧到本帧」全部字幕段（末帧取余下）；② 时间戳默认本地 `http://127.0.0.1:8000/?p={id}&t={秒}`，每帧附 `🌐 B站` 网页版入口；③ [index.html](web/templates/index.html) 自定义播放器（播放/暂停、进度、时长、倍速 0.5/1/1.25/1.5/2×、音量、全屏、空格快捷），支持 `?p=&t=` 自动加载并定位。
- 结果：md 含全部口述信息；跳转默认本地播放器；播放器功能完整。

## 2026-07-15 #5 — 前端原型图（HTML）
- 场景：需要前端原型图。
- 问题：原用 ASCII 原型，不直观。
- 解决（优化）方法：写 [prototype.html](prototype.html) 离线原型，忠实呈现布局（顶栏/进度/播放器/文档区），可交互（点帧跳转、倍速切换），引用真实关键帧做示例画面，加载失败回退占位块。
- 结果：双击即可在浏览器预览布局，无需服务/数据。

## 2026-07-15 #6 — md 在界面内查看 + 可调宽 + 历史项目
- 场景：前端只能下载 md 不能查看；关键帧网格作用不清；想视频左/md 右边看边学。
- 问题：① 无 md 预览，只有下载；② 关键帧网格与 md 内容重复、作用不明；③ 不能加载已处理好的本地项目；④ 「隐藏视频」后按钮随面板消失，没法点回。
- 解决（优化）方法：① 右栏渲染文档：[markdown.py](v2md/markdown.py) `sections()` + Web `/api/project/{pid}/doc` 返回结构化区段；② 关键帧网格折叠进文档（每段＝一帧＋其字幕，点击定位）；③ [app.py](web/app.py) `/api/projects` + 前端「历史项目」下拉；④ 切换按钮移到始终可见的文档工具栏；⑤ 加可拖拽分隔条调宽（22%–80%）；⑥ `跟随视频` 高亮并平滑跟随当前段。
- 结果：md 在界面右侧直接读、边看边学；可拖拽调宽；可加载历史项目；显示/隐藏视频双向可用。

## 2026-07-15 #7 — 一键启动/关闭脚本
- 场景：需要一键启停。
- 问题：只有 `bootstrap.ps1` 装环境，无启停；PowerShell 5.1 对无 BOM 的 UTF-8 `.ps1` 按 GBK 解析，中文字符串致语法错误。
- 解决（优化）方法：写 [start.bat](start.bat)/[start.ps1](start.ps1)（uv sync + uvicorn + 延迟开浏览器，环境缺失时自动先跑 bootstrap）；写 [stop.bat](stop.bat)/[stop.ps1](stop.ps1)（杀 8000 端口监听进程）；`.ps1` 全用 ASCII 字符串避免编码崩溃，`.bat` 加 `chcp 65001`。
- 结果：双击 `start.bat` 即用并自动开浏览器；`stop.bat` 干净关闭；无编码崩溃。

## 2026-07-15 #8 — 视频字幕覆盖 + 双语
- 场景：播放器无字幕；需双语。
- 问题：播放器无字幕覆盖层；无英文轨。
- 解决（优化）方法：后端 [subtitle.py](v2md/subtitle.py) 加 `_translate_with_whisper(task='translate')` 生成英文轨 + `Project.subtitles_en` + [pipeline.py](v2md/pipeline.py) `--bilingual` + `/doc` 返回 `subtitles_en`/`bilingual`；[scripts/rebuild.py](scripts/rebuild.py) 给老项目补双语；前端加字幕覆盖层，控制改两级——「字幕」总开关 + `[中][英][双语]` 分段选择（无英文轨时禁用，默认双语/中文）。
- 结果：播放器显示字幕，默认中英两行，可切中/英/双语；测试项目补 237 段英文轨，翻译正确（「今天我们来讲一下VLLM」→「Today, let's talk about VLLM.」）。

## 2026-07-15 #9 — 支持抖音 + 本地文件输入
- 场景：URL 下载是否只支持 B站；需支持抖音与本地文件。
- 问题：需确认抖音支持；本地文件输入需复制进项目 data。
- 解决（优化）方法：确认 yt-dlp（2026.07.04）带 `Douyin` extractor，抖音 URL 直接走 yt-dlp；[downloader.py](v2md/downloader.py) `download()` 开头加本地文件分支 `_is_local_file()` → `shutil.copy2` 进 workdir/video.mp4、跳过 yt-dlp、`subtitle=None`/`bvid=None`、用 ffmpeg 探测 `duration_s`；前端输入框提示支持本地路径。
- 结果：B站/抖音/YouTube URL + 本地文件路径都支持；本地文件实测复制成功、时长探测 727.5s。

---

<!-- 后续更新按上面格式在此处尾加 -->

## 2026-07-15 #20 — 修 bug：section 内图片排在早于它的字幕前（渲染顺序按时间）
- 场景：#19 修了 nearest 归配后，用户仍看到「图片时间戳 23 排在字幕 12/16/20 前面」——因为 section 模板固定「先图片后字幕」，而 nearest 给 section@23 的字幕 12/16/20 都早于 23，却被渲染在图片下方，整篇时间倒着走。
- 问题：[index.html](web/templates/index.html) renderDoc 与 [markdown.py](v2md/markdown.py) build() 都是 `sec-h + img + 全部subs` 结构，没区分字幕与帧的时间先后，导致早于帧的字幕被画在图片后面（视觉时间倒序）。nearest 归配本身正确（section@23 确实该收 12-40s 字幕），错在渲染顺序。
- 解决（优化）方法：section 内按时间分两段——**早于帧 t 的字幕渲染在图片上方，晚于等于帧的渲染在下方**，帧图片夹在自身时间位置，全篇时间单调递增。
  - 前端 renderDoc：`before = subs.filter(start<t)` 渲染在 `sec-h`+`img` 之前，`after = subs.filter(start>=t)` 渲染在之后；抽 `subLine(x)` 复用中/英行（含 contenteditable 编辑）。
  - md build()：同理，`before` 用 `_emit_sub` 发在 `## ⏱` 标题+`![image]` 之前，`after` 发在之后，`---` 分隔。
- 结果：实测（删 t=20 + 截图 t=23 复现用户场景）后——服务端 capture 重建的 project.md：`> [00:20] ... > [00:23] 它被飞温3个token` 在 `## ⏱ [00:23]` + `![frame@00:23]` **之前**；section@23 的 subs=[12.92,16.56,20.16,22.92,25.04,...]，其中 <23.385 的在图上、≥ 的在图下。前后端一致。演示已 reframe 恢复 38 帧。

## 2026-07-15 #19 — 修 bug：删帧后字幕与截图错位（改 nearest-frame 归配）
- 场景：在右侧 md 文档视图删一张截图后，字幕的位置和截图位置不再按时间戳对齐——早字幕挂到了晚截图下面。
- 问题：[markdown.py](v2md/markdown.py) `sections()` 用**向后窗**：section@T 的字幕 = `(上一帧T, 本帧T]`（"本帧收口上一段口述"）。删一帧后，被删帧那段时间窗的字幕**并入下一帧**，于是 3.2s 的字幕会挂在 60s 截图下（字幕时间远早于截图时间），视觉错位。后端 section 时间戳/字幕 start 本身都升序正确，错的是"字幕归到哪一帧"的语义。
- 解决（优化）方法：`sections()` 改为 **nearest-frame 归配**——每条字幕归到时间最近的帧（`min |sub.start_s - frame.timestamp_s|`），中/英各自分配。这样每条字幕都贴近其截图（误差 ≤ 半个帧间隔），删一帧后字幕自动重新分配到最近邻，不会出现"早字幕挂晚截图"。字幕按 start_s 升序（flat list 已升序，按序 append 即保序）。前后端一致（`build()` 也用 `sections()`）。
- 结果：实测删 t=80 后——section@60 收 31.2..79.56s 字幕（72-79s 因 80 帧没了、最近邻是 60 而非 100），section@100 从 81.28s 起，边界 ~80s（两帧中点）；"sub.start < 上一帧时间"的错位条数=0。reframe 重抽后演示项目恢复 38 帧(0,20,40,…)/254 中/237 英字幕。

## 2026-07-15 #18 — 内联字幕编辑 / 断点续传+取消+ETA / 首次模型下载提示
- 场景：真人使用三大痛点。① whisper 转写有错字（「飞温3个token」），非开发者没法改；② 长视频 CPU 转写十几分钟，无进度无取消，盲等；③ 首次下模型 ~470MB 卡几分钟像死机；④ 续传：处理到一半暂停后想从断点继续，不从头来。
- 问题：① 无字幕文本编辑入口，只能手改 srt 再 rebuild；② `ensure_subtitle` 一次性返回，无 pct/ETA/取消，`_run_whisper` 不支持按时间段转写；③ `get_whisper_model` 加载前不提示「下载模型」，且 huggingface_hub 即使已缓存仍联网查 revision（hf-mirror 504 超时 ~30s）；④ 取消后已转部分丢失（只在结尾写 srt），重投从头重转写。
- 解决（优化）方法：
  - **#1 内联字幕编辑**：[app.py](web/app.py) `PUT /api/project/{pid}/subtitle` body{track,start_s,text}——按 start_s ±0.2s 定位段、改 text、`write_srt` 回写、`Project.load`+`markdown.build`+`save_project_json` 重建 json/md。前端 [index.html](web/templates/index.html) 把字幕文本包成 `<span class="subtxt" contenteditable data-track data-start>`，失焦时比对 `dataset.orig` 未改不提交，PUT 后 `refreshDoc` 重渲染。
  - **#3 续传+取消+ETA**：① [subtitle.py](v2md/subtitle.py) `_run_whisper(start_t, on_seg, cancel)` 用 `clip_timestamps=f"{start_t}"` 只转 [start_t,末尾]；`_append_srt` 增量追加 `subtitle.part.srt`/`subtitle.en.part.srt`（取消时已转部分不丢）；`ensure_subtitle/ensure_subtitle_secondary` 返回 `(segs, complete)`：有 `*.part.srt` 则续传（`start_t=max(end_s)`），完成 rename 成 `subtitle.srt`/`subtitle.en.srt`；`cancel()` 返回 True 时段循环 break、不 rename、返回 complete=False。② ETA：`_make_progress_cb` 用 `time.monotonic` + `asset.duration_s` 算 `pct/已转/剩~ETA/已N段` 经 `on_status` 上报。③ [pipeline.py](v2md/pipeline.py) `_save_source` 写 `.source.json`(url/bvid/content_hash/title)；`find_inprogress(url)` 扫有 video.mp4 无 project.json 且 .source 匹配的 workdir；`run(url,cancel,on_progress)` 返回 `(project,complete)`：命中完成→复用、命中未完成→接管 workdir 续传、字幕 complete=False→`Step.PAUSED` 返回不存 json。`JobState.cancel_requested` + `request_cancel(job_id)` + `run_async_job` 把 `_cancel` 透传到 whisper 段循环。④ [app.py](web/app.py) `POST /api/jobs/{id}/cancel`。
  - **#4 模型下载提示+离线**：`get_whisper_model` 加载前若 `not _whisper_model_cached()` 则 `on_status` 发「首次下载 whisper 模型 small (~470MB)…」；已缓存则设 `HF_HUB_OFFLINE=1` 跳过联网查 revision。
  - **前端**：进度区加 `jobCancel`/`jobResume` 按钮；`pollJob` 1s 轮询，活跃显示取消、`paused` 显示续传；`jobResume` 重投 `curJobUrl`（后端 find_inprogress 续传）。
- 结果：① 内联编辑实测 httpx PUT 200 → `/doc` 与 `project.md` 均含 `EDITED_BY_TEST`，可恢复原文本；② 续传实测（cancel-after-5）：Run1 `complete=False` step=paused、part.srt 5 段 last_end=12.9；Run2 `命中未完成项目 46565ce97cfd，断点续传` `start=12.9` clip_timestamps 续转 3 段 → part 8 段 last_end=22.8（>12.9，未重做前段）；③ Web 取消实测：POST process(resume)→POST cancel→`step=paused` msg「已暂停，已转写 8 段（重投同 URL 可续传）」，part.srt 保留；④ ETA 消息「转写 X%（mm:ss/mm:ss）剩~yy 已N段」经 on_status 上报；⑤ 模型已缓存设 HF_HUB_OFFLINE=1，取消后无 30s 联网卡顿，秒级到段循环响应取消。前端 `jobCancel/jobResume/subtxt/contenteditable/cancel 端点` 元素就位。

## 2026-07-15 #17 — 前端删除某帧并同步本地（图片/json/md/frames.txt）
- 场景：前端查看 md 时，人工删掉某张没用的截图帧，需同步本地「数据库」（project.json + project.md + frames.txt + 图片文件），保持一致。
- 问题：原只有「截图插入」(POST /frame)，没有「删除帧」入口；删一帧后若只改前端不回写，刷新即复活，磁盘也残留图片。
- 解决（优化）方法：
  - 后端 [app.py](web/app.py) `DELETE /api/project/{pid}/frame?t=`：`Project.load` 重建 → 匹配 ±0.5s 内的帧（section.timestamp_s 来自帧时间戳，通常精确相等）→ 从 `proj.frames` 移除、`unlink` 图片文件 → 重排 → 重写 `frames.txt`、`markdown.build`、`pipeline.save_project_json`。删除该帧后，其时间窗的字幕并入下一段（sections() 自动重分区）。
  - 前端 [index.html](web/templates/index.html)：每个 `.sec` 头部加 `🗑` 按钮（`data-t`）；点击 confirm → `DELETE /api/project/{pid}/frame?t=` → `refreshDoc`（只重渲染文档不重载视频）→ 进度提示「已删除 …（剩 N 帧）」。按钮 `stopPropagation` 避免触发时间戳跳转。
- 结果：实测删前 sections=39、`frame_000005000.jpg` 在；DELETE `t=5` → `{removed:5.0, frames:38}`；删后图片文件已删、sections 回 38、前4帧时间 0,20,40,60（t=5 已移除）；project.json/project.md/frames.txt 同步重建。前端 `class="delfrm"` 按钮与 DELETE 调用就位。删除与插入对称：插一帧/删一帧都即时同步本地并刷新文档。

## 2026-07-15 #16 — 帧图片改为按时间位置命名（自动/手动一致，中间插入合群）
- 场景：原自动帧用顺序号 `{pid}_u000001.jpg`、手动帧用 `manual_0000005.jpg`——两种前缀、一个序号一个时间，手动截图夹在自动帧中间命名"不合群"；且每视频已有独立 frames/ 目录，pid 前缀防冲突已无必要。
- 问题：顺序号命名无法在中间插入新帧（序号断档/要重排），与时间位置脱节；自动/手动两套命名并存，浏览 frames/ 目录时割裂。
- 解决（优化）方法：
  - [frames.py](v2md/frames.py) 新增 `_time_name(t)=frame_{ms:09d}.jpg`（ms=round(t*1000)，定长 9 位可字典序排序）与 `_rename_to_time(files,times,out_dir)`：ffmpeg 先按临时序号写出 → 解析 showinfo 的 pts_time → 改名为 `frame_{ms}.jpg`（冲突加 `_2/_3` 后缀）。`_run_scene_extract`/`_run_uniform_extract` 都调用之，自动帧全部按时间命名。
  - [app.py](web/app.py) 截图端点同步改用 `_time_name`：同时刻(±0.3s)已有帧→直接覆盖其图片文件；否则新建 `frame_{ms}.jpg`。手动帧与自动帧命名完全一致。
  - [scripts/rebuild.py](scripts/rebuild.py) 加 `--reframe`：删旧 frames/ 重新抽帧，供老项目从 `{pid}_u*.jpg`/`manual_*.jpg` 迁移到 `frame_{ms}.jpg`。
- 结果：① 迁移实测：老项目 `a3c7c1030a6c_u000001.jpg` → `--reframe` 后变 `frame_000000000.jpg`(t=0)/`frame_000020000.jpg`(t=20)/…，frames.txt 同步更新；② 手动截图实测：POST t=5 生成 `frame_000005000.jpg`，与邻居 `frame_000000000`/`frame_000020000` 同格式，sections 38→39，排序后前4帧时间 0,5,20,40（5 正确落在 0 与 20 之间）；③ 自动/手动帧在文件名层面无法区分，中间插入天然合群；④ 时间位置即文件名，可字典序排序、可肉眼读出时刻(ms/1000=秒)。

## 2026-07-15 #15 — 播放界面手动截图插入 md（补全机器抽帧遗漏）
- 场景：自动抽帧（场景检测+均匀采样）可能漏掉某些关键画面；用户在播放界面看到该补的画面时，需能一键截图并自动落到 md 对应时间位置、与预处理帧同存储。
- 问题：原前端只能消费已有 frames，无「按当前播放时刻手动补帧」的入口；后端无按时间抽单帧并回写 project.json/project.md 的能力。
- 解决（优化）方法：
  - 后端 [app.py](web/app.py) `POST /api/project/{pid}/frame` body{t}：用 ffmpeg `-ss {t} -i video -frames:v 1 -q:v 2` 抽当前时刻单帧 → `workdir/frames/manual_{secs:07d}.jpg`；调 `frames._phash` 算 pHash；`Project.load` 重建 → 若 ±0.3s 内已有帧则替换其图/时间戳，否则 `Frame` 追加并按时间排序；重写 `frames.txt`、`markdown.build`、`pipeline.save_project_json`，使 json/md/时间清单与预处理帧一致。
  - 前端 [index.html](web/templates/index.html)：播放器控件加「📸 截图」按钮 + `captureFrame()`（POST 当前 `v.currentTime` → `refreshDoc` 只重渲染文档不重载视频 → 滚动到新插入区段）。
- 结果：实测截图前 sections=38，POST `t=5` → 39 区段、`manual_0000005.jpg` 落在 t=5（0s 与 20s 之间），`/media/.../manual_0000005.jpg` 200，project.json/project.md 已重建，`/api/project/{pid}/doc` 返回新区段；图片存储、时间清单、md 与预处理帧完全同构，可反复截图（同时刻覆盖、异时刻插入）。前端 `id="capture"`/`captureFrame`/`refreshDoc` 就位。

## 2026-07-15 #14 — 专栏导出 zip / 导入 zip 到本地
- 场景：专栏（多视频系列）需可整体打包分享、并在另一台机器/目录导入还原。
- 问题：原专栏只存在于本地 data/collections/{cid}.json 且只引用项目 id，视频内容在各自 data/projects/{id}/，无法整体搬移；缺导出/导入。
- 解决（优化）方法：
  - 导出 [app.py](web/app.py) `GET /api/collection/{cid}/export`：tempfile+zipfile 打包，含 `collection.json`（id/title/desc/video_ids/created_at 清单）+ 每个 video 的 `projects/{pid}/` 完整目录（video.mp4/subtitle.srt/frames/.../project.json/project.md），FileResponse + BackgroundTask 删临时文件，文件名按标题做 ASCII 安全化。
  - 导入 `POST /api/collection/import`（UploadFile，python-multipart）：读 zip 内 `collection.json`，对每个 video_id——若本地已存在该 project 目录则复用、否则从 `projects/{pid}/` 解压还原（防 zip slip：拒绝绝对路径与 `..` 上跳）；最后新建本地专栏（新 cid 避免冲突，video_ids 引用已落地的项目）。
  - 前端 [index.html](web/templates/index.html)：专栏详情工具栏加「⬇ 导出zip」（a[href] 触发下载）；专栏列表工具栏加「📥 导入zip」+ 隐藏 file input，选文件后 FormData 上传，成功进新专栏并 renderCollection。
- 结果：① 导出实测 21.5MB / 46 条目，含 collection.json + projects/3a60e18f1f53/{frames.txt,project.json,project.md,...}；② 导入往返实测：删专栏+删项目后导入 zip → project 解压还原（project.json/video.mp4/frames 均恢复）、新专栏 d00fa67b7acb 重建、video 数=1、thumb 正确、`/api/project/{pid}/doc` 200；③ 已存在的项目导入时复用不重复解压；④ 前端 `importCollBtn/importCollFile/collExport` 元素就位。导出/导入在 UI 与 API 均可用。


## 2026-07-15 #13 — 专栏组织（collection：多视频系列 → 列表 → 详情 → 视频）
- 场景：原每个视频独立无组织关系，需要把多个视频组织成专栏/系列，列表浏览→点专栏→看视频列表→点视频。
- 问题：只有扁平的 `/api/projects`，缺上一层「合集/专栏」概念；前端只有视频视图，没有导航层级。
- 解决（优化）方法：
  - 后端 [app.py](web/app.py) 新增专栏层：`data/collections/{cid}.json`（id/title/desc/video_ids[]/created_at）。端点 `GET /api/collections`、`POST /api/collections`、`GET /api/collection/{cid}`(含 videos 元信息)、`PATCH /api/collection/{cid}`(改 title/desc/video_ids，保序去重仅留存在的)、`DELETE /api/collection/{cid}`。`_project_meta(pid)` 给列表/封面返回 title/duration/frames/字幕数/thumb_url/source_lang。
  - 前端 [index.html](web/templates/index.html) 重构为三视图 + 面包屑：`viewCollections`(专栏卡片网格+新建+全部视频)、`viewCollection`(标题/简介可编辑、添加视频下拉、视频行带↑↓移出)、`viewVideo`(原播放器+文档)。`setView()` 切换+面包屑；`renderCollections/renderCollection` 拉取渲染；视频行点击→`loadProject` 进视频视图带「← 返回专栏」面包屑；`?c=` 直接进专栏、`?p=` 进视频。
  - 联动：在专栏视图里粘贴 URL 处理新视频，完成后自动加入当前专栏并打开。
- 结果：① 专栏 CRUD 实测：创建→PATCH 加视频 200→详情 videos=1 含 thumb_url→列表 1→DELETE 200 列表回 0；② 前端三视图元素 `viewCollections/viewCollection/viewVideo` + `newCollBtn/allVideosBtn/renderCollections/renderCollection/setView` 均就位；③ 已建演示专栏「VLLM 推理系列」含现有视频，打开 `http://127.0.0.1:8000` 默认进专栏列表。专栏内视频支持↑↓排序、移出、重命名、改简介、删除专栏（删专栏不删视频，仅解除组织）。


## 2026-07-15 #12 — 原音语言自适应（中/英自动检测 + 双语方向自动选择）
- 场景：原音可能是中文或英文，旧逻辑把主轨语言硬编码为中文，英文原音的主轨会乱码。
- 问题：[config.py](config.py) `WHISPER_LANGUAGE="zh"` 强制源语言=中文；[subtitle.py](v2md/subtitle.py) 主轨 transcribe 时 `language="zh"` 写死 → 英文原音被按中文识别产生乱码；双语第二轨只支持中→英(whisper translate)，不支持英→中(whisper translate 只能译成英文)。
- 解决（优化）方法：
  - 主轨自动检测：`config.WHISPER_LANGUAGE=None`；[subtitle.py](v2md/subtitle.py) `_run_whisper(task=None)` 不传 language→whisper 自动检测，并从返回的 `info.language` 捕获源语言；`ensure_subtitle` 把 `asset.source_lang` 设为检测到的语言。
  - 双语方向自动选择：新增 `ensure_subtitle_secondary(asset, workdir)` 返回 `(segs, secondary_lang)`——原音非英文(如中文)→第二轨=英文，走 whisper task=translate（离线）；原音为英文→第二轨=中文，走新 [translator.py](v2md/translator.py)（OpenAI 兼容 chat API，httpx 批量翻译，每批 60 段，逐行编号解析）。翻译器未配 KEY 时 `is_available()=False`，英文原音双语自动跳过（仅留英文主轨，降级可用）。
  - 数据模型：[models.py](v2md/models.py) `VideoAsset`/`Project` 加 `source_lang`/`secondary_lang`，`to_dict`/`Project.load` 读写之；`subtitles_en` 字段语义改为「双语第二轨」（语言见 secondary_lang）。
  - 配置：[config.py](config.py) 加 `TRANSLATE_BASE_URL`(默认 DeepSeek)/`TRANSLATE_API_KEY`(None)/`TRANSLATE_MODEL`。
  - pipeline/前端：[pipeline.py](v2md/pipeline.py) 记录 `source_lang`/`secondary_lang`；[app.py](web/app.py) `/doc` 返回这两字段；[index.html](web/templates/index.html) 字幕语言选择器标签动态化（中/英→原声/译文，按检测语言显示，如「原声中·译文英」或「原声英·译文中」），按钮 data-l 仍为 zh/en/bi。
- 结果：① 主轨自动检测：对中文视频跑 subtitle CLI，whisper 正确转写中文、exit 0、`asset.source_lang` 由 `info.language` 捕获；② 中文原音双语=中+英(whisper translate，#11 已验)；③ 英文原音双语=英+中翻译器：`translator.translate_to_zh` 桩测返回对齐译文（['你好世界','这是测试','第三句']，数量匹配），无 KEY 时 `is_available()=False` 优雅跳过；④ `/doc` 返回 `source_lang`/`secondary_lang`，前端 `LANG_LABEL`/`langLabel`/`原声` 元素就位。注：英→中实际 API 调用需用户在 [config.py](config.py) 填 `TRANSLATE_API_KEY`（DeepSeek 或其它 OpenAI 兼容服务）后方可端到端验证。


## 2026-07-15 #11 — Whisper 单例 / 转写段进度 / 双语逐句对齐 / 文档大纲搜索 / 项目删除导出 / 字幕样式
- 场景：继续完善性能与体验。双语时模型重复加载；转写期间前端只显示静态「生成字幕…」无反馈；中英两套独立分段在双语阅读时错位；文档区 38 段+长字幕缺目录与检索；历史项目只能看不能删/导出；字幕覆盖样式固定不可调。
- 问题：① [subtitle.py](v2md/subtitle.py) `_load_whisper()` 每次新建 WhisperModel，双语中文+英文各加载一次（约数秒×2）；② whisper 转写是流式 generator，但 `_transcribe_with_whisper` 只在结尾一次性返回，pipeline 的 `subtitle` 步骤 1-2 分钟无细粒度进度；③ 中文字幕段（254）与英文段（237）边界不一致，sections() 里 subs/subs_en 各自独立列在区块上下，双语阅读时同句中英不对行；④ 前端文档区无大纲跳转、无字幕全文搜索；⑤ 无项目删除/导出 zip；⑥ 字幕覆盖层字号/背景/位置写死。
- 解决（优化）方法：
  - #5 单例：[subtitle.py](v2md/subtitle.py) 加模块级 `_WHISPER_MODEL` + `get_whisper_model()`（首次加载并 log，之后直接返回），`_run_whisper()` 复用之；`_transcribe_with_whisper`/`_translate_with_whisper` 改走 `_run_whisper`。
  - #8 段进度：`_run_whisper(video_path, task, on_segment)` 在 generator 迭代每段时回调 `on_segment(n)`；`ensure_subtitle`/`ensure_subtitle_en` 透传 `on_segment`；[pipeline.py](v2md/pipeline.py) `run()` 定义 `_on_seg(n)`→`_emit(Step.SUBTITLE, f"转写中… 已生成 {n} 段")`（同步骤只更新 message），英文用 lambda 报「英文转写中…」。前端轮询 `/api/jobs/{id}` 即可见实时段数。
  - #2 双语对齐：新增 `align_en_to_zh(zh, en)`（按时间重叠最大为每条中文配英文段，返回 Optional[SubtitleSegment]）；[markdown.py](v2md/markdown.py) `sections()` 给每条中文字幕附 `en:{start,end,text}`；`build()` 渲染逐句对照 `> [t] 中文` + `>   ↳ [t] 英文`（去掉旧的独立 EN 块）；[index.html](web/templates/index.html) 文档区每条中文下渲染其 en。
  - #14 大纲+搜索：文档区上方加 `docSearch` 输入 + `docOutline` 下拉；outline 由 sections 生成 `mm:ss 首条字幕`，选中 scrollIntoView 该 `.sec`；搜索按字幕文本（含 en）包含匹配，不匹配的 `.sec` 加 `.hide` 隐藏。
  - #15 删除/导出：[app.py](web/app.py) `DELETE /api/project/{pid}`（`shutil.rmtree`）+ `GET /api/project/{pid}/export`（tempfile+zipfile 打包整个 workdir，BackgroundTask 删临时文件）；前端 docTools 加「📦 zip」「🗑 删除」（confirm 后 DELETE 并刷新下拉）。
  - #16 字幕样式：[index.html](web/templates/index.html) 播放器加 `<details class=substyle>` 浮层（字号 slider 12-28px、背景透明度 slider、位置 底部/顶部），通过 CSS 变量 `--sub-size`/`--sub-bg` 与 `.subs.top` 类实时改 `#subs` 样式。
- 结果：① 模型单例验证 `m1 is m2: True`、日志「已加载 whisper 模型 small」全流程只出现一次（中文转写后英文 translate 未重复加载）；② 转写进度实测中文「已生成 1→254 段」、英文「1→230 段」逐段递增可见；③ md 逐句对照 `> [00:00](/?p=…&t=0) 今天我们来讲一下VLLM` + `>   ↳ [00:00](…) Today, let's talk about VLLM.`，/doc `section[1].subs[0].en.text="It can make your push-and-pull speed"`；④ 前端 `docSearch`/`docOutline` 元素就位；⑤ `GET /export` 返回 200 / 21.4MB application/zip，DELETE 按钮带 confirm；⑥ `subSize`/`subBg`/`subPos` 控件就位。`--force --bilingual` 全流程 exit 0，新项目 38 帧/254 中/230 英。


## 2026-07-15 #10 — 统一日志 / 处理复用缓存 / md 链接相对化+单文件导出 / 字幕时间戳跳转
- 场景：完善工程化与内容可用性。用户重复处理同一视频会重新下载+转写浪费时间；md 跳转链接硬编码 `127.0.0.1:8000` 换端口即失效；md 里每条字幕的时间戳是纯文本不能跳转；各模块 `print` 散落难排查。
- 问题：① 各模块（downloader/subtitle/frames/markdown）用 `print` 输出，无级别无模块名，无法统一控制；② 同一 URL/bvid/本地文件重复「处理」无缓存，每次重跑 yt-dlp+Whisper（约数分钟）；③ [markdown.py](v2md/markdown.py) `_local_link` 写死 `http://{WEB_HOST}:{WEB_PORT}/?p=&t=`，换端口旧 md 失效；④ md 字幕行 `> 00:03 文本` 的时间戳是纯文本，不像标题那样可点跳转；⑤ 连带 bug：`run()` 从未调用 `save_project_json`，**Web 处理的新视频不落 project.json**，导致 `/doc`/`/projects` 在 job 内存状态丢失后 404（仅 CLI 路径保存过）。
- 解决（优化）方法：
  - #11 日志：[config.py](config.py) 加 `logging.basicConfig`（StreamHandler→已重配 UTF-8 的 stdout，格式 `%(asctime)s [%(name)s] %(message)s`）；downloader/subtitle/frames/markdown/pipeline 各加 `log=logging.getLogger(__name__)`，把 `print("[xxx] ...")` 改 `log.info(...)`；pipeline 步骤进度仍走 `on_progress` 回调（供前端），与日志分离。
  - #9 复用：[models.py](v2md/models.py) 加 `Project.load(workdir)` 类方法（从 project.json 重建）；[pipeline.py](v2md/pipeline.py) 加 `_input_key(url)`（本地文件→sha256、B站→bvid、其余→URL 串）与 `find_reusable(url)`（扫 `data/projects/*/project.json` 按键匹配，命中返回 `Project.load`）；`run(url, force=False)` 开头非 force 时调 `find_reusable`，命中直接 `_emit(DONE)` 返回旧项目。[downloader.py](v2md/downloader.py) 加 `_file_sha256()`，本地文件分支算 `content_hash` 存入 `VideoAsset`/`Project`；`VideoAsset` 与 `Project` 各加 `content_hash` 字段并写入 `to_dict`。CLI 加 `--force` 强制重跑。
  - #10 链接相对化+单文件：`_local_link` 改相对 `/?p={id}&t={秒}`（端口无关）；[markdown.py](v2md/markdown.py) `build(project, embed=False)` + `build_embedded()`，embed 时 `_image_src` 返回 `data:image/jpeg;base64,...` 内嵌图片，写 `project.embedded.md`；[app.py](web/app.py) `/api/project/{pid}/markdown?embed=1` 生成单文件版，`/doc` 返回 `md_embed_url`；前端文档工具栏加「⬇ 单文件.md」按钮。
  - 字幕时间戳跳转：[markdown.py](v2md/markdown.py) build() 把字幕行 `> {time} text` 改为 `> [{time}](/?p=&t=) text`（中英皆然）；前端 [index.html](web/templates/index.html) 把字幕时间戳从 `<span class="st">` 改 `<a class="st" data-t=...>`，点击 `seekTo`，纳入 `.ts/.sec-img/.st` 统一点击处理。
  - bug 修复：`run()` 在 `markdown.build` 后调 `save_project_json(project)`，确保 Web job 也落盘；CLI 的 `save_project_json` 保留为幂等兜底。
- 结果：① 重复处理同一 B站 URL 秒回 `step=done project_id=3a60e18f1f53`（按 bvid 复用，不重下不重转写），日志 `12:23:38 [v2md.pipeline] 命中已有项目 3a60e18f1f53（按 bvid 复用）`；② md 标题与字幕行链接全为相对 `/?p=…&t=…`，无 `127.0.0.1:8000`；③ 字幕行 `> [00:00](/?p=3a60e18f1f53&t=0) 今天我们来讲一下VLLM` 可点跳转；④ 单文件导出 `?embed=1` 返回 200 / 2.3MB / 含 `data:image/jpeg;base64`；⑤ `/doc` 新增 `md_embed_url`；⑥ Web job 现正确落盘 project.json。`--force` 可跳过复用。

