"""A private, persistent Chromium browser driven by the DevTools protocol."""
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from .transport import CDP

_SNAPSHOT = r'''(maxChars) => {
 const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
 const map = Object.create(null), elements = [];
 const visible = e => { const s = getComputedStyle(e), r = e.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && s.visibility !== 'collapse' && r.width > 0 && r.height > 0 && r.top < innerHeight && r.bottom > 0 && r.left < innerWidth && r.right > 0 && !e.closest('[hidden],[inert],[aria-hidden="true"]'); };
 const candidates = document.querySelectorAll('a[href],button,input,textarea,select,[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="textbox"],[tabindex],[contenteditable="true"]');
 for (const e of candidates) {
  if (elements.length >= 100) break;
  if (!visible(e) || (e.tagName === 'INPUT' && e.type === 'hidden')) continue;
  const ref = nonce + '-' + elements.length;
  map[ref] = e;
  const label = e.labels ? Array.from(e.labels).map(x => x.innerText.trim()).join(' ') : '';
  const item = {ref, tag:e.tagName.toLowerCase(), role:e.getAttribute('role') || '', name:(e.getAttribute('aria-label') || label || e.getAttribute('title') || e.innerText || e.getAttribute('placeholder') || '').trim().slice(0,500), disabled:!!e.disabled, readonly:!!e.readOnly};
  if (e.tagName === 'A') item.url = e.href;
  if (e.tagName === 'INPUT') item.type = e.type;
  if (e.tagName === 'SELECT') item.value = e.value;
  if (e.tagName === 'TEXTAREA' || (e.tagName === 'INPUT' && ['text','search','email','url','tel'].includes(e.type))) item.value = e.value.slice(0, 500);
  elements.push(item);
 }
 window.__nexusBrowserRefs = map;
 const text = document.body ? document.body.innerText : '';
 return {url:location.href,title:document.title,text:text.slice(0,maxChars),elements,truncated:text.length > maxChars};
}'''

_ACTION = r'''(ref, action, text, value) => {
 const e = window.__nexusBrowserRefs && window.__nexusBrowserRefs[ref];
 if (!e || !e.isConnected) throw new Error('Stale or removed element reference; take a fresh snapshot');
 if (e.disabled) throw new Error('Element is disabled');
 if (action === 'click') { e.click(); return; }
 if (action === 'focus') { e.focus(); return; }
 if (e.readOnly) throw new Error('Element is read-only');
 if (action === 'select') {
  if (!(e instanceof HTMLSelectElement)) throw new Error('Element is not a select');
  if (!Array.from(e.options).some(o => o.value === value)) throw new Error('Select value not found');
  e.value = value; e.dispatchEvent(new Event('input', {bubbles:true})); e.dispatchEvent(new Event('change', {bubbles:true})); return;
 }
 if (action === 'fill') {
  if (e instanceof HTMLInputElement || e instanceof HTMLTextAreaElement) {
   const proto = e instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
   Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, text);
  } else if (e.isContentEditable) e.textContent = text;
  else throw new Error('Element cannot be filled');
  e.dispatchEvent(new Event('input', {bubbles:true})); e.dispatchEvent(new Event('change', {bubbles:true}));
 }
}'''


class Browser:
    def __init__(self, data_dir, settings=None):
        self.data_dir = Path(data_dir).resolve()
        self.settings = settings or {}
        self.lock = threading.RLock()
        self.process = None
        self.port = None
        self.path = ''
        self.error = ''
        self.log = None
        self._default_tab = ''
        self._closed_tabs = set()
        self._opener = build_opener(ProxyHandler({}))

    def _find_browser(self):
        configured = self.settings.get('browser_path', '')
        if configured:
            path = shutil.which(str(configured)) or str(configured)
            if not Path(path).is_file():
                raise RuntimeError('Configured browser executable not found: ' + str(configured))
            return path
        paths = []
        if sys.platform == 'win32':
            for root in (os.environ.get('PROGRAMFILES', ''), os.environ.get('PROGRAMFILES(X86)', ''), os.environ.get('LOCALAPPDATA', '')):
                paths.extend([Path(root) / 'Microsoft/Edge/Application/msedge.exe', Path(root) / 'Google/Chrome/Application/chrome.exe'])
        elif sys.platform == 'darwin':
            paths = [Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'), Path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'), Path('/Applications/Chromium.app/Contents/MacOS/Chromium')]
        for path in paths:
            if path.is_file(): return str(path)
        for name in ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable', 'microsoft-edge', 'msedge'):
            path = shutil.which(name)
            if path: return path
        raise RuntimeError('Chrome, Edge, or Chromium was not found; configure browser_path')

    def _ensure(self):
        if self.process and self.process.poll() is None:
            return
        if self.process is not None or self.log is not None:
            self.close()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        profile = self.data_dir / 'browser-profile'
        profile.mkdir(exist_ok=True)
        active = profile / 'DevToolsActivePort'
        active.unlink(missing_ok=True)
        try:
            self.path = self._find_browser()
            args = [self.path, '--user-data-dir=' + str(profile), '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', '--no-first-run', '--no-default-browser-check']
            if self.settings.get('headless', False): args.append('--headless=new')
            if self.settings.get('browser_proxy'): args.append('--proxy-server=' + str(self.settings['browser_proxy']))
            args.append('about:blank')
            self.log = open(self.data_dir / 'browser.log', 'ab')  # noqa: SIM115 - kept open for the browser process
            self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT, shell=False)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if self.process.poll() is not None: raise RuntimeError('Browser exited during startup; see browser.log')
                try:
                    self.port = int(active.read_text().splitlines()[0])
                    self._json('/json/version')
                    self.error = ''
                    return
                except (OSError, ValueError, IndexError):
                    time.sleep(.05)
            raise RuntimeError('Timed out starting browser; see browser.log')
        except Exception as exc:
            self.error = str(exc)
            self.close()
            raise

    def _json(self, path):
        with self._opener.open(f'http://127.0.0.1:{self.port}{path}', timeout=10) as response:
            return json.load(response)

    def status(self):
        with self.lock:
            result = {'running': bool(self.process and self.process.poll() is None), 'path': self.path}
            if self.error: result['error'] = self.error
            return result

    def close(self):
        with self.lock:
            if self.process:
                if self.process.poll() is None:
                    try:
                        cdp = CDP(self._json('/json/version')['webSocketDebuggerUrl'])
                        try:
                            cdp.call('Browser.close')
                        finally:
                            cdp.close()
                    except (OSError, RuntimeError, KeyError, ValueError):
                        pass
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                            self.process.wait(timeout=2)
                self.process = None
            if self.log:
                self.log.close()
                self.log = None
            self.port = None
            self._default_tab = ''
            self._closed_tabs.clear()

    def _tabs(self):
        self._ensure()
        return [x for x in self._json('/json/list') if x.get('type') == 'page' and x.get('webSocketDebuggerUrl') and x['id'] not in self._closed_tabs]

    def tabs(self):
        with self.lock:
            return [{'id': x['id'], 'url': x.get('url', ''), 'title': x.get('title', '')} for x in self._tabs()]

    def _target(self, tab_id=''):
        tabs = self._tabs()
        wanted = tab_id or self._default_tab
        for tab in tabs:
            if tab['id'] == wanted: return tab
        if tab_id: raise ValueError('Tab not found: ' + tab_id)
        if not tabs: raise RuntimeError('No browser tabs are open')
        self._default_tab = tabs[0]['id']
        return tabs[0]

    def _eval(self, cdp, expression):
        result = cdp.call('Runtime.evaluate', expression=expression, returnByValue=True, awaitPromise=True, userGesture=True)
        if 'exceptionDetails' in result:
            details = result['exceptionDetails']
            raise RuntimeError(details.get('exception', {}).get('description') or details.get('text', 'JavaScript failed'))
        return result.get('result', {}).get('value')

    def _ready(self, cdp):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if self._eval(cdp, "document.readyState") in ('interactive', 'complete'): return
            except RuntimeError as exc:
                if 'context' not in str(exc).lower(): raise
            time.sleep(.05)
        raise TimeoutError('Timed out waiting for page to load')

    def snapshot(self, tab_id='', max_chars=16000):
        with self.lock:
            tab = self._target(tab_id)
            cdp = CDP(tab['webSocketDebuggerUrl'])
            try:
                self._ready(cdp)
                result = self._eval(cdp, '(' + _SNAPSHOT + ')(' + json.dumps(max(0, int(max_chars))) + ')')
                result['tab_id'] = tab['id']
                return result
            finally: cdp.close()

    def _new_tab(self):
        self._ensure()
        cdp = CDP(self._json('/json/version')['webSocketDebuggerUrl'])
        try:
            return cdp.call('Target.createTarget', url='about:blank')['targetId']
        finally:
            cdp.close()

    def navigate(self, url, tab_id='', new_tab=False):
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
            raise ValueError('Only HTTP and HTTPS URLs are supported')
        if new_tab and tab_id:
            raise ValueError('Choose a tab_id or new_tab, not both')
        with self.lock:
            tabs = self._tabs()
            if new_tab or (not tab_id and not tabs):
                tab_id = self._new_tab()
            tab = self._target(tab_id)
            cdp = CDP(tab['webSocketDebuggerUrl'])
            try:
                cdp.call('Page.enable')
                result = cdp.call('Page.navigate', url=url)
                if result.get('errorText'): raise RuntimeError(result['errorText'])
                self._ready(cdp)
            finally: cdp.close()
            return self.snapshot(tab['id'])

    def interact(self, action, tab_id='', ref='', text='', key='', value='', delta_y=600):
        with self.lock:
            tab = self._target(tab_id)
            if action == 'close':
                last_tab = not any(item['id'] != tab['id'] for item in self._tabs())
                with self._opener.open(f"http://127.0.0.1:{self.port}/json/close/{tab['id']}", timeout=10) as response:
                    response.read()
                self._closed_tabs.add(tab['id'])
                if self._default_tab == tab['id']: self._default_tab = ''
                if last_tab:
                    return {'tab_id': '', 'url': '', 'title': '', 'text': '', 'elements': [], 'truncated': False}
                try:
                    remaining = [item for item in self._json('/json/list') if item.get('type') == 'page' and item.get('webSocketDebuggerUrl') and item['id'] != tab['id']]
                except OSError:
                    if self.process and self.process.poll() is None: raise
                    remaining = []
                return self.snapshot(remaining[0]['id']) if remaining else {'tab_id': '', 'url': '', 'title': '', 'text': '', 'elements': [], 'truncated': False}
            cdp = CDP(tab['webSocketDebuggerUrl'])
            try:
                if action in ('click', 'fill', 'select'):
                    try:
                        self._eval(cdp, '(' + _ACTION + ')(' + ','.join(json.dumps(x) for x in (ref, action, text, value)) + ')')
                    except RuntimeError as exc:
                        # A click can replace its execution context after the event is sent.
                        if action != 'click' or 'context' not in str(exc).lower(): raise
                elif action == 'press':
                    if ref: self._eval(cdp, '(' + _ACTION + ')(' + ','.join(json.dumps(x) for x in (ref, 'focus', '', '')) + ')')
                    keys = {'Enter': ('Enter', 13), 'Tab': ('Tab', 9), 'Escape': ('Escape', 27), 'Backspace': ('Backspace', 8), 'ArrowUp': ('ArrowUp', 38), 'ArrowDown': ('ArrowDown', 40), 'ArrowLeft': ('ArrowLeft', 37), 'ArrowRight': ('ArrowRight', 39), 'Control+A': ('a', 65)}
                    if key not in keys: raise ValueError('Unsupported key: ' + key)
                    k, code = keys[key]
                    params = {'key': k, 'windowsVirtualKeyCode': code, 'nativeVirtualKeyCode': code, 'modifiers': 2 if key == 'Control+A' else 0}
                    if key == 'Enter':
                        params['text'] = '\r'
                        params['unmodifiedText'] = '\r'
                    cdp.call('Input.dispatchKeyEvent', type='keyDown', **params)
                    if key == 'Enter':
                        time.sleep(.05)  # Allow the browser to start an implicit form navigation.
                    cdp.call('Input.dispatchKeyEvent', type='keyUp', **params)
                elif action == 'scroll': self._eval(cdp, 'window.scrollBy(0,' + json.dumps(int(delta_y)) + ')')
                elif action in ('back', 'forward'):
                    history = cdp.call('Page.getNavigationHistory')
                    index = history['currentIndex'] + (-1 if action == 'back' else 1)
                    if 0 <= index < len(history['entries']): cdp.call('Page.navigateToHistoryEntry', entryId=history['entries'][index]['id'])
                elif action == 'reload': cdp.call('Page.reload')
                else: raise ValueError('Unsupported browser action: ' + str(action))
                self._ready(cdp)
            finally: cdp.close()
            return self.snapshot(tab['id'])

    def screenshot(self, tab_id='', full_page=False):
        with self.lock:
            tab = self._target(tab_id)
            cdp = CDP(tab['webSocketDebuggerUrl'])
            try:
                self._ready(cdp)
                result = cdp.call('Page.captureScreenshot', format='png', captureBeyondViewport=bool(full_page), **({'clip': self._clip(cdp)} if full_page else {}))
            finally: cdp.close()
            directory = self.data_dir / 'screenshots'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (uuid.uuid4().hex + '.png')
            path.write_bytes(base64.b64decode(result['data']))
            return {'path': str(path), 'tab_id': tab['id']}

    def _clip(self, cdp):
        size = cdp.call('Page.getLayoutMetrics').get('cssContentSize')
        return {'x': 0, 'y': 0, 'width': max(1, size['width']), 'height': max(1, size['height']), 'scale': 1}
