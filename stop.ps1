# one-click stop for video-to-md server: kill whatever listens on port 8000
$port = 8000
$conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "No server running on port $port." -ForegroundColor Yellow
    Start-Sleep -Seconds 2
    exit 0
}
$pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($p in $pids) {
    try {
        $proc = Get-Process -Id $p -ErrorAction Stop
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped: PID $p ($($proc.ProcessName))" -ForegroundColor Green
    } catch {
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped: PID $p" -ForegroundColor Green
    }
}
Start-Sleep -Seconds 1
$still = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($still) { Write-Host "Port $port still in use. End it in Task Manager." -ForegroundColor Red }
else { Write-Host "Server stopped." -ForegroundColor Green }
Start-Sleep -Seconds 2
