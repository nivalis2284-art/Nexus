# 网页搜索与浏览器插件：实现参考

本插件使用 Nexus 公开插件接口注册工具和页面；浏览器使用 Python 标准库启动本机已安装的 Chromium 系浏览器，通过本机回环地址连接 Chrome DevTools Protocol（CDP）。独立配置目录保存会话，关闭时先请求 `Browser.close`，必要时结束插件自己启动的进程。模型工具使用网页快照中的元素引用执行基本操作，每次快照更新引用，引用失效时需要重新读取。插件实现不依赖 Playwright 或 Node。

协议参考：[Chrome DevTools Protocol 文档](https://chromedevtools.github.io/devtools-protocol/)，包括 [Browser](https://chromedevtools.github.io/devtools-protocol/tot/Browser/)、[Page](https://chromedevtools.github.io/devtools-protocol/tot/Page/)、[Runtime](https://chromedevtools.github.io/devtools-protocol/tot/Runtime/) 和 [Target](https://chromedevtools.github.io/devtools-protocol/tot/Target/) 域。搜索适配分别使用 Bing 公共 HTML、[Brave Search API](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started) 和 [SearXNG 搜索 API](https://docs.searxng.org/dev/search_api.html) 的 JSON 接口。Bing 的结果页面结构及访问限制可能变化，测试夹具不能证明线上服务始终可用。

Hermes Agent 的浏览器工具是相关的上游参考项目：[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)。本次尝试读取其源码时 GitHub API 返回 403，原始源码请求返回 429，未能实际阅读或比较其浏览器工具实现；本插件不声称复制该实现。

验证边界：当前云环境网络策略不允许访问 Bing，未进行 Bing 公网实测；Windows 环境和外部 Brave、SearXNG 服务也需要在用户环境中验证。自动化测试使用本地服务与页面夹具，不能替代真实网站的兼容性验证。云环境测试使用无头浏览器；实际用户默认使用有窗口的浏览器。
