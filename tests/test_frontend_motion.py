"""Formal HTML, API and SSE integration for the approved preview motion."""
import json

import pytest

from test_http import live as live
from nexus.model.request_history import RequestHistory
from nexus.plugins import PluginManager
from nexus.web import server as web

playwright = pytest.importorskip("playwright.sync_api")


def test_formal_motion_keeps_backend_and_preferences(live, tmp_path, request):
    base, workspace, store, tasks, _ = live
    store.save_preferences(workspace=str(workspace), permission="yolo")
    manager = PluginManager(web.STATE, root=tmp_path / "plugins")
    web.STATE.update(plugins=manager, request_history=RequestHistory(store.home / "requests.sqlite3"))
    request.addfinalizer(manager.close)
    manager.start(base)
    task = tasks.create(title="动效集成验收", goal="核对工作详情", workspace=str(workspace))
    tasks.command(task.id, "paused", request_id="motion-paused")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors, failed = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: failed.append(response.url)
                if response.url.startswith(base) and response.status >= 400 else None)
        # Font supplements are unrelated to the local application integration.
        page.route("https://fonts.googleapis.com/**", lambda route: route.fulfill(body=""))
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_function('N.state.sessionId && N.state.connected && N.state.works.length===1')
        assert page.evaluate('typeof Preview') == "undefined"
        page.wait_for_timeout(1050)

        # Observe real requests without replacing API calls or the EventSource connection.
        page.evaluate("""() => {
          const fetch=window.fetch; window.inputMotion=null;
          window.fetch=(url,options)=>{
            if(url==='/api/ling/input') inputMotion={
              moving:document.querySelector('#composer-wrap')._sendMotion?.playState,
              body:JSON.parse(options.body)
            };
            return fetch(url,options);
          };
        }""")
        page.locator("#input").fill("读一下 a.txt")
        page.locator("#send").click()
        page.wait_for_function('N.state.baseItems.some(i=>i.role==="assistant" && i.text.includes("hello"))')
        sent = page.evaluate("inputMotion")
        assert sent["moving"] == "running", sent
        assert sent["body"]["text"] == "读一下 a.txt"
        assert page.locator(".msg-user").count() == 1
        page.wait_for_timeout(800)
        assert page.locator(".send-text-ghost, .send-ripple").count() == 0
        page.locator(".msg-ling").last.evaluate("e=>window.savedReply=e")
        page.evaluate("async()=>{await N.sync.chatOnly();N.renderChat()}")
        assert page.evaluate('document.querySelector(`[data-key="${savedReply.dataset.key}"]`)===savedReply')

        def go(view):
            page.evaluate("v=>N.go(v)", view)
            page.wait_for_function("v=>!document.querySelector('#view-'+v).hidden", arg=view)
            page.wait_for_timeout(1050)

        timings = {}
        for view in ["work", "context", "skills", "plugins", "settings", "chat"]:
            go(view)
            motions = page.evaluate("""v=>{
              const root=document.querySelector('#view-'+v);
              if(v==='chat')N.enterChat();else N.stagger(root);
              return [...root.querySelectorAll('*')].filter(e=>e._enterMotion).map(e=>e._enterMotion.effect.getTiming());
            }""", view)
            assert len(motions) >= 2, (view, motions)
            assert len({a["delay"] for a in motions}) >= 2, (view, motions)
            assert all(a["duration"] == 560 and a["easing"] != "linear" for a in motions)
            assert max(a["duration"] + a["delay"] for a in motions) <= 1000
            timings[view] = [a["delay"] for a in motions]
            page.evaluate("v=>N.cancelMotion(document.querySelector('#view-'+v))", view)

        go("work")
        curve = page.evaluate("""() => {
          N.stagger(document.querySelector('#view-work'));
          const el=document.querySelector('.work-item'), a=el._enterMotion, t=a.effect.getTiming(), values=[];
          a.pause();
          for(const p of [0,.25,.5,.75,1]){
            a.currentTime=t.delay+t.duration*p;
            values.push(new DOMMatrixReadOnly(getComputedStyle(el).transform).m42);
          }
          N.cancelMotion(document.querySelector('#view-work'));return values;
        }""")
        distances = [curve[i] - curve[i + 1] for i in range(4)]
        assert abs(curve[0] - 24) < .1 and abs(curve[-1]) < .1, curve
        assert max(distances) - min(distances) > 2, curve
        page.evaluate('N.go("settings")')
        page.wait_for_timeout(60)
        page.evaluate('N.go("plugins")')
        page.wait_for_timeout(60)
        page.evaluate('N.go("work")')
        page.wait_for_timeout(1050)
        assert page.evaluate('[...document.querySelectorAll(".view:not([hidden])")].map(e=>e.id)') == ["view-work"]
        fold = page.locator('[data-act="group-toggle"]').first
        fold.click()
        page.wait_for_timeout(70)
        fold.click()
        page.wait_for_timeout(700)
        assert fold.get_attribute("aria-expanded") == "true"
        assert fold.locator("xpath=..").locator(".work-group-body").is_visible()

        page.evaluate("""() => {
          const count=N.countUp;window.countSamples=[];
          N.countUp=(el,to,dur)=>{
            const s={to,dur,values:[]};countSamples.push(s);count(el,to,dur);s.initial=el.textContent;
            const tick=()=>{s.values.push(Number(el.textContent));if(s.values.at(-1)!==to)requestAnimationFrame(tick)};tick();
          };N.go('context');
        }""")
        page.wait_for_function('countSamples.length && countSamples[0].values.at(-1)===countSamples[0].to')
        sample = page.evaluate("countSamples[0]")
        assert sample["to"] > 0 and sample["initial"] == "0" and sample["dur"] == 560, sample
        assert sample["values"] == sorted(sample["values"]), sample
        assert any(0 < n < sample["to"] for n in sample["values"]), sample
        page.locator('[data-act="ctx-refresh"]').click()
        page.locator('[data-act="ctx-tab"][data-k="history"]').click()
        page.wait_for_timeout(800)
        assert page.evaluate("countSamples.length") == 1
        assert page.locator(".ctx-big strong").inner_text() == str(sample["to"])
        page.screenshot(path=str(tmp_path / "formal-desktop.png"))

        for speed, factor in [("0.5", .68), ("1.5", 1.32)]:
            timing = page.evaluate("""s=>{
              N.setMotion(s,true);const root=document.querySelector('#view-context');N.stagger(root);
              const t=root.querySelector('h1').parentElement._enterMotion.effect.getTiming();N.cancelMotion(root);return t;
            }""", speed)
            assert abs(timing["duration"] - 560 * factor) < .01
        page.evaluate('N.setMotion("1",true)')

        for palette in ["cool", "jade"]:
            for theme in ["day", "night"]:
                for width, height in [(1440, 1000), (390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.evaluate('([p,t])=>{N.applyPalette(p);N.applyTheme(t);N.morphIsland("overview")}', [palette, theme])
                    page.wait_for_timeout(80)
                    page.evaluate('N.morphIsland("compact")')
                    page.wait_for_timeout(350)
                    assert page.locator(".island-ghost").count() == 0
                    assert page.evaluate('getComputedStyle(document.querySelector("#island")).height') == "46px"
        page.screenshot(path=str(tmp_path / "formal-mobile.png"))

        page.set_viewport_size({"width": 1440, "height": 1000})
        go("settings")
        page.locator('[data-act="edit-provider"]').first.click()
        page.wait_for_timeout(90)
        page.keyboard.press("Escape")
        page.wait_for_timeout(70)
        page.evaluate('N.openDialog(document.querySelector("#provider-dialog"))')
        page.wait_for_timeout(1000)
        assert page.locator("#provider-dialog").is_visible()
        assert page.evaluate('[...document.querySelectorAll("#provider-dialog *")].every(e=>getComputedStyle(e).filter==="none")')
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert page.locator("dialog[open]").count() == 0

        page.evaluate('N.applyPalette("jade");N.applyTheme("night");N.setMotion("1.5");N.save()')
        page.wait_for_function('JSON.parse(localStorage.getItem("nexus-ui-prefs-v1"))?.motion==="1.5"')
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function('N.state.connected && !!document.querySelector("#view-settings h1")')
        assert page.evaluate('[N.state.view,N.state.palette,N.state.theme,N.state.motion]') == ["settings", "jade", "night", "1.5"]
        page.emulate_media(reduced_motion="reduce")
        page.wait_for_function("N.REDUCED")
        go("context")
        assert page.locator(".ctx-big strong").inner_text() == str(sample["to"])
        assert page.evaluate('N.enterItems([document.querySelector("#island")],document.documentElement).length') == 0
        go("settings")
        page.locator('[data-act="edit-provider"]').first.click()
        page.keyboard.press("Escape")
        assert page.locator("dialog[open]").count() == 0
        assert not errors, errors
        assert not failed, failed
        print(json.dumps({"page_delays": timings, "trajectory": curve, "context_count": sample,
                          "screenshots": str(tmp_path)}, ensure_ascii=False))
        browser.close()
