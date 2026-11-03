/* 纯虚构设计数据。没有密钥、真实会话、用户路径或后端状态。 */
'use strict';
window.Preview = { timers: new Set(), scenario: 'conversation', failNext: false, files: new Map() };
(function (P, N) {
  const markdown = `## 一份清晰的工作结果

这是独立设计演示。**已读内容不会重播**，新内容逐步显现。

### 已完成
- 整理设计参数与主题配色
- 检查页面布局和卡片追光
- 保留阅读位置与消息身份

> 引用保留引用线，普通正文没有额外左边框。

| 组件 | 状态 | 说明 |
| --- | --- | --- |
| 卡片 | 完成 | 不透明表面 |
| 动效 | 检查中 | 统一时序 |

\`\`\`js
const duration = N.DUR.enter; // 560ms
N.enterItems(cards, viewport);
\`\`\`

支持中文、emoji 🌙，以及[本地设计说明](README.md)。`;
  P.markdown = markdown;
  P.fixture = () => {
    const items = [
      { key: 'u:sample', requestId: 'sample', index: 0, role: 'user', text: '帮我整理一下设计模板，并说明关键取舍。', at: '10:20' },
      { key: 't:sample', role: 'thinking', text: '', thinking: '先检查现有设计，再整理可以复用的参数与组件。', at: '10:20', elapsed: 2.6 },
      { key: 'tool:sample', role: 'tool', name: 'read', callId: 'demo-read', arguments: { path: '示例项目/DESIGN.md' }, text: '卡片 20px；控件 12px；间距遵循 4px 网格。', ok: true, at: '10:20' },
      { key: 'a:sample', index: 1, role: 'assistant', text: markdown, at: '10:21', elapsed: 14 },
    ];
    const work = (id, title, state, extra = {}) => ({ id, title, state, goal: '制作清晰、可复用的设计预览。', path: '示例项目/Design', model: 'demo-model', started: '10:20', progress: '整理组件及交互细节', work_summary: '已完成参数梳理，正在核对各页面状态。', next_step: '检查窄屏布局与阅读锚点', steps: [{ content: '整理现有设计', status: 'completed', verification: 'verified' }, { content: '核对交互和动效', status: 'in_progress', verification: '' }], records: items.slice(0, 1).concat(items.slice(-1)), pending_interactions: [], ...extra });
    return {
      ...N.seed(), connected: true, sessionId: 'design-preview', historyVersion: 'preview-v1', view: 'chat', scene: 'conversation',
      workspace: '示例项目/Work', project: { name: 'Work', path: '示例项目/Work' }, defaultProject: { name: 'Work', path: '示例项目/Work' },
      projects: [{ name: '设计实验', path: '示例项目/Design' }, { name: '很长的项目名称用于检查省略和布局适应能力', path: '示例项目/Long/用于检查路径溢出的长目录名称' }],
      items, baseItems: items, pending: [], liveItems: [],
      works: [work('work-1', '整理组件与参数', 'running'), work('work-2', '核对设计方向', 'waiting_user', { pending_interactions: [{ id: 'approval-1', kind: 'approval', payload: { name: 'write', arguments: { path: '示例项目/说明.md' }, reason: '演示审批卡片，不会写入磁盘。' } }] }), work('child-1', '检查移动布局', 'queued', { parent_id: 'work-1', parent_kind: 'task' }), work('work-3', '准备主题样张', 'paused'), work('work-4', '完成参数清单', 'completed', { finished: '10:12' }), work('work-5', '旧版方案', 'cancelled'), work('work-6', '已归档研究', 'completed', { archived: true, finished: '昨天' })],
      context: { system: 6000, tools: 5000, messages: 37000, window: 200000 }, contextWindow: 200000,
      usage: { input: 12000, cached: 36000, created: 0, output: 4200, total_input: 48000, total: 52200, has_usage: true, complete: true, estimated: false },
      contextData: { version: 'preview-v1', running: false, owner_instructions: ['先说明关键取舍，再执行。', '保持两套主题的几何和动效节奏一致。'], roles: ['main', 'work', 'subagent'].map((id, i) => ({ id, title: ['主交流', '后台工作', '子代理'][i], text: ['持续交流，监督并解释工作结果。', '围绕目标执行，保留步骤和验证证据。', '处理聚焦的委派任务，说明结果与限制。'][i] })), sections: ['运行环境', '项目指令', '技能', '工作观察', '权限与工具'].map(title => ({ title, text: `${title}示例\n这是只读的设计演示数据。展开区域使用统一边界、圆角和行距。` })), summary: { through: 5, text: '此前已确认四种主题组合、4px 网格及统一动效。这里只展示摘要的编辑与排版。' }, turns: [{ user: items[0].text, reply: '已整理参数和组件，正在核对交互。', indices: [0, 1], tools: 1, at: '2026-11-01T10:20:00' }, { user: '长文本如何处理？', reply: '正文换行，路径省略，代码与表格必要时横向滚动。', indices: [2, 3], tools: 0, at: '2026-11-01T10:22:00' }] },
      providers: [{ id: 'demo-provider', name: '示例提供商', family: 'openai_responses', base_url: 'https://example.invalid/v1', has_key: false, models: [{ id: 'demo-model', context_window_tokens: 200000, max_output_tokens: 8192, capabilities: { images: true } }, { id: 'text-model', context_window_tokens: 128000, max_output_tokens: 4096, capabilities: { images: false } }] }],
      current: { provider: 'demo-provider', model: 'demo-model', effort: 'medium' }, effort: { levels: ['low', 'medium', 'high'], labels: { low: '低', medium: '中', high: '高' }, current: 'medium' }, instructions: '先说明关键取舍；设计参数复用现有规范。',
      skills: [{ name: 'writing-style', source: 'personal', description: '按清晰、简洁的风格整理文档。', body: '# Writing\n\n先给结论，再给依据。', path: 'personal/writing-style', effective: false, read_only: false }, { name: 'writing-style', source: 'project', description: '项目版本覆盖同名个人技能。', body: '# Project writing\n\n沿用项目词汇。', path: 'project/writing-style', effective: true, read_only: false }, { name: 'design-review', source: 'builtin', description: '检查视觉、交互和可访问性。', body: '# Review\n\n检查真实渲染效果。', path: 'builtin/design-review', effective: true, read_only: true }],
      mcp: [{ name: 'design-tools', transport: 'stdio', command: 'demo-command', args: ['--preview'], env: {}, enabled: true, connected: true, tool_count: 3 }, { name: 'http-example', transport: 'http', url: 'https://example.invalid/mcp', enabled: false, connected: false, tool_count: 0, error: '' }],
      pluginRoot: '示例插件目录', plugins: [{ id: 'design-demo', name: '设计示例插件', version: '1.0.0', description: '展示插件管理、介绍、运行状态和两种功能页。所有操作仅在浏览器内演示。', enabled: true, status: 'ready', active_calls: 0, capabilities: [{ title: '组件预览' }, { title: '本地演示' }, { title: '状态检查' }], pages: [{ id: 'components', title: '宿主组件全览', renderer: 'host' }, { id: 'iframe', title: 'iframe 设计示例', renderer: 'iframe', path: 'demo.html' }], settings_schema: { properties: { label: { type: 'string', title: '显示名称', description: '用于检查表单草稿。' }, count: { type: 'integer', title: '显示条数', minimum: 1, maximum: 20 }, enabled: { type: 'boolean', title: '显示辅助信息' }, mode: { type: 'string', title: '排列方式', enum: ['均衡', '紧凑'] } } }, settings: { label: '设计预览', count: 6, enabled: true, mode: '均衡' } }, { id: 'disabled-demo', name: '停用状态示例', version: '0.2.0', description: '检查停用、加载和错误状态。', enabled: false, status: 'unloaded', capabilities: [], pages: [], settings: {} }],
      modelRequests: [{ id: 1, created_at: '2026-11-01T10:21:00', kind: 'ling', owner_id: 'design-preview', model: 'demo-model', status: 'completed', plugins: [{ name: '设计示例插件', status: 'used', reason: '演示参与来源' }], materials: [{ title: '设计规范', text: '示例材料' }], body: { model: 'demo-model', messages: [{ role: 'user', content: '虚构请求，仅展示 JSON 排版。' }] } }, { id: 2, created_at: '2026-11-01T10:22:00', kind: 'task', owner_id: 'work-1', model: 'demo-model', status: 'error', error: '演示请求失败', plugins: [], body: { model: 'demo-model' } }],
    };
  };
  P.hooks = [{ name: 'before_request', description: '模型请求前', owner: 'host', enabled: true, available: true, subscribers: [{ plugin_id: 'design-demo', key: 'append-preview', enabled: true, available: true, required: false }] }, { name: 'after_tool', description: '工具执行后', owner: 'host', enabled: false, available: true, subscribers: [] }];
  const initialHooks = JSON.parse(JSON.stringify(P.hooks));
  P.resetData = () => { Object.assign(N.state, P.fixture()); N.state.baseItems = N.state.items; P.hooks = JSON.parse(JSON.stringify(initialHooks)); P.history = null; };
  P.resetData();
})(window.Preview, window.N);
