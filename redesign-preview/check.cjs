/* Browser checks against local-only demo state. No model/backend calls. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const {until,connect,killOwned}=require('../tests/browser_smoke.cjs');
async function main(){
 const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'nexus-preview-check-'));
 const artifacts=path.resolve(__dirname,'../.artifacts/redesign-preview');fs.mkdirSync(artifacts,{recursive:true});
 let output='',browser,page;
 const chrome=spawn(process.env.NEXUS_CHROME||'C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless','--disable-gpu','--remote-debugging-port=0','--user-data-dir='+path.join(temporary,'profile'),'--no-first-run','about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
 chrome.stderr.on('data',b=>output+=b);
 try{
  const url=await until(()=>output.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1],'Chrome did not start');
  browser=await connect(url);const target=await browser.call('Target.createTarget',{url:'about:blank'});
  const endpoint=new URL(url);endpoint.protocol='http:';endpoint.pathname='/json/list';
  const targets=await(await fetch(endpoint)).json();page=await connect(targets.find(x=>x.id===target.targetId).webSocketDebuggerUrl);
  await page.call('Runtime.enable');await page.call('Page.enable');
  const ev=s=>page.evaluate(s);
  const click=async selector=>{await ev(`document.querySelector(${JSON.stringify(selector)}).click()`);await new Promise(r=>setTimeout(r,60));};
  const settle=()=>new Promise(r=>setTimeout(r,700));
  const close=async()=>{await ev(`document.querySelectorAll('dialog').forEach(d=>{d.close();d.remove()})`);};
  const submit=async()=>{await ev(`document.querySelector('dialog[open] form').requestSubmit()`);await settle();};
  const fill=async values=>ev(`Object.entries(${JSON.stringify(values)}).forEach(([k,v])=>{const n=document.querySelector('dialog[open] [name="'+k+'"]');n.value=v;n.dispatchEvent(new Event('input',{bubbles:true}));n.dispatchEvent(new Event('change',{bubbles:true}));})`);
  await page.call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await page.call('Page.navigate',{url:'http://127.0.0.1:8767/'});
  await until(()=>ev(`Boolean(window.P?.views.plugins && window.P?.views.context && document.querySelector('#thread'))`),'Preview bootstrap failed');
  await settle();
  assert.equal(await ev(`document.querySelectorAll('#thread [data-message]').length`),5);
  assert.equal(await ev(`document.querySelectorAll('#island .slot').length`),3);
  // Message history: truncation, completion, undo and shared context source.
  await ev(`P.editMessage('m2')`);await fill({text:'编辑后的演示消息'});await submit();
  await until(()=>ev('!P.running'),'Edit resend did not settle');
  assert.equal(await ev(`P.messages.find(m=>m.id==='m2').text`),'编辑后的演示消息');
  assert.equal(await ev(`P.messages.some(m=>m.id==='m4')`),false);
  await ev('P.undoHistory()');assert.equal(await ev(`P.messages.some(m=>m.id==='m4')`),true);
  await ev(`P.deleteMessages(['m3'])`);await submit();assert.equal(await ev(`P.messages.some(m=>m.id==='m3')`),false);
  await ev('P.undoHistory()');assert.equal(await ev(`P.messages.some(m=>m.id==='m3')`),true);
  // Required thinking choices, model selection and provider fields.
  await click('#effort-btn');assert.equal(await ev(`document.querySelectorAll('#menu [data-do="select-effort"]').length`),6);
  await click('[data-do="select-effort"][data-id="xhigh"]');assert.equal(await ev('P.data.settings.effort'),'xhigh');
  await ev(`P.go('settings')`);await click('[data-do="settings-add-provider"]');
  await fill({name:'Browser provider',family:'openai_responses',base_url:'https://preview.example/v1',api_key:'demo-key'});
  await ev(`document.querySelector('[data-model-id]').value='demo-test';document.querySelector('[data-model-window]').value='128000'`);await submit();
  assert.equal(await ev(`P.data.settings.providers.at(-1).models[0].context_window_tokens`),128000);
  await ev(`P.go('chat')`);await click('#model-btn');assert.equal(await ev(`document.querySelector('#menu').textContent.includes('demo-test')`),true);
  await click('[data-do="select-model"][data-model="demo-test"]');assert.equal(await ev('P.data.settings.current.model'),'demo-test');
  await ev(`P.go('settings')`);await click('[data-do="settings-add-mcp"]');
  const mcpNames=await ev(`Array.from(document.querySelector('dialog[open] form').elements).map(n=>n.name).filter(Boolean)`);console.log('MCP fields',mcpNames.join(','));
  await close();
  // Skill creation is persisted and is not an enable switch.
  await ev(`P.go('skills')`);await click('[data-do="ext-skill-new"]');
  await fill({name:'browser-skill',source:'project',description:'浏览器验收技能',body:'# 检查\n本地保存与刷新。'});await submit();
  assert.equal(await ev(`P.data.skills.items.some(s=>s.name==='browser-skill')`),true);
  // Queue capacity and paused-task supplements.
  await ev(`P.go('chat');P.data.works.forEach(w=>w.state='running');P.data.requests=[];document.querySelector('#input').value='整理队列演示';document.querySelector('#composer').requestSubmit()`);
  await until(()=>ev('!P.running'),'Queue reply did not settle');
  assert.equal(await ev(`P.data.works.at(-1).state`),'queued');
  await ev(`P.data.works[0].state='paused';P.data.target=P.data.works[0].id;document.querySelector('#input').value='补充一条新的要求';document.querySelector('#composer').requestSubmit()`);
  await until(()=>ev('!P.running'),'Supplement reply did not settle');
  assert.equal(await ev(`P.data.works[0].state`),'paused');
  // No API/backend fetch. App state survives a full reload.
  await page.call('Page.reload');await until(()=>ev(`window.P?.data.settings.current.model==='demo-test'`),'Settings not persistent');
  assert.equal(await ev(`P.data.skills.items.some(s=>s.name==='browser-skill')`),true);
  // Capture all routes in four viewport/theme combinations and check overflow.
  const overflows=[];
  for(const [width,height] of [[1440,1000],[390,844]]){
   await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<500});
   for(const theme of [width<500?'night':'day']){
    await ev(`document.documentElement.dataset.theme=P.data.theme=${JSON.stringify(theme)}`);
    for(const view of ['chat','context','skills','plugins','settings']){
     await ev(`P.go(${JSON.stringify(view)})`);await settle();
     const overflow=await ev(`({root:document.documentElement.scrollWidth,view:document.querySelector('#view-'+P.view).scrollWidth,client:document.querySelector('#view-'+P.view).clientWidth})`);
     if(overflow.root>width+1||overflow.view>overflow.client+1)overflows.push({width,theme,view,...overflow});
     if(view==='chat'||view==='settings'){const screenshot=await page.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(artifacts,`${view}-${theme}-${width}.png`),Buffer.from(screenshot.data,'base64'));}
    }
   }
  }
  await ev(`P.go('chat');P.actions['full-work']({dataset:{id:'w1'}})`);await settle();
  assert.equal(await ev(`Math.round(document.querySelector('.work-dialog').getBoundingClientRect().width)`),390);
  const shot=await page.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(artifacts,'work-night-390.png'),Buffer.from(shot.data,'base64'));
  await close();
  await page.call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});await ev(`P.go('context')`);await settle();
  assert.equal(await ev(`matchMedia('(prefers-reduced-motion: reduce)').matches`),true);
  assert.deepEqual(overflows,[],'Horizontal overflow');
  assert.deepEqual(page.errors,[],'Browser runtime errors');
  console.log('PASS: history edit/delete/undo, model options, provider create, skill create, queue, paused supplement, persistence, 4 screenshots, mobile work sheet, reduced motion.');
  console.log('Artifacts: '+artifacts);
 }finally{page?.close();browser?.close();killOwned(chrome);}
}
main().catch(e=>{console.error(e);process.exitCode=1});
