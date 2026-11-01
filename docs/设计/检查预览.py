"""独立设计站的定向验收。需要已有 Python Playwright；不启动 Nexus。"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    for path in (ROOT / "预览资源").rglob("*.js"):
        subprocess.run(["node", "--check", str(path)], check=True, capture_output=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/页面模板.html?tools=hidden"
    shots = ROOT / "验收样张"
    shots.mkdir(exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(8000)
            errors, failed, remote = [], [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("response", lambda r: failed.append([r.status, r.url]) if r.status >= 400 else None)
            page.on("request", lambda r: remote.append(r.url) if r.url.startswith("http") and (not r.url.startswith(f"http://127.0.0.1:{server.server_port}/") or "/api/" in r.url) else None)
            page.goto(url, wait_until="networkidle")

            def scene(view, name):
                page.evaluate("([v,s])=>Preview.selectScene(v,s)", [view, name])
                page.wait_for_timeout(180)

            for view, rows in page.evaluate("Preview.scenes").items():
                for key, _label in rows:
                    scene(view, key)
                    assert page.locator(f"#view-{view}").is_visible()
            print("PASS: six pages and all preset scenes", flush=True)
            for width, height in [(1440, 1000), (390, 900), (720, 480)]:
                page.set_viewport_size({"width": width, "height": height})
                for palette in ["cool", "jade"]:
                    for theme in ["day", "night"]:
                        page.evaluate("([p,t])=>{N.applyPalette(p);N.applyTheme(t)}", [palette, theme])
                        for view, rows in page.evaluate("Preview.scenes").items():
                            scene(view, rows[0][0])
                            assert not page.evaluate("document.documentElement.scrollWidth>innerWidth"), (width, view)
                        if width == 1440:
                            page.screenshot(path=str(shots / f"设置-{palette}-{theme}.png"))
            print("PASS: four themes, desktop/mobile/short viewport", flush=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.evaluate("N.applyPalette('cool');N.applyTheme('night')")
            scene("chat", "empty")
            page.locator("#input").fill("本地消息验收\n第二行\n第三行\n第四行")
            page.wait_for_timeout(300)
            before = page.locator("#composer-wrap").bounding_box()
            for _ in range(5):
                page.locator("#input").press("a")
            after = page.locator("#composer-wrap").bounding_box()
            assert abs(before["y"] - after["y"]) < 1
            page.locator("#send").click()
            page.wait_for_function("N.state.replying && N.state.items.some(x=>x.role==='assistant')")
            page.wait_for_timeout(400)
            assert page.locator("#thread .msg-user").count() == 1
            page.locator("#send").click()
            page.wait_for_function("!N.state.replying")
            assert len(page.evaluate("N.state.items.find(x=>x.role==='assistant').text")) > 0
            assert not page.locator('#thread .msg-ling').last.evaluate("e=>e.classList.contains('is-streaming')")
            scene("chat", "attention")
            page.locator('.req-card[data-req="approval-preview"] [data-act="approve"][data-allow="1"]').click()
            page.wait_for_function("!N.state.requests.some(x=>x.id==='approval-preview')")
            scene("chat", "long")
            page.locator("#conversation").hover()
            page.mouse.wheel(0, -500)
            page.wait_for_timeout(180)
            top = page.locator("#conversation").evaluate("e=>e.scrollTop")
            page.evaluate("N.state.baseItems.push({key:'new',role:'assistant',text:'追加内容'});N.composeItems();N.renderChat()")
            page.wait_for_timeout(200)
            assert abs(page.locator("#conversation").evaluate("e=>e.scrollTop") - top) < 2
            print("PASS: multiline input, send/stop, approval, reading anchor", flush=True)
            scene("work", "overview")
            page.locator('[data-act="work-archive"]').first.click()
            page.wait_for_function("N.state.works.find(x=>x.id==='work-4').archived")
            scene("settings", "overview")
            page.locator('[data-act="edit-provider"]').click()
            page.locator("#p-name").fill("演示改名")
            page.evaluate("Preview.failNext=true")
            page.locator("#p-save").click()
            page.wait_for_function("document.querySelector('#provider-error').textContent.includes('演示失败')")
            assert page.locator("#p-name").input_value() == "演示改名"
            page.locator("#p-save").click()
            page.wait_for_function("N.state.providers[0].name==='演示改名'")
            page.wait_for_function("!document.querySelector('#provider-dialog').open")
            scene("plugins", "overview")
            page.locator('.plug-detail [role="switch"]').click()
            page.wait_for_function("!N.state.plugins[0].enabled")
            page.locator('.plug-detail [role="switch"]').click()
            page.wait_for_function("N.state.plugins[0].enabled")
            page.locator('#plug-settings [data-field="label"]').fill("未保存草稿")
            page.locator(".plug-expand").click()
            assert page.locator('#plugin-manager-dialog [data-field="label"]').input_value() == "未保存草稿"
            page.locator("#plugin-manager-dialog .plug-expand").click()
            page.wait_for_function("!document.querySelector('#plugin-manager-dialog').open")
            for action, body, closing in [("plug-readme", "#plugin-readme-body .md", "plugin-readme-dialog"), ("host-runtime", ".host-runtime-sections", "host-runtime-dialog")]:
                page.locator(f'[data-act="{action}"]').click()
                page.wait_for_selector(body)
                page.locator(f'[data-close="{closing}"]').first.click()
                page.wait_for_function("id=>!document.getElementById(id).open", arg=closing)
            page.evaluate("Preview.openGallery('host')")
            page.wait_for_function("document.querySelectorAll('#gallery-host [data-hui-component]').length>20")
            page.locator('#gallery-host button').filter(has_text="字段错误").click()
            page.wait_for_function("document.querySelector('#gallery-host [aria-invalid=true]')")
            page.locator('#gallery-host button').filter(has_text="打开弹窗").click()
            page.wait_for_selector("dialog.hui-dialog[open]")
            page.keyboard.press("Escape")
            page.wait_for_function("!document.querySelector('dialog.hui-dialog[open]')")
            page.screenshot(path=str(shots / "组件库.png"))
            page.locator('[data-close="preview-components"]').click()
            page.wait_for_function("!document.querySelector('#preview-components').open")
            print("PASS: work state, failure/retry, plugins, host components", flush=True)
            page.locator('[data-act="plug-page"][data-renderer="iframe"]').click()
            frame = page.frame_locator("#plugin-frame")
            frame.locator("#demo").click()
            assert "操作完成" in frame.locator("#status").inner_text()
            page.locator('[data-close="plugin-page-dialog"]').click()
            page.wait_for_function("!document.querySelector('#plugin-page-dialog').open")
            scene("chat", "conversation")
            page.evaluate("N.setBackgroundOnly(true)")
            assert page.locator("#stage").evaluate("e=>e.inert")
            page.mouse.move(720, 500)
            page.mouse.down()
            page.wait_for_timeout(700)
            page.mouse.move(770, 525)
            page.mouse.up()
            page.keyboard.press("Escape")
            assert not page.evaluate("N.state.backgroundOnly")
            shape = page.evaluate("""() => {
              N.renderIsland('detail',{id:'work-1'});N.morphIsland('compact');
              const e=document.querySelector('#island'),a=e.getAnimations().find(a=>a.effect.getKeyframes()[0].width);
              a.pause();let min=Infinity;
              for(let i=0;i<=20;i++){a.currentTime=a.effect.getTiming().duration*i/20;min=Math.min(min,e.getBoundingClientRect().height)}
              a.finish();return min;
            }""")
            assert shape >= 45.9
            page.locator("#theme-btn").click()
            page.wait_for_timeout(1000)
            assert page.evaluate("N.state.theme") == "day"
            page.emulate_media(reduced_motion="reduce")
            page.wait_for_function("N.REDUCED")
            scene("chat", "empty")
            page.emulate_media(reduced_motion="no-preference")
            page.wait_for_function("!N.REDUCED")
            page.screenshot(path=str(shots / "主交流空态.png"))
            scene("chat", "conversation")
            page.screenshot(path=str(shots / "主交流.png"))
            page.set_viewport_size({"width": 390, "height": 900})
            scene("plugins", "overview")
            page.screenshot(path=str(shots / "手机插件.png"))
            assert not errors, errors
            assert not failed, failed
            assert not remote, remote
            print("PASS: iframe, background, island, theme, reduced motion", flush=True)
            print("PASS: no JS errors, missing resources, backend or external requests", flush=True)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
