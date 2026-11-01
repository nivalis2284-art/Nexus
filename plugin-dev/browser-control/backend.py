"""Expose the browser worker through ordinary Nexus tools."""
import base64
import uuid

from nexus.cache import cache_dir
from nexus.plugins.api import BaseModel, Field, ToolSpec


class BrowserArgs(BaseModel):
    action: str = Field(description="open, snapshot, click, fill, press, select, scroll, tabs, new_tab, switch, close_tab, back, or close")
    value: str = Field(default="", description="URL, text, key, option, scroll direction, or tab number, depending on action")
    ref: str = Field(default="", description="Element reference from the latest snapshot")


class ScreenshotArgs(BaseModel):
    pass


class Plugin:
    def __init__(self, host):
        self.host = host

    def tools(self, scope):
        owner = str(scope.get("kind", "session")) + ":" + str(scope.get("owner_id", "default"))

        def browser(ctx, args):
            return self.host.call_service("worker", "browser", owner=owner,
                                          **args)

        def screenshot(ctx, args):
            profile = getattr(getattr(ctx, "agent", None), "profile", None)
            if not profile or not profile.capabilities.images:
                raise ValueError("当前模型未启用图片识别，请在模型设置中启用多模态。")
            result = self.host.call_service("worker", "screenshot", owner=owner)
            data = result.pop("image_base64")
            folder = cache_dir() / "screenshots"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (uuid.uuid4().hex + ".jpg")
            path.write_bytes(base64.b64decode(data))
            return {"content": f"当前浏览器页面截图已保存至 {path}。", "image": {
                "type": "image", "mime": "image/jpeg", "data": data}}

        return [
            ToolSpec("browser_control", "Operate a visible browser. Use snapshot to inspect the current page and its element refs before interacting. Ask the user before consequential actions such as sending, publishing, purchases or deletion. Open only URLs relevant to the user's request.", BrowserArgs, browser),
            ToolSpec("browser_screenshot", "Visually inspect a screenshot of the current browser tab. Requires an image-capable model.", ScreenshotArgs, screenshot, read_only=True),
        ]


def create_plugin(host):
    return Plugin(host)
