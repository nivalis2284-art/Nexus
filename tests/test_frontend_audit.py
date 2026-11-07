"""Regressions reproduced during the frontend review on 2026-11-04."""
import pytest

from test_http import live as live
from nexus.model.request_history import RequestHistory
from nexus.plugins import PluginManager
from nexus.web import server as web

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def frontend(live, tmp_path, request):
    base, _, store, _, _ = live
    manager = PluginManager(web.STATE, root=tmp_path / "plugins")
    web.STATE.update(plugins=manager, request_history=RequestHistory(store.home / "requests.sqlite3"))
    manager.start(base)
    request.addfinalizer(manager.close)
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(6000)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("https://fonts.googleapis.com/**", lambda route: route.fulfill(body=""))
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_function("N.state.sessionId && N.state.connected")
        yield page
        browser.close()
        assert not errors, errors


def test_island_immediate_close_releases_scrim_and_keyboard(frontend):
    page = frontend
    opacity = page.evaluate("""()=>{
      N.morphIsland('overview');const inner=document.querySelector('#island-inner');
      inner._shapeMotion.pause();inner._shapeMotion.currentTime=0;
      const before=+getComputedStyle(inner).opacity;N.morphIsland('overview');
      const ghost=document.querySelector('.island-ghost');
      return {before,after:+ghost.getAnimations()[0].effect.getKeyframes()[0].opacity};
    }""")
    assert opacity == {'before': 0, 'after': 0}, opacity
    state = page.evaluate("""async()=>{
      N.morphIsland('overview');N.morphIsland('compact');
      await new Promise(r=>setTimeout(r,650));
      const scrim=document.querySelector('#island-scrim');
      return {hidden:scrim.hidden,shown:scrim.classList.contains('show'),inert:document.querySelector('#stage').inert};
    }""")
    assert state == {"hidden": True, "shown": False, "inert": False}, state
    page.locator('#input').fill('keyboard remains available')
    assert page.locator('#input').input_value() == 'keyboard remains available'
    page.evaluate("N.morphIsland('overview')")
    page.keyboard.press('Shift+Tab')
    assert page.evaluate('document.querySelector("#island-inner").contains(document.activeElement)')
    page.keyboard.press('Tab')
    assert page.evaluate('document.activeElement===document.querySelector("#island-inner button")')
    page.keyboard.press('Escape')
    assert page.locator('#input').evaluate('e=>e===document.activeElement')


def test_overlay_exit_file_order_keyboard_and_usage(frontend):
    page = frontend
    samples = page.evaluate("""async()=>{
      const d=document.querySelector('#provider-dialog');N.openDialog(d);
      await new Promise(r=>setTimeout(r,900));N.closeDialog(d);
      const animations=d.getAnimations({subtree:true});animations.forEach(a=>a.pause());
      const samples=[];
      for(let i=0;i<=40;i++){
        animations.forEach(a=>a.currentTime=220*i/40);
        const s=getComputedStyle(d),m=new DOMMatrixReadOnly(s.transform);
        samples.push({scale:m.m11,y:m.m42,opacity:+s.opacity});
      }
      animations.forEach(a=>a.finish());return samples;
    }""")
    for key, direction in [("scale", -1), ("y", 1), ("opacity", -1)]:
        values = [s[key] * direction for s in samples]
        assert all(a <= b + .0001 for a, b in zip(values, values[1:])), (key, samples)
    page.wait_for_function('!document.querySelector("#provider-dialog").open')
    file = page.evaluate("""async()=>{
      const get=N.api.get,reads={};N.api.get=url=>new Promise(r=>reads[new URL(url,location.href).searchParams.get('path')]=r);
      const a=N.openFile('A.txt'),b=N.openFile('B.txt');
      reads['B.txt']({path:'B.txt',text:'FILE_B'});await b;
      reads['A.txt']({path:'A.txt',text:'FILE_A'});await a;
      const result=[document.querySelector('#fp-title').textContent,document.querySelector('#fp-body').textContent];
      const c=N.openFile('C.txt');N.openImage('data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==','IMAGE');
      reads['C.txt']({path:'C.txt',text:'FILE_C'});await c;
      result.push(document.querySelector('#fp-title').textContent,!!document.querySelector('#fp-body img'));
      N.api.get=get;N.closeDialog(document.querySelector('#file-preview'));return result;
    }""")
    assert file == ["B.txt", "FILE_B", "IMAGE", True], file
    page.wait_for_function('!document.querySelector("#file-preview").open')
    page.evaluate("N.menu(document.querySelector('#model-btn'),[{label:'One'},{label:'Two',on:true},{label:'Three'}])")
    assert page.locator("#menu button").nth(1).evaluate("e=>e===document.activeElement")
    page.keyboard.press("ArrowDown")
    assert page.locator("#menu button").nth(2).evaluate("e=>e===document.activeElement")
    page.keyboard.press("Home")
    assert page.locator("#menu button").nth(0).evaluate("e=>e===document.activeElement")
    page.keyboard.press("Escape")
    assert page.locator("#model-btn").evaluate("e=>e===document.activeElement")
    page.evaluate("N.menu(document.querySelector('#model-btn'),[{label:'One'}])")
    page.keyboard.press("Tab")
    assert page.locator("#effort-btn").evaluate("e=>e===document.activeElement")
    usage = page.evaluate("""async()=>{
      const anchor=document.querySelector('#stat-tokens'),panel=document.querySelector('#usage-panel');
      N.toggleUsage(anchor);await new Promise(r=>setTimeout(r,500));N.toggleUsage(anchor);
      const closing=anchor.getAttribute('aria-expanded');
      await new Promise(r=>setTimeout(r,60));N.toggleUsage(anchor);
      await new Promise(r=>setTimeout(r,500));return {closing,hidden:panel.hidden,expanded:anchor.getAttribute('aria-expanded')};
    }""")
    assert usage == {"closing": "false", "hidden": False, "expanded": "true"}, usage


def test_failed_edits_keep_drafts_and_old_saves_leave_new_forms(frontend):
    page = frontend
    page.evaluate('N.go("context")')
    page.wait_for_function('!document.querySelector("#view-context").hidden')
    page.locator('[data-act="add-rule"]').click()
    page.evaluate("() => {N.api.saveContext=async()=>{throw Error('SAVE_FAILED')}}")
    page.locator("#editor-text").fill("keep this instruction")
    page.locator("#editor-save").click()
    page.wait_for_function('document.querySelector("#editor-error").textContent==="SAVE_FAILED"')
    assert page.locator("#editor").is_visible()
    assert page.locator("#editor-text").input_value() == "keep this instruction"
    page.evaluate("N.confirmEdit({title:'Another operation'})")
    assert page.locator("#editor-text").input_value() == "keep this instruction"
    page.keyboard.press("Escape")
    page.wait_for_function('!document.querySelector("#editor").open')
    result = page.evaluate("""async()=>{
      N.go('settings');await new Promise(r=>setTimeout(r,1050));
      const update=N.api.updateProvider,refresh=N.sync.settingsOnly;let finish;
      N.api.updateProvider=()=>new Promise(r=>finish=r);N.sync.settingsOnly=async()=>{};
      document.querySelector('[data-act="edit-provider"]').click();document.querySelector('#p-save').click();
      N.closeDialog(document.querySelector('#provider-dialog'));await new Promise(r=>setTimeout(r,280));
      document.querySelector('[data-act="edit-provider"]').click();document.querySelector('#p-name').value='NEW_DRAFT';
      finish({});await new Promise(r=>setTimeout(r,300));
      N.api.updateProvider=update;N.sync.settingsOnly=refresh;
      return {open:document.querySelector('#provider-dialog').open,draft:document.querySelector('#p-name').value,disabled:document.querySelector('#p-save').disabled};
    }""")
    assert result == {"open": True, "draft": "NEW_DRAFT", "disabled": False}, result
    forms = page.evaluate("""async()=>{
      N.closeDialog(document.querySelector('#provider-dialog'));await new Promise(r=>setTimeout(r,350));
      const results=[];
      for(const type of ['mcp','skill']){
        if(type==='skill'){N.go('skills');await new Promise(r=>setTimeout(r,1050))}
        const action=type==='mcp'?'add-mcp':'new-skill',dialog=document.querySelector(type==='mcp'?'#mcp-dialog':'#skill-dialog');
        const button=document.querySelector(`[data-act="${action}"]`);button.click();
        const method=type==='mcp'?'saveMcp':'saveSkill',sync=type==='mcp'?'mcpOnly':'skillsOnly';
        const original=N.api[method],refresh=N.sync[sync];let finish;
        N.api[method]=()=>new Promise(r=>finish=r);N.sync[sync]=async()=>{};
        if(type==='mcp'){
          document.querySelector('#mcp-name').value='first';document.querySelector('#mcp-command').value='test';document.querySelector('#mcp-save').click();
        }else{
          document.querySelector('#skill-name').value='first';document.querySelector('#skill-description').value='description';
          document.querySelector('#skill-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
        }
        N.closeDialog(dialog);await new Promise(r=>setTimeout(r,350));
        document.querySelector(`[data-act="${action}"]`).click();
        const name=document.querySelector(type==='mcp'?'#mcp-name':'#skill-name');name.value='new-draft';
        finish({});await new Promise(r=>setTimeout(r,100));
        results.push({type,open:dialog.open,name:name.value});
        N.api[method]=original;N.sync[sync]=refresh;N.closeDialog(dialog);await new Promise(r=>setTimeout(r,350));
      }
      return results;
    }""")
    assert forms == [{"type": "mcp", "open": True, "name": "new-draft"}, {"type": "skill", "open": True, "name": "new-draft"}], forms


def test_latest_work_request_filters_and_mobile_input(frontend, live):
    page = frontend
    _, workspace, _, tasks, _ = live
    ids = [tasks.create(title=f"Work {i}", goal="Review", workspace=str(workspace)).id for i in range(2)]
    work = page.evaluate("""async ids=>{
      await N.sync.worksOnly();const sync=N.sync.taskDetail,reads={};
      N.sync.taskDetail=id=>new Promise(r=>reads[id]=r);
      const a=N.openWork(ids[0]),b=N.openWork(ids[1]);reads[ids[1]]();await b;reads[ids[0]]();await a;
      N.sync.taskDetail=sync;const id=document.querySelector('#island').data.id;N.morphIsland('compact');return id;
    }""", ids)
    assert work == ids[1]
    filters = page.evaluate("""async()=>{
      N.go('plugins');await new Promise(r=>setTimeout(r,1000));
      document.querySelector('[data-act="plug-tab"][data-k="requests"]')?.click();
      const request=N.api.modelRequests,reads=[];N.api.modelRequests=()=>new Promise(r=>reads.push(r));
      const form=document.querySelector('#plug-filters');
      if(!form)return {tabs:[...document.querySelectorAll('#view-plugins [data-act]')].map(e=>e.dataset)};
      document.querySelector('#f-kind').value='ling';form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
      document.querySelector('#f-kind').value='task';form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
      reads[1]({requests:[{id:'new',kind:'task'}],next:null});await new Promise(r=>setTimeout(r,0));
      reads[0]({requests:[{id:'old',kind:'ling'}],next:null});await new Promise(r=>setTimeout(r,0));
      N.api.modelRequests=request;return N.state.modelRequests.map(r=>r.id);
    }""")
    assert filters == ["new"], filters
    page.evaluate('N.go("chat")')
    page.wait_for_function('!document.querySelector("#view-chat").hidden')
    for palette in ["cool", "jade"]:
        for theme in ["day", "night"]:
            page.set_viewport_size({"width": 390, "height": 844})
            page.evaluate('([p,t])=>{N.applyPalette(p);N.applyTheme(t)}', [palette, theme])
            bounds = page.locator("#composer-wrap").bounding_box()
            assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 844, bounds
            send = page.locator("#send").bounding_box()
            assert send["y"] >= 0 and send["y"] + send["height"] <= 844, send


def test_runtime_responses_and_background_scroll_anchor(frontend):
    page = frontend
    runtime = page.evaluate("""async()=>{
      const fetch=window.fetch,reads={};
      window.fetch=url=>new Promise(resolve=>reads[url]=value=>resolve({ok:true,json:async()=>value}));
      const open=id=>{const b=document.createElement('button');b.dataset.act='host-runtime';b.dataset.id=id;document.body.append(b);b.click();b.remove()};
      const value=id=>({capabilities:{},requires_host:{},services:[id],jobs:[],dialogues:[],operations:[]});
      open('first');open('second');
      reads['/api/plugins/second/runtime'](value('SECOND_RUNTIME'));await new Promise(r=>setTimeout(r,0));
      reads['/api/plugins/first/runtime'](value('FIRST_RUNTIME'));await new Promise(r=>setTimeout(r,0));
      window.fetch=fetch;const text=document.querySelector('#host-runtime-body').textContent;
      N.closeDialog(document.querySelector('#host-runtime-dialog'));return text;
    }""")
    assert "SECOND_RUNTIME" in runtime and "FIRST_RUNTIME" not in runtime, runtime
    page.wait_for_function('!document.querySelector("#host-runtime-dialog").open')
    page.set_viewport_size({"width": 390, "height": 600})
    page.evaluate("""()=>{
      const hero=document.querySelector('.hero');
      const extra=document.createElement('div');extra.style.height='400px';extra.style.flexShrink='0';hero.append(extra);
      const conversation=document.querySelector('#conversation');conversation.scrollTop=60;N.syncPivot();
    }""")
    page.wait_for_timeout(450)
    offset = page.evaluate("""()=>{
      const a=document.querySelector('#pivot-grip').getBoundingClientRect(),c=document.querySelector('#pivot-core').getBoundingClientRect();
      return Math.abs(a.top+a.height/2-c.top-c.height/2);
    }""")
    assert offset < 1, offset


def test_mobile_plugins_are_full_width_rows(frontend, tmp_path):
    page = frontend
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_function("N.REDUCED")
    page.evaluate("""()=>{
      N.state.plugins=[
        {id:'browser-control',name:'浏览器控制',version:'1.0',status:'unloaded'},
        {id:'web-research',name:'网页搜索与阅读',version:'1.0',status:'unloaded'},
        {id:'third-plugin',name:'第三个插件',version:'1.0',status:'unloaded'}
      ];N.go('plugins');
    }""")
    for palette in ["cool", "jade"]:
        for theme in ["day", "night"]:
            for width, height in [(390, 844), (390, 600)]:
                page.set_viewport_size({"width": width, "height": height})
                page.evaluate('([p,t])=>{N.applyPalette(p);N.applyTheme(t)}', [palette, theme])
                layout = page.locator(".plug-list").evaluate("""list=>({
                  width:list.clientWidth,scroll:list.scrollWidth,
                  cards:[...list.querySelectorAll('.plug-item')].map(e=>{
                    const r=e.getBoundingClientRect();return {width:r.width,top:r.top,bottom:r.bottom};
                  })
                })""")
                assert layout["scroll"] <= layout["width"] + 1, layout
                assert len(layout["cards"]) == 3
                assert all(abs(c["width"] - layout["width"]) < 1 for c in layout["cards"]), layout
                assert all(a["bottom"] <= b["top"] for a, b in zip(layout["cards"], layout["cards"][1:])), layout
    page.locator('.plug-item[data-id="web-research"]').click()
    assert page.locator(".plug-detail-head h2").inner_text() == "网页搜索与阅读"
    page.screenshot(path=str(tmp_path / "mobile-plugins-fixed.png"))
    page.set_viewport_size({"width": 1440, "height": 1000})
    assert page.locator(".plug-layout").evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length") == 2
    page.set_viewport_size({"width": 820, "height": 1000})
    assert page.locator(".plug-list").evaluate("e=>getComputedStyle(e).gridAutoFlow") == "column"


def test_all_pages_fit_four_themes_and_desktop_mobile_short_screens(frontend, tmp_path):
    page = frontend
    failed = []
    origin = page.evaluate('location.origin')
    page.on('response', lambda response: failed.append((response.status, response.url))
            if response.url.startswith(origin + '/') and response.status >= 400 else None)
    checks = 0
    for width, height in [(1440, 1000), (390, 844), (720, 480)]:
        page.set_viewport_size({'width': width, 'height': height})
        for palette in ['cool', 'jade']:
            for theme in ['day', 'night']:
                page.evaluate('([p,t])=>{N.applyPalette(p);N.applyTheme(t)}', [palette, theme])
                for view in ['chat', 'work', 'context', 'skills', 'plugins', 'settings']:
                    page.evaluate('v=>N.go(v)', view)
                    page.wait_for_function('v=>!document.querySelector("#view-"+v).hidden', arg=view)
                    page.evaluate("""async v=>{
                      const root=document.querySelector('#view-'+v);
                      await Promise.all(root.getAnimations({subtree:true})
                        .filter(a=>a.effect.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})));
                    }""", view)
                    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'), (width, height, palette, theme, view)
                    if view == 'chat':
                        for selector in ['#composer-wrap', '#send']:
                            box = page.locator(selector).bounding_box()
                            assert box and box['y'] >= 0 and box['y'] + box['height'] <= height + 1, (width, height, palette, theme, selector, box)
                    checks += 1
                page.screenshot(path=str(tmp_path / f'formal-{width}-{height}-{palette}-{theme}.png'))
    assert not failed, failed
    print(f'PASS {checks} formal page/theme/viewport combinations; screenshots: {tmp_path}')


def test_delete_last_provider_and_add_another_through_real_ui(frontend):
    page = frontend
    page.evaluate("N.go('settings')")
    page.locator('[data-act="edit-provider"]').first.click()
    page.locator('#p-delete').click()
    page.locator('#editor-save').click()
    page.wait_for_function('N.state.providers.length===0 && !document.querySelector("#provider-dialog").open')
    assert page.locator('#provider-error').inner_text() == ''
    page.reload(wait_until='domcontentloaded')
    page.wait_for_function('N.state.connected && N.state.view==="settings"')
    assert page.evaluate('N.state.providers.length') == 0
    page.locator('[data-act="add-provider"]').click()
    page.locator('#p-name').fill('Browser test provider')
    page.locator('#p-family').select_option('openai_chat_completions')
    page.locator('#p-base').fill('https://fake.test')
    page.locator('#p-model-new').fill('fake')
    page.locator('#p-model-add').click()
    page.locator('#p-save').click()
    page.wait_for_function('N.state.providers.length===1 && !document.querySelector("#provider-dialog").open')
    assert page.evaluate('N.state.providers[0].name') == 'Browser test provider'
