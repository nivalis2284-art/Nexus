/* Optional browser integration check. Requires Playwright and Chromium.
 * NEXUS_TEST_PYTHON selects a Python with the project's dependencies installed.
 * NEXUS_PLAYWRIGHT_MODULE may point at an existing Playwright installation.
 * Uses a temporary home, fake local provider and fake key only.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn, execFileSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require(process.env.NEXUS_PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const root = path.resolve(__dirname, '..');
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-browser-'));
  const home = path.join(temporary, 'home');
  const workspace = path.join(temporary, 'workspace');
  fs.mkdirSync(home); fs.mkdirSync(workspace);
  fs.mkdirSync(path.join(root, '.artifacts'), { recursive: true });
  const requests = [];
  let taskRequests = 0;
  const provider = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    requests.push(body);
    const taskRequest = JSON.stringify(body.messages).includes('TASK_WRITE');
    if (taskRequest) taskRequests += 1;
    const toolRequest = requests.length === 1 || (taskRequest && taskRequests <= 2);
    let delta = toolRequest ? {
      reasoning_content: 'Checking the image and creating the requested file.',
      tool_calls: [{ index: 0, id: 'call_browser_' + requests.length, type: 'function', function: {
        name: 'write_file', arguments: JSON.stringify({
          path: taskRequest ? 'task-result.txt' : 'browser-result.txt', content: 'browser verified',
        }),
      }}],
    } : { content: taskRequest ? 'Task smoke complete.' : 'Browser smoke complete.' };
    const attachmentRun = /a_[a-f0-9]{32}/.test(JSON.stringify(body.messages));
    if (attachmentRun) {
      const last = body.messages[body.messages.length - 1];
      if (last.role === 'tool') {
        assert.ok(last.content.includes('7319'), 'the agent must receive real extracted attachment text');
        delta = {content: 'Attachment smoke complete: 7319'};
      } else {
        const match = JSON.stringify(body.messages).match(/a_[a-f0-9]{32}/);
        assert.ok(match, 'attachment id must be available in model context');
        delta = {tool_calls: [{index: 0, id: 'attachment_' + requests.length, type: 'function',
          function: {name: 'read_attachment', arguments: JSON.stringify({attachment_id: match[0]})}}]};
      }
    }
    const frame = (delta, finish_reason = null) => 'data: ' + JSON.stringify({
      id: 'local-smoke', choices: [{ index: 0, delta, finish_reason }],
    }) + '\n\n';
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end(frame(delta) + frame({}, delta.tool_calls ? 'tool_calls' : 'stop') + 'data: [DONE]\n\n');
  });
  let python, browser;
  try {
    await new Promise(resolve => provider.listen(0, '127.0.0.1', resolve));
    fs.writeFileSync(path.join(home, 'config.json'), JSON.stringify({
      providers: [{ id: 'smoke', name: 'Local smoke', family: 'openai_chat_completions',
        base_url: `http://127.0.0.1:${provider.address().port}`,
        models: [{ id: 'fake', context_window_tokens: 32000, max_output_tokens: 1024,
          capabilities: { streaming: true, tools: true, thinking: true, images: true } }] }],
      current: { provider: 'smoke', model: 'fake' }, permission: 'ask', workspace,
    }));
    fs.writeFileSync(path.join(home, 'credentials.json'), JSON.stringify({ smoke: 'fake-local-key' }));
    async function launchNexus() {
    python = spawn(process.env.NEXUS_TEST_PYTHON || 'python', [
      '-u', '-m', 'nexus', '--home', home, '--workspace', workspace, '--port', '0', '--no-browser',
    ], { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    return await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Nexus startup timeout: ' + output)), 15000);
      python.stdout.on('data', data => {
        output += data;
        const match = output.match(/Nexus: (http:\/\/127\.0\.0\.1:\d+)/);
        if (match) { clearTimeout(timeout); resolve(match[1]); }
      });
      python.stderr.on('data', data => { output += data; });
      python.once('error', reject);
      python.once('exit', code => { clearTimeout(timeout); reject(new Error('Nexus exited ' + code + ': ' + output)); });
    });
    }
    let base = await launchNexus();
    browser = await chromium.launch({ headless: true,
      ...(process.env.NEXUS_BROWSER_CHANNEL ? { channel: process.env.NEXUS_BROWSER_CHANNEL } : {}) });
    const page = await browser.newPage({ viewport: { width: 1360, height: 960 } });
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(base);
    await page.locator('#model-label').filter({ hasText: 'fake' }).waitFor();
    // This suite protects the legacy chat path; the default entry is now Ling.
    await page.locator('#new').click();
    await page.waitForFunction(() => !lingView.active && Boolean(state.sessionId));
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aPe4AAAAASUVORK5CYII=', 'base64');
    await page.locator('#image-input').setInputFiles({ name: 'pixel.png', mimeType: 'image/png', buffer: png });
    await page.locator('.attach-chip img').waitFor();
    await page.locator('#send').click();
    await page.locator('#approval-dialog[open]').waitFor();
    await page.locator('#allow').click();
    await page.locator('#transcript .answer').filter({ hasText: 'Browser smoke complete.' }).waitFor();
    await page.locator('#send[aria-label="发送"]').waitFor();
    await page.reload();
    await page.locator('#sessions li').filter({ hasText: '图片' }).click();
    await page.locator('#transcript .answer').filter({ hasText: 'Browser smoke complete.' }).waitFor();
    const thumbnail = page.locator('#transcript .msg.user img');
    await thumbnail.waitFor();
    assert.equal(await thumbnail.evaluate(img => img.complete && img.naturalWidth > 0), true);
    assert.equal(fs.readFileSync(path.join(workspace, 'browser-result.txt'), 'utf8'), 'browser verified');
    assert.equal(requests.length, 2);
    assert.ok(requests[0].messages.some(m => Array.isArray(m.content) && m.content.some(b => b.type === 'image_url')));
    assert.deepEqual(errors, []);
    await page.screenshot({ path: path.join(root, '.artifacts', 'browser-smoke.png'), fullPage: true });

    await page.locator('#task-new').click();
    await page.locator('#task-name').fill('browser task');
    await page.locator('#task-goal').fill('remain available without a model call');
    await page.locator('#task-create').click();
    await page.locator('#task-panel').waitFor();
    assert.ok((await page.locator('#task-status').innerText()).includes('待执行'));
    assert.equal(requests.length, 2, 'creating a task must not invoke a model');

    await page.locator('.task-schedule summary').click();
    await page.locator('#task-due').fill('2030-01-01T12:30');
    await page.waitForTimeout(1800);
    assert.equal(await page.locator('#task-due').inputValue(), '2030-01-01T12:30',
      'task refresh must preserve an unsaved schedule');
    await page.locator('#new').click();
    await page.locator('#task-panel').waitFor({ state: 'hidden' });
    await page.locator('#input').fill('ordinary chat still works');
    await page.locator('#send').click();
    await page.locator('#transcript .answer').filter({ hasText: 'Browser smoke complete.' }).waitFor();
    await page.locator('#send[aria-label="发送"]').waitFor();
    assert.equal(requests.length, 3, 'ordinary text must reach the chat model with task controls loaded');
    await page.reload();
    await page.locator('#tasks li').filter({ hasText: 'browser task' }).click();
    await page.locator('#task-panel').waitFor();
    assert.ok((await page.locator('#task-status').innerText()).includes('待执行'));

    await page.locator('#task-new').click();
    await page.locator('#task-name').fill('approval resume');
    await page.locator('#task-goal').fill('TASK_WRITE: create task-result.txt with approval');
    await page.locator('#task-create').click();
    await page.locator('#task-title').filter({ hasText: 'approval resume' }).waitFor();
    await page.locator('#task-start').click();
    await page.locator('#task-interactions input').waitFor();
    assert.equal(fs.existsSync(path.join(workspace, 'task-result.txt')), false);
    await page.reload();
    await page.locator('#tasks li').filter({ hasText: 'approval resume' }).click();
    await page.locator('#task-interactions input').fill('允许');
    await page.locator('#task-interactions button').click();
    await page.locator('#transcript .answer').filter({ hasText: 'Task smoke complete.' }).waitFor();
    assert.equal(fs.readFileSync(path.join(workspace, 'task-result.txt'), 'utf8'), 'browser verified');
    assert.equal(taskRequests, 3, 'task approval must resume the exact call and continue to its answer');
    assert.ok((await page.locator('#task-status').innerText()).includes('已挂起'));
    await page.screenshot({ path: path.join(root, '.artifacts', 'task-approval-smoke.png'), fullPage: true });

    const fixtures = path.join(temporary, 'fixtures');
    execFileSync(process.env.NEXUS_TEST_PYTHON || 'python', ['tests/attachment_fixtures.py', fixtures],
      {cwd: root, windowsHide: true});
    await page.locator('#new').click();
    await page.locator('#task-panel').waitFor({state: 'hidden'});
    const fileNames = ['notes.md', 'report.docx', 'table.xlsx', 'deck.pptx', 'report.pdf', 'archive.zip'];
    await page.locator('#image-input').setInputFiles(fileNames.map(name => path.join(fixtures, name)));
    assert.equal(await page.locator('#attachments .file-chip').count(), 6);
    await page.locator('#input').fill('');
    await page.locator('#send').click();
    await page.locator('#transcript .answer').filter({hasText: 'Attachment smoke complete: 7319'}).waitFor();
    assert.equal(await page.locator('#transcript .file-link').count(), 6);
    const links = await page.locator('#transcript .file-link').evaluateAll(nodes => nodes.map(node => node.href));
    for (let index = 0; index < links.length; index += 1) {
      const download = await page.request.get(links[index]);
      assert.equal(download.status(), 200);
      assert.deepEqual(await download.body(), fs.readFileSync(path.join(fixtures, fileNames[index])));
    }
    await page.reload();
    await page.locator('#sessions li').filter({hasText: 'notes.md'}).click();
    await page.locator('#transcript .file-link').first().waitFor();
    assert.equal(await page.locator('#transcript .file-link').count(), 6);
    await page.screenshot({path: path.join(root, '.artifacts', 'attachments-chat-smoke.png'), fullPage: true});

    await page.locator('#task-new').click();
    await page.locator('#task-name').fill('attachment task');
    await page.locator('#task-goal').fill('Read the attached evidence');
    await page.locator('#task-create').click();
    await page.locator('#task-title').filter({hasText: 'attachment task'}).waitFor();
    await page.locator('#transcript .file-link').first().waitFor({state: 'detached'});
    await page.locator('#image-input').setInputFiles(path.join(fixtures, 'notes.md'));
    await page.locator('#input').fill('');
    await page.locator('#send').click();
    await page.locator('#transcript .file-link').waitFor();
    await page.locator('#task-start').click();
    await page.locator('#transcript .answer').filter({hasText: 'Attachment smoke complete: 7319'}).waitFor();
    await page.reload();
    await page.locator('#tasks li').filter({hasText: 'attachment task'}).click();
    await page.locator('#task-title').filter({hasText: 'attachment task'}).waitFor();
    await page.locator('#transcript .file-link').first().waitFor();
    assert.equal(await page.locator('#transcript .file-link').count(), 1);
    await page.screenshot({path: path.join(root, '.artifacts', 'attachments-task-smoke.png'), fullPage: true});

    const beforeRestart = requests.length;
    const stopped = new Promise(resolve => python.once('exit', resolve));
    python.kill('SIGINT');
    await stopped;
    base = await launchNexus();
    await page.goto(base);
    await page.locator('#tasks li').filter({hasText: 'attachment task'}).click();
    await page.locator('#task-title').filter({hasText: 'attachment task'}).waitFor();
    await page.locator('#transcript .file-link').first().waitFor();
    const restoredFile = await page.request.get(await page.locator('#transcript .file-link').evaluate(node => node.href));
    assert.deepEqual(await restoredFile.body(), fs.readFileSync(path.join(fixtures, 'notes.md')));
    assert.equal(requests.length, beforeRestart, 'restarting must not execute a paused task');

    const map = await browser.newPage({ viewport: { width: 1360, height: 960 } });
    const external = [];
    map.on('request', r => { if (r.url().startsWith('http')) external.push(r.url()); });
    map.on('pageerror', error => errors.push(String(error)));
    await map.goto(pathToFileURL(path.join(root, 'docs', 'BUSINESS-MAP.html')).href);
    await map.locator('svg').first().waitFor();
    assert.ok((await map.locator('body').innerText()).includes('MCP'));
    const geometry = await map.evaluate(() => {
      const view = document.getElementById('business-map').viewBox.baseVal;
      const cards = [...document.querySelectorAll('#map-nodes .node-card')];
      const problems = [];
      for (const card of cards) {
        const r = card.querySelector('.node-shell').getBBox();
        if (r.x < view.x || r.y < view.y || r.x + r.width > view.x + view.width || r.y + r.height > view.y + view.height) {
          problems.push(card.dataset.id + ': outside viewBox');
        }
        const texts = [...card.querySelectorAll('text')];
        for (const text of texts) {
          const t = text.getBBox();
          if (t.x < r.x - 1 || t.y < r.y - 1 || t.x + t.width > r.x + r.width + 1 || t.y + t.height > r.y + r.height + 1) {
            problems.push(card.dataset.id + ': text outside card');
          }
        }
      }
      return { cards: cards.length, problems };
    });
    assert.ok(geometry.cards > 0);
    assert.deepEqual(geometry.problems, []);
    await map.locator('#search').fill('MCP');
    const filtered = await map.locator('.node-card[aria-hidden="false"]').count();
    assert.ok(filtered > 0 && filtered < geometry.cards);
    await map.locator('#search').fill('');
    await map.locator('.node-card[data-id="extensions"]').click();
    assert.ok((await map.locator('#detail').innerText()).includes('MCP'));
    assert.deepEqual(external, [], 'map should render without network requests');
    assert.deepEqual(errors, []);
    await map.screenshot({ path: path.join(root, '.artifacts', 'business-map.png'), fullPage: true });
    await map.locator('.stage-card').screenshot({ path: path.join(root, '.artifacts', 'business-map-detail.png') });
    console.log('Browser smoke passed: chat/task attachments, six file formats and original downloads, read_attachment calls, refresh and process restart persistence, images, approval and offline map.');
  } finally {
    if (browser) await browser.close();
    if (python && python.exitCode === null) {
      const exited = new Promise(resolve => python.once('exit', resolve));
      python.kill('SIGINT');
      await exited;
    }
    await new Promise(resolve => provider.close(resolve));
    assert.equal(path.dirname(path.resolve(temporary)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(temporary).startsWith('nexus-browser-'));
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
