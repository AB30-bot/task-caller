# Deploy Task Caller to an Oracle Cloud Always-Free VM

End state: a stable HTTPS URL, the dashboard installable as an iPhone app, calls
working over mobile data. No ongoing cost.

---

## 1. Create the VM (Oracle Console)

1. cloud.oracle.com → **Compute → Instances → Create instance**.
2. **Image**: Canonical **Ubuntu 22.04**. **Shape**: change → Ampere →
   **VM.Standard.A1.Flex** (Always-Free; 1 OCPU / 6 GB is plenty).
3. **SSH keys**: upload your public key, or download the generated private key.
4. Create. Note the **public IP**.

## 2. Open ports 80 + 443

**Cloud side** — the instance's subnet → **Security List** → Add Ingress Rules:
- Source `0.0.0.0/0`, IP Protocol TCP, Destination port **80**
- Source `0.0.0.0/0`, IP Protocol TCP, Destination port **443**

**OS firewall** (Oracle Ubuntu blocks by default) — after you SSH in (step 3):
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 3. Get the code onto the VM

On your **Windows PC** (PowerShell), from the project's parent folder:
```powershell
cd "C:\Users\User\Claude\Projects\claude-optimizer"
tar --exclude=live-caller/.venv312 --exclude=live-caller/openwa/node_modules `
    --exclude=live-caller/openwa/.wwebjs_auth --exclude=live-caller/openwa/.wwebjs_cache `
    --exclude=live-caller/jobs.db --exclude=live-caller/transcripts `
    --exclude=live-caller/qr.png --exclude="live-caller/*.log" --exclude="live-caller/*.out" `
    -czf live-caller.tgz live-caller
scp -i <your-key> live-caller.tgz ubuntu@<public-ip>:~/
```
On the **VM**:
```bash
tar xzf ~/live-caller.tgz && cd ~/live-caller
```

## 4. Free stable domain — DuckDNS

1. duckdns.org → sign in → create a subdomain, e.g. **adam-taskcaller**.
2. Set its IP to the VM's **public IP**. Your domain is now
   `adam-taskcaller.duckdns.org`.

## 5. Configure + install

```bash
cd ~/live-caller
cp deploy/.env.example .env
nano .env        # fill GEMINI_API_KEY, TWILIO_* , ADAM_WHATSAPP,
                 # and LIVE_DOMAIN=adam-taskcaller.duckdns.org
chmod +x deploy/setup.sh && ./deploy/setup.sh
```

## 6. HTTPS with Caddy (auto Let's Encrypt)

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# Point Caddy at the call server:
echo 'adam-taskcaller.duckdns.org {
    reverse_proxy localhost:7860
}' | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy
```
Caddy fetches a real HTTPS cert automatically (needs 80+443 open — step 2).

## 7. Start the app + link WhatsApp

```bash
sudo supervisorctl start all
sudo supervisorctl tail -f openwa     # a QR code prints — scan it in
                                      # WhatsApp → Linked Devices (one time)
```
The WhatsApp session persists on the VM; you won't rescan after this.

Check it's live: open `https://adam-taskcaller.duckdns.org/control` in any browser.

## 8. Install the iPhone app

On the iPhone, open `https://adam-taskcaller.duckdns.org/control` in **Safari** →
Share → **Add to Home Screen** → "Task Caller". It launches fullscreen like a
native app. Type a task + number, send.

---

## Operating it

- Logs: `sudo supervisorctl tail -f callserver` / `... openwa`
- Restart after a code change: `sudo supervisorctl restart all`
- WhatsApp dropped: `sudo supervisorctl restart openwa` and re-scan if prompted.
- The VM's public IP is fixed, so the DuckDNS domain stays valid across reboots.
