"""Nexus tool declarations; dependencies run in the plugin's MCP worker."""
from nexus.plugins.api import BaseModel, Field, ToolSpec


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, description="Search query.")
    limit: int = Field(default=5, ge=1, le=10)


class ReadArgs(BaseModel):
    url: str = Field(description="Public http(s) page or raw text URL.")
    offset: int = Field(default=0, ge=0, description="Character offset for the next page.")
    limit: int = Field(default=12000, ge=1000, le=30000)


class GithubArgs(BaseModel):
    repository: str = Field(description="Public GitHub repository, owner/name.")
    path: str = Field(default="", description="Repository directory or file path.")
    ref: str = Field(default="", description="Branch, tag or commit; empty means default branch.")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=12000, ge=1000, le=30000)


class Plugin:
    def __init__(self, host):
        self.host = host

    def tools(self, scope):
        def search(ctx, args):
            settings = self.host.settings
            return self.host.call_service("worker", "search", **args,
                provider=settings.get("provider", "DDGS"),
                api_key=settings.get("tavily_api_key", ""))

        def read(ctx, args):
            return self.host.call_service("worker", "read_page", **args)

        def github(ctx, args):
            return self.host.call_service("worker", "read_github", **args)

        return [
            ToolSpec("nexus_web_search", "Search the web for sources; open relevant results to verify them.", SearchArgs, search, read_only=True),
            ToolSpec("nexus_web_read", "Read a public webpage and its links. Use offset to continue long pages.", ReadArgs, read, read_only=True),
            ToolSpec("nexus_github_read", "List a public GitHub repository directory or read a source file, optionally at a ref.", GithubArgs, github, read_only=True),
        ]


def create_plugin(host):
    return Plugin(host)
