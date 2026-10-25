import puppeteer from 'puppeteer-core';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const edgePath = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const outDir = path.join(__dirname, 'preview');
fs.mkdirSync(outDir, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: edgePath,
  headless: 'new',
  protocolTimeout: 0,
  defaultViewport: { width: 1920, height: 1080, deviceScaleFactor: 1 },
  args: ['--no-sandbox', '--disable-gpu-vsync', '--allow-file-access-from-files'],
});

const page = await browser.newPage();
page.on('console', (msg) => console.log('[PAGE]', msg.type(), msg.text()));
page.on('pageerror', (err) => console.error('[PAGE ERROR]', err));

const url = 'file:///' + path.join(__dirname, 'index.html').replace(/\\/g, '/') + '?capture=1';
await page.goto(url, { waitUntil: 'load' });
await page.waitForFunction('window.__ready === true', { timeout: 15000 });

const times = [
  [5.2, '00-prologue'],
  [21.5, '01-archive'],
  [24.3, '01b-shatter'],
  [38.0, '02-events'],
  [43.0, '02b-resplit'],
  [58.5, '03-clusters'],
  [65.8, '03b-cluster-card'],
  [85.5, '04-network'],
  [97.5, '05a-activation'],
  [104.8, '05b-prob-workset'],
  [115.2, '05c-context-switch'],
  [124.5, '06a-quantities'],
  [134.5, '06b-familiarity'],
  [149.8, '07-revision'],
  [169.2, '08-branches'],
  [185.5, '09-projection'],
  [196.5, '10a-final-host'],
  [204.2, '10b-model-swap'],
  [212.0, '10c-climax'],
  [219.5, '10d-title'],
];

for (const [t, name] of times) {
  await page.evaluate((sec) => window.__renderAt(sec), t);
  const file = path.join(outDir, `${name}.png`);
  await page.screenshot({ path: file, type: 'png' });
  console.log('Captured', name, 'at', t);
}

const audioInfo = await page.evaluate(async () => {
  return await window.MF.Audio.renderOffline(window.MF.DURATION, 48000);
});
console.log('Audio offline render info:', audioInfo);

await browser.close();
