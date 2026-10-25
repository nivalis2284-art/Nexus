import puppeteer from 'puppeteer-core';
import path from 'node:path';
import fs from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const edgePath = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const outDir = path.join(__dirname, 'out');
fs.mkdirSync(outDir, { recursive: true });

const FPS = 30;
const DURATION = 225;
const TOTAL_FRAMES = DURATION * FPS;
const WORKERS = 6;
const BATCH = 5;

function writeWavHeader(samples, sampleRate, channels = 2) {
  const byteRate = sampleRate * channels * 2;
  const blockAlign = channels * 2;
  const dataSize = samples * channels * 2;
  const buf = Buffer.alloc(44);
  buf.write('RIFF', 0);
  buf.writeUInt32LE(36 + dataSize, 4);
  buf.write('WAVE', 8);
  buf.write('fmt ', 12);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20); // PCM
  buf.writeUInt16LE(channels, 22);
  buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(byteRate, 28);
  buf.writeUInt16LE(blockAlign, 32);
  buf.writeUInt16LE(16, 34);
  buf.write('data', 36);
  buf.writeUInt32LE(dataSize, 40);
  return buf;
}

function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    const p = spawn('ffmpeg', args, { stdio: ['ignore', 'ignore', 'pipe'] });
    let err = '';
    p.stderr.on('data', (d) => { err += d.toString(); });
    p.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg exited ${code}: ${err.slice(-600)}`))));
  });
}

const t0 = Date.now();
console.log(`Starting render: ${TOTAL_FRAMES} frames @ ${FPS}fps (${DURATION}s), ${WORKERS} parallel workers...`);

const browser = await puppeteer.launch({
  executablePath: edgePath,
  headless: 'new',
  protocolTimeout: 0,
  defaultViewport: { width: 1920, height: 1080, deviceScaleFactor: 1 },
  args: ['--no-sandbox', '--disable-gpu-vsync', '--allow-file-access-from-files'],
});

const url = 'file:///' + path.join(__dirname, 'index.html').replace(/\\/g, '/') + '?capture=1';

async function renderAudio() {
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForFunction('window.__ready === true', { timeout: 20000 });
  const a0 = Date.now();
  const info = await page.evaluate(async (dur) => {
    return await window.MF.Audio.renderOffline(dur, 48000);
  }, DURATION);
  console.log(`Audio synthesized in ${((Date.now() - a0) / 1000).toFixed(1)}s:`, info);

  const wavPath = path.join(outDir, 'soundtrack.wav');
  const fd = fs.openSync(wavPath, 'w');
  fs.writeSync(fd, writeWavHeader(info.samples, info.sr, 2));
  const totalInt16 = info.samples * 2;
  const chunkLen = 48000 * 4;
  for (let i = 0; i < totalInt16; i += chunkLen) {
    const len = Math.min(chunkLen, totalInt16 - i);
    const b64 = await page.evaluate((s, l) => window.MF.Audio.pcmChunk(s, l), i, len);
    fs.writeSync(fd, Buffer.from(b64, 'base64'));
  }
  fs.closeSync(fd);
  await page.close();
  console.log('Saved soundtrack.wav');
  return wavPath;
}

async function renderSegment(workerIdx, startFrame, endFrame) {
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForFunction('window.__ready === true', { timeout: 20000 });

  const segPath = path.join(outDir, `seg-${workerIdx}.mp4`);
  const ff = spawn('ffmpeg', [
    '-y',
    '-f', 'image2pipe',
    '-vcodec', 'mjpeg',
    '-framerate', String(FPS),
    '-i', '-',
    '-c:v', 'libx264',
    '-preset', 'fast',
    '-crf', '17',
    '-pix_fmt', 'yuv420p',
    segPath,
  ], { stdio: ['pipe', 'ignore', 'pipe'] });

  let ffErr = '';
  ff.stderr.on('data', (d) => { ffErr += d.toString(); });
  const ffDone = new Promise((resolve, reject) => {
    ff.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`seg-${workerIdx} ffmpeg exited ${code}: ${ffErr.slice(-500)}`))));
  });

  const count = endFrame - startFrame;
  for (let f = startFrame; f < endFrame; f += BATCH) {
    const batchFrames = [];
    for (let k = f; k < Math.min(endFrame, f + BATCH); k++) batchFrames.push(k);
    const b64List = await page.evaluate((frames, fps) => {
      const glCanvas = document.getElementById('gl-canvas');
      return frames.map((fr) => {
        window.__renderAt(fr / fps);
        return glCanvas.toDataURL('image/jpeg', 0.94).slice(23);
      });
    }, batchFrames, FPS);

    for (const b64 of b64List) {
      const buf = Buffer.from(b64, 'base64');
      if (!ff.stdin.write(buf)) {
        await new Promise((r) => ff.stdin.once('drain', r));
      }
    }
    const done = Math.min(endFrame, f + BATCH) - startFrame;
    if (done % 225 === 0 || done === count) {
      console.log(`[Worker ${workerIdx}] ${done}/${count} frames (${((done / count) * 100).toFixed(0)}%)`);
    }
  }

  ff.stdin.end();
  await ffDone;
  await page.close();
  return segPath;
}

const perWorker = Math.ceil(TOTAL_FRAMES / WORKERS);
const segPromises = [];
for (let w = 0; w < WORKERS; w++) {
  const s = w * perWorker;
  const e = Math.min(TOTAL_FRAMES, (w + 1) * perWorker);
  segPromises.push(renderSegment(w, s, e));
}

const [wavPath, ...segPaths] = await Promise.all([renderAudio(), ...segPromises]);
await browser.close();

const concatListPath = path.join(outDir, 'concat.txt');
fs.writeFileSync(concatListPath, segPaths.map((p) => `file '${p.replace(/\\/g, '/')}'`).join('\n'), 'utf8');

const finalMp4 = path.join(outDir, 'memory-theory-film.mp4');
console.log('Muxing segments and soundtrack into final MP4...');
await runFfmpeg([
  '-y',
  '-f', 'concat',
  '-safe', '0',
  '-i', concatListPath,
  '-i', wavPath,
  '-c:v', 'copy',
  '-c:a', 'aac',
  '-b:a', '192k',
  '-movflags', '+faststart',
  '-shortest',
  finalMp4,
]);

for (const p of segPaths) { try { fs.unlinkSync(p); } catch (e) { /* ignore */ } }
try { fs.unlinkSync(concatListPath); } catch (e) { /* ignore */ }

const stat = fs.statSync(finalMp4);
console.log(`Done in ${((Date.now() - t0) / 1000).toFixed(1)}s! Output: ${finalMp4} (${(stat.size / 1024 / 1024).toFixed(2)} MB)`);
