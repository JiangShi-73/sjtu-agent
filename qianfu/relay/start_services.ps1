# 启动微信读书中继 + WeWe RSS（两个独立进程，关掉窗口也继续跑）
#   powershell -ExecutionPolicy Bypass -File start_services.ps1

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Split-Path -Parent $here
$wewe = Join-Path $env:TEMP "wewe-rss\apps\server"

function Stop-Port($port) {
    $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
}

Write-Host "清理旧进程..."
Stop-Port 4000
Stop-Port 4002
Start-Sleep -Seconds 2

Write-Host "启动中继 (4002)..."
Start-Process -FilePath "python" -ArgumentList "-m","relay.server","--port","4002" -WorkingDirectory $project -WindowStyle Hidden
Start-Sleep -Seconds 3

if (Test-Path $wewe) {
    Write-Host "启动 WeWe RSS (4000)..."
    Start-Process -FilePath "node" -ArgumentList "dist/main" -WorkingDirectory $wewe -WindowStyle Hidden
    Start-Sleep -Seconds 8
} else {
    Write-Host "找不到 WeWe RSS: $wewe"
}

foreach ($p in 4000, 4002) {
    $n = @(Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }).Count
    if ($n -gt 0) { Write-Host "端口 $p : 已监听" } else { Write-Host "端口 $p : 没起来" }
}

Write-Host ""
Write-Host "WeWe RSS  http://127.0.0.1:4000/dash   授权码 xinge2026"
