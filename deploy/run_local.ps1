# Auto-start the Task Caller stack on this PC (registered as a logon task).
# Tailscale + Funnel resume on their own via the tailscaled service; this brings
# back the call server and the WhatsApp gateway, and re-asserts the funnel.
$ErrorActionPreference = "SilentlyContinue"
# Project root (this script lives in <root>/deploy)
$proj = Split-Path -Parent $PSScriptRoot

$env:LIVE_USE_TURN = "1"
$env:LIVE_DOMAIN   = "your-machine.your-tailnet.ts.net"   # your Tailscale Funnel host

# Call server (skip if already listening on 7860)
if (-not (Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath "$proj\.venv312\Scripts\python.exe" -ArgumentList "server.py" `
        -WorkingDirectory $proj -WindowStyle Hidden `
        -RedirectStandardError "$proj\server.log" -RedirectStandardOutput "$proj\server.out"
}

# WhatsApp gateway (skip if already listening on 3000)
if (-not (Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath "node" -ArgumentList "index.js" `
        -WorkingDirectory "$proj\openwa" -WindowStyle Hidden `
        -RedirectStandardError "$proj\openwa\openwa.err" -RedirectStandardOutput "$proj\openwa\openwa.out"
}

# Re-assert the public funnel (idempotent)
& "C:\Program Files\Tailscale\tailscale.exe" funnel --bg 7860 | Out-Null
