/* Actual browser, Agent loop, task tools and observer; scripted provider.
 * This verifies integration, not the product's real-model autonomy quality. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.NEXUS_PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const root = path.resolve(__dirname, '..');
  fs.mkdirSync(path.join(root,'.artifacts'),{recursive:true});
  const temp = fs.mkdtempSync(path.join(root,'.artifacts','autonomy-ui-'));
  const home = path.join(temp, 'home'), data = path.join(temp, 'data'), out = path.join(temp, 'output');
  for (const dir of [home, data, out]) fs.mkdirSync(dir);
  const source = path.join(data, 'observations.md'), artifact = path.join(out, 'finding.md');
  fs.writeFileSync(source, 'Observation: trial A has value 4. Interpretation is unresolved.\n');
  let calls = 0, taskStep = 0, reviews = 0, output = '';
  const errors = [];
  const provider = http.createServer(async (req, res) => {
    try {
      let raw = ''; for await (const part of req) raw += part;
      const body = JSON.parse(raw); calls++;
      const system = body.messages.filter(m => m.role === 'system').map(m => m.content).join('\n');
      const call = (name, args) => ({tool_calls:[{index:0,id:'qa-'+calls,type:'function',function:{name,arguments:JSON.stringify(args)}}]});
      let delta;
      if (system.includes('bounded private deliberation')) {
        reviews++;
        const material = JSON.parse(body.messages.find(m => m.role === 'user').content.split('\n').slice(1).join('\n'));
        const facts = material.opportunities_and_evidence;
        const existing = material.goals.find(g => g.id === 'open-question');
        const baseSource = existing?.sources?.[0] || facts.find(f => ['dialogue','owner_message'].includes(f.kind))?.id || facts[0].id;
        let decision = {reason:'No new evidence justifies disturbing the owner; keep waiting'};
        if (!existing) {
          decision = {reason:'The discussion left a testable uncertainty',goals:[{id:'open-question',expected_version:0,title:'Resolve the observation',question:'What explains the observed change?',status:'waiting',reason:'The actual discussion and authorized source left an unresolved explanation',understanding:'The current record does not establish a cause',sources:[baseSource]}],
            watches:[{id:'source-watch',expected_version:0,goal_id:'open-question',path:data,reason:'Look for new observations',status:'active'}],
            plans:[{id:'future-review',expected_version:0,goal_id:'open-question',due_at:new Date(Date.now()+8000).toISOString(),reason:'Give the source time to update',status:'pending'}]};
        } else {
          const result = facts.find(f => f.kind === 'work_result' && f.data.state === 'completed');
          const changed = facts.find(f => f.kind === 'source_change' && f.data.observation.kind === 'modified');
          if (result && existing.status !== 'completed') {
            decision = {reason:'Actual work verified the new source and artifact',goals:[{...Object.fromEntries(['id','title','question','reason','sources'].map(k=>[k,existing[k]])),expected_version:existing.version,status:'completed',understanding:'The previous interpretation was incomplete; the new evidence changed the finding',sources:[baseSource,result.id]}],
              message:{text:'资料更新后我重新核对了记录，并保存了有来源的研究结果。原来的解释不足，新的证据已纳入判断。',reason:'A verified result is worth sharing',sources:[result.id]}};
          } else if (changed && !material.actions.length) {
            decision = {reason:'New actual source content warrants bounded investigation',actions:[{key:'investigate-update',goal_id:'open-question',title:'Check the new evidence',goal:'Read the authorized observations.md, save a finding in the output directory, read it back, record verification and finish.',constraints:'Use actual data only.',sources:[changed.id]}]};
          }
        }
        delta = call('autonomy_decide',decision);
      } else if (system.includes('bounded autonomous work task')) {
        const steps = [{content:'Read, save and verify findings',status:'in_progress'}];
        if (taskStep===0) delta=call('task_update',{steps,work_summary:'Reading actual authorized source'});
        else if (taskStep===1) delta=call('read_file',{path:source});
        else if (taskStep===2) delta=call('write_file',{path:artifact,content:'Finding from actual source: '+fs.readFileSync(source,'utf8')});
        else if (taskStep===3) delta=call('read_file',{path:artifact});
        else if (taskStep===4) delta=call('task_evidence',{kind:'verification',source:'read_file',summary:'Read back the actual finding: '+fs.readFileSync(artifact,'utf8'),target:artifact,ok:true});
        else if (taskStep===5) delta=call('task_update',{steps:[{content:steps[0].content,status:'completed',verification:'verified',evidence:['1']}],work_summary:'Actual updated source has been read, artifact saved and verified'});
        else if (taskStep===6) delta=call('task_finish',{summary:'Verified actual source and result',evidence_ids:['1'],unresolved:[]});
        else delta={content:'Work finished with actual evidence.'};
        taskStep++;
      } else delta={content:'记下了这次交流；当前资料仍有未解的问题。'};
      const response={choices:[{index:0,delta,finish_reason:delta.tool_calls?'tool_calls':'stop'}],usage:{prompt_tokens:200,completion_tokens:100,total_tokens:300}};
      res.writeHead(200,{'content-type':'text/event-stream'});
      res.end('data: '+JSON.stringify(response)+'\n\ndata: [DONE]\n\n');
    } catch(error) {res.writeHead(500);res.end(String(error.stack));}
  });
  await new Promise(resolve=>provider.listen(0,'127.0.0.1',resolve));
  fs.writeFileSync(path.join(home,'config.json'),JSON.stringify({providers:[{id:'local',name:'Scripted integration provider',family:'openai_chat_completions',base_url:'http://127.0.0.1:'+provider.address().port+'/v1',models:[{id:'integration',max_output_tokens:2048,context_window_tokens:64000,capabilities:{streaming:true,tools:true,thinking:false}}]}],current:{provider:'local',model:'integration'},effort:'off',workspace:temp,permission:'ask'}));
  fs.writeFileSync(path.join(home,'credentials.json'),JSON.stringify({local:'fake-local-key'}));
  let child,browser;
  try {
    child=spawn(process.env.NEXUS_TEST_PYTHON||'python',['-u','-m','nexus','--home',home,'--workspace',temp,'--port','0','--no-browser'],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
    const base=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('startup '+output)),15000);
      child.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/Nexus: (http:\/\/127\.0\.0\.1:\d+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
      child.stderr.on('data',chunk=>{output+=chunk;}); child.once('error',reject);
    });
    browser=await chromium.launch({headless:true,...(process.env.NEXUS_BROWSER_CHANNEL?{channel:process.env.NEXUS_BROWSER_CHANNEL}:{})});
    const context=await browser.newContext({viewport:{width:1440,height:1050}});
    let page=await context.newPage();
    page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(base);
    await page.locator('#input').fill('我对试验的解释仍有疑问，资料已放在允许读取的目录中；现在先不指定新的工作目标。');
    await page.locator('#send').click();
    await page.locator('#transcript .answer').filter({hasText:'记下了这次交流'}).waitFor();
    const posted=await page.request.post(base+'/api/autonomy/settings',{data:{read_roots:[data],output_root:out,token_limit:200000,max_output_tokens:2048,fallback_seconds:3,contact_after_seconds:1,observer_interval:.1,min_review_seconds:1}});
    assert.equal(posted.status(),200,await posted.text());
    await page.locator('#autonomy-toggle').click();
    await page.locator('#autonomy-refresh').click();
    await page.locator('#autonomy-enable').click();
    await page.waitForFunction(()=>autonomyView.snapshot?.goals.some(g=>g.id==='open-question'),null,{timeout:20000});
    assert.equal((await (await page.request.get(base+'/api/autonomy')).json()).messages.length,0,'no fixed greeting');
    await page.waitForFunction(()=>autonomyView.snapshot?.watches.some(w=>w.actual),null,{timeout:15000});
    await page.close();
    fs.writeFileSync(source,'Observation: trial A has value 4; trial B, independently repeated, has value 9. The earlier explanation does not cover B.\n');
    const deadline=Date.now()+40000;
    let offlineMessage=false;
    while(Date.now()<deadline) {
      const snapshot=await (await context.request.get(base+'/api/autonomy')).json();
      if(snapshot.messages.length>0) {offlineMessage=true;break;}
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    assert.ok(offlineMessage,'server completes work and saves contact with no product page open');
    page=await context.newPage();
    page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(base);
    await page.locator('#autonomy-toggle').click();
    await page.waitForFunction(()=>autonomyView.snapshot?.messages.length>0);
    assert.ok(fs.existsSync(artifact));
    assert.match(fs.readFileSync(artifact,'utf8'),/value 9/);
    assert.ok((await (await page.request.get(base+'/api/ling')).json()).items.some(m=>m.autonomy_message_id));
    await page.locator('#autonomy-pause').click();
    await page.waitForFunction(()=>autonomyView.snapshot?.control.state==='paused');
    const stoppedCalls=calls;
    await page.reload();
    await page.locator('#autonomy-toggle').click();
    await page.waitForFunction(()=>autonomyView.snapshot?.messages.length>0);
    assert.equal(await page.evaluate(()=>autonomyView.snapshot.control.state),'paused');
    const evidenceDir=process.env.NEXUS_EVIDENCE_DIR||path.join(root,'.artifacts','autonomy-browser');
    fs.mkdirSync(evidenceDir,{recursive:true});
    await page.screenshot({path:path.join(evidenceDir,'autonomy-ui.png'),fullPage:true});
    const sourceDetail=page.locator('#autonomy-messages .autonomy-source-event').first();
    await sourceDetail.locator('summary').click();
    await sourceDetail.locator('.autonomy-source-content').waitFor();
    assert.match(await sourceDetail.locator('.autonomy-source-content').innerText(),/task_id/);
    await page.locator('#autonomy-messages').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(evidenceDir,'autonomy-message-source.png'),fullPage:true});
    await page.waitForTimeout(1300);
    assert.equal(calls,stoppedCalls,'paused autonomy cannot make additional model calls');
    assert.deepEqual(errors,[]);
    const snapshot=await (await page.request.get(base+'/api/autonomy')).json();
    assert.equal(snapshot.observer.error,'','normal pause is not an observer failure');
    fs.writeFileSync(path.join(evidenceDir,'summary.json'),JSON.stringify({provider:'scripted; integration only',calls,reviews,taskStep,goalStates:snapshot.goals.map(g=>({id:g.id,status:g.status})),messageCount:snapshot.messages.length,pageClosedActivity:offlineMessage,sourceViewer:true,budget:snapshot.budget,browserErrors:errors},null,2));
    console.log(JSON.stringify({result:'PASS',provider:'scripted integration only',calls,reviews,artifact:true,pageClosedActivity:offlineMessage,persistentMessage:true,pause:true,browserErrors:errors}));
  } finally {
    if(browser) await browser.close();
    if(child&&child.exitCode===null) {child.kill();await new Promise(resolve=>child.once('exit',resolve));}
    await new Promise(resolve=>provider.close(resolve));
  }
}
main().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
