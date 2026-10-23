"""Read readable text from JavaScript-rendered pages with headless Edge."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

NAV_TIMEOUT_MS = 30_000
LOAD_WAIT_MS = 1_000

_PAGE_TEXT_SCRIPT = """
() => {
  let root = null;
  for (const selector of ["main", "article", "body"]) {
    const candidate = document.querySelector(selector);
    if (candidate && candidate.innerText.trim()) {
      root = candidate;
      break;
    }
  }
  if (!root) {
    return {title: document.title.trim(), text: ""};
  }

  const clone = root.cloneNode(true);
  clone.querySelectorAll("script, style, noscript, nav, header, footer, aside, form, " +
    "iframe, svg, canvas, template, dialog").forEach((node) => node.remove());

  const holder = document.createElement("div");
  holder.style.position = "absolute";
  holder.style.left = "-100000px";
  holder.style.top = "0";
  holder.style.width = "800px";
  if (clone.tagName === "BODY") {
    while (clone.firstChild) holder.appendChild(clone.firstChild);
  } else {
    holder.appendChild(clone);
  }
  document.body.appendChild(holder);
  const text = holder.innerText || "";
  holder.remove();
  return {title: document.title.trim(), text};
}
"""


class PageReadError(RuntimeError):
    """A page could not be read; the message is suitable for the model."""


def _normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


@contextmanager
def _temporary_process_temp(path: Path):
    """Set TEMP/TMP only while Playwright starts its driver and browser."""
    previous = {key: os.environ.get(key) for key in ("TEMP", "TMP")}
    os.environ["TEMP"] = str(path)
    os.environ["TMP"] = str(path)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class PageReader:
    """Fetch a URL with headless Microsoft Edge and return its title and text."""

    def __init__(self, temp_dir=None) -> None:
        self._playwright = None
        self._browser = None
        self._thread_id: int | None = None
        self._temp_dir = temp_dir
        self._context = None
        self._run_dir = None

    def _check_thread(self) -> None:
        current = threading.get_ident()
        if self._thread_id is None:
            self._thread_id = current
        elif self._thread_id != current:
            raise PageReadError(
                "PageReader must be used from the thread that created it."
            )

    def _ensure_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser

        if self._playwright is None:
            if self._temp_dir is not None:
                Path(self._temp_dir).mkdir(parents=True, exist_ok=True)
                self._run_dir = tempfile.mkdtemp(prefix="edge-", dir=self._temp_dir)
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise PageReadError(
                    "Playwright is not installed. Install the scheduler dependencies."
                ) from exc
            try:
                if self._run_dir is not None:
                    with _temporary_process_temp(Path(self._run_dir)):
                        self._playwright = sync_playwright().start()
                else:
                    self._playwright = sync_playwright().start()
            except Exception as exc:
                raise PageReadError(
                    "Could not start Playwright. Reinstall the scheduler dependencies "
                    "so the Playwright driver is available."
                ) from exc

        try:
            launch_env = {"TEMP": self._run_dir, "TMP": self._run_dir} if self._run_dir else None
            if self._temp_dir is not None:
                self._context = self._playwright.chromium.launch_persistent_context(
                    str(Path(self._run_dir) / "edge-profile"), channel="msedge",
                    headless=True, env=launch_env)
                self._browser = self._context.browser
            else:
                self._browser = self._playwright.chromium.launch(
                    channel="msedge", headless=True)
        except Exception as exc:
            raise PageReadError(
                "Could not launch headless Microsoft Edge through Playwright. "
                "Ensure Microsoft Edge is installed and the Playwright dependency is usable."
            ) from exc
        return self._browser

    def read(self, url: str) -> str:
        """Return the page title followed by normalized readable body text."""
        self._check_thread()
        url = url.strip()
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
        except ValueError as exc:
            raise PageReadError(f"Invalid URL: {url}") from exc
        if parsed.scheme.lower() not in ("http", "https") or not hostname:
            raise PageReadError("PageReader only accepts absolute http or https URLs.")
        if any(character.isspace() for character in url):
            raise PageReadError("Invalid URL: whitespace must be percent-encoded.")

        browser = self._ensure_browser()
        try:
            from playwright.sync_api import Error as PlaywrightError
        except ImportError as exc:
            raise PageReadError(
                "Playwright is not installed. Install the scheduler dependencies first."
            ) from exc

        page = None
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(LOAD_WAIT_MS)
            content = page.evaluate(_PAGE_TEXT_SCRIPT)
        except PlaywrightError as exc:
            message = str(exc).splitlines()[0].strip()
            raise PageReadError(f"Could not read {url}: {message or 'browser error'}") from exc
        finally:
            if page is not None:
                with suppress(PlaywrightError):
                    page.close()

        if not isinstance(content, dict):
            raise PageReadError(f"Could not extract readable text from {url}.")
        title = _normalize_text(str(content.get("title") or ""))
        body = _normalize_text(str(content.get("text") or ""))
        if title and body:
            return f"{title}\n\n{body}"
        if title or body:
            return title or body
        raise PageReadError(f"The page at {url} contained no readable text.")

    def close(self) -> None:
        """Close the browser and stop Playwright. Safe to call more than once."""
        self._check_thread()
        browser, self._browser = self._browser, None
        context, self._context = self._context, None
        playwright, self._playwright = self._playwright, None
        try:
            if context is not None:
                with suppress(Exception):
                    context.close()
            elif browser is not None:
                with suppress(Exception):
                    browser.close()
        finally:
            if playwright is not None:
                with suppress(Exception):
                    playwright.stop()
        if self._run_dir is not None:
            shutil.rmtree(self._run_dir, ignore_errors=True)
            self._run_dir = None
