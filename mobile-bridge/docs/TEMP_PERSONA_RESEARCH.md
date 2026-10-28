# 临时人设：Hermes 参考研究

研究日期：2026-10-28。

以下参考 NousResearch 的公开 Hermes Agent 源码，链接指向 `main`，后续版本的行号和行为可能变化。Hermes 的实现用于理解身份与人格配置，不代表本插件复制或依赖 Hermes。

- Hermes 从当前 `HERMES_HOME` 或指定 profile home 读取 `SOUL.md` 作为身份材料，加载时检查内容并限制长度；参见 [`agent/prompt_builder.py` 的 `load_soul_md`](https://github.com/NousResearch/hermes-agent/blob/main/agent/prompt_builder.py#L1512-L1557)。[`hermes_cli/config.py`](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/config.py#L528-L539) 首次播种默认内容，并注明不会覆盖用户实际定制的文件。默认模板见 [`default_soul.py`](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/default_soul.py#L9-L25)。
- 人格 overlay 与身份文件是不同机制。[`hermes_cli/personality.py`](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/personality.py#L1-L8) 区分人格选择、用户手写系统提示和自定义人格；它支持将系统提示、语气和风格渲染为文本，以及合并内置与自定义人格（[渲染](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/personality.py#L58-L67)、[解析与持久化](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/personality.py#L86-L139)）。Gateway 的人格选择写入 profile 配置并在后续轮次生效，见 [`session_tui_config.py`](https://github.com/NousResearch/hermes-agent/blob/main/gateway/session_tui_config.py#L267-L281)。
- Hermes 默认安全配置将 `SOUL.md` 等列为受保护的指令文件，源码说明写入需要人工批准，见 [`config_defaults.py`](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/config_defaults.py#L1771-L1775)。已核查的核心实现不足以证明 Hermes 默认自动、无审批地演化人格。记忆或技能的维护也不能作为人格自我演化的证据。

本插件自己的设计是：将所有者设定与可变描述分开，基于启用后的主对话由独立模型生成候选，默认在版本和冻结检查通过后应用，并保留依据、版本及回滚。定时反思、自动应用和记录机制是本插件的设计，不声称它们来自 Hermes；人格文字更新也不等同于已验证的行为改善。具体配置与数据范围见 [TEMP_PERSONA.md](TEMP_PERSONA.md)。
