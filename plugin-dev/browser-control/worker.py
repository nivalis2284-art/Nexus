"""Playwright worker for Nexus browser sessions."""
import asyncio
import base64
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer
from playwright.async_api import async_playwright

mcp = MCPServer("Nexus browser control")
DATA = Path(sys.argv[1])
sessions = {}
locks = {}
playwright = None


async def session(owner):
    global playwright
    existing = sessions.get(owner)
    if existing and existing["context"].pages:
        return existing
    if existing:
        await existing["context"].close()
    if playwright is None:
        playwright = await async_playwright().start()
    profile = DATA / "profiles" / hashlib.sha256(owner.encode()).hexdigest()
    profile.mkdir(parents=True, exist_ok=True)
    context = await playwright.chromium.launch_persistent_context(
        str(profile), channel="msedge", headless=False, no_viewport=True)
    state = {"context": context, "page": context.pages[0] if context.pages else await context.new_page()}
    sessions[owner] = state
    return state


def current(state):
    pages = state["context"].pages
    if state["page"] not in pages:
        if not pages:
            raise ValueError("浏览器已关闭，请重新打开页面。")
        state["page"] = pages[-1]
    return state["page"]


async def snapshot(page):
    return await page.evaluate("""() => {
      document.querySelectorAll('[data-nexus-ref]').forEach(e => e.removeAttribute('data-nexus-ref'));
      const nodes = [...document.querySelectorAll('a,button,input,textarea,select,[role=button],[role=link],[contenteditable=true]')]
        .filter(e => {const r=e.getBoundingClientRect(), s=getComputedStyle(e); return r.width && r.height && s.visibility !== 'hidden';}).slice(0, 200);
      const elements = nodes.map((e,i) => {
        const ref = 'e' + (i+1); e.setAttribute('data-nexus-ref',ref);
        return {ref, tag:e.tagName.toLowerCase(), role:e.getAttribute('role'),
          name:(e.getAttribute('aria-label') || e.innerText || e.getAttribute('placeholder') || e.getAttribute('title') || '').trim().slice(0,200),
          type:e.getAttribute('type'), href:e.tagName === 'A' ? e.href : undefined};
      });
      return {title:document.title, url:location.href, text:(document.body?.innerText || '').slice(0,16000), elements};
    }""")


@mcp.tool()
async def browser(owner: str, action: str, value: str = "", ref: str = "") -> dict:
    async with locks.setdefault(owner, asyncio.Lock()):
        if action == "close":
            state = sessions.pop(owner, None)
            if state:
                await state["context"].close()
            return {"content": "浏览器已关闭。"}
        state = await session(owner)
        page = current(state)
        if action == "open":
            parsed = urlsplit(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("请输入完整的 HTTP(S) 网页地址。")
            await page.goto(value, wait_until="domcontentloaded", timeout=25000)
        elif action == "snapshot":
            pass
        elif action in ("click", "fill", "select"):
            if not ref or not ref.startswith("e") or not ref[1:].isdigit():
                raise ValueError("请使用最近一次 snapshot 返回的元素引用。")
            element = page.locator(f'[data-nexus-ref="{ref}"]')
            if action == "click":
                await element.click(timeout=10000)
            elif action == "fill":
                await element.fill(value, timeout=10000)
            else:
                await element.select_option(label=value, timeout=10000)
        elif action == "press":
            await page.keyboard.press(value)
        elif action == "scroll":
            if value not in ("up", "down"):
                raise ValueError("滚动方向应为 up 或 down。")
            await page.mouse.wheel(0, 650 if value == "down" else -650)
        elif action == "back":
            await page.go_back(wait_until="domcontentloaded", timeout=25000)
        elif action == "tabs":
            return {"content": "浏览器标签页", "tabs": [{"index": i, "url": p.url, "title": await p.title(), "current": p == page} for i, p in enumerate(state["context"].pages)]}
        elif action == "new_tab":
            page = state["page"] = await state["context"].new_page()
        elif action == "switch":
            pages = state["context"].pages
            if not value.isdigit() or int(value) >= len(pages):
                raise ValueError("标签页编号无效，请先查看 tabs。")
            page = state["page"] = pages[int(value)]
            await page.bring_to_front()
        elif action == "close_tab":
            await page.close()
            if not state["context"].pages:
                await state["context"].close()
                sessions.pop(owner, None)
                return {"content": "最后一个标签页已关闭。"}
            page = state["page"] = state["context"].pages[-1]
        else:
            raise ValueError("未知的浏览器操作。")
        result = await snapshot(page)
        return {"content": json.dumps(result, ensure_ascii=False), **result}


@mcp.tool()
async def screenshot(owner: str) -> dict:
    async with locks.setdefault(owner, asyncio.Lock()):
        if owner not in sessions:
            raise ValueError("请先通过 browser_control 打开浏览器。")
        page = current(sessions[owner])
        image = await page.screenshot(type="jpeg", quality=75)
        if len(image) > 5 * 1024 * 1024:
            raise ValueError("截图超过 5MB，无法传给模型。")
        return {"image_base64": base64.b64encode(image).decode("ascii")}


if __name__ == "__main__":
    mcp.run()
