'use strict';

// Context workspace: editable instructions, roles and history; read-only assembly.
(function () {
  const P = window.P;
  if (!P) return;

  P.views = P.views || {};
  P.actions = P.actions || {};

  const esc = value => P.esc(value === null || value === undefined ? '' : String(value));
  const settings = () => P.data.settings || {};
  const messages = () => P.messages || [];
  const running = () => Boolean(P.running);
  const selected = new Set();

  const DEFAULT_RULES = [
    '当前任务以明确需求为准，不提前扩展功能。',
    '先完成最小可用闭环，必要时说明取舍。',
  ];
  const DEFAULT_SUMMARY = [
    '此前已确认：Nexus 以“灵”作为持续交流入口，后台工作独立执行；主交流负责承接要求、派发工作与解释结果。',
    '本轮目标是为现有前端制作独立预览，保留纸感、灵岛与弹簧动效，并把主色调整为浅蓝与淡紫。先确认主交流、后台工作与上下文管理的整体使用感受。',
    '界面调整仍在预览阶段，确认后才会替换正式前端；插件内部页面保持现状，下一步再逐项验收桌面与手机布局。',
  ].join('\n\n');

  const ROLE_CAPTIONS = {
    main: '持续交流、承接要求并解释工作结果',
    work: '围绕目标独立执行，保留过程与证据',
    subagent: '聚焦清晰的委派范围，向父工作汇报',
  };
  const MESSAGE_LABELS = { user: '所有者', assistant: '灵', tool: '工具', notice: '记录' };
  const MESSAGE_MARKS = { user: '你', assistant: '灵', tool: '具', notice: '记' };
  const SOURCE_ORDER = ['project', 'personal', 'builtin'];
  const SOURCE_LABEL = { project: '项目', personal: '个人', builtin: '内置' };

  const PROJECT_TEXT = [
    '# AGENTS.md · Nexus',
    'Path: H:/文档/LingNexus/Nexus/AGENTS.md',
    'Scope: 仓库根目录及以下；嵌套文件覆盖其所在目录及子目录。',
    '',
    '本文件是仓库级开发约定。当前任务以用户明确指令和已确认的需求（如 PRD）为准；技术实现由 Agent 在这些边界内自主判断。项目介绍、启动方式和现有架构见 README.md。',
    '',
    '## 产品边界',
    '- 不立假想敌。不得仅凭假设的威胁、极端负载、未知兼容场景或未来使用方式扩大当前任务、堆叠防御措施。对实际证实的安全问题、数据损坏或其他缺陷，应分析影响并妥善处理。',
    '- 先完成最小可用闭环，防御措施须有具体依据。新增校验、限制、审批、重试、降级或兼容处理，应能对应当前明确需求、已复现的缺陷，或从现有代码与实际运行条件可直接推导的具体问题；仅凭“可能出错”不足以作为依据。必要处理以解决该问题的最小范围为限，不借机扩展成通用防御框架。不影响当前交付的假设性问题可记录后续处理，不默认纳入实现；不得以本条为由省略正常业务逻辑、必要错误提示或已确认的验收要求。',
    '- 没有需求，不提前投资技术。不为“将来可能有用”而预先引入框架、插件体系、复杂抽象、兼容层等。确有当前需求或确实是交付所必需时，再依据实际情况选型。',
    '- 不擅自改变产品目标。已确认的功能范围、关键用户体验和验收标准由用户决定；有歧义或涉及重大产品取舍时先确认。不得以技术便利为由增加或削减需求。',
    '',
    '## 工程执行',
    '- 按需并发委派。对适合拆分的工作，默认使用多个 DeepSeek 子代理并发执行，并按子任务难度选择 high 或 max 思考强度。主代理负责重难点任务的推理与执行，以及整合与关键判断；将重复度高、繁琐或简单且可独立完成的任务合理拆分给子代理。无需为简单修改或存在先后依赖的步骤强行拆分、并发。服务不可用时及时说明并接手，不反复空等。',
    '- 对实现方式保留自主判断。按实际需求选择架构、工具和代码组织方式，以正确性与可维护性为目标；不为形式上的“最佳实践”而制造额外复杂度。',
    '- 及时止损。同一问题经多种有依据的尝试仍无进展时，停止盲目试错，按需查阅指定参考项目、官方资料或成熟案例；仍无法解决则记录阻塞与已尝试方案，优先推进不受影响的任务。关键阻塞及时说明。',
    '- 如实验证和汇报。根据风险与影响进行必要测试，区分实测、推断和未验证事项；不得以跳过必要检查或掩盖真实问题的方式宣称完成。',
    '',
    '## 文档与操作',
    '- 当前需求决定交付目标；源码及实测说明现状；docs/愿景/ 是长期方向，docs/参考/ 是历史材料，均不自动构成当前任务清单。',
    '- 需求、研究、进度、验收等项目文档放在 docs/；按实际变更更新，不为记录而制造重复文档。',
    '- 提交、推送、合并和破坏性操作遵循当前用户授权，不从历史对话推定权限。',
  ].join('\n');

  const PRIORITY_TEXT = [
    '# Instruction sources and priority',
    '- Owner instructions are the owner\'s persistent preferences from the Nexus data',
    '  directory. They are not repository files.',
    '- Project instructions are labeled files found from the filesystem root down to this',
    '  workspace and in directories already touched in this run. Each block names its',
    '  absolute path and scope; a nested file covers that directory and below.',
    '- The owner is the highest authority. An explicit current owner request overrides',
    '  general preferences, examples, templates, ordinary habits, older summaries, and',
    '  the owner\'s own project instructions, and may change them; state which rule',
    '  changed.',
    '- A general or vague request yields to an explicit, specific project boundary in',
    '  scope. That boundary never overrides an explicit owner request.',
    '- Only when a conflict cannot be resolved from the request and the rules, ask the',
    '  owner instead of guessing.',
    '- A template, example, or ordinary habit is guidance; do not promote it into a hard',
    '  prohibition.',
  ].join('\n');

  const DEMO_SKILLS = [
    { name: 'project-conventions', source: 'project', path: 'project-conventions/SKILL.md', description: '当前项目的开发边界、文档位置与提交授权约定。' },
    { name: 'impeccable', source: 'personal', path: 'impeccable/SKILL.md', description: '界面审视、层级、排版、动效、响应式与可访问性检查。' },
    { name: 'demo-design-notes', source: 'builtin', path: 'demo-design-notes/SKILL.md', description: '记录页面视觉方向，以及留白、字号和内容密度的检查重点。' },
    { name: 'demo-interface-review', source: 'builtin', path: 'demo-interface-review/SKILL.md', description: '核对交互细节、真实视口布局与减少动效偏好。' },
  ];

  const TOOL_GROUPS = [
    {
      title: '文件与检索',
      items: [
        ['read_file', '读取 UTF-8 文件并带行号；大文件分页读取。'],
        ['write_file', '创建或覆盖文件；已有文件优先使用 edit_file。'],
        ['edit_file', '替换唯一且完全匹配的文本块；匹配不唯一时拒绝。'],
        ['glob', '按 glob 模式查找文件，例如 **/*.py。'],
        ['grep', '使用正则表达式搜索文件内容。'],
        ['apply_patch', '应用标准 unified diff；所有 hunk 必须精确匹配。'],
      ],
    },
    {
      title: '命令与后台任务',
      items: [
        ['bash', '以参数列表运行命令，不经过 shell 解释；工作目录为项目根目录。'],
        ['bash_background', '启动可长期运行的命令并立即返回 job id。'],
        ['job_output', '从游标读取后台任务的新输出，可短暂等待新内容。'],
        ['job_kill', '停止仍在运行的后台任务。'],
        ['job_list', '列出后台任务及其运行状态。'],
      ],
    },
    {
      title: '交流与工作',
      items: [
        ['work_start', '启动独立工作并立即返回 task id，不等待完成。'],
        ['work_list', '列出实际工作状态，不启动或恢复工作。'],
        ['work_read', '分页读取工作摘要、消息、决定、证据或未确认操作。'],
        ['work_message', '把所有者补充要求写入现有工作的下一个模型边界。'],
        ['work_control', '仅对明确指定的工作暂停、恢复或取消。'],
        ['work_models', '列出可用于后台工作的已配置模型。'],
        ['work_model', '修改指定工作下次请求使用的模型与思考强度。'],
        ['work_pause_all', '仅在所有者明确要求时暂停全部工作。'],
        ['task_read', '分页读取当前任务保存的状态与记录。'],
        ['todo_write', '维护本轮待办清单、状态与验证结果。'],
        ['ask_user', '在决定确实属于所有者时集中提问；不用于确认可自行验证的事项。'],
      ],
    },
    {
      title: '委派',
      items: [
        ['subagent', '委派独立范围并立即返回 task id；不等待子任务完成。'],
        ['subagent_list', '列出子代理的真实状态。'],
        ['subagent_read', '读取子代理结果或分页记录。'],
        ['subagent_message', '在子代理下一个模型边界发送有序补充。'],
        ['subagent_control', '仅控制自己的子代理，不能代为批准所有者决定。'],
        ['subagent_model', '修改子代理下次请求使用的模型与思考强度。'],
        ['subagent_wait', '父代理没有独立工作时等待全部子代理完成。'],
      ],
    },
    {
      title: '技能与外部信息',
      items: [
        ['read_skill', '按名称读取已安装技能的正文。'],
        ['read_attachment', '读取本次交流实际发送的附件文本；需要附件来源。'],
        ['webfetch', '抓取 allowlist 内 http(s) 地址的正文；需要 webfetch_hosts。'],
        ['websearch', '搜索网页并返回标题、链接和摘要；需要 search_endpoint。'],
      ],
    },
  ];

  const context = P.data.context || (P.data.context = {});
  let initialized = false;
  if (!Array.isArray(context.rules)) {
    context.rules = DEFAULT_RULES.slice();
    initialized = true;
  }
  if (typeof context.summary !== 'string') {
    context.summary = DEFAULT_SUMMARY;
    initialized = true;
  }
  if (context.tab !== 'history' && context.tab !== 'system') {
    context.tab = 'system';
    initialized = true;
  }
  if (typeof context.refreshedAt !== 'string') {
    context.refreshedAt = '';
    initialized = true;
  }
  if (initialized) P.save();

  function currentModel() {
    const value = settings();
    const providers = Array.isArray(value.providers) ? value.providers : [];
    const current = value.current || {};
    const provider = providers.find(item => item && item.id === current.provider) || providers[0] || null;
    const models = provider && Array.isArray(provider.models) ? provider.models : [];
    const model = models.find(item => item && item.id === current.model) || models[0] || null;
    const windowSize = Number(model && model.context_window_tokens) || 64000;
    return {
      provider,
      model,
      windowSize,
      name: provider ? provider.name : '未配置提供商',
      modelName: model ? model.id : '选择模型',
    };
  }

  function shortPath(value) {
    const parts = String(value || '').replace(/\\/g, '/').split('/').filter(Boolean);
    return parts.length > 2 ? '…/' + parts.slice(-2).join('/') : String(value || '');
  }

  function formatRefresh(value) {
    if (!value) return '尚未手动刷新';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '刚刚';
    return date.toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  }

  function environmentText() {
    const workspace = settings().workspace || '未设置工作目录';
    const platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || 'Windows';
    const date = new Date();
    const day = [
      date.getFullYear(),
      String(date.getMonth() + 1).padStart(2, '0'),
      String(date.getDate()).padStart(2, '0'),
    ].join('-');
    return [
      'Environment:',
      '- Platform: ' + platform,
      '- Date: ' + day,
      '- Shell: PowerShell (Windows)',
      '- Workspace: ' + workspace,
      '- Application: Nexus 本地界面预览',
      '- Network: 本地演示，不发起模型或后端请求',
      '',
      'Workspace:',
      '- Root: ' + workspace,
      '- Instruction files: AGENTS.md 位于仓库根目录，作用范围为整个工作区。',
      '- Preview assets: redesign-preview/ 作为独立视觉与交互预览。',
    ].join('\n');
  }

  function skillRows() {
    const state = P.data.skills;
    const items = state && Array.isArray(state.items) ? state.items : [];
    if (!items.length) return DEMO_SKILLS.map(item => ({ ...item, covered: false }));
    const claimed = new Set();
    const ordered = items.slice().sort((a, b) => {
      const left = SOURCE_ORDER.indexOf(a.source);
      const right = SOURCE_ORDER.indexOf(b.source);
      return (left < 0 ? 9 : left) - (right < 0 ? 9 : right) ||
        String(a.name).localeCompare(String(b.name), 'en');
    });
    return ordered.map(item => {
      const covered = claimed.has(item.name);
      if (!covered) claimed.add(item.name);
      return { ...item, covered };
    });
  }

  function skillsText(rows) {
    const lines = [
      '# Skills',
      'Source: installed skill manifests.',
      'Nature: reusable procedures when they apply; they are not project authority.',
      'When one applies, call read_skill with the exact name before doing the work.',
      '',
      'Installed skills:',
    ];
    rows.forEach(item => {
      lines.push('- ' + String(item.name || '未命名技能') + ' · ' +
        (SOURCE_LABEL[item.source] || item.source || '未知来源'));
      if (item.path) lines.push('  path: ' + item.path);
      if (item.description) lines.push('  description: ' + item.description);
      lines.push('  effective: ' + (item.covered ? 'no — 被同名高优先级技能覆盖' : 'yes'));
    });
    return lines.join('\n');
  }

  function observationText() {
    const works = Array.isArray(P.data.works) ? P.data.works : [];
    const requests = Array.isArray(P.data.requests) ? P.data.requests : [];
    const queue = Array.isArray(P.data.queue) ? P.data.queue : [];
    const current = messages();
    const compressed = current.filter(item => item.compressed).length;
    const labels = P.labels || {};
    const lines = [
      '# Continuing interaction',
      'Source: current Nexus conversation and work state.',
      'Nature: context and pending events, not new permission or authorization.',
      '',
      '交流状态:',
      '- 所有者轮次: ' + current.filter(item => item.role === 'user').length,
      '- 未压缩消息: ' + current.filter(item => !item.compressed).length,
      '- 已压缩消息: ' + compressed,
      '- 待处理决定: ' + requests.length,
      '- 等待恢复输入: ' + queue.length,
      '',
      '工作观察:',
    ];
    if (!works.length) lines.push('- 当前没有后台工作。');
    works.forEach(work => {
      lines.push('- 「' + (work.title || '未命名工作') + '」 · ' +
        (labels[work.state] || work.state || '未知状态') + ' · ' + (work.id || ''));
      if (work.goal) lines.push('  goal: ' + work.goal);
      if (work.model || work.effort) {
        lines.push('  model: ' + (work.model || '默认') + ' · effort: ' + (work.effort || '默认'));
      }
      if (work.progress) lines.push('  progress: ' + work.progress);
      if (work.next) lines.push('  next: ' + work.next);
      if (work.blocked) lines.push('  blocked: ' + work.blocked);
      if (work.result) lines.push('  result: ' + work.result);
      if (work.workspace) lines.push('  workspace: ' + work.workspace);
    });
    if (requests.length) {
      lines.push('', '待所有者处理:');
      requests.forEach(request => {
        const work = works.find(item => item.id === request.taskId);
        lines.push('- ' + (request.kind === 'approval' ? '操作审批' : '问题') +
          ' · ' + (work ? work.title : request.taskId || '主交流'));
        if (Array.isArray(request.questions)) {
          request.questions.forEach(question => {
            lines.push('  question: ' + (question.text || ''));
            if (Array.isArray(question.options) && question.options.length) {
              lines.push('  options: ' + question.options.join(' / '));
            }
          });
        }
        if (request.tool) lines.push('  tool: ' + request.tool);
        if (request.status === 'unknown') lines.push('  status: 结果待核对，不自动重复执行');
      });
    }
    if (queue.length) {
      lines.push('', '等待处理的输入:');
      queue.forEach(item => lines.push('- ' + (item.text || '附件消息')));
    }
    return lines.join('\n');
  }

  function permissionText() {
    const mode = settings().permission === 'yolo' ? 'yolo' : 'ask';
    const rules = mode === 'yolo' ? [] : [
      { tool: 'write_file', pattern: '*', action: 'ask' },
      { tool: 'edit_file', pattern: '*', action: 'ask' },
      { tool: 'apply_patch', pattern: '*', action: 'ask' },
      { tool: 'bash', pattern: '*', action: 'ask' },
      { tool: 'bash_background', pattern: '*', action: 'ask' },
    ];
    const lines = [
      '# Permission context',
      'Source: supplied by the main agent for this request.',
      'Nature: the permission and approval information actually available in this',
      'run. Do not assume broader access.',
      '',
      'Tool permission default: allow',
      'Rules (last match wins): ' + JSON.stringify(rules),
      'File tools access only the workspace; allowing a call does not widen this boundary.',
      'Commands run as argument-list processes, without shell interpolation; invoke an',
      'installed shell explicitly when needed.',
      'A tool approval grants only that saved call. Task instructions do not change runtime permissions.',
      '',
      '当前设置: ' + (mode === 'yolo' ? '全部放行' : '写文件和运行命令前询问'),
      '工作区边界: ' + (settings().workspace || '未设置工作目录'),
    ];
    return lines.join('\n');
  }

  function toolsText() {
    const lines = [
      '# Tools',
      'Source: this run\'s tool registry. Only the tools listed below are available.',
      'Call them with their exact names. Arguments must match the schema.',
    ];
    TOOL_GROUPS.forEach(group => {
      lines.push('', '## ' + group.title);
      group.items.forEach(item => lines.push('- ' + item[0] + ' — ' + item[1]));
    });
    const servers = (settings().mcp || []).filter(item => item && item.enabled !== false);
    lines.push('', '## MCP 服务');
    if (!servers.length) {
      lines.push('- 当前没有启用的 MCP 服务。');
    } else {
      servers.forEach(server => {
        lines.push('- ' + (server.name || '未命名服务') + ' · ' +
          (server.transport || 'stdio') + ' · ' +
          (server.connected ? '已连接' : '未连接') + ' · ' +
          (Number(server.tool_count) || 0) + ' 个工具');
        if (server.error) lines.push('  error: ' + server.error);
        if (server.managed) lines.push('  managed by plugin: ' + (server.plugin_id || '插件'));
      });
    }
    return lines.join('\n');
  }

  function sourceSections() {
    const rows = skillRows();
    const value = settings();
    const mode = value.permission === 'yolo' ? '全部放行' : '写前询问';
    const works = Array.isArray(P.data.works) ? P.data.works.length : 0;
    const servers = (value.mcp || []).filter(item => item && item.enabled !== false);
    const sections = [
      { id: 'environment', title: '运行环境', meta: 'PowerShell · 本地预览', text: environmentText() },
      { id: 'priority', title: '指令来源与优先级', meta: '所有者 > 当前请求 > 项目边界', text: PRIORITY_TEXT },
      { id: 'project', title: '项目指令', meta: 'AGENTS.md · 仓库根目录', text: PROJECT_TEXT },
      { id: 'skills', title: '可用技能', meta: rows.length + ' 个已发现', text: skillsText(rows) },
      { id: 'interaction', title: '当前观察', meta: works + ' 项工作 · ' + messages().length + ' 条消息', text: observationText() },
      { id: 'permission', title: '有效权限', meta: mode, text: permissionText() },
      { id: 'tools', title: '本轮可用工具', meta: '核心工具 · ' + servers.length + ' 个 MCP', text: toolsText() },
    ];
    return sections;
  }

  function estimateTokens(sections) {
    let chars = 0;
    sections.forEach(section => { chars += section.text.length; });
    context.rules.forEach(rule => { chars += String(rule).length; });
    context.summary.split('\n').forEach(line => { chars += line.length; });
    (P.data.roles || []).forEach(role => { chars += String(role.text || '').length; });
    messages().forEach(message => { chars += String(message.text || '').length; });
    return Math.ceil(chars / 2.2);
  }

  function overviewHTML(sections) {
    const model = currentModel();
    const tokens = estimateTokens(sections);
    const percent = Math.max(1, Math.min(100, Math.round(tokens / model.windowSize * 100)));
    const compressed = messages().filter(item => item.compressed).length;
    const summaryLength = context.summary.trim().length;
    return [
      '<section class="ctx-overview" aria-label="上下文概况">',
      '<div class="ctx-overview-head">',
      '<div class="ctx-overview-lead">',
      '<span class="ctx-kicker">当前主交流</span>',
      '<h2>约 ' + percent + '% <small>· ' + tokens.toLocaleString('zh-CN') + ' / ' +
        model.windowSize.toLocaleString('zh-CN') + ' tok</small></h2>',
      '<p>' + esc(model.name) + ' · <span class="ctx-mono">' + esc(model.modelName) +
        '</span> · 估算占用，按组装文本与消息字符量计算。</p>',
      '</div>',
      '<div class="ctx-overview-badge' + (running() ? ' is-running' : '') + '">',
      '<i></i><span>' + (running() ? '运行中 · 编辑已锁定' : '空闲 · 可编辑') + '</span>',
      '</div>',
      '</div>',
      '<div class="ctx-meter" role="img" aria-label="上下文估算占用 ' + percent + '%">',
      '<i style="width:' + percent + '%"></i>',
      '</div>',
      '<div class="ctx-metrics">',
      metric('所有者指令', context.rules.length + ' 条'),
      metric('角色规则', (P.data.roles || []).length + ' 个'),
      metric('未压缩消息', messages().filter(item => !item.compressed).length + ' 条'),
      metric('压缩摘要', summaryLength ? summaryLength + ' 字' : '暂无'),
      '</div>',
      '<div class="ctx-overview-foot">',
      '<span>' + esc(shortPath(settings().workspace)) + '</span>',
      '<span>' + compressed + ' 条已压缩记录 · ' + formatRefresh(context.refreshedAt) + '</span>',
      '</div>',
      '</section>',
    ].join('');
  }

  function metric(label, value) {
    return '<div class="ctx-metric"><span>' + esc(label) + '</span><b>' + esc(value) + '</b></div>';
  }

  function lockHTML() {
    if (!running()) return '';
    return [
      '<div class="ctx-lock" role="status">',
      '<span class="ctx-lock-dot"></span>',
      '<div><b>灵正在处理当前交流</b><p>摘要与交流历史暂时锁定；所有者指令和角色规则仍可调整。可返回交流停止当前回应。</p></div>',
      '<button class="btn ghost sm" type="button" data-do="ctx-back">返回交流</button>',
      '</div>',
    ].join('');
  }

  function rulesHTML() {
    const rows = context.rules.map((rule, index) => [
      '<article class="ctx-rule">',
      '<span class="ctx-rule-index">' + String(index + 1).padStart(2, '0') + '</span>',
      '<div class="ctx-rule-text">' + esc(rule) + '</div>',
      '<div class="ctx-rule-actions">',
      '<button class="ctx-mini" type="button" data-do="ctx-rule-edit" data-index="' + index + '"' +
        ' aria-label="编辑第 ' + (index + 1) + ' 条所有者指令">编辑</button>',
      '<button class="ctx-mini is-danger" type="button" data-do="ctx-rule-delete" data-index="' + index + '"' +
        ' aria-label="删除第 ' + (index + 1) + ' 条所有者指令">删除</button>',
      '</div>',
      '</article>',
    ].join('')).join('');
    return [
      '<section class="ctx-card" aria-labelledby="ctx-rules-title">',
      '<header class="ctx-card-head">',
      '<div><h2 id="ctx-rules-title">所有者指令</h2><p>每条独立生效，与设置中的基本原则共享同一份数据。</p></div>',
      '<button class="btn ghost sm" type="button" data-do="ctx-rule-add">' +
        P.icon('plus') + '添加指令</button>',
      '</header>',
      '<div class="ctx-rules">' + (rows || '<div class="empty-state">还没有所有者指令。灵仍会遵守角色与项目规则。</div>') + '</div>',
      '<p class="ctx-note">当前明确要求优先于一般偏好；这里的内容不代替系统权限。</p>',
      '</section>',
    ].join('');
  }

  function rolesHTML() {
    const roles = Array.isArray(P.data.roles) ? P.data.roles : [];
    const cards = roles.map(role => [
      '<article class="ctx-role" data-role="' + esc(role.id) + '">',
      '<header>',
      '<div><h3>' + esc(role.title || role.id || '角色') + '</h3><p>' +
        esc(ROLE_CAPTIONS[role.id] || '生效中的角色规则') + '</p></div>',
      '<button class="ctx-mini" type="button" data-do="ctx-role-edit" data-id="' + esc(role.id) + '">编辑全文</button>',
      '</header>',
      '<div class="ctx-role-meta"><span>' + esc(role.id) + '</span><span>' +
        String(role.text || '').length.toLocaleString('zh-CN') + ' 字符</span></div>',
      '<div class="ctx-role-text">' + esc(role.text || '') + '</div>',
      '</article>',
    ].join('')).join('');
    return [
      '<section class="ctx-card" aria-labelledby="ctx-roles-title">',
      '<header class="ctx-card-head">',
      '<div><h2 id="ctx-roles-title">角色规则</h2><p>三个角色分别生效；编辑保存后用于后续请求。</p></div>',
      '<span class="pill">' + roles.length + ' 个角色</span>',
      '</header>',
      '<div class="ctx-roles">' + (cards || '<div class="empty-state">角色默认全文尚未载入。</div>') + '</div>',
      '</section>',
    ].join('');
  }

  function sourceHTML(sections) {
    const cards = sections.map(section => [
      '<details class="ctx-source" data-source="' + esc(section.id) + '" open>',
      '<summary>',
      '<span class="ctx-source-mark"></span>',
      '<span class="ctx-source-title">' + esc(section.title) + '</span>',
      '<span class="ctx-source-meta">' + esc(section.meta) + '</span>',
      '<span class="pill">只读</span>',
      '</summary>',
      '<div class="ctx-source-body"><pre>' + esc(section.text) + '</pre></div>',
      '</details>',
    ].join('')).join('');
    return [
      '<section class="ctx-assembly" aria-labelledby="ctx-assembly-title">',
      '<header class="ctx-sec-head">',
      '<div><h2 id="ctx-assembly-title">本次自动组装</h2><p>按当前设置、项目文件与运行状态生成；此处只能查看。</p></div>',
      '<span class="ctx-assembly-count">' + sections.length + ' 个来源</span>',
      '</header>',
      '<div class="ctx-sources">' + cards + '</div>',
      '</section>',
    ].join('');
  }

  function systemPanel(sections) {
    return [
      '<div class="ctx-panel" id="ctx-panel-system" role="tabpanel" aria-labelledby="ctx-tab-system">',
      rulesHTML(),
      rolesHTML(),
      sourceHTML(sections),
      '</div>',
    ].join('');
  }

  function summaryHTML() {
    const compressed = messages().filter(item => item.compressed).length;
    const length = context.summary.trim().length;
    return [
      '<section class="ctx-card ctx-summary" aria-labelledby="ctx-summary-title">',
      '<header class="ctx-card-head">',
      '<div><h2 id="ctx-summary-title">已压缩摘要</h2><p>较早交流收束为摘要；编辑会替换当前摘要，并影响后续请求。</p></div>',
      '<button class="btn ghost sm" type="button" data-do="ctx-summary-edit"' + (running() ? ' disabled' : '') + '>' +
        P.icon('edit') + '编辑摘要</button>',
      '</header>',
      '<div class="ctx-summary-meta"><span>覆盖 ' + compressed + ' 条压缩记录</span><span>' +
        length.toLocaleString('zh-CN') + ' 字符</span><span>最新要求仍优先</span></div>',
      context.summary.trim()
        ? '<div class="ctx-summary-text">' + esc(context.summary) + '</div>'
        : '<div class="empty-state">当前没有压缩摘要。可写入摘要来收束较早记录。</div>',
      '<p class="ctx-note">摘要记录过去的工作；当前明确要求、项目边界与实际权限仍然优先。</p>',
      '</section>',
    ].join('');
  }

  function messageText(message) {
    const attachments = Array.isArray(message.attachments) ? message.attachments : [];
    return [
      '<span class="ctx-msg-role">' + esc(MESSAGE_LABELS[message.role] || message.role || '记录') + '</span>',
      message.time ? '<time>' + esc(message.time) + '</time>' : '',
      message.taskId ? '<span class="ctx-msg-task">工作 ' + esc(message.taskId) + '</span>' : '',
      attachments.length ? '<span class="ctx-msg-files">' + esc(attachments.map(item => item.name).join(' · ')) + '</span>' : '',
    ].join('');
  }

  function messageRow(message, selectable) {
    const checked = selected.has(message.id);
    const canEdit = selectable && message.role === 'user' && !message.compressed;
    return [
      '<article class="ctx-msg role-' + esc(message.role || 'notice') + (checked ? ' is-selected' : '') +
        '" data-ctx-row="' + esc(message.id) + '">',
      selectable
        ? '<label class="ctx-check"><input type="checkbox" data-ctx-message="' + esc(message.id) + '"' +
          (checked ? ' checked' : '') + (running() ? ' disabled' : '') +
          ' aria-label="选择' + esc(MESSAGE_LABELS[message.role] || '') + '消息"></label>'
        : '<span class="ctx-check-ph" aria-hidden="true"></span>',
      '<span class="ctx-avatar role-' + esc(message.role || 'notice') + '">' +
        esc(MESSAGE_MARKS[message.role] || '记') + '</span>',
      '<div class="ctx-msg-main">',
      '<div class="ctx-msg-meta">' + messageText(message) + '</div>',
      '<div class="ctx-msg-text">' + esc(message.text || '') + '</div>',
      canEdit
        ? '<div class="ctx-msg-actions"><button class="ctx-mini" type="button" data-do="ctx-edit-message" data-id="' +
          esc(message.id) + '"' + (running() ? ' disabled' : '') + '>编辑并重新发送</button></div>'
        : '',
      '</div>',
      '</article>',
    ].join('');
  }

  function historyHTML() {
    const current = messages();
    const editable = current.filter(item => !item.compressed);
    const compressed = current.filter(item => item.compressed);
    editable.forEach(item => { if (!selected.has(item.id)) return; });
    [...selected].forEach(id => {
      if (!editable.some(item => item.id === id)) selected.delete(id);
    });
    const selectedCount = editable.filter(item => selected.has(item.id)).length;
    const allSelected = editable.length > 0 && selectedCount === editable.length;
    const undo = P.data.undo;
    const rows = editable.map(item => messageRow(item, true)).join('');
    const compressedRows = compressed.map(item => messageRow(item, false)).join('');
    return [
      '<div class="ctx-panel" id="ctx-panel-history" role="tabpanel" aria-labelledby="ctx-tab-history">',
      undo
        ? '<div class="ctx-undo"><span>已修改交流记录，可在继续交流前撤销最近一次操作。</span>' +
          '<button class="btn ghost sm" type="button" data-do="ctx-undo"' +
          (running() ? ' disabled' : '') + '>撤销最近一次修改</button></div>'
        : '',
      summaryHTML(),
      '<section class="ctx-card ctx-history" aria-labelledby="ctx-history-title">',
      '<header class="ctx-card-head">',
      '<div><h2 id="ctx-history-title">未压缩交流</h2><p>保留在当前上下文中的消息；删除前会再次确认。</p></div>',
      '<span class="pill">' + editable.length + ' 条</span>',
      '</header>',
      '<div class="ctx-history-toolbar">',
      '<label class="ctx-select-all"><input type="checkbox" data-ctx-all' +
        (allSelected ? ' checked' : '') + (running() || !editable.length ? ' disabled' : '') + '>' +
        '<span>全选消息</span></label>',
      '<span class="ctx-selected" data-ctx-selected>' +
        (selectedCount ? '已选 ' + selectedCount + ' 条' : '可多选后删除') + '</span>',
      '<button class="btn ghost sm ctx-delete" type="button" data-do="ctx-delete-selected"' +
        (running() || !selectedCount ? ' disabled' : '') + '>删除所选' +
        (selectedCount ? ' (' + selectedCount + ')' : '') + '</button>',
      '</div>',
      '<div class="ctx-messages">' +
        (rows || '<div class="empty-state">没有未压缩消息。较早记录已收束在摘要中。</div>') +
      '</div>',
      compressed.length
        ? '<details class="ctx-compressed"><summary>已压缩记录 · ' + compressed.length +
          ' 条（只读，由摘要管理）</summary><div class="ctx-messages is-compressed">' +
          compressedRows + '</div></details>'
        : '',
      '<p class="ctx-note">删除只改变交流记录；已执行的工具、文件修改和后台工作不会回滚。</p>',
      '</section>',
      '</div>',
    ].join('');
  }

  function renderContext() {
    const sections = sourceSections();
    const tab = context.tab === 'history' ? 'history' : 'system';
    const body = [
      '<div class="ctx-page">',
      overviewHTML(sections),
      lockHTML(),
      '<div class="ctx-tabs" role="tablist" aria-label="上下文类别">',
      '<button id="ctx-tab-system" type="button" role="tab" aria-selected="' + (tab === 'system') + '"' +
        ' aria-controls="ctx-panel-system" class="' + (tab === 'system' ? 'is-active' : '') +
        '" data-do="ctx-tab" data-tab="system"><span>系统与角色</span><small>' +
        (context.rules.length + (P.data.roles || []).length + sections.length) + ' 项</small></button>',
      '<button id="ctx-tab-history" type="button" role="tab" aria-selected="' + (tab === 'history') + '"' +
        ' aria-controls="ctx-panel-history" class="' + (tab === 'history' ? 'is-active' : '') +
        '" data-do="ctx-tab" data-tab="history"><span>历史上下文</span><small>' +
        messages().filter(item => !item.compressed).length + ' 条</small></button>',
      '</div>',
      tab === 'history' ? historyHTML() : systemPanel(sections),
      '</div>',
    ].join('');
    return P.page(
      '上下文管理',
      '查看灵实际参考的信息，编辑所有者指令、角色规则与交流历史。',
      body,
      '<button class="btn ghost" type="button" data-do="ctx-refresh">' + P.icon('orbit') +
        '重新组装</button><button class="btn solid" type="button" data-do="ctx-back">' +
        P.icon('back') + '返回交流</button>'
    );
  }

  function guard() {
    if (!running()) return true;
    P.toast('请先停止当前交流，再修改上下文。');
    return false;
  }

  function editArea(label, value, rows, extra) {
    return '<label class="field">' + esc(label) + '<textarea name="text" rows="' + rows +
      '" class="ctx-edit-area"' + (extra || '') + '>' + esc(value) + '</textarea></label>';
  }

  P.action('ctx-tab', button => {
    context.tab = button.dataset.tab === 'history' ? 'history' : 'system';
    P.save();
    P.render();
  });

  P.action('ctx-refresh', () => {
    context.refreshedAt = new Date().toISOString();
    P.save();
    P.render();
    P.toast('已按当前设置重新组装上下文');
  });

  P.action('ctx-back', () => P.go('chat'));

  P.action('ctx-rule-add', () => {
    P.dialog({
      title: '添加所有者指令',
      submit: '添加指令',
      body: editArea('指令内容', '', 7, ' required'),
      onSubmit(form) {
        const value = form.elements.text.value.trim();
        if (!value) throw Error('请输入一条指令');
        context.rules.push(value);
        P.save();
        P.render();
        P.toast('所有者指令已添加');
      },
    });
  });

  P.action('ctx-rule-edit', button => {
    const index = Number(button.dataset.index);
    const value = context.rules[index];
    if (typeof value !== 'string') return;
    P.dialog({
      title: '编辑所有者指令',
      submit: '保存指令',
      body: editArea('指令内容', value, 8, ' required'),
      onSubmit(form) {
        const next = form.elements.text.value.trim();
        if (!next) throw Error('指令不能为空');
        context.rules[index] = next;
        P.save();
        P.render();
        P.toast('所有者指令已保存');
      },
    });
  });

  P.action('ctx-rule-delete', button => {
    const index = Number(button.dataset.index);
    const value = context.rules[index];
    if (typeof value !== 'string') return;
    P.confirm('删除这条所有者指令？', value, () => {
      context.rules.splice(index, 1);
      P.save();
      P.render();
      P.toast('所有者指令已删除');
    });
  });

  P.action('ctx-role-edit', button => {
    const roles = Array.isArray(P.data.roles) ? P.data.roles : [];
    const role = roles.find(item => item.id === button.dataset.id);
    if (!role) return;
    P.dialog({
      title: '编辑' + (role.title || role.id) + '规则',
      submit: '保存角色',
      wide: true,
      body: '<p class="ctx-dialog-note">完整规则会替代当前角色文本；空白内容不能保存。</p>' +
        editArea('角色全文', role.text || '', 18, ' required'),
      onSubmit(form) {
        const value = form.elements.text.value;
        if (!value.trim()) throw Error('角色规则不能为空');
        role.text = value;
        P.save();
        P.render();
        P.toast('角色规则已保存');
      },
    });
  });

  P.action('ctx-summary-edit', () => {
    if (!guard()) return;
    P.dialog({
      title: '编辑压缩摘要',
      submit: '保存摘要',
      wide: true,
      body: '<p class="ctx-dialog-note">摘要会替代此前的压缩记录；保存后仍可在继续交流前撤销。</p>' +
        editArea('摘要全文', context.summary, 16, ' required'),
      onSubmit(form) {
        const value = form.elements.text.value.trim();
        if (!value) throw Error('摘要不能为空');
        P.setSummary(value);
        P.toast('压缩摘要已保存');
      },
    });
  });

  P.action('ctx-delete-selected', () => {
    if (!guard()) return;
    const editable = messages().filter(item => !item.compressed);
    const ids = editable.filter(item => selected.has(item.id)).map(item => item.id);
    if (!ids.length) {
      P.toast('请先选择要删除的消息');
      return;
    }
    P.deleteMessages(ids);
  });

  P.action('ctx-undo', () => {
    if (!guard()) return;
    selected.clear();
    P.undoHistory();
  });

  P.action('ctx-edit-message', button => {
    if (!guard()) return;
    P.editMessage(button.dataset.id);
  });

  document.addEventListener('change', event => {
    const all = event.target.closest('[data-ctx-all]');
    const one = event.target.closest('[data-ctx-message]');
    if (!all && !one) return;
    if (running()) {
      event.target.checked = false;
      return;
    }
    const editable = messages().filter(item => !item.compressed);
    if (all) {
      if (all.checked) editable.forEach(item => selected.add(item.id));
      else selected.clear();
    } else if (one) {
      if (one.checked) selected.add(one.dataset.ctxMessage);
      else selected.delete(one.dataset.ctxMessage);
    }
    syncSelection();
  });

  function syncSelection() {
    const root = document.getElementById('view-context');
    if (!root) return;
    const editable = messages().filter(item => !item.compressed);
    [...selected].forEach(id => {
      if (!editable.some(item => item.id === id)) selected.delete(id);
    });
    const count = editable.filter(item => selected.has(item.id)).length;
    root.querySelectorAll('[data-ctx-row]').forEach(row => {
      const on = selected.has(row.dataset.ctxRow);
      row.classList.toggle('is-selected', on);
      const box = row.querySelector('[data-ctx-message]');
      if (box) box.checked = on;
    });
    const all = root.querySelector('[data-ctx-all]');
    if (all) {
      all.checked = editable.length > 0 && count === editable.length;
      all.indeterminate = count > 0 && count < editable.length;
    }
    const label = root.querySelector('[data-ctx-selected]');
    if (label) label.textContent = count ? '已选 ' + count + ' 条' : '可多选后删除';
    const remove = root.querySelector('[data-do="ctx-delete-selected"]');
    if (remove) {
      remove.disabled = running() || !count;
      remove.textContent = count ? '删除所选 (' + count + ')' : '删除所选';
    }
  }

  P.views.context = renderContext;
})();
