#!/usr/bin/env bash
set -euo pipefail

# Run as the 'ubuntu' user on a fresh Oracle Cloud Always-Free Ubuntu 22.04 VM.
# Before running: scp the project to ~/live-caller and fill in ~/live-caller/.env

echo "=== System packages ==="
sudo apt-get update -y
sudo apt-get install -y python3.12 python3.12-venv python3-pip supervisor curl \
  chromium-browser ca-certificates

# whatsapp-web.js drives a real Chromium via puppeteer. On Ubuntu 22.04
# 'chromium-browser' is a snap shim; if it misbehaves under supervisor, install
# the deb instead:  sudo apt-get install -y chromium  (path /usr/bin/chromium)
echo "Chromium at: $(command -v chromium-browser || command -v chromium || echo 'NOT FOUND')"

echo "=== Node.js 20 (apt's nodejs is too old for whatsapp-web.js) ==="
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node --version

echo "=== cloudflared ==="
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared jammy main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update -y && sudo apt-get install -y cloudflared

echo "=== Python venv ==="
cd ~/live-caller
python3.12 -m venv .venv312
.venv312/bin/pip install --upgrade pip
.venv312/bin/pip install -r requirements.txt

echo "=== Node deps ==="
cd openwa && npm install && cd ..

echo "=== Check .env ==="
grep -q GEMINI_API_KEY .env || { echo "ERROR: fill in ~/live-caller/.env first"; exit 1; }

echo "=== supervisord ==="
sudo cp deploy/supervisord.conf /etc/supervisor/conf.d/taskcaller.conf
sudo supervisorctl reread && sudo supervisorctl update

echo "=== Done ==="
echo "Start everything:        sudo supervisorctl start all"
echo "Link WhatsApp (1st run): sudo supervisorctl tail -f openwa   # scan the QR"
