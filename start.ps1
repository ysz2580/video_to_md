# 一键启动：确保依赖 → 启动 Web 服务 → 打开浏览器
# 用法：双击 start.bat，或在此目录 pwsh -File start.ps1
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot
# 让 uv（装在用户 Scripts 目录）可用
$env:Path = "$env:APPDATA\Python\Python314\Scripts;$env:Path"

function Resolve-LaunchCmd {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv sync --quiet          # 依赖已是最新时几乎瞬完成
        return @{ Exe = "uv"; Args = @("run","uvicorn","web.app:app","--host","127.0.0.1","--port","8000") }
    }
    if (Test-Path ".venv\Scripts\python.exe") {
        return @{ Exe = ".venv\Scripts\python.exe"; Args = @("-m","uvicorn","web.app:app","--host","127.0.0.1","--port","8000") }
    }
    return $null
}

$cmd = Resolve-LaunchCmd
if (-not $cmd) {
    Write-Host "Neither uv nor .venv found. Running bootstrap first..." -ForegroundColor Yellow
    & "$PSScriptRoot\scripts\bootstrap.ps1"
    $env:Path = "$env:APPDATA\Python\Python314\Scripts;$env:Path"
    $cmd = Resolve-LaunchCmd
    if (-not $cmd) {
        Write-Host "Environment still not ready. Check scripts\bootstrap.ps1 output." -ForegroundColor Red
        Read-Host "Press Enter to exit"; exit 1
    }
}

Write-Host ""
Write-Host "================ video-to-md ================" -ForegroundColor Cyan
Write-Host "Server: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Stop:   close this window or press Ctrl+C" -ForegroundColor DarkGray
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# 前台运行服务（日志直接打在当前窗口）
$proc = Start-Process -FilePath $cmd.Exe -ArgumentList $cmd.Args -NoNewWindow -PassThru
# 等服务起来再开浏览器
Start-Sleep -Seconds 2
try { Start-Process "http://127.0.0.1:8000" } catch {}
$proc.WaitForExit()
