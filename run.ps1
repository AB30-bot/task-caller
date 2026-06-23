# Launches the live caller: starts server.py (if not already up) + a cloudflared
# quick tunnel, captures the public https URL, and writes it to current_url.txt.
#
# Usage:  powershell -ExecutionPolicy Bypass -File run.ps1
$ErrorActionPreference = "Stop"
$root = "C:\Users\User\Claude\Projects\claude-optimizer\live-caller"
$py   = Join-Path $root ".venv312\Scripts\python.exe"
$cf   = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$env:PYTHONUTF8 = "1"

# 1. Start the call server unless something is already serving on :7860.
$alreadyUp = $false
try {
  $resp = Invoke-WebRequest -Uri "http://localhost:7860/client/" -UseBasicParsing -TimeoutSec 3
  if ($resp.StatusCode -eq 200) { $alreadyUp = $true }
} catch { $alreadyUp = $false }

if ($alreadyUp) {
  Write-Host "server.py already running on :7860 — reusing it."
} else {
  Write-Host "Starting server.py ..."
  Start-Process -FilePath $py -ArgumentList "server.py" -WorkingDirectory $root -PassThru -WindowStyle Hidden | Out-Null
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try {
      $r = Invoke-WebRequest -Uri "http://localhost:7860/client/" -UseBasicParsing -TimeoutSec 2
      if ($r.StatusCode -eq 200) { break }
    } catch {}
  }
}

# 2. Start the cloudflared quick tunnel, capturing its log so we can parse the URL.
$cfLog = Join-Path $root "cloudflared.log"
if (Test-Path $cfLog) { Remove-Item $cfLog -Force }
Write-Host "Starting cloudflared tunnel ..."
Start-Process -FilePath $cf -ArgumentList @("tunnel", "--url", "http://localhost:7860") `
  -RedirectStandardError $cfLog -RedirectStandardOutput "$cfLog.out" -WindowStyle Hidden -PassThru | Out-Null

# 3. Wait for the public URL to appear, then persist it.
$url = $null
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 1
  if (Test-Path $cfLog) {
    $m = Select-String -Path $cfLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($m) { $url = $m.Matches[0].Value; break }
  }
}

if ($url) {
  Set-Content -Path (Join-Path $root "current_url.txt") -Value $url -Encoding utf8 -NoNewline
  Write-Host ""
  Write-Host "================================================================"
  Write-Host " Live AI call is PUBLIC at:  $url"
  Write-Host " (written to current_url.txt — send_link.py will WhatsApp it)"
  Write-Host "================================================================"
} else {
  Write-Host "ERROR: could not capture the tunnel URL. See $cfLog"
  exit 1
}
