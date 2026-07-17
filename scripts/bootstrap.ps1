# 环境引导脚本（PowerShell）
# 用法：在项目根目录执行  pwsh -File scripts/bootstrap.ps1
# 或在 PowerShell 里：  .\scripts\bootstrap.ps1
#
# 做三件事：
#   1) 装 uv（如未装）
#   2) 用 uv 拉取 Python 3.12 并建虚拟环境、装依赖
#   3) 确保 ffmpeg 可用（yt-dlp 自带 / winget 安装）

$ErrorActionPreference = "Stop"
# -- 中文 Windows 编码基线：每层都钉死 UTF-8，杜绝 GBK 乱码 --
try { chcp 65001 > $null } catch {}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$PSDefaultParameterValues['Get-Content:Encoding']  = 'utf8'
$PSDefaultParameterValues['Set-Content:Encoding']  = 'utf8'
$PSDefaultParameterValues['Add-Content:Encoding'] = 'utf8'
$PSDefaultParameterValues['Out-File:Encoding']    = 'utf8'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "===> [1/3] 检查/安装 uv" -ForegroundColor Cyan
$uv = (Get-Command uv -ErrorAction SilentlyContinue)
if (-not $uv) {
    Write-Host "未检测到 uv，用 pip 安装..." -ForegroundColor Yellow
    # 优先用系统 python 装 uv
    python -m pip install --upgrade uv
} else {
    Write-Host "uv 已就绪: $($uv.Source)"
}

Write-Host "===> [2/3] 拉 Python 3.12 并建虚拟环境 + 装依赖" -ForegroundColor Cyan
uv python install 3.12
uv venv --python 3.12
uv sync                  # 按 pyproject.toml 装依赖

Write-Host "===> [3/3] 检查 ffmpeg" -ForegroundColor Cyan
$ff = (Get-Command ffmpeg -ErrorAction SilentlyContinue)
if (-not $ff) {
    Write-Host "未检测到系统 ffmpeg。" -ForegroundColor Yellow
    Write-Host "  方案A（推荐）：让 yt-dlp 自动下载 ffmpeg，首次下载视频时会自取，无需手动。" -ForegroundColor Gray
    Write-Host "  方案B：winget install Gyan.FFmpeg  （装完重开终端）" -ForegroundColor Gray
} else {
    Write-Host "ffmpeg 已就绪: $($ff.Source)"
}

Write-Host ""
Write-Host "完成！接下来：" -ForegroundColor Green
Write-Host "  uv run python -m v2md.downloader <视频URL>   # 测试下载模块" -ForegroundColor Green
Write-Host "  uv run python -m v2md.pipeline <视频URL>    # 端到端跑通" -ForegroundColor Green
Write-Host "  uv run uvicorn web.app:app --reload        # 启动 Web 界面" -ForegroundColor Green
