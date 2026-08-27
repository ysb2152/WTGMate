# WTGMate 라이브 데모 실행: 백엔드(FastAPI) + Cloudflare Tunnel.
# 사용: powershell -ExecutionPolicy Bypass -File deploy\run-demo.ps1
#
# 백엔드는 새 창에서, 터널은 이 창에서 실행된다. 터널이 출력하는
# https://<...>.trycloudflare.com URL을 Vercel의 VITE_API_BASE_URL에 넣는다(임시 URL).
# 안정 URL이 필요하면 named tunnel을 쓴다(deploy/CLOUDFLARE.md 참고).

$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"

Write-Host "▶ 백엔드 기동 (새 창, :8000)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "cd `"$backend`"; venv\Scripts\python.exe -m uvicorn main:app --port 8000"
)

Start-Sleep -Seconds 5

$cf = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cf)) { $cf = "cloudflared" }  # PATH 폴백

Write-Host "▶ Cloudflare Tunnel 시작 — 아래 trycloudflare.com URL을 Vercel에 등록하세요." -ForegroundColor Cyan
& $cf tunnel --url http://localhost:8000
