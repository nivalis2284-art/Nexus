"""Network tools: fetch a page, search the web.

Both are **off by default**. Network access is the one capability that can turn a
local coding agent into an exfiltration path, so it is opt-in in two ways:

* ``webfetch`` needs a domain allowlist; an empty allowlist means the tool is not
  registered at all (see ``ToolSpec.requires``).
* ``websearch`` needs a configured endpoint.

Learned from DSH's ``dsh-web-fetch-http``: convert to text, cap the size, and
give the model a clear truncation marker rather than a giant blob.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from typing import Any

from pydantic import BaseModel, Field

from .registry import ToolSpec

MAX_BYTES = 200_000
MAX_TEXT = 100_000
TIMEOUT = 20.0
USER_AGENT = "Nexus/0.3 (+local agent)"


class FetchArgs(BaseModel):
    url: str = Field(description="Absolute http/https URL to fetch.")
    max_chars: int = Field(
        default=20_000, ge=1_000, le=MAX_TEXT, description="Text budget for the result."
    )


class SearchArgs(BaseModel):
    query: str = Field(description="Search query.")
    limit: int = Field(default=5, ge=1, le=10, description="How many results.")


def _host_allowed(host: str, allowlist: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for entry in allowlist:
        entry = entry.lower().strip().lstrip(".")
        if not entry:
            continue
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _is_private(host: str) -> bool:
    """Reject addresses that reach into the local network.

    A DNS name that *resolves* to a private address is just as dangerous as a
    literal one, so the check is on the resolved address, not the string.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        address = info[4][0].split("%")[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return True
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_reserved
            or parsed.is_multicast
            or (parsed.version == 4 and parsed.is_unspecified)
        ):
            return True
    return False


def _to_text(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    if "html" not in (content_type or "").lower() and not text.lstrip().startswith("<"):
        return text
    return _strip_html(text)


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
        ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def fetch(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    url = args["url"].strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http and https URLs are supported.")
    if not parsed.hostname:
        raise ValueError("URL has no host.")

    allowlist = getattr(context, "web_allowlist", None) or []
    if not _host_allowed(parsed.hostname, allowlist):
        raise PermissionError(
            f"Host '{parsed.hostname}' is not in the webfetch allowlist. "
            f"Allowed: {', '.join(allowlist) or '(none)'}."
        )
    if _is_private(parsed.hostname):
        raise PermissionError(f"Refusing to fetch a private or local address: {parsed.hostname}")

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        status = getattr(response, "status", 200)
        content_type = response.headers.get("Content-Type", "")
        body = response.read(MAX_BYTES + 1)
        final_url = response.geturl()

    truncated_bytes = len(body) > MAX_BYTES
    text = _to_text(body[:MAX_BYTES], content_type)
    budget = args["max_chars"]
    truncated_text = len(text) > budget
    if truncated_text:
        text = text[:budget] + "\n\n... [response truncated] ..."

    header = f"{status} {final_url}\n{content_type or 'unknown type'}\n\n"
    return {
        "content": header + text,
        "status": status,
        "url": final_url,
        "bytes": len(body),
        "truncated": truncated_bytes or truncated_text,
    }


def search(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    endpoint = getattr(context, "search_endpoint", None)
    if not endpoint:
        raise ValueError("No search endpoint configured.")
    query = urllib.parse.urlencode({"q": args["query"], "limit": args["limit"]})
    separator = "&" if urllib.parse.urlparse(endpoint).query else "?"
    request = urllib.request.Request(
        f"{endpoint}{separator}{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        import json

        payload = json.loads(response.read(MAX_BYTES).decode("utf-8", errors="replace"))

    results = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(results, list):
        raise ValueError("Search endpoint returned an unexpected shape.")
    lines: list[str] = []
    for item in results[: args["limit"]]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        link = str(item.get("url") or item.get("link") or "").strip()
        snippet = str(item.get("snippet") or item.get("content") or "").strip()
        lines.append(f"- {title}\n  {link}\n  {snippet}".rstrip())
    return {
        "content": "\n".join(lines) or "(no results)",
        "count": len(lines),
    }


TOOLS = [
    ToolSpec(
        "webfetch",
        "Fetch an http(s) URL and return its readable text. Only allowlisted hosts.",
        FetchArgs,
        fetch,
        read_only=True,
        requires=("webfetch_hosts",),
    ),
    ToolSpec(
        "websearch",
        "Search the web and return titles, links and snippets.",
        SearchArgs,
        search,
        read_only=True,
        requires=("search_endpoint",),
    ),
]
