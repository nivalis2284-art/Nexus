"""Search and read public pages in an isolated plugin process."""
import ipaddress
import json
import re
import socket
from functools import wraps
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, NavigableString
from ddgs import DDGS
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("Nexus web research")
HEADERS = {"User-Agent": "Nexus-Web-Research/0.1", "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.1"}
MAX_BYTES = 2_000_000


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("仅支持不含凭据的公开 HTTP(S) 地址。")
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
        def permitted(address):
            ip = ipaddress.ip_address(address.split("%")[0])
            # Common DNS proxies return benchmark-range fake IPs for domain names.
            return ip.is_global or (not re.fullmatch(r"[\d.]+", parts.hostname)
                                    and ip.version == 4 and ip in ipaddress.ip_network("198.18.0.0/15"))
        if not addresses or any(not permitted(info[4][0]) for info in addresses):
            raise ValueError("不支持读取本机或内网地址。")
    except socket.gaierror as exc:
        raise ValueError("无法解析网页地址。") from exc
    return url


def report_errors(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, httpx.HTTPError) as exc:
            raise ToolError(str(exc)) from exc
    return wrapped


def get_public(url, headers=None):
    with httpx.Client(timeout=20, follow_redirects=False, headers={**HEADERS, **(headers or {})}) as client:
        for _ in range(6):
            public_url(url)
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("网页重定向缺少地址。")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise ValueError("网页超过读取大小限制。")
                return url, response.headers.get("content-type", ""), bytes(content)
    raise ValueError("网页重定向次数过多。")


def page_slice(text, offset, limit):
    part = text[offset:offset + limit]
    next_offset = offset + len(part)
    return {"content": part, "next_offset": next_offset if next_offset < len(text) else None,
            "total_chars": len(text)}


@mcp.tool()
@report_errors
def search(query: str, limit: int = 5, provider: str = "DDGS", api_key: str = "") -> dict:
    if provider == "DDGS":
        try:
            hits = DDGS(timeout=15).text(query, max_results=limit)
        except Exception as exc:
            raise ValueError(f"免费搜索暂不可用：{type(exc).__name__}。请检查网络或切换搜索服务。") from exc
        results = [{"title": x.get("title", ""), "url": x.get("href", ""), "snippet": x.get("body", "")} for x in hits]
    elif provider == "Tavily":
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {"X-Tavily-Access-Mode": "keyless", "X-Client-Name": "nexus-web-research"}
        try:
            response = httpx.post("https://api.tavily.com/search", headers=headers,
                                  json={"query": query, "max_results": limit}, timeout=20)
            response.raise_for_status()
            hits = response.json().get("results", [])
        except httpx.HTTPError as exc:
            raise ValueError(f"Tavily 搜索失败：{type(exc).__name__}；请检查网络、密钥或额度。") from exc
        results = [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")} for x in hits]
    else:
        raise ValueError("未知搜索服务，请在插件设置中选择 DDGS 或 Tavily。")
    return {"content": json.dumps({"provider": provider, "results": results}, ensure_ascii=False), "results": results}


@mcp.tool()
@report_errors
def read_page(url: str, offset: int = 0, limit: int = 12000) -> dict:
    final_url, content_type, body = get_public(url)
    if not (content_type.startswith("text/") or any(x in content_type for x in ("json", "xml", "javascript"))):
        raise ValueError(f"暂不支持读取此内容类型：{content_type or '未知'}")
    text = body.decode("utf-8", errors="replace")
    if "html" in content_type.lower():
        soup = BeautifulSoup(body, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for node in soup.select("script, style, noscript, svg, nav, footer, header"):
            node.decompose()
        root = soup.find("main") or soup.find("article") or soup.body or soup
        for link in root.find_all("a", href=True):
            target = urljoin(final_url, link["href"])
            if urlsplit(target).scheme in ("http", "https"):
                link.append(NavigableString(f" ({target})"))
        text = (title + "\n\n" if title else "") + root.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
    return {"url": final_url, **page_slice(text, offset, limit)}


@mcp.tool()
@report_errors
def read_github(repository: str, path: str = "", ref: str = "", offset: int = 0, limit: int = 12000) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("仓库格式应为 owner/name。")
    url = f"https://api.github.com/repos/{repository}/contents/{quote(path.lstrip('/'), safe='/')}"
    if ref:
        url += "?ref=" + quote(ref, safe="")
    _, _, body = get_public(url, {"Accept": "application/vnd.github+json"})
    data = json.loads(body)
    if isinstance(data, list):
        items = [{"name": item["name"], "path": item["path"], "type": item["type"], "url": item.get("html_url")} for item in data]
        return {"content": json.dumps(items, ensure_ascii=False, indent=2), "items": items}
    if data.get("type") != "file" or not data.get("download_url"):
        raise ValueError("此路径不是可读取的普通文件。")
    _, _, raw = get_public(data["download_url"])
    text = raw.decode("utf-8", errors="replace")
    return {"url": data.get("html_url"), "path": data.get("path"), "ref": ref or "默认分支", **page_slice(text, offset, limit)}


if __name__ == "__main__":
    mcp.run()
