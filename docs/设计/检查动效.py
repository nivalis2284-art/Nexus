"""独立预览的定向动效验收与标准速度实播；不启动 Nexus。"""
import argparse
import json
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright
from 检查预览 import ROOT, QuietHandler

SHOTS = ROOT / '验收样张' / '动效'


def verify_context_count(browser, url):
    page = browser.new_page()
    page.goto(url + '&page=work', wait_until='networkidle')
    page.evaluate('''() => {
      const countUp=N.countUp; window.countSamples=[];
      N.countUp=(el,to,dur)=>{
        const sample={to,dur,values:[]};countSamples.push(sample);
        countUp(el,to,dur);
        sample.initial=el.textContent;
        const tick=()=>{sample.values.push(Number(el.textContent));if(sample.values.at(-1)!==to)requestAnimationFrame(tick)};
        tick();
      };
      N.go('context');
    }''')
    page.wait_for_function('countSamples.length===1 && countSamples[0].values.at(-1)===24')
    sample = page.evaluate('countSamples[0]')
    assert sample['initial'] == '0' and sample['dur'] == 620, sample
    assert any(0 < n < 24 for n in sample['values']), sample
    assert sample['values'] == sorted(sample['values']), sample
    page.evaluate('N.state.context.messages+=12000;N.renderContext()')
    assert page.locator('.ctx-big strong').inner_text() == '30'
    assert page.evaluate('countSamples.length') == 1
    page.evaluate('N.state.context.messages-=12000;N.renderContext()')
    page.locator('[data-act="ctx-tab"][data-k="history"]').click()
    page.wait_for_timeout(300)
    assert page.evaluate('countSamples.length') == 1
    assert page.locator('.ctx-big strong').inner_text() == '24'
    for speed, factor in [('0.5', .68), ('1.5', 1.32)]:
        page.evaluate('s=>{N.setMotion(s,true);N.markEnter("context");N.renderContext()}', speed)
        assert abs(page.evaluate('countSamples.at(-1).dur') - 620 * factor) < .01
        page.wait_for_function('countSamples.at(-1).values.at(-1)===24')
    page.evaluate('Preview.selectScene("context","empty")')
    assert page.locator('.ctx-big strong').inner_text() == '—'
    page.emulate_media(reduced_motion='reduce')
    page.wait_for_function('N.REDUCED')
    page.evaluate('Preview.selectScene("context","system")')
    assert page.locator('.ctx-big strong').inner_text() == '24'
    assert page.evaluate('countSamples.at(-1).initial') == '24'
    page.close()
    print('PASS context count: 0 -> intermediate values -> 24, no refresh/tab replay, speed scaling, unknown and reduced motion', flush=True)


def verify(browser, url):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.set_default_timeout(6000)
    errors, failed, remote = [], [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('response', lambda r: failed.append([r.status, r.url]) if r.status >= 400 else None)
    origin = url.split('/页面模板')[0]
    page.on('request', lambda r: remote.append(r.url) if r.url.startswith('http') and
            (not r.url.startswith(origin + '/') or '/api/' in r.url) else None)
    page.goto(url, wait_until='networkidle')
    page.evaluate('document.fonts.ready')
    assert page.evaluate('N.DUR.enter === 620 && N.DUR.content === 440 && N.DUR.text === 420 && N.SP.soft.d > 500 && N.SP.bouncy.d > 450 && N.SP.island.d > 450')

    def scene(view, name='overview'):
        page.evaluate('([v,s])=>Preview.selectScene(v,s)', [view, name])
        page.wait_for_timeout(1150)

    evidence = {}
    for view in ['work', 'context', 'skills', 'plugins', 'settings', 'chat']:
        scene(view, 'conversation' if view == 'chat' else 'system' if view == 'context' else 'overview')
        timings = page.evaluate('''() => {
          const root = document.querySelector('.view:not([hidden])');
          if (N.state.view === 'chat') N.enterChat(); else N.stagger(root);
          return [...root.querySelectorAll('*')].filter(e=>e._enterMotion).map(e=>({
            name:e.className, ...e._enterMotion.effect.getTiming(),
            frames:e._enterMotion.effect.getKeyframes().map(k=>({transform:k.transform,filter:k.filter}))
          }));
        }''')
        assert len(timings) >= 2, (view, timings)
        assert len({a['delay'] for a in timings}) >= 2, (view, timings)
        assert all(a['easing'] != 'linear' and a['duration'] == page.evaluate('N.SP.soft.d') for a in timings)
        assert max(a['delay'] + a['duration'] for a in timings) <= 1050, (view, timings)
        assert all('filter' not in f or f['filter'] is None for a in timings for f in a['frames'])
        evidence[view] = timings
        page.evaluate('document.querySelectorAll(".view:not([hidden]) *").forEach(e=>e._enterMotion?.finish())')
    print('PASS six pages: curved motion, stagger, visible-item budget, no reading blur', flush=True)

    scene('work')
    curve = page.evaluate('''() => {
      N.stagger(document.querySelector('#view-work'));
      const e=document.querySelector('#view-work .work-item'), a=e._enterMotion;
      a.pause(); const t=a.effect.getTiming(), ys=[];
      for(const p of [0,.25,.5,.75,1]) {a.currentTime=t.delay+t.duration*p;ys.push(new DOMMatrixReadOnly(getComputedStyle(e).transform).m42)}
      a.finish();return ys;
    }''')
    deltas = [curve[i] - curve[i+1] for i in range(4)]
    assert max(deltas) - min(deltas) > 2, curve
    assert abs(curve[-1]) < .1 and 17.9 < curve[0] < 18.1, curve
    evidence['curve'] = curve
    page.evaluate('N.cancelMotion(document.querySelector("#view-work"));N.stagger(document.querySelector("#view-work"));document.querySelectorAll("#view-work *").forEach(e=>e._enterMotion?.pause())')
    for t in [0, 140, 280, 560, 840]:
        page.evaluate('t=>document.querySelectorAll("#view-work *").forEach(e=>{if(e._enterMotion)e._enterMotion.currentTime=t})', t)
        page.screenshot(path=str(SHOTS / f'页面-{t}ms.png'))
    page.evaluate('N.cancelMotion(document.querySelector("#view-work"))')
    page.evaluate('''() => {
      N.morphIsland('overview');
      document.querySelector('#island').getAnimations({subtree:true}).forEach(a=>{
        if(a.effect.getTiming().iterations!==Infinity)a.pause();
      });
    }''')
    phases = page.evaluate('[...document.querySelectorAll("#island-inner *")].filter(e=>e._enterMotion).map(e=>e._enterMotion.effect.getTiming().delay)')
    assert len(phases) >= 3 and phases[0] == 90 and phases[1] == 122, phases
    for t in [0, 70, 140, 280, 560, 840]:
        page.evaluate('''t=>document.querySelector('#island').getAnimations({subtree:true}).forEach(a=>{
          if(a.effect.getTiming().iterations!==Infinity)a.currentTime=t;
        })''', t)
        page.screenshot(path=str(SHOTS / f'灵岛-{t}ms.png'))
    page.evaluate('N.morphIsland("compact")')
    page.wait_for_timeout(350)

    for palette in ['cool', 'jade']:
        for theme in ['day', 'night']:
            for width, height in [(1440, 1000), (390, 844)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.evaluate('([p,t])=>{N.applyPalette(p);N.applyTheme(t);N.morphIsland("compact")}', [palette, theme])
                page.wait_for_timeout(300)
                shape = page.evaluate('''() => {
                  N.morphIsland('overview');const e=document.querySelector('#island');
                  const a=e.getAnimations().find(a=>a.effect.getKeyframes().some(k=>k.width));
                  a.pause();const rows=[];
                  for(let i=0;i<=20;i++){a.currentTime=a.effect.getTiming().duration*i/20;const r=e.getBoundingClientRect();rows.push([r.width,r.height])}
                  a.finish();return rows;
                }''')
                assert shape[-1][0] > shape[0][0] and shape[-1][1] > shape[0][1] and max(r[1] for r in shape) >= shape[-1][1], shape
                page.wait_for_timeout(1050)
                assert page.evaluate('[...document.querySelectorAll("#island-inner *")].filter(e=>e._enterMotion).length') == 0
                page.evaluate('N.morphIsland("detail",{id:N.state.works[0].id})')
                page.wait_for_timeout(100)
                page.evaluate('N.morphIsland("compact")')
                page.wait_for_timeout(500)
                assert page.locator('.island-ghost').count() == 0
                assert page.evaluate('getComputedStyle(document.querySelector("#island")).height') == '46px'
    print('PASS four themes and desktop/mobile: island monotonic geometry and interrupted cleanup', flush=True)

    page.set_viewport_size({'width': 1440, 'height': 1000})
    for speed, factor in [('0.5', .68), ('1', 1), ('1.5', 1.32)]:
        page.evaluate('s=>N.setMotion(s,true)', speed)
        timings = page.evaluate('N.enterItems([document.querySelector("#island")], document.documentElement).map(a=>a.effect.getTiming())')
        assert abs(timings[0]['duration'] - page.evaluate('N.SP.soft.d') * factor) < 1
        page.evaluate('N.cancelMotion(document.querySelector("#island"))')
    page.evaluate('N.setMotion("1",true)')

    scene('chat', 'conversation')
    page.locator('#model-btn').click()
    page.wait_for_timeout(70)
    page.evaluate('N.closeMenu()')
    page.wait_for_timeout(70)
    page.locator('#model-btn').click()
    page.wait_for_timeout(850)
    assert page.locator('#menu').is_visible()
    assert page.evaluate('getComputedStyle(document.querySelector("#menu")).opacity') == '1'
    page.evaluate('N.closeMenu()')
    page.wait_for_timeout(300)

    scene('settings')
    page.locator('[data-act="edit-provider"]').click()
    page.wait_for_timeout(90)
    page.keyboard.press('Escape')
    page.wait_for_timeout(80)
    page.evaluate('N.openDialog(document.querySelector("#provider-dialog"))')
    page.wait_for_timeout(1050)
    assert page.locator('#provider-dialog').is_visible()
    assert page.evaluate('getComputedStyle(document.querySelector("#provider-dialog")).opacity') == '1'
    assert page.evaluate('[...document.querySelectorAll("#provider-dialog *")].every(e=>getComputedStyle(e).filter==="none")')
    page.keyboard.press('Escape')
    page.wait_for_timeout(350)
    assert page.locator('dialog[open]').count() == 0

    scene('work')
    page.evaluate('N.go("settings")')
    page.wait_for_timeout(70)
    page.evaluate('N.go("plugins")')
    page.wait_for_timeout(70)
    page.evaluate('N.go("work")')
    page.wait_for_timeout(1150)
    assert page.evaluate('[...document.querySelectorAll(".view:not([hidden])")].map(e=>e.id)') == ['view-work']
    fold = page.locator('[data-act="group-toggle"]').first
    fold.click()
    page.wait_for_timeout(70)
    fold.click()
    page.wait_for_timeout(800)
    assert fold.get_attribute('aria-expanded') == 'true'
    assert fold.locator('xpath=..').locator('.work-group-body').is_visible()
    print('PASS fast/slow timing, menu/dialog reopen, navigation reversal and fold reversal', flush=True)

    scene('chat', 'empty')
    page.locator('#input').fill('定向动效验收')
    page.locator('#send').click()
    page.wait_for_function('N.state.replying')
    assert page.locator('.msg-user').count() == 1
    page.wait_for_function('N.state.items.some(i=>i.role==="assistant")')
    page.wait_for_timeout(300)
    page.locator('.msg-ling').last.evaluate('e=>(window.motionMessage=e)')
    page.wait_for_timeout(350)
    assert page.evaluate('document.querySelector(`[data-key="${motionMessage.dataset.key}"]`)===motionMessage')
    page.locator('#send').click()
    page.wait_for_function('!N.state.replying')
    page.wait_for_timeout(750)
    assert page.locator('.send-text-ghost, .send-ripple').count() == 0
    assert len(page.evaluate('N.state.items.find(i=>i.role==="assistant").text')) > 0
    page.emulate_media(reduced_motion='reduce')
    page.wait_for_function('N.REDUCED')
    page.evaluate('N.go("settings")')
    page.wait_for_timeout(100)
    assert page.locator('#view-settings').is_visible()
    assert page.evaluate('N.enterItems([document.querySelector("#island")],document.documentElement).length') == 0
    page.locator('[data-act="edit-provider"]').click()
    page.keyboard.press('Escape')
    assert page.locator('dialog[open]').count() == 0
    assert not errors, errors
    assert not failed, failed
    assert not remote, remote
    print('PASS send/stop, stable streaming nodes, cleanup, reduced motion, no browser errors or remote/API requests', flush=True)
    (SHOTS / '验收数据.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    page.close()


def record(browser, url, label):
    SHOTS.mkdir(parents=True, exist_ok=True)
    for name in ['页面切换', '灵岛切换', '发送与回复']:
        context = browser.new_context(viewport={'width': 1440, 'height': 1000},
                                      record_video_dir=str(SHOTS),
                                      record_video_size={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.goto(url, wait_until='networkidle')
        page.evaluate('document.fonts.ready')
        page.wait_for_timeout(1100)
        if name == '页面切换':
            for view in ['work', 'settings', 'plugins', 'context']:
                page.locator(f'.dock-btn[data-view="{view}"]').click()
                page.wait_for_timeout(1500)
        elif name == '灵岛切换':
            page.locator('#island [data-isl="overview"]').first.click()
            page.wait_for_timeout(1300)
            page.locator('#island [data-isl="detail"]').first.click()
            page.wait_for_timeout(1300)
            page.keyboard.press('Escape')
            page.wait_for_timeout(1300)
        else:
            page.evaluate('Preview.selectScene("chat", "empty")')
            page.wait_for_timeout(1100)
            page.locator('#input').fill('请说明这份界面的动效设计。')
            page.wait_for_timeout(400)
            page.locator('#send').click()
            page.wait_for_timeout(4300)
            if page.evaluate('N.state.replying'):
                page.locator('#send').click()
            page.wait_for_timeout(1000)
        video = page.video
        video_path = SHOTS / f'{label}-{name}.webm'
        context.close()
        video.save_as(str(video_path))
        video.delete()
        print(f'RECORDED {video_path.name}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', choices=['before', 'after'])
    parser.add_argument('--context-count', action='store_true', help='只验收上下文占用数字入场')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/页面模板.html?tools=hidden'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            if args.record:
                record(browser, url, args.record)
            elif args.context_count:
                verify_context_count(browser, url)
            else:
                SHOTS.mkdir(parents=True, exist_ok=True)
                verify(browser, url)
                verify_context_count(browser, url)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
