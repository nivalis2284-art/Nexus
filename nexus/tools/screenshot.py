"""Capture the desktop or a public page for visual inspection."""
import base64
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from ..cache import cache_dir
from .registry import ToolSpec


class ScreenshotArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: str = Field(description="'screen' for the full desktop, or an http(s) URL for a webpage.")


def _edge() -> str:
    for candidate in (shutil.which("msedge"),
                      str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe"),
                      str(Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe")):
        if candidate and Path(candidate).is_file():
            return candidate
    raise ValueError("Microsoft Edge 未安装，无法截取网页。")


def capture(context, args):
    if not getattr(getattr(context, "agent", None), "profile", None) or not context.agent.profile.capabilities.images:
        raise ValueError("当前模型未启用图片识别；如果模型支持图片，请在设置中编辑提供商并勾选该模型的多模态。")
    from PIL import Image, ImageGrab

    target = args["target"].strip()
    folder = cache_dir() / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (uuid.uuid4().hex + ".jpg")
    if target == "screen":
        if os.name != "nt":
            raise ValueError("屏幕截图目前仅支持 Windows。")
        image = ImageGrab.grab(all_screens=True)
    else:
        parsed = urlsplit(target)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("target 应为 screen 或完整的 HTTP(S) 网页地址。")
        with tempfile.TemporaryDirectory(prefix="browser-", dir=cache_dir()) as scratch:
            png = Path(scratch) / "page.png"
            try:
                proc = subprocess.run([_edge(), "--headless", "--disable-gpu", "--no-first-run",
                    "--hide-scrollbars", "--window-size=1440,900", "--user-data-dir=" + str(Path(scratch) / "profile"),
                    "--screenshot=" + str(png), target], capture_output=True, timeout=45,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired as exc:
                raise ValueError("网页截图超时。") from exc
            if proc.returncode != 0 or not png.is_file():
                raise ValueError("网页截图失败，请检查地址和 Edge 是否可用。")
            with Image.open(png) as source:
                image = source.convert("RGB")
    image.convert("RGB").save(path, format="JPEG", quality=80, optimize=True)
    data = path.read_bytes()
    if len(data) > 5 * 1024 * 1024:
        path.unlink()
        raise ValueError("截图超过 5MB，无法传给模型。")
    return {"content": f"Screenshot saved to {path}. Inspect the attached image.",
            "image": {"type": "image", "mime": "image/jpeg", "data": base64.b64encode(data).decode("ascii")}}


TOOLS = [ToolSpec("screenshot", "Capture the Windows desktop (target='screen') or a webpage by URL and visually inspect it. Requires an image-capable model. Webpages open in a new temporary Edge session.", ScreenshotArgs, capture, read_only=True)]
