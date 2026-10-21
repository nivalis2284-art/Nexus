/* Observe a running real-model fixture server. No model response is scripted. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.NEXUS_PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const url = process.argv[2], out = path.resolve(process.argv[3]);
  fs.mkdirSync(out, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:process.env.NEXUS_BROWSER_CHANNEL || 'msedge'});
  const errors=[];
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1050}});
    page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(url);
    await page.locator('#autonomy-toggle').click();
    await page.waitForFunction(()=>autonomyView.snapshot);
    await page.screenshot({path:path.join(out,'real-goals.png'),fullPage:true});
    const snapshot=await (await page.request.get(url+'/api/autonomy')).json();
    if(snapshot.messages.length) {
      await page.locator('#autonomy-messages').scrollIntoViewIfNeeded();
      const row=process.argv.includes('--latest')
        ? page.locator('#autonomy-messages .autonomy-message').last()
        : page.locator('#autonomy-messages .autonomy-message').first();
      const source=row.locator('.autonomy-source-event').first();
      await source.locator('summary').click();
      await source.locator('.autonomy-source-content').waitFor();
      await row.scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(out,'real-message-source.png'),fullPage:true});
      assert.ok((await (await page.request.get(url+'/api/ling')).json()).items.some(m=>m.autonomy_message_id));
    }
    if(process.argv.includes('--pause')) {
      await page.locator('#autonomy-pause').click();
      await page.waitForFunction(()=>autonomyView.snapshot?.control.state==='paused');
      await page.screenshot({path:path.join(out,'real-paused.png'),fullPage:true});
    }
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify({realModel:true,
      goals:snapshot.goals.length,messages:snapshot.messages.length,plans:snapshot.plans.length,
      actions:snapshot.actions.length,errors},null,2));
    console.log(JSON.stringify({realModel:true,goals:snapshot.goals.length,messages:snapshot.messages.length,
      plans:snapshot.plans.length,actions:snapshot.actions.length,errors}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
