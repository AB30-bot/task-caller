const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const qrimage = require('qrcode');
const express = require('express');

const app = express();
app.use(express.json());

// puppeteer-core ships no browser, so point it at an installed Chromium-based
// one. Platform-aware default: Edge on Windows (dev), Chromium on Linux (the VM).
// Override either with CHROME_PATH in .env.
const DEFAULT_BROWSER = process.platform === 'win32'
  ? 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
  : '/usr/bin/chromium-browser';
const BROWSER_PATH = process.env.CHROME_PATH || DEFAULT_BROWSER;

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: './.wwebjs_auth' }),
  puppeteer: {
    executablePath: BROWSER_PATH,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  },
});

let ready = false;

client.on('qr', qr => {
  console.log('\n=== Scan this QR code in WhatsApp (Linked Devices) ===\n');
  qrcode.generate(qr, { small: true });
  const pngPath = path.join(__dirname, 'qr.png');
  qrimage.toFile(pngPath, qr, { width: 420, margin: 2 }, err => {
    if (err) console.error('QR PNG write failed:', err.message);
    else console.log('QR also saved as image:', pngPath);
  });
});

client.on('ready', () => {
  ready = true;
  console.log('WhatsApp client ready');
});

client.on('auth_failure', msg => {
  console.error('Auth failure:', msg);
  ready = false;
});

client.on('disconnected', reason => {
  console.warn('WhatsApp disconnected:', reason);
  ready = false;
});

client.initialize();

app.get('/health', (_req, res) => {
  res.json({ ready });
});

app.post('/send', async (req, res) => {
  const { to, message } = req.body;
  if (!to || !message) {
    return res.status(400).json({ error: 'Missing to or message' });
  }
  if (!ready) {
    return res.status(503).json({ error: 'WhatsApp not ready' });
  }
  try {
    const digits = to.replace(/[^0-9]/g, '');
    // Resolve the real chat id — getNumberId handles WhatsApp's newer LID
    // identifiers, which the bare "<digits>@c.us" form fails on ("No LID for user").
    const numberId = await client.getNumberId(digits);
    if (!numberId) {
      console.warn(`Number not on WhatsApp: ${to}`);
      return res.status(404).json({ error: `${to} is not on WhatsApp` });
    }
    await client.sendMessage(numberId._serialized, message);
    res.json({ ok: true });
  } catch (e) {
    console.error('Send error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// Keep the gateway alive even if whatsapp-web.js emits an unhandled rejection.
process.on('unhandledRejection', err =>
  console.error('Unhandled rejection (non-fatal):', (err && err.message) || err));

const PORT = process.env.OPENWA_PORT || 3000;
app.listen(PORT, () => console.log(`OpenWA gateway listening on :${PORT}`));
