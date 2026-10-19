/* Real browser + real Nexus task runtime, isolated local model and data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.NEXUS_PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const root = path.resolve(__dirname, '..');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-continuity-'));
  const home = path.join(temp, 'home'), workspace = path.join(temp, 'workspace');
  fs.mkdirSync(home); fs.mkdirSync(workspace);
  const output = path.join(workspace, 'continuity-result.txt');
  let taskStage = 0, evidence = '1', requests = 0, processOutput = '';
  let releaseInitialWork;
  const initialWorkGate = new Promise(resolve => { releaseInitialWork = resolve; });
  const provider = http.createServer(async (req, res) => {
    try {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const body = JSON.parse(raw); requests += 1;
      const messages = body.messages;
      const system = messages.filter(m => m.role === 'system').map(m => m.content).join('\n');
      const last = messages[messages.length - 1];
      let delta;
      const call = (name, args) => ({tool_calls:[{index:0,id:'browser-'+requests,type:'function',
        function:{name,arguments:JSON.stringify(args)}}]});
      if (system.includes('# Continuing interaction')) {
        if (last.role === 'tool') delta = {content:'工作已独立进行，我仍可交流。'};
        else if (String(last.content).includes('DELEGATE')) {
          delta = call('work_start', {title:'持续性验证工作',goal:'LONG_WORK: write and verify continuity-result.txt, record evidence and finish.'});
        } else delta = {content:'普通交流正常，证据值7319。'};
      } else if (system.includes('LONG_WORK')) {
        if (taskStage === 0) {
          delta = call('task_update', {steps:[{content:'write and verify output',status:'in_progress'}],work_summary:'开始独立工作'});
          taskStage = 1;
          await initialWorkGate;
        } else if (taskStage === 1 && !fs.existsSync(output)) {
          delta = call('write_file', {path:'continuity-result.txt',content:'continuity verified 7319'});
        } else if (taskStage === 1) {
          delta = call('read_file', {path:'continuity-result.txt'}); taskStage = 2;
        } else if (taskStage === 2) {
          assert.equal(fs.readFileSync(output,'utf8'),'continuity verified 7319');
          delta = call('task_evidence', {kind:'verification',source:'read_file',summary:'read actual result: continuity verified 7319',target:'continuity-result.txt',ok:true}); taskStage = 3;
        } else if (taskStage === 3) {
          const snapshot = system.match(/Evidence: (\[.*\])/);
          assert.ok(snapshot, 'updated system task state exposes saved evidence');
          evidence = String(JSON.parse(snapshot[1]).length);
          delta = call('task_update', {steps:[{content:'write and verify output',status:'completed',verification:'verified',evidence:[evidence]}],work_summary:'已读取并验证产物'}); taskStage = 4;
        } else if (taskStage === 4) {
          delta = call('task_finish', {summary:'工作完成，文件验证值7319。',evidence_ids:[evidence],unresolved:[]}); taskStage = 5;
        } else delta = {content:'工作完成。'};
      } else delta = {content:'普通会话正常。'};
      const frame = (d, finish) => 'data: '+JSON.stringify({id:'continuity',choices:[{index:0,delta:d,finish_reason:finish}]})+'\n\n';
      res.writeHead(200,{'Content-Type':'text/event-stream'});
      res.end(frame(delta,null)+frame({},delta.tool_calls?'tool_calls':'stop')+'data: [DONE]\n\n');
    } catch(error) { res.writeHead(500); res.end(String(error.stack)); }
  });
  let python, browser;
  async function launch() {
    processOutput = '';
    python = spawn(process.env.NEXUS_TEST_PYTHON || 'python',['-u','-m','nexus','--home',home,'--workspace',workspace,'--port','0','--no-browser'],
      {cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
    return await new Promise((resolve,reject) => {
      const timer = setTimeout(()=>reject(new Error('startup timeout '+processOutput)),15000);
      python.stdout.on('data',data=>{processOutput+=data; const match=processOutput.match(/Nexus: (http:\/\/127\.0\.0\.1:\d+)/); if(match){clearTimeout(timer);resolve(match[1]);}});
      python.stderr.on('data',data=>{processOutput+=data;});
      python.once('error',reject);
      python.once('exit',code=>{clearTimeout(timer);reject(new Error('Nexus exit '+code+' '+processOutput));});
    });
  }
  async function stop() {
    if (!python || python.exitCode !== null) return;
    const child=python; await new Promise(resolve=>{child.once('exit',resolve);child.kill();});
    python=null;
  }
  try {
    await new Promise(resolve=>provider.listen(0,'127.0.0.1',resolve));
    fs.writeFileSync(path.join(home,'config.json'),JSON.stringify({providers:[{id:'local',name:'Local test',family:'openai_chat_completions',base_url:`http://127.0.0.1:${provider.address().port}`,models:[{id:'fake',context_window_tokens:64000,max_output_tokens:1024,capabilities:{streaming:true,tools:true,images:true}}]}],current:{provider:'local',model:'fake'},permission:'ask',workspace}));
    fs.writeFileSync(path.join(home,'credentials.json'),JSON.stringify({local:'fake-local-key'}));
    let base=await launch();
    browser=await chromium.launch({headless:true,...(process.env.NEXUS_BROWSER_CHANNEL?{channel:process.env.NEXUS_BROWSER_CHANNEL}:{})});
    const page=await browser.newPage({viewport:{width:1360,height:960}});
    const errors=[]; page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(base);
    await page.locator('#ling-mode-label').waitFor();
    const identity=(await (await page.request.get(base+'/api/ling')).json()).session_id;
    await page.locator('#input').fill('DELEGATE 此工作独立进行，我还要继续交流');
    await page.locator('#send').click();
    await page.locator('#transcript .answer').filter({hasText:'工作已独立进行'}).waitFor();
    console.log('browser: independent delegation replied');
    await page.waitForFunction(()=>lingView.tasks.some(task=>task.state==='running'));
    assert.equal(fs.existsSync(output),false,'task writes need explicit approval');
    await page.locator('#input').fill('现在聊聊无关问题');
    await page.locator('#send').click();
    await page.locator('#transcript .answer').filter({hasText:'普通交流正常'}).waitFor();
    const running=(await (await page.request.get(base+'/api/tasks')).json()).tasks[0];
    assert.equal(running.state,'running','a held task model request must not block Ling conversation');
    console.log('browser: unrelated conversation while task model request remains running');
    releaseInitialWork();
    await page.waitForFunction(()=>lingView.tasks.some(task=>task.state==='waiting_user'));
    await page.locator('#notification-region').waitFor();
    await page.locator('.notification-handle').waitFor();
    let tasks=(await (await page.request.get(base+'/api/tasks')).json()).tasks;
    const task=tasks[0]; assert.equal(task.state,'waiting_user');
    const before=(await (await page.request.get(base+'/api/tasks/'+task.id)).json()).pending_interactions[0];
    const message=await page.request.post(base+'/api/tasks/'+task.id+'/messages',{data:{text:'保留当前输出格式，继续原任务',request_id:'while-approval'}});
    assert.equal(message.status(),200);
    const after=(await (await page.request.get(base+'/api/tasks/'+task.id)).json()).pending_interactions[0];
    assert.equal(after.id,before.id); assert.equal(after.control_version,before.control_version);
    await page.reload();
    await page.locator('#ling-mode-label').waitFor();
    assert.equal((await (await page.request.get(base+'/api/ling')).json()).session_id,identity);
    // Offline task decision remains persisted across a real service restart.
    await stop(); base=await launch(); await page.goto(base);
    console.log('browser: service restarted for offline decision backfill');
    await page.locator('#ling-mode-label').waitFor();
    await page.locator('#notification-count').filter({hasText:/[1-9]/}).waitFor();
    await page.locator('#input').fill('未发送的日常交流草稿');
    const decisionFeed=(await (await page.request.get(base+'/api/notifications')).json()).notifications;
    assert.ok(decisionFeed.some(item=>item.interaction_id), 'saved decision must have an actionable notification');
    console.log('browser: decision feed',JSON.stringify(decisionFeed.map(item=>({id:item.id,kind:item.kind,interaction:!!item.interaction_id}))));
    if (!(await page.locator('#notification-region').isVisible())) await page.locator('#notifications-toggle').click();
    await page.getByRole('button',{name:'处理请求',exact:true}).first().click();
    await page.getByRole('button',{name:'允许并继续',exact:true}).last().click();
    console.log('browser: decision answered in notification');
    try {
      await page.waitForFunction(()=>lingView.tasks.some(task=>['completed','paused'].includes(task.state)),null,{timeout:30000});
      const finalTask=await (await page.request.get(base+'/api/tasks/'+task.id)).json();
      assert.equal(finalTask.state,'completed');
    } catch (error) {
      const detail=await (await page.request.get(base+'/api/tasks/'+task.id)).json();
      console.error('completion diagnostic',JSON.stringify({taskStage,requests,state:detail.state,blocked:detail.blocked,
        reason:detail.wait_reason,pending:detail.pending_interactions,unknown:detail.unfinished_operations,
        messages:(detail.messages||[]).slice(-10).map(m=>({kind:m.kind,name:m.name,text:m.text?.slice(0,1400)}))}));
      throw error;
    }
    assert.equal(await page.locator('#input').inputValue(),'未发送的日常交流草稿');
    assert.equal(fs.readFileSync(output,'utf8'),'continuity verified 7319');
    await page.locator('.notification-text').filter({hasText:'工作完成'}).waitFor();
    const notices=(await (await page.request.get(base+'/api/notifications')).json()).notifications;
    assert.equal(notices.filter(item=>item.kind==='completed').length,1);
    await page.reload(); await page.locator('#ling-mode-label').waitFor();
    const retained=(await (await page.request.get(base+'/api/notifications')).json()).notifications;
    assert.equal(retained.filter(item=>item.kind==='completed').length,1);
    assert.equal((await (await page.request.get(base+'/api/ling')).json()).session_id,identity);
    await page.locator('#new').click();
    await page.locator('#input').fill('legacy question'); await page.locator('#send').click();
    await page.locator('#transcript .answer').filter({hasText:'普通会话正常'}).waitFor();
    await page.locator('#ling-home').click();
    await page.locator('#transcript .answer').filter({hasText:'普通交流正常'}).waitFor();
    assert.deepEqual(errors,[]);
    fs.mkdirSync(path.join(root,'.artifacts'),{recursive:true});
    await page.screenshot({path:path.join(root,'.artifacts','continuity-smoke.png'),fullPage:true});
    console.log('Continuity browser passed: delegation, ordinary conversation, durable decision, message while waiting, restart/backfill, user approval, verified completion, single notification, draft/history and legacy chat.');
  } finally {
    releaseInitialWork();
    if(browser) await browser.close(); await stop();
    await new Promise(resolve=>provider.close(resolve));
    assert.equal(path.dirname(path.resolve(temp)),path.resolve(os.tmpdir()));
    assert.ok(path.basename(temp).startsWith('nexus-continuity-'));
    fs.rmSync(temp,{recursive:true,force:true});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
