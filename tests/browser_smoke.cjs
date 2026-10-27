/* Real Chrome + Nexus, isolated data and local fake provider. No npm packages.
 * Node 22+ (built-in WebSocket), NEXUS_CHROME / NEXUS_TEST_PYTHON override paths.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {spawn, execFileSync} = require('node:child_process');

async function until(check, message, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((resolve, reject) => { ws.addEventListener('open', resolve, {once:true}); ws.addEventListener('error', reject, {once:true}); });
  let id = 0;
  const pending = new Map(), errors = [];
  ws.addEventListener('message', event => {
    const value = JSON.parse(String(event.data));
    if (value.method === 'Runtime.exceptionThrown') errors.push(value.params.exceptionDetails);
    if (value.id && pending.has(value.id)) {
      const {resolve, reject, timer} = pending.get(value.id); pending.delete(value.id); clearTimeout(timer);
      value.error ? reject(new Error(JSON.stringify(value.error))) : resolve(value.result);
    }
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const current = ++id;
    const timer = setTimeout(() => { pending.delete(current); reject(new Error('CDP timeout: '+method)); }, 15000);
    pending.set(current, {resolve,reject,timer}); ws.send(JSON.stringify({id:current,method,params}));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', {expression,returnByValue:true,awaitPromise:true});
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  return {call,evaluate,errors,close:()=>ws.close()};
}

function killOwned(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    try { execFileSync('taskkill', ['/PID',String(child.pid),'/T','/F'], {windowsHide:true,stdio:'ignore'}); }
    catch (error) { if (!String(error.message).includes('taskkill')) throw error; }
  } else child.kill('SIGTERM');
}

async function main() {
  const root = path.resolve(__dirname,'..');
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(),'nexus-convergence-browser-'));
  const home = path.join(temporary,'home'), workspace = path.join(temporary,'workspace');
  fs.mkdirSync(home); fs.mkdirSync(workspace);
  const artifact = path.join(workspace,'browser-result.txt');
  let requests = 0, reads = 0, output = '', chromeOutput = '', python, chrome, page, browser;
  const provider = http.createServer(async (req,res) => {
    try {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const body = JSON.parse(raw); requests++;
      const messages = body.messages, last = messages.at(-1);
      const system = messages.filter(m=>m.role==='system').map(m=>m.content).join('\n');
      const call = (name,args) => ({tool_calls:[{index:0,id:'browser-'+requests,type:'function',function:{name,arguments:JSON.stringify(args)}}]});
      let delta;
      if (system.includes('# Continuing interaction')) {
        if (last.role === 'tool') delta = {reasoning_content:'Work accepted independently.',content:'工作已接收，主交流仍可聊。'};
        else if (String(last.content) === 'DELEGATE') delta = call('work_start',{title:'Browser work',goal:'BROWSER_WORK: write browser-result.txt with browser verified 7319, read it and finish.'});
        else if (String(last.content).includes('[Work event;')) delta = {content:String(last.content).includes('"kind": "completed"') ? '后台工作已完成7319，文件已读回验证。' : '这项工作需要你批准写入，可以继续聊其他话题。'};
        else delta = {reasoning_content:'Checking the new topic independently.',content:'主交流正常7319。'};
      } else if (system.includes('BROWSER_WORK')) {
        if (!fs.existsSync(artifact)) delta = call('write_file',{path:'browser-result.txt',content:'browser verified 7319'});
        else if (!reads) { reads++; delta = call('read_file',{path:'browser-result.txt'}); }
        else { assert.ok(JSON.stringify(messages).includes('browser verified 7319')); delta = call('task_finish',{summary:'工作完成7319；已读回实际文件。'}); }
      } else delta = {content:'ready'};
      const frame = (value,finish=null) => 'data: '+JSON.stringify({choices:[{index:0,delta:value,finish_reason:finish}]})+'\n\n';
      res.writeHead(200,{'Content-Type':'text/event-stream'});
      res.end(frame(delta)+frame({},delta.tool_calls?'tool_calls':'stop')+'data: [DONE]\n\n');
    } catch (error) { res.writeHead(500); res.end(String(error.stack)); }
  });
  try {
    await new Promise(resolve=>provider.listen(0,'127.0.0.1',resolve));
    fs.writeFileSync(path.join(home,'config.json'),JSON.stringify({providers:[{id:'local',name:'Browser test',family:'openai_chat_completions',base_url:`http://127.0.0.1:${provider.address().port}`,models:[{id:'fake',max_output_tokens:1024,context_window_tokens:32000,capabilities:{tools:true,images:true,thinking:true}}]}],current:{provider:'local',model:'fake'},permission:'ask',workspace,effort:'off',max_steps:40}));
    fs.writeFileSync(path.join(home,'credentials.json'),JSON.stringify({local:'fake-browser-key'}));
    python = spawn(process.env.NEXUS_TEST_PYTHON || path.join(root,'.venv','Scripts','python.exe'),['-u','-m','nexus','--home',home,'--workspace',workspace,'--port','0','--no-browser'],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
    python.stdout.on('data',data=>{output+=data}); python.stderr.on('data',data=>{output+=data});
    const base = await until(()=>{ const match=output.match(/Nexus: (http:\/\/127\.0\.0\.1:\d+)/); if(python.exitCode!==null)throw new Error(output); return match&&match[1]; },'Nexus startup timeout: '+output);
    const chromePath = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
    chrome = spawn(chromePath,['--headless','--disable-gpu','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--user-data-dir='+path.join(temporary,'chrome'),'--no-first-run','--no-default-browser-check','about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
    chrome.stderr.on('data',data=>{chromeOutput+=data});
    const browserUrl = await until(()=>{const match=chromeOutput.match(/DevTools listening on (ws:\/\/127\.0\.0\.1:\d+\/devtools\/browser\/[^\s]+)/);if(chrome.exitCode!==null)throw new Error(chromeOutput);return match&&match[1]},'Chrome startup timeout');
    browser = await connect(browserUrl);
    const target = await browser.call('Target.createTarget',{url:'about:blank'});
    const endpoint = new URL(browserUrl); endpoint.protocol='http:'; endpoint.pathname='/json/list';
    const targets = await (await fetch(endpoint)).json();
    page = await connect(targets.find(item=>item.id===target.targetId).webSocketDebuggerUrl);
    await page.call('Runtime.enable'); await page.call('Page.enable');
    await page.call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
    await page.call('Page.navigate',{url:base});
    await until(()=>page.evaluate('Boolean(document.querySelector("#input") && typeof lingView !== "undefined" && lingView.sessionId)'),'Main UI not initialized');
    assert.equal(await page.evaluate('Boolean(document.querySelector("#new") || document.querySelector("#sessions") || document.querySelector("#persona-open"))'),false);
    const sessionId = await page.evaluate('lingView.sessionId');
    await page.evaluate('document.querySelector("#settings").click()');
    await until(()=>page.evaluate('document.querySelector("#settings-dialog").open'),'Settings did not open');
    await page.evaluate('document.querySelector("#instructions").value="Browser owner principle"; document.querySelector("#save-settings").click()');
    await until(()=>fs.existsSync(path.join(home,'owner-instructions.txt')),'Instructions did not save');
    assert.equal(fs.readFileSync(path.join(home,'owner-instructions.txt'),'utf8'),'Browser owner principle');
    const send = text => page.evaluate('document.querySelector("#input").value='+JSON.stringify(text)+'; document.querySelector("#input").dispatchEvent(new Event("input",{bubbles:true})); document.querySelector("#composer").requestSubmit()');
    await send('DELEGATE');
    await until(()=>page.evaluate('Boolean(document.querySelector(".ling-approval-allow"))'),'Work approval missing');
    assert.equal(fs.existsSync(artifact),false);
    await send('NORMAL_CHAT');
    await until(()=>page.evaluate('document.querySelector("#transcript").textContent.includes("主交流正常7319")'),'Pending approval blocked ordinary chat');
    await page.evaluate('document.querySelector("#input").value="保存这个草稿"; document.querySelector("#input").dispatchEvent(new Event("input",{bubbles:true})); document.querySelector("#tasks li").click()');
    await until(()=>page.evaluate('!document.querySelector("#task-panel").hidden'),'Work detail missing');
    assert.equal(await page.evaluate('document.querySelectorAll("#task-panel textarea, #task-panel input").length'),0);
    assert.equal(await page.evaluate('document.querySelector("#input").value'),'保存这个草稿');
    await page.evaluate('document.querySelector("#task-close").click(); document.querySelector(".ling-approval-allow").click()');
    await until(()=>fs.existsSync(artifact),'Approved work did not write');
    assert.equal(fs.readFileSync(artifact,'utf8'),'browser verified 7319');
    await until(()=>page.evaluate('document.querySelector("#transcript").textContent.includes("后台工作已完成7319")'),'Ling result was not delivered');
    const snapshot = await (await fetch(base+'/api/ling')).json();
    assert.equal(snapshot.tasks[0].state,'completed'); assert.equal(snapshot.tasks[0].pending_interactions.length,0);
    await until(async()=>{const value=await(await fetch(base+'/api/ling')).json();return !value.running&&!value.queue_count},'Main queue did not settle');
    const count = requests;
    await page.call('Page.reload');
    await until(()=>page.evaluate('typeof lingView !== "undefined" && lingView.sessionId === '+JSON.stringify(sessionId)+' && document.querySelector("#transcript").textContent.includes("后台工作已完成7319")'),'Reload lost the main conversation or result');
    await new Promise(resolve=>setTimeout(resolve,500)); assert.equal(requests,count,'Reload repeated a model action');
    assert.equal(page.errors.length,0,JSON.stringify(page.errors));
    fs.mkdirSync(path.join(root,'.artifacts'),{recursive:true});
    const screenshot = await page.call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    fs.writeFileSync(path.join(root,'.artifacts','convergence-browser.png'),Buffer.from(screenshot.data,'base64'));
    console.log(JSON.stringify({browser:'Chrome headless',mainSessionRetained:true,ownerInstructionsSaved:true,approvalDidNotBlockChat:true,workDetailReadOnly:true,draftRetained:true,artifactVerified:true,lingResultSaved:true,reloadDidNotRepeat:true,requests,pageErrors:0}));
  } finally {
    if (page) page.close();
    if (browser) { try {await browser.call('Browser.close')} catch {} browser.close(); }
    killOwned(chrome); killOwned(python);
    provider.closeAllConnections(); await new Promise(resolve=>provider.close(resolve));
    const resolved=fs.realpathSync(temporary);
    assert.ok(resolved.startsWith(fs.realpathSync(os.tmpdir())+path.sep) && path.basename(resolved).startsWith('nexus-convergence-browser-'));
    fs.rmSync(resolved,{recursive:true,force:true,maxRetries:3,retryDelay:100});
  }
}
main().catch(error=>{console.error(error.stack);process.exitCode=1});
