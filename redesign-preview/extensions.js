'use strict';

// Independent preview module: skills, plugin management and model request history.
// Local state only. No network or backend calls.

(function () {
  const P = window.P;
  if (!P) return;

  const e = value => P.esc(value === null || value === undefined ? '' : String(value));
  const j = (...parts) => parts.join('');
  const clone = value => JSON.parse(JSON.stringify(value));
  const nowIso = () => new Date().toISOString();
  const SOURCE_ORDER = ['project', 'personal', 'builtin'];
  const SOURCE_LABEL = { project: '项目', personal: '个人', builtin: '内置' };
  const CAPABILITY_LABEL = { context: '上下文材料', tools: '工具', page: '功能页', services: 'MCP 服务', chat: '聊天订阅' };

  const SKILL_SEEDS = [
    {
      id: 'sk-project-conventions', source: 'project', path: 'project-conventions/SKILL.md',
      name: 'project-conventions', description: '当前项目的开发边界、文档位置与提交授权约定。',
      body: ['# 项目开发约定', '', '先读当前需求与相关源码，再决定最小改动范围。', '项目文档放在 docs/；预览与正式前端分开处理。', '提交、推送与破坏性操作跟随当前授权。'].join('\n'),
      updatedAt: '2026-10-21T09:30:00+08:00'
    },
    {
      id: 'sk-project-design-review', source: 'project', path: 'demo-design-review/SKILL.md',
      name: 'demo-design-review', description: '本轮预览的项目技能：界面层级、排版、动效与响应式检查。',
      body: ['# 本轮界面审视', '', '保留纸感、灵岛与弹簧动效，主题转向浅蓝与淡紫。', '桌面端保持清晰的主次层级；手机端操作区不得拥挤。', '检查昼夜两套对比度与键盘焦点。'].join('\n'),
      updatedAt: '2026-10-27T18:10:00+08:00'
    },
    {
      id: 'sk-personal-writing', source: 'personal', path: 'writing-style/SKILL.md',
      name: 'writing-style', description: '写给用户看的说明：先给结论，再解释依据与限制。',
      body: ['# 写作风格', '', '使用具体动词与短段落。', '先说明结果，再补充证据。', '不使用空泛总结或未经核实的完成声明。'].join('\n'),
      updatedAt: '2026-10-12T11:00:00+08:00'
    },
    {
      id: 'sk-personal-design-review', source: 'personal', path: 'demo-design-review/SKILL.md',
      name: 'demo-design-review', description: '个人视觉审视清单：先构建，再检查截图和交互细节。',
      body: ['# 视觉审视清单', '', '先确认页面能构建，再用真实浏览器检查。', '关注留白、字号、焦点态和小屏布局。', '只修正能说明原因的问题。'].join('\n'),
      updatedAt: '2026-10-08T14:20:00+08:00'
    },
    {
      id: 'sk-builtin-taste', source: 'builtin', path: 'demo-design-notes/SKILL.md',
      name: 'demo-design-notes', description: '本地演示技能：记录页面视觉方向与留白、字号的检查重点。',
      body: ['# 设计方向', '', '先确定页面要传达的气质，再选择字体、颜色与版式。', '每个区块都要有明确用途。', '用真实内容检查布局，不用占位文案掩盖问题。'].join('\n'),
      updatedAt: '2026-09-18T10:00:00+08:00'
    },
    {
      id: 'sk-builtin-interface', source: 'builtin', path: 'demo-interface-review/SKILL.md',
      name: 'demo-interface-review', description: '本地演示技能：界面层级、交互细节、可访问性与动效检查。',
      body: ['# 界面审视（本地演示）', '', '检查信息的阅读顺序与操作路径。', '为键盘和减少动效偏好保留可用界面。', '在真实视口下检查桌面与手机状态。'].join('\n'),
      updatedAt: '2026-09-01T09:00:00+08:00'
    },
    {
      id: 'sk-builtin-review', source: 'builtin', path: 'demo-code-review/SKILL.md',
      name: 'demo-code-review', description: '本地演示技能：审查改动是否解决根因，并找出真实回归风险。',
      body: ['# 代码审查', '', '先读调用方和共享代码。', '优先报告会导致错误行为、数据损坏或安全问题的缺陷。', '验证修复，不只阅读 diff。'].join('\n'),
      updatedAt: '2026-08-22T16:40:00+08:00'
    }
  ];

  const REAL_PLUGIN_SEEDS = [
    {
      id: 'mobile-bridge', name: 'Nexus 手机接入', version: '0.2.0',
      description: '局域网安卓聊天入口，与电脑上的灵共享交流记录。',
      entry: 'backend.py:create_plugin', capabilities: ['page'],
      pages: [{ id: 'connect', title: '手机连接与配对', path: 'web/index.html' }],
      settings_schema: {}, settings: {}, enabled: true, status: 'ready',
      active_calls: 1, error: '', progress: ''
    },
    {
      id: 'temp-memory', name: '临时长期记忆', version: '0.1.0',
      description: '原文归档、可维护记忆、中文检索与可选嵌入混合召回。',
      entry: 'backend.py:create_plugin', capabilities: ['context', 'tools', 'page'],
      pages: [{ id: 'main', title: '记忆与原文备份', path: 'web/index.html' }],
      settings_schema: {}, settings: {}, enabled: false, status: 'unloaded',
      active_calls: 0, error: '', progress: ''
    }
  ];

  const IMPORT_PACKAGES = [
    {
      key: 'mobile-bridge', file: 'mobile-bridge-0.2.1.zip',
      label: 'mobile-bridge-0.2.1.zip · 同名升级包',
      plugin: {
        id: 'mobile-bridge', name: 'Nexus 手机接入', version: '0.2.1',
        description: '局域网安卓聊天入口（演示升级包），与电脑上的灵共享交流记录。',
        entry: 'backend.py:create_plugin', capabilities: ['page'],
        pages: [{ id: 'connect', title: '手机连接与配对', path: 'web/index.html' }],
        settings_schema: {}
      }
    },
    {
      key: 'temp-memory', file: 'temp-memory-0.1.1.zip',
      label: 'temp-memory-0.1.1.zip · 同名升级包',
      plugin: {
        id: 'temp-memory', name: '临时长期记忆', version: '0.1.1',
        description: '原文归档、可维护记忆、中文检索与可选嵌入混合召回（演示升级包）。',
        entry: 'backend.py:create_plugin', capabilities: ['context', 'tools', 'page'],
        pages: [{ id: 'main', title: '记忆与原文备份', path: 'web/index.html' }],
        settings_schema: {}
      }
    },
    {
      key: 'focus-timer', file: 'focus-timer-0.1.0.zip',
      label: 'focus-timer-0.1.0.zip · 新插件与 schema',
      plugin: {
        id: 'focus-timer', name: '专注计时', version: '0.1.0',
        description: '一轮专注的计时与安静提醒，用来演示通用设置项。',
        entry: 'backend.py:create_plugin', capabilities: ['tools', 'page'],
        pages: [{ id: 'timer', title: '专注计时', path: 'web/index.html' }],
        settings_schema: {
          type: 'object',
          properties: {
            theme: { type: 'string', title: '主题色', enum: ['浅蓝', '淡紫', '自动'], default: '浅蓝', description: '计时器界面的主色。' },
            minutes: { type: 'integer', title: '专注时长（分钟）', default: 25, minimum: 5, maximum: 120, description: '每轮专注的分钟数。' },
            remind: { type: 'boolean', title: '完成提醒', default: true, description: '一轮结束时发出安静提醒。' }
          }
        }
      }
    }
  ];

  const SEED_CLOCK = Date.parse('2026-10-28T22:10:00+08:00');

  function seedRequest(id, kind, owner, minutesAgo, summary, plugins, materials, extra) {
    const options = extra || {};
    const created = new Date(SEED_CLOCK - minutesAgo * 60000).toISOString();
    return {
      id: id, created_at: created, updated_at: created,
      owner_id: owner, kind: kind, model: options.model || 'deepseek-v4.1',
      protocol: options.protocol || 'openai-chat', status: options.status || 'complete',
      error: options.error || '',
      scope: Object.assign({ kind: kind, owner_id: owner }, options.task_id ? { task_id: options.task_id } : {}, {
        materials: materials, plugins: plugins
      }),
      plugins: plugins, materials: materials,
      body: {
        model: options.model || 'deepseek-v4.1', stream: true,
        messages: [
          { role: 'system', content: '你是 Nexus 的灵，按当前上下文完成交流或后台工作。' },
          { role: 'user', content: summary }
        ],
        tools: kind === 'ling' ? ['answer', 'dispatch_work', 'inspect_work'] : ['read_file', 'write_file', 'run_command', 'task_read'],
        metadata: { owner_id: owner, kind: kind, preview: true }
      }
    };
  }

  const REQUEST_SEEDS = [
    seedRequest(241, 'ling', 'sess-ling-main', 8, '把技能与插件管理页做成独立预览，先保留真实交互和持久化。', [
      { name: '临时长期记忆', status: 'used', reason: '召回了本轮对纸感、浅蓝与淡紫的视觉要求。' },
      { name: 'Nexus 手机接入', status: 'skipped', reason: '本轮无需手机端联调。' }
    ], [
      { source: 'temp-memory:visual-notes', purpose: 'reference', content: '保留纸感、灵岛与弹簧动效；主色为浅蓝与淡紫。' },
      { source: 'AGENTS.md', purpose: 'instruction', content: '不立假想敌，最小闭环优先。' }
    ]),
    seedRequest(240, 'task', 'task-8f21', 21, '整理当前预览与 nexus/web 源码的功能差异，列出待实现项。', [
      { name: '临时长期记忆', status: 'used', reason: '读取了上一轮记录的源码边界。' }
    ], [
      { source: 'temp-memory:task-notes', purpose: 'reference', content: '技能覆盖顺序：项目、个人、内置。' }
    ], { task_id: 'task-8f21' }),
    seedRequest(239, 'ling', 'sess-ling-main', 48, '解释手机接入插件发现不到设备时应检查什么。', [
      { name: 'Nexus 手机接入', status: 'used', reason: '该问题直接属于手机接入插件。' },
      { name: '临时长期记忆', status: 'not_applicable', reason: '不需要历史记忆材料。' }
    ], [
      { source: 'mobile-bridge:readme', purpose: 'reference', content: '确认电脑与手机处于同一局域网，并检查端口占用。' }
    ], { protocol: 'anthropic-messages' }),
    seedRequest(238, 'task', 'task-2c90', 95, '评估长期记忆召回是否应该进入这一轮聊天上下文。', [
      { name: '临时长期记忆', status: 'used', reason: '返回了两条与当前项目相关的材料。' }
    ], [
      { source: 'temp-memory:archive-31', purpose: 'reference', content: '技能页需要呈现来源与同名覆盖关系。' }
    ], { status: 'error', error: '上游响应在读取材料阶段超时。', task_id: 'task-2c90' }),
    seedRequest(237, 'task', 'task-8f21', 190, '将预览主题从绿色调整为浅蓝与淡紫，并检查昼夜模式。', [
      { name: '临时长期记忆', status: 'used', reason: '命中视觉偏好记录。' }
    ], [
      { source: 'temp-memory:visual-notes', purpose: 'reference', content: '不要固定绿色，使用 CSS 变量。' }
    ], { task_id: 'task-8f21' }),
    seedRequest(236, 'ling', 'sess-ling-main', 350, '请求记录保留多久，插件停用后历史还在吗？', [], []),
    seedRequest(235, 'task', 'task-550e', 720, '为插件设置项制作通用表单，并对齐布尔、枚举、文本和数字校验。', [
      { name: '临时长期记忆', status: 'skipped', reason: '当前任务不需要历史材料。' }
    ], [], { status: 'error', error: '插件 schema 中有一个未支持的数组字段。', task_id: 'task-550e' }),
    seedRequest(234, 'ling', 'sess-ling-main', 1480, '解释为什么同名技能会被覆盖，以及如何让项目技能生效。', [
      { name: '临时长期记忆', status: 'used', reason: '读取了项目技能覆盖规则。' }
    ], [
      { source: 'temp-memory:skill-rules', purpose: 'reference', content: '项目技能优先于同名的个人与内置技能。' }
    ]),
    seedRequest(233, 'task', 'task-2c90', 3000, '检查插件 draining 状态下为什么不能重复下发停用。', [
      { name: 'Nexus 手机接入', status: 'not_applicable', reason: '与手机接入无关。' },
      { name: '临时长期记忆', status: 'used', reason: '提供了插件生命周期笔记。' }
    ], [
      { source: 'temp-memory:plugin-life', purpose: 'reference', content: '停用后停止接收新工作，等待在途调用结束。' }
    ], { task_id: 'task-2c90' }),
    seedRequest(232, 'ling', 'sess-ling-main', 4300, '把一个 ZIP 包装进预览，导入后不要自动启用。', [
      { name: '临时长期记忆', status: 'used', reason: '召回了用户对导入行为的说明。' }
    ], [
      { source: 'temp-memory:import-notes', purpose: 'reference', content: '同名冲突需要确认替换；新版本默认不启用。' }
    ]),
    seedRequest(231, 'task', 'task-550e', 5900, '检查请求详情中的材料与参与原因是否都来自同一次请求。', [
      { name: '临时长期记忆', status: 'used', reason: '提供了材料字段的结构。' }
    ], [
      { source: 'temp-memory:request-shape', purpose: 'reference', content: '详情从 scope 读取 materials 与 plugins。' }
    ], { task_id: 'task-550e' }),
    seedRequest(230, 'ling', 'sess-ling-main', 7400, '整理本轮预览还需要保留的插件功能页入口。', [
      { name: 'Nexus 手机接入', status: 'skipped', reason: '只保留入口，不打开内部页面。' }
    ], [], { protocol: 'anthropic-messages' })
  ];

  P.data.skills ||= { items: clone(SKILL_SEEDS), refreshedAt: '' };
  P.data.plugins = P.data.plugins || {
    root: 'plugins',
    plugins: clone(REAL_PLUGIN_SEEDS),
    requests: clone(REQUEST_SEEDS),
    lastRefresh: '',
    lastScan: ''
  };
  const pluginData = P.data.plugins;
  if (!Array.isArray(pluginData.items) && Array.isArray(pluginData.plugins)) {
    pluginData.items = pluginData.plugins;
    delete pluginData.plugins;
  }

  let skillFilter = 'all';

  function formatTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    });
  }

  function sourceLabel(source) {
    return SOURCE_LABEL[source] || source || '未知来源';
  }

  function skillState(items) {
    const claimed = new Map();
    const effective = new Set();
    const coveredBy = new Map();
    for (const source of SOURCE_ORDER) {
      for (const skill of items.filter(item => item.source === source)) {
        const winner = claimed.get(skill.name);
        if (winner) coveredBy.set(skill.id, winner);
        else {
          effective.add(skill.id);
          claimed.set(skill.name, skill);
        }
      }
    }
    return { effective: effective, coveredBy: coveredBy };
  }

  function skillCounts(items) {
    const counts = { personal: 0, project: 0, builtin: 0 };
    for (const skill of items) counts[skill.source] = (counts[skill.source] || 0) + 1;
    return counts;
  }

  function skillOrdered(items) {
    const order = { project: 0, personal: 1, builtin: 2 };
    return items.slice().sort((a, b) => {
      const source = (order[a.source] || 9) - (order[b.source] || 9);
      return source || String(a.name).localeCompare(String(b.name), 'en');
    });
  }

  function skillReadOnlyDialog(skill) {
    const state = skillState(P.data.skills.items);
    P.dialog({
      title: skill.source === 'builtin' ? '查看内置技能' : '查看技能正文',
      submit: null,
      wide: true,
      body: j(
        '<div class="ext-dialog ext-skill-reader">',
        '<div class="ext-dialog-meta">',
        '<span class="ext-badge ext-badge-' + e(skill.source) + '">' + e(sourceLabel(skill.source)) + '</span>',
        skill.source === 'builtin' ? '<span class="ext-badge">只读</span>' : '',
        state.coveredBy.has(skill.id) ? '<span class="ext-badge ext-badge-covered">被覆盖</span>' : '<span class="ext-badge ext-badge-on">当前生效</span>',
        '</div>',
        '<dl class="ext-read-facts">',
        '<div><dt>名称</dt><dd class="ext-mono">' + e(skill.name) + '</dd></div>',
        '<div><dt>摘要</dt><dd>' + e(skill.description) + '</dd></div>',
        '<div><dt>副本路径</dt><dd class="ext-mono">' + e(skill.path) + '</dd></div>',
        '</dl>',
        '<h3 class="ext-dialog-subtitle">正文</h3>',
        '<pre class="ext-body-pre">' + e(skill.body || '（正文为空）') + '</pre>',
        skill.source === 'builtin'
          ? '<p class="ext-dialog-note">内置技能随应用提供，只读；同名项目技能会覆盖它的生效副本。</p>'
          : '<p class="ext-dialog-note">此技能属于' + e(sourceLabel(skill.source)) + '技能源，可在技能页编辑。</p>',
        '</div>'
      )
    });
  }

  function skillEditorDialog(skill) {
    const editing = Boolean(skill);
    const source = editing ? skill.source : 'personal';
    const sourceField = editing
      ? '<div class="ext-field"><span>来源</span><div class="ext-static">' + e(sourceLabel(source)) + '</div></div>'
      : '<label class="ext-field"><span>来源</span><select name="source"><option value="personal">个人技能</option><option value="project">项目技能</option></select></label>';
    P.dialog({
      title: editing ? '编辑技能' : '新建技能',
      submit: editing ? '保存技能' : '创建技能',
      wide: true,
      body: j(
        '<div class="ext-dialog ext-skill-form">',
        '<p class="ext-dialog-note">填写名称、摘要和正文；同名时项目技能优先，内置演示副本不可编辑。</p>',
        '<div class="ext-form-grid">',
        '<label class="ext-field"><span>技能名称 · slug</span><input name="name" type="text" maxlength="64" autocomplete="off" placeholder="writing-style" value="' + e(editing ? skill.name : '') + '"></label>',
        sourceField,
        '<label class="ext-field ext-span"><span>摘要</span><textarea name="description" rows="3" maxlength="1000" placeholder="说明何时使用这个技能">' + e(editing ? skill.description : '') + '</textarea></label>',
        '<label class="ext-field ext-span"><span>正文 · Markdown</span><textarea name="body" rows="10" placeholder="# 技能正文">' + e(editing ? skill.body : '') + '</textarea></label>',
        '</div>',
        '<p class="ext-dialog-hint">名称只能使用小写字母、数字与连字符，例如 project-conventions。</p>',
        '</div>'
      ),
      onSubmit: function (form) {
        const name = String(form.querySelector('[name="name"]').value || '').trim();
        const description = String(form.querySelector('[name="description"]').value || '').trim();
        const body = String(form.querySelector('[name="body"]').value || '');
        const chosenSource = editing ? skill.source : String(form.querySelector('[name="source"]').value || 'personal');
        if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) throw new Error('名称需为英文 slug，例如 writing-style。');
        if (!description) throw new Error('请填写摘要，摘要不能为空。');
        if (!['personal', 'project'].includes(chosenSource)) throw new Error('来源只能是个人或项目技能。');
        const duplicate = P.data.skills.items.find(item => item.source === chosenSource && item.name === name && (!editing || item.id !== skill.id));
        if (duplicate) throw new Error('同一来源已有同名技能，请换一个名称。');
        if (editing) {
          const previousName = skill.name;
          skill.name = name;
          skill.description = description;
          skill.body = body;
          skill.path = skill.path === previousName + '.md' ? name + '.md' : name + '/SKILL.md';
          skill.updatedAt = nowIso();
        } else {
          P.data.skills.items.push({
            id: 'sk-' + chosenSource + '-' + Date.now().toString(36),
            source: chosenSource, path: name + '/SKILL.md', name: name,
            description: description, body: body, updatedAt: nowIso()
          });
        }
        P.save();
        P.render();
        P.toast(editing ? '技能已保存。' : '技能已创建。');
      }
    });
  }

  function skillCard(skill, state) {
    const cover = state.coveredBy.get(skill.id);
    return j(
      '<article class="card ext-skill' + (cover ? ' is-covered' : '') + '">',
      '<div class="ext-skill-head">',
      '<span class="ext-glyph" aria-hidden="true">' + e(String(skill.name || '?').charAt(0).toUpperCase()) + '</span>',
      '<div class="ext-skill-tags">',
      '<span class="ext-badge ext-badge-' + e(skill.source) + '">' + e(sourceLabel(skill.source)) + '</span>',
      skill.source === 'builtin' ? '<span class="ext-badge">只读</span>' : '',
      '</div>',
      '</div>',
      '<h3 class="ext-skill-name ext-mono">' + e(skill.name) + '</h3>',
      '<p class="ext-skill-summary">' + e(skill.description) + '</p>',
      '<p class="ext-coverage ' + (cover ? 'is-covered' : 'is-on') + '">',
      '<i aria-hidden="true"></i>',
      cover ? '被' + e(sourceLabel(cover.source)) + '技能 ' + e(cover.name) + ' 覆盖' : '当前生效',
      '</p>',
      '<footer class="ext-skill-actions">',
      '<button class="btn ghost sm" type="button" data-do="ext-skill-view" data-id="' + e(skill.id) + '">查看正文</button>',
      skill.source !== 'builtin' ? '<button class="btn ghost sm" type="button" data-do="ext-skill-edit" data-id="' + e(skill.id) + '">编辑</button>' : '',
      skill.source !== 'builtin' ? '<button class="ext-text-danger" type="button" data-do="ext-skill-delete" data-id="' + e(skill.id) + '">删除</button>' : '',
      '</footer>',
      '</article>'
    );
  }

  function renderSkills() {
    const items = P.data.skills.items;
    const state = skillState(items);
    const counts = skillCounts(items);
    const shown = skillFilter === 'all' ? skillOrdered(items) : skillOrdered(items.filter(item => item.source === skillFilter));
    const covered = items.length - state.effective.size;
    const tabs = [['all', '全部', items.length], ['project', '项目', counts.project], ['personal', '个人', counts.personal], ['builtin', '内置', counts.builtin]]
      .map(tab => '<button type="button" role="tab" aria-selected="' + String(skillFilter === tab[0]) + '" class="' + (skillFilter === tab[0] ? 'is-active' : '') + '" data-do="ext-skill-filter" data-source="' + tab[0] + '">' + tab[1] + '<span>' + tab[2] + '</span></button>')
      .join('');
    const stats = SOURCE_ORDER.map(source => j(
      '<div class="ext-source-stat"><span>' + e(sourceLabel(source)) + '</span><b>' + counts[source] + '</b></div>'
    )).join('');
    const actions = j(
      '<span class="pill">本地演示</span>',
      '<button class="btn ghost sm" type="button" data-do="ext-skill-refresh">' + P.icon('tune') + '刷新</button>',
      '<button class="btn solid sm" type="button" data-do="ext-skill-new">' + P.icon('plus') + '新建技能</button>'
    );
    const body = j(
      '<div class="ext-page ext-skills-page">',
      '<section class="card ext-skill-overview">',
      '<div class="ext-overview-copy"><h2>' + items.length + ' 个技能，' + state.effective.size + ' 个生效</h2>',
      '<p>同名时项目覆盖个人，个人覆盖内置。' + (covered ? '当前有 ' + covered + ' 个副本被覆盖。' : '当前没有同名覆盖。') + '</p></div>',
      '<div class="ext-source-stats">' + stats + '</div>',
      '</section>',
      '<div class="ext-skill-toolbar">',
      '<div class="ext-tabs" role="tablist" aria-label="技能来源">' + tabs + '</div>',
      '<p class="ext-refresh-note">' + (P.data.skills.refreshedAt ? '上次本地刷新 ' + e(formatTime(P.data.skills.refreshedAt)) : '尚未手动刷新') + '</p>',
      '</div>',
      shown.length
        ? '<div class="ext-skill-grid">' + shown.map(skill => skillCard(skill, state)).join('') + '</div>'
        : '<div class="card empty-state ext-empty">没有这个来源的技能。可以新建个人或项目技能。</div>',
      '<p class="foot-note">刷新会检查本地副本，补齐缺失的演示技能。</p>',
      '</div>'
    );
    return P.page('技能', '管理个人与项目技能，查看同名覆盖和只读内置技能。', body, actions);
  }

  P.views.skills = renderSkills;

  P.action('ext-skill-filter', function (button) {
    skillFilter = button.dataset.source || 'all';
    P.render();
  });

  P.action('ext-skill-new', function () {
    skillEditorDialog(null);
  });

  P.action('ext-skill-edit', function (button) {
    const skill = P.data.skills.items.find(item => item.id === button.dataset.id);
    if (skill && skill.source !== 'builtin') skillEditorDialog(skill);
  });

  P.action('ext-skill-view', function (button) {
    const skill = P.data.skills.items.find(item => item.id === button.dataset.id);
    if (skill) skillReadOnlyDialog(skill);
  });

  P.action('ext-skill-delete', function (button) {
    const skill = P.data.skills.items.find(item => item.id === button.dataset.id);
    if (!skill || skill.source === 'builtin') return;
    P.confirm('删除技能', '删除' + sourceLabel(skill.source) + '技能“' + skill.name + '”？此副本会从演示数据中移除。', function () {
      P.data.skills.items = P.data.skills.items.filter(item => item.id !== skill.id);
      P.save();
      P.render();
      P.toast('技能已删除。');
    });
  });

  P.action('ext-skill-refresh', function () {
    const existing = new Set(P.data.skills.items.map(item => item.id));
    let added = 0;
    for (const seed of skillSeedList()) {
      if (!existing.has(seed.id)) {
        P.data.skills.items.push(clone(seed));
        added += 1;
      }
    }
    P.data.skills.refreshedAt = nowIso();
    P.save();
    P.render();
    P.toast(added ? '已刷新，补齐 ' + added + ' 个缺失的内置副本。' : '已刷新，技能与本地副本一致。');
  });

  function skillSeedList() {
    return SKILL_SEEDS.filter(skill => skill.source === 'builtin');
  }

  P.action('ext-plugin-tab', function (button) {
    P.data.plugins.tab = button.dataset.tab === 'requests' ? 'requests' : 'plugins';
    P.save();
    P.render();
  });

  const lifecycleTimers = new Map();

  function pluginStore() {
    const store = P.data.plugins;
    if (!Array.isArray(store.items)) {
      store.items = Array.isArray(store.plugins) ? store.plugins : clone(REAL_PLUGIN_SEEDS);
      delete store.plugins;
    }
    if (!Array.isArray(store.requests)) store.requests = clone(REQUEST_SEEDS);
    store.filters = store.filters || { owner: '', kind: '' };
    if (!Number.isInteger(store.requestLimit)) store.requestLimit = 6;
    if (!store.items.some(item => String(item.id) === String(store.selectedId)) && store.items.length) {
      store.selectedId = store.items[0].id;
    }
    return store;
  }

  function selectedPlugin() {
    const store = pluginStore();
    return store.items.find(item => String(item.id) === String(store.selectedId)) || null;
  }

  function pluginStatus(plugin) {
    const status = String(plugin && plugin.status || '').toLowerCase();
    if (status === 'ready') return { label: '已加载', tone: 'on' };
    if (status === 'loading') return { label: '加载中', tone: 'wait' };
    if (status === 'draining') return { label: '正在收尾', tone: 'wait' };
    if (status === 'closing') return { label: '正在释放', tone: 'wait' };
    if (status === 'error' || status === 'failed') return { label: '错误', tone: 'error' };
    return { label: status ? '未启用' : '未启用', tone: 'off' };
  }

  function pluginBusy(plugin) {
    return ['loading', 'draining', 'closing'].includes(String(plugin.status || '').toLowerCase());
  }

  function pluginSaveAndRender() {
    P.save();
    if (P.view === 'plugins') P.render();
  }

  function changePlugin(plugin, values) {
    Object.assign(plugin, values);
    pluginSaveAndRender();
  }

  function schedulePlugin(plugin, delay, callback) {
    clearTimeout(lifecycleTimers.get(plugin.id));
    lifecycleTimers.set(plugin.id, setTimeout(function () {
      lifecycleTimers.delete(plugin.id);
      if (!pluginStore().items.includes(plugin)) return;
      callback();
    }, delay));
  }

  function loadPlugin(plugin) {
    if (!plugin || !['unloaded', 'error'].includes(String(plugin.status || '').toLowerCase())) return;
    plugin.enabled = true;
    plugin.status = 'loading';
    plugin.error = '';
    plugin.progress = '正在准备插件运行环境…';
    plugin.active_calls = 0;
    pluginSaveAndRender();
    schedulePlugin(plugin, 900, function () {
      if (plugin.status !== 'loading') return;
      plugin.status = 'ready';
      plugin.progress = '';
      plugin.active_calls = plugin.id === 'mobile-bridge' ? 1 : 0;
      pluginSaveAndRender();
      P.toast('插件“' + plugin.name + '”已加载。');
    });
  }

  function finishDrain(plugin) {
    if (plugin.status !== 'draining' || plugin.active_calls > 0) return;
    plugin.status = 'closing';
    plugin.progress = '正在释放插件资源…';
    pluginSaveAndRender();
    schedulePlugin(plugin, 420, function () {
      if (plugin.status !== 'closing') return;
      const pending = plugin.pending || 'unload';
      plugin.pending = '';
      plugin.progress = '';
      plugin.active_calls = 0;
      if (pending === 'uninstall') {
        const store = pluginStore();
        store.items = store.items.filter(item => item !== plugin);
        if (String(store.selectedId) === String(plugin.id)) {
          store.selectedId = store.items.length ? store.items[0].id : null;
        }
        pluginSaveAndRender();
        P.toast('插件已卸载。');
        return;
      }
      if (pending === 'restart') {
        plugin.enabled = true;
        plugin.status = 'loading';
        plugin.progress = '正在重新载入插件…';
        pluginSaveAndRender();
        schedulePlugin(plugin, 900, function () {
          if (plugin.status !== 'loading') return;
          plugin.status = 'ready';
          plugin.progress = '';
          plugin.active_calls = plugin.id === 'mobile-bridge' ? 1 : 0;
          pluginSaveAndRender();
          P.toast('插件“' + plugin.name + '”已重载。');
        });
        return;
      }
      plugin.enabled = false;
      plugin.status = 'unloaded';
      pluginSaveAndRender();
      P.toast('插件“' + plugin.name + '”已停用。');
    });
  }

  function beginDrain(plugin, operation) {
    plugin.enabled = false;
    plugin.status = 'draining';
    plugin.pending = operation;
    plugin.progress = operation === 'restart'
      ? '等待在途操作结束，然后重新载入。'
      : '停止接收新工作，等待在途操作结束。';
    pluginSaveAndRender();
    if (!plugin.active_calls) schedulePlugin(plugin, 620, function () { finishDrain(plugin); });
  }

  function stopPluginCalls(plugin) {
    if (!plugin.active_calls && plugin.status !== 'draining') {
      P.toast('当前没有在途操作。');
      return;
    }
    plugin.active_calls = 0;
    plugin.progress = plugin.status === 'draining' ? '在途操作已停止，正在完成收尾。' : '在途操作已停止。';
    pluginSaveAndRender();
    P.toast('已停止在途操作。');
    if (plugin.status === 'draining') finishDrain(plugin);
  }

  function removePlugin(plugin) {
    if (!plugin || pluginBusy(plugin)) return;
    plugin.status = 'closing';
    plugin.enabled = false;
    plugin.pending = 'uninstall';
    plugin.progress = '正在移除插件文件…';
    pluginSaveAndRender();
    schedulePlugin(plugin, 500, function () {
      if (plugin.status !== 'closing') return;
      const store = pluginStore();
      store.items = store.items.filter(item => item !== plugin);
      if (String(store.selectedId) === String(plugin.id)) {
        store.selectedId = store.items.length ? store.items[0].id : null;
      }
      pluginSaveAndRender();
      P.toast('插件已卸载。');
    });
  }

  function pluginBadge(plugin) {
    const status = pluginStatus(plugin);
    return '<span class="ext-status is-' + status.tone + '"><i aria-hidden="true"></i>' + status.label + '</span>';
  }

  function pluginCapabilityLabel(value) {
    if (value && typeof value === 'object') return value.title || value.name || value.id || '扩展能力';
    return CAPABILITY_LABEL[value] || String(value || '扩展能力');
  }

  function pluginSettingsFields(plugin) {
    const schema = plugin.settings_schema;
    if (!schema || typeof schema !== 'object' || !schema.properties ||
        typeof schema.properties !== 'object' || Array.isArray(schema.properties)) return [];
    return Object.keys(schema.properties)
      .filter(key => schema.properties[key] && typeof schema.properties[key] === 'object')
      .map(key => ({ key: key, schema: schema.properties[key] }));
  }

  function pluginSettingValue(plugin, key, schema) {
    if (plugin.settings && Object.prototype.hasOwnProperty.call(plugin.settings, key)) return plugin.settings[key];
    if (Object.prototype.hasOwnProperty.call(schema, 'default')) return schema.default;
    return schema.type === 'boolean' ? false : '';
  }

  function pluginSettingControl(plugin, entry) {
    const schema = entry.schema;
    const value = pluginSettingValue(plugin, entry.key, schema);
    const field = 'data-plugin-field="' + e(entry.key) + '"';
    const title = '<span>' + e(schema.title || entry.key) + '</span>';
    const help = schema.description ? '<small>' + e(schema.description) + '</small>' : '';
    let control = '';
    const type = String(schema.type || '').toLowerCase();
    if (type === 'boolean') {
      control = '<label class="ext-check"><input type="checkbox" ' + field + (value === true ? ' checked' : '') + '><span>启用</span></label>';
    } else if (Array.isArray(schema.enum) && schema.enum.length) {
      control = '<select ' + field + '>' + schema.enum.map(choice => {
        const text = String(choice);
        return '<option value="' + e(text) + '"' + (text === String(value) ? ' selected' : '') + '>' + e(text) + '</option>';
      }).join('') + '</select>';
    } else if (type === 'string') {
      control = '<input type="text" ' + field + ' value="' + e(value) + '">';
    } else if (type === 'number' || type === 'integer') {
      control = '<input type="number" step="' + (type === 'integer' ? '1' : 'any') + '" ' + field +
        (schema.minimum !== undefined ? ' min="' + e(schema.minimum) + '"' : '') +
        (schema.maximum !== undefined ? ' max="' + e(schema.maximum) + '"' : '') +
        ' value="' + e(value) + '">';
    } else {
      return '<div class="ext-field"><div class="ext-unsupported">此设置类型暂不支持（' +
        e(type || '未声明类型') + '），已保留现有值。</div>' + help + '</div>';
    }
    return '<label class="ext-field ext-setting-field">' + title + control + help + '</label>';
  }

  function pluginSettingsPanel(plugin) {
    const fields = pluginSettingsFields(plugin);
    if (!fields.length) return '<p class="ext-settings-empty">当前插件没有声明设置 schema。</p>';
    return j(
      '<div class="ext-settings-grid">',
      fields.map(entry => pluginSettingControl(plugin, entry)).join(''),
      '</div>',
      '<div class="ext-settings-foot"><span>保存后立即用于下一次插件操作。</span><button class="btn solid sm" type="button" data-do="ext-plugin-settings-save" data-id="' + e(plugin.id) + '">保存设置</button></div>'
    );
  }

  function pluginActions(plugin) {
    const status = String(plugin.status || '').toLowerCase();
    const calls = Number(plugin.active_calls || 0);
    let controls = '';
    if (status === 'ready' || status === 'loading') {
      controls += '<button class="btn ghost sm" type="button" data-do="ext-plugin-op" data-op="unload" data-id="' + e(plugin.id) + '">停用</button>';
    }
    if (status === 'ready') {
      controls += '<button class="btn ghost sm" type="button" data-do="ext-plugin-op" data-op="restart" data-id="' + e(plugin.id) + '">重载</button>';
      controls += '<button class="btn ghost sm" type="button" data-do="ext-plugin-op" data-op="stop" data-id="' + e(plugin.id) + '">停止在途' + (calls ? '（' + calls + '）' : '') + '</button>';
    }
    if (status === 'draining' && calls) {
      controls += '<button class="btn ghost sm" type="button" data-do="ext-plugin-op" data-op="stop" data-id="' + e(plugin.id) + '">停止在途以完成收尾</button>';
    }
    if (status === 'unloaded' || status === 'error') {
      controls += '<button class="btn solid sm" type="button" data-do="ext-plugin-op" data-op="load" data-id="' + e(plugin.id) + '">加载</button>';
    }
    const remove = status === 'draining' || status === 'closing' || status === 'loading'
      ? '<button class="btn ghost sm" type="button" disabled>卸载</button>'
      : '<button class="btn ghost sm ext-text-danger" type="button" data-do="ext-plugin-op" data-op="uninstall" data-id="' + e(plugin.id) + '">卸载</button>';
    return '<div class="ext-plugin-actions">' + controls + remove + '</div>';
  }

  function pluginDetail(plugin) {
    if (!plugin) return '<div class="card empty-state ext-empty">还没有插件。可以导入演示包或重新发现插件。</div>';
    const status = pluginStatus(plugin);
    const pages = Array.isArray(plugin.pages) ? plugin.pages : [];
    const caps = Array.isArray(plugin.capabilities) ? plugin.capabilities : [];
    return j(
      '<section class="card ext-plugin-detail" aria-label="插件详情">',
      '<div class="ext-plugin-title-row">',
      '<div><span class="ext-kicker">' + e(plugin.id) + ' · v' + e(plugin.version || '未声明') + '</span>',
      '<h2>' + e(plugin.name || plugin.id) + '</h2></div>',
      pluginBadge(plugin),
      '</div>',
      '<p class="ext-plugin-description">' + e(plugin.description || '此插件没有填写简介。') + '</p>',
      plugin.progress ? '<p class="ext-progress">' + e(plugin.progress) + '</p>' : '',
      plugin.error ? '<p class="ext-error" role="alert">插件错误：' + e(plugin.error) + '</p>' : '',
      '<dl class="ext-facts">',
      '<div><dt>状态</dt><dd>' + status.label + '</dd></div>',
      '<div><dt>启用</dt><dd>' + (plugin.enabled ? '是' : '否') + '</dd></div>',
      '<div><dt>在途操作</dt><dd>' + Number(plugin.active_calls || 0) + '</dd></div>',
      '<div><dt>入口</dt><dd class="ext-mono">' + e(plugin.entry || '未声明') + '</dd></div>',
      '</dl>',
      pluginActions(plugin),
      '<div class="ext-detail-section"><h3>能力</h3>',
      caps.length ? '<div class="ext-tag-row">' + caps.map(cap => '<span class="ext-tag">' + e(pluginCapabilityLabel(cap)) + '</span>').join('') + '</div>'
        : '<p class="ext-section-empty">未声明能力。</p>',
      '</div>',
      '<div class="ext-detail-section"><h3>功能页</h3>',
      pages.length ? pages.map(page => j(
        '<div class="ext-page-row"><div><b>' + e(page.title || page.id || '功能页') + '</b><span class="ext-mono">' + e(page.path || '') + '</span></div>',
        '<button class="btn ghost sm" type="button" data-do="ext-plugin-page" data-id="' + e(plugin.id) + '" data-page="' + e(page.title || page.id || '功能页') + '">打开页面</button></div>'
      )).join('') : '<p class="ext-section-empty">此插件没有提供功能页。</p>',
      '</div>',
      '<div class="ext-detail-section"><div class="ext-section-head"><h3>插件设置</h3><span class="ext-mono">通用 schema</span></div>',
      pluginSettingsPanel(plugin),
      '</div>',
      '</section>'
    );
  }

  function pluginList(plugins) {
    if (!plugins.length) return '<div class="card empty-state ext-empty">还没有登记插件。</div>';
    const selectedId = pluginStore().selectedId;
    return '<div class="ext-plugin-list" aria-label="插件列表">' + plugins.map(plugin => j(
      '<button class="ext-plugin-list-item' + (String(selectedId) === String(plugin.id) ? ' is-selected' : '') + '" type="button" data-do="ext-plugin-select" data-id="' + e(plugin.id) + '" aria-current="' + String(String(selectedId) === String(plugin.id)) + '">',
      '<span class="ext-plugin-list-head"><b>' + e(plugin.name || plugin.id) + '</b>' + pluginBadge(plugin) + '</span>',
      '<span class="ext-mono">' + e(plugin.id) + ' · v' + e(plugin.version || '?') + '</span>',
      Number(plugin.active_calls || 0) ? '<span class="ext-calls">在途 ' + Number(plugin.active_calls) + '</span>' : '',
      '</button>'
    )).join('') + '</div>';
  }

  function pluginPageDialog(plugin, pageName) {
    P.dialog({
      title: '插件功能页 · 本轮范围',
      submit: null,
      body: j(
        '<div class="ext-dialog ext-scope-dialog">',
        '<div class="ext-scope-mark" aria-hidden="true">' + P.icon('plug') + '</div>',
        '<h3>' + e(plugin && plugin.name || '插件') + ' · ' + e(pageName || '功能页') + '</h3>',
        '<p>本轮只预览插件管理，不实现或加载插件内部业务页。</p>',
        '<p>正式 Nexus 会通过隔离页面打开这个入口，插件页面与宿主分别运行。此处保留入口，便于后续集成时检查位置与交互。</p>',
        '<p class="ext-dialog-hint">插件当前状态：' + e(pluginStatus(plugin).label) + '。关闭本弹窗不会改变插件状态。</p>',
        '</div>'
      )
    });
  }

  function openImportDialog() {
    const options = IMPORT_PACKAGES.map(pkg => '<option value="' + e(pkg.key) + '">' + e(pkg.label) + '</option>').join('');
    const detail = IMPORT_PACKAGES.map(pkg => j(
      '<div class="ext-package" data-package-detail="' + e(pkg.key) + '" hidden>',
      '<div><b>' + e(pkg.plugin.name) + '</b><span class="ext-mono">' + e(pkg.plugin.id) + ' · v' + e(pkg.plugin.version) + '</span></div>',
      '<p>' + e(pkg.plugin.description) + '</p>',
      '<span>' + (pluginStore().items.some(item => item.id === pkg.plugin.id) ? '同名已安装，可选择后确认替换' : '新插件，导入后默认不启用') + '</span>',
      '</div>'
    )).join('');
    const dialog = P.dialog({
      title: '导入 ZIP',
      submit: '导入选中包',
      body: j(
        '<div class="ext-dialog ext-import-dialog">',
        '<p class="ext-dialog-note">选择本地 ZIP 后只显示文件名；本预览不会读取、解压或安装文件。也可以直接选用演示包。</p>',
        '<div class="ext-file-pick">',
        '<input type="file" class="ext-file-input" accept=".zip,application/zip" aria-label="选择 ZIP 文件">',
        '<span class="ext-file-name">尚未选择文件</span>',
        '<button class="btn ghost sm" type="button" data-do="ext-pick-file">选择 ZIP</button>',
        '</div>',
        '<label class="ext-field"><span>或使用演示包</span><select name="package">' + options + '</select></label>',
        '<div class="ext-package-list">' + detail + '</div>',
        '<p class="ext-dialog-hint">导入或替换后默认禁用；同名包需要确认替换，已加载的插件需先停用并完成收尾。</p>',
        '</div>'
      ),
      onSubmit: function (form) {
        const key = String(form.querySelector('[name="package"]').value || '');
        const pkg = IMPORT_PACKAGES.find(item => item.key === key);
        if (!pkg) throw new Error('请选择演示包。');
        const current = pluginStore().items.find(item => item.id === pkg.plugin.id);
        if (!current) {
          applyImport(pkg, false);
          return;
        }
        P.dialog({
          title: '发现同名插件',
          submit: '替换版本',
          body: j(
            '<div class="ext-dialog ext-conflict-dialog">',
            '<p>已安装 <b>' + e(current.name || current.id) + '</b> v' + e(current.version || '?') + '，演示包是 v' + e(pkg.plugin.version) + '。</p>',
            '<p>替换后保留原设置，插件默认不启用。当前状态：' + e(pluginStatus(current).label) + '。</p>',
            '<p class="ext-dialog-hint">如果插件已加载，请先停用并等待收尾，再执行替换。</p>',
            '</div>'
          ),
          onSubmit: function () {
            if (!['unloaded', 'error'].includes(String(current.status || '').toLowerCase())) {
              throw new Error('请先停用插件并等待状态变为未启用，再替换版本。');
            }
            applyImport(pkg, true);
          }
        });
      }
    });
    const select = dialog.querySelector('[name="package"]');
    const file = dialog.querySelector('.ext-file-input');
    const nameNode = dialog.querySelector('.ext-file-name');
    dialog.querySelector('[data-do="ext-pick-file"]').onclick = function () { file.click(); };
    file.addEventListener('change', function () {
      nameNode.textContent = file.files && file.files[0] ? file.files[0].name : '尚未选择文件';
      nameNode.classList.toggle('is-picked', Boolean(file.files && file.files[0]));
    });
    const sync = function () {
      dialog.querySelectorAll('[data-package-detail]').forEach(node => {
        node.hidden = node.dataset.packageDetail !== select.value;
      });
    };
    select.addEventListener('change', sync);
    sync();
  }

  function applyImport(pkg, replace) {
    const store = pluginStore();
    const current = store.items.find(item => item.id === pkg.plugin.id);
    if (current) {
      if (!replace) return;
      Object.assign(current, clone(pkg.plugin), {
        settings: current.settings || {},
        enabled: false, status: 'unloaded', active_calls: 0,
        error: '', progress: '', pending: '', importedAt: nowIso(), sourceFile: pkg.file
      });
      store.selectedId = current.id;
    } else {
      const plugin = clone(pkg.plugin);
      Object.assign(plugin, {
        settings: {}, enabled: false, status: 'unloaded', active_calls: 0,
        error: '', progress: '', pending: '', importedAt: nowIso(), sourceFile: pkg.file
      });
      store.items.push(plugin);
      store.selectedId = plugin.id;
    }
    P.save();
    P.render();
    P.toast(replace ? '插件版本已替换，默认未启用。' : '插件已导入，默认未启用。');
  }

  function collectPluginSettings(plugin) {
    const root = document.querySelector('#view-plugins .ext-settings-grid');
    if (!root) return plugin.settings || {};
    const settings = Object.assign({}, plugin.settings || {});
    const fields = pluginSettingsFields(plugin);
    for (const entry of fields) {
      const input = root.querySelector('[data-plugin-field="' + CSS.escape(entry.key) + '"]');
      if (!input) continue;
      const schema = entry.schema;
      const type = String(schema.type || '').toLowerCase();
      const label = schema.title || entry.key;
      if (type === 'boolean') {
        settings[entry.key] = input.checked === true;
      } else if (Array.isArray(schema.enum) && schema.enum.length) {
        const choice = schema.enum.find(item => String(item) === input.value);
        settings[entry.key] = choice === undefined ? input.value : choice;
      } else if (type === 'number' || type === 'integer') {
        const raw = String(input.value || '').trim();
        const value = Number(raw);
        if (!raw || !Number.isFinite(value) || (type === 'integer' && !Number.isInteger(value))) {
          throw new Error(label + ' 需要填写有效' + (type === 'integer' ? '整数' : '数值') + '。');
        }
        if (schema.minimum !== undefined && value < Number(schema.minimum)) throw new Error(label + ' 不能小于 ' + schema.minimum + '。');
        if (schema.maximum !== undefined && value > Number(schema.maximum)) throw new Error(label + ' 不能大于 ' + schema.maximum + '。');
        settings[entry.key] = value;
      } else if (type === 'string') {
        settings[entry.key] = input.value;
      }
    }
    return settings;
  }

  function pluginSummary(items) {
    const loaded = items.filter(item => item.status === 'ready').length;
    const calls = items.reduce((sum, item) => sum + Number(item.active_calls || 0), 0);
    return { loaded: loaded, calls: calls, off: items.length - loaded };
  }

  function requestStatus(value) {
    const status = String(value || '').toLowerCase();
    const map = {
      complete: ['已完成', 'on'], sent: ['已发送', 'on'], error: ['错误', 'error'],
      failed: ['失败', 'error'], prepared_failed: ['未发送', 'wait']
    };
    return map[status] || [value || '未记录', 'off'];
  }

  function requestKind(value) {
    return value === 'ling' ? '主交流' : value === 'task' ? '后台工作' : String(value || '未记录');
  }

  function requestTime(value, full) {
    if (!value) return '未记录';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('zh-CN', full
      ? { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }
      : { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function filteredRequests() {
    const store = pluginStore();
    const owner = String(store.filters.owner || '').trim();
    const kind = String(store.filters.kind || '').trim();
    return store.requests.slice().sort((a, b) => Number(b.id) - Number(a.id)).filter(request => {
      if (owner && String(request.owner_id || '') !== owner) return false;
      if (kind && String(request.kind || '') !== kind) return false;
      return true;
    });
  }

  function requestSelectOptions(values, selected) {
    return values.map(value => '<option value="' + e(value) + '"' + (String(value) === String(selected) ? ' selected' : '') + '>' + e(value) + '</option>').join('');
  }

  function requestFilterPanel(all, filtered) {
    const store = pluginStore();
    const owners = [...new Set(all.map(item => String(item.owner_id || '')).filter(Boolean))].sort();
    const kinds = [...new Set(all.map(item => String(item.kind || '')).filter(Boolean))].sort();
    return j(
      '<section class="card ext-request-filters" aria-label="请求筛选">',
      '<div class="ext-filter-head"><div><h2>请求记录</h2><p>保留最近 7 天，可按来源与类型缩小范围。</p></div><span class="ext-count">' + filtered.length + ' / ' + all.length + ' 条</span></div>',
      '<div class="ext-filter-grid">',
      '<label class="ext-field"><span>活动 · owner_id</span><input id="ext-request-owner" list="ext-owner-options" placeholder="全部活动" value="' + e(store.filters.owner) + '"><datalist id="ext-owner-options">' + requestSelectOptions(owners, store.filters.owner) + '</datalist></label>',
      '<label class="ext-field"><span>类型 · kind</span><input id="ext-request-kind" list="ext-kind-options" placeholder="全部类型" value="' + e(store.filters.kind) + '"><datalist id="ext-kind-options">' + requestSelectOptions(kinds, store.filters.kind) + '</datalist></label>',
      '<div class="ext-filter-actions"><button class="btn solid sm" type="button" data-do="ext-request-apply">筛选</button><button class="btn ghost sm" type="button" data-do="ext-request-clear">清除</button></div>',
      '</div>',
      '<p class="ext-filter-hint">留空查询全部记录，也可输入活动标识或类型精确筛选。</p>',
      '</section>'
    );
  }

  function requestRows(requests) {
    if (!requests.length) return '<div class="card empty-state ext-empty">没有符合条件的请求记录。可以清除筛选后再看。</div>';
    const selected = pluginStore().selectedRequestId;
    return '<div class="ext-request-list">' + requests.map(request => {
      const status = requestStatus(request.status);
      return j(
        '<button class="ext-request-row' + (String(selected) === String(request.id) ? ' is-selected' : '') + '" type="button" data-do="ext-request-select" data-id="' + e(request.id) + '">',
        '<span class="ext-request-row-head"><b>#' + e(request.id) + ' · ' + e(requestKind(request.kind)) + '</b><span class="ext-status is-' + status[1] + '"><i aria-hidden="true"></i>' + e(status[0]) + '</span></span>',
        '<span class="ext-request-row-meta"><span class="ext-mono">' + e(request.owner_id || '未记录活动') + '</span><time>' + e(requestTime(request.created_at)) + '</time></span>',
        '<span class="ext-request-row-foot"><span>' + e(request.model || '模型未记录') + '</span><span>' + (request.plugins || []).length + ' 个插件参与</span></span>',
        '</button>'
      );
    }).join('') + '</div>';
  }

  function jsonText(value) {
    if (value === undefined) return '未记录';
    if (typeof value === 'string') {
      try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
    }
    try { return JSON.stringify(value, null, 2); } catch { return String(value); }
  }

  function participationLabel(entry) {
    if (!entry || typeof entry !== 'object') return String(entry || '未命名插件');
    const statusMap = { used: '已参与', skipped: '未参与', not_applicable: '无需参与', failed: '失败' };
    const state = entry.status ? (statusMap[String(entry.status)] || entry.status) :
      (entry.used === true ? '已参与' : entry.used === false ? '未参与' : '已记录');
    return {
      name: entry.name || entry.title || entry.id || entry.plugin_id || '未命名插件',
      state: state,
      reason: entry.reason || entry.participation_reason || entry.why || entry.message || '未记录原因'
    };
  }

  function requestDetail(request) {
    if (!request) return '<div class="card ext-request-detail ext-empty">选择一条请求查看来源、参与原因、材料和最终 JSON。</div>';
    const status = requestStatus(request.status);
    const plugins = Array.isArray(request.plugins) ? request.plugins : [];
    return j(
      '<section class="card ext-request-detail" aria-label="请求详情">',
      '<div class="ext-request-detail-head"><div><span class="ext-kicker">请求详情</span><h2>请求 #' + e(request.id) + '</h2></div><span class="ext-status is-' + status[1] + '"><i aria-hidden="true"></i>' + e(status[0]) + '</span></div>',
      '<dl class="ext-facts ext-facts-wide">',
      '<div><dt>时间</dt><dd>' + e(requestTime(request.created_at, true)) + '</dd></div>',
      '<div><dt>活动</dt><dd class="ext-mono">' + e(request.owner_id || '未记录') + '</dd></div>',
      '<div><dt>类型</dt><dd>' + e(requestKind(request.kind)) + '</dd></div>',
      '<div><dt>模型</dt><dd>' + e(request.model || '未记录') + '</dd></div>',
      '<div><dt>协议</dt><dd class="ext-mono">' + e(request.protocol || '未记录') + '</dd></div>',
      '<div><dt>更新时间</dt><dd>' + e(requestTime(request.updated_at, true)) + '</dd></div>',
      '</dl>',
      request.error ? '<p class="ext-error">请求错误：' + e(request.error) + '</p>' : '',
      '<div class="ext-detail-section"><h3>来源与参与原因</h3>',
      plugins.length ? '<div class="ext-participation-list">' + plugins.map(entry => {
        const row = participationLabel(entry);
        return '<div class="ext-participation"><div><b>' + e(row.name) + '</b><span class="ext-tag">' + e(row.state) + '</span></div><p>' + e(row.reason) + '</p></div>';
      }).join('') + '</div>' : '<p class="ext-section-empty">此请求没有记录插件参与。</p>',
      '</div>',
      '<div class="ext-detail-section"><h3>材料</h3>',
      Array.isArray(request.materials) && request.materials.length
        ? '<pre class="ext-json">' + e(jsonText(request.materials)) + '</pre>'
        : '<p class="ext-section-empty">此请求没有记录材料。</p>',
      '</div>',
      '<div class="ext-detail-section"><h3>最终 JSON</h3><pre class="ext-json">' + e(jsonText(request.body)) + '</pre></div>',
      '</section>'
    );
  }

  function renderPlugins() {
    const store = pluginStore();
    const tab = store.tab === 'requests' ? 'requests' : 'plugins';
    const items = store.items;
    let plugin = selectedPlugin();
    if (!plugin && items.length) plugin = items[0];
    const summary = pluginSummary(items);
    const tabs = j(
      '<div class="ext-tabs ext-plugin-tabs" role="tablist" aria-label="插件管理栏目">',
      '<button type="button" role="tab" aria-selected="' + String(tab === 'plugins') + '" class="' + (tab === 'plugins' ? 'is-active' : '') + '" data-do="ext-plugin-tab" data-tab="plugins">插件<span>' + items.length + '</span></button>',
      '<button type="button" role="tab" aria-selected="' + String(tab === 'requests') + '" class="' + (tab === 'requests' ? 'is-active' : '') + '" data-do="ext-plugin-tab" data-tab="requests">请求记录<span>' + store.requests.length + '</span></button>',
      '</div>'
    );
    const actions = j(
      '<span class="pill">本地演示</span>',
      '<button class="btn ghost sm" type="button" data-do="ext-plugin-refresh">' + P.icon('tune') + '刷新</button>',
      '<button class="btn ghost sm" type="button" data-do="ext-plugin-scan">' + P.icon('orbit') + '发现插件</button>',
      '<button class="btn solid sm" type="button" data-do="ext-plugin-import">' + P.icon('zip') + '导入 ZIP</button>'
    );
    let body = '';
    if (tab === 'plugins') {
      body = j(
        '<div class="ext-page ext-plugins-page">',
        '<section class="card ext-plugin-overview">',
        '<div><span class="ext-kicker">插件中心</span><h2>' + summary.loaded + ' 个已加载，' + summary.off + ' 个未启用</h2><p>插件目录 <span class="ext-mono">' + e(store.root || 'plugins') + '</span>' + (store.lastScan ? ' · 上次发现 ' + e(formatTime(store.lastScan)) : '') + '</p></div>',
        '<div class="ext-plugin-metrics"><div><b>' + items.length + '</b><span>已登记</span></div><div><b>' + summary.calls + '</b><span>在途操作</span></div><div><b>' + items.filter(item => pluginBusy(item)).length + '</b><span>处理中</span></div></div>',
        '</section>',
        tabs,
        '<div class="ext-plugin-layout">',
        pluginList(items),
        pluginDetail(plugin),
        '</div>',
        '<p class="foot-note">停用会停止接收新工作，并等待在途操作结束；导入或替换后的插件默认不启用。</p>',
        '</div>'
      );
    } else {
      const all = store.requests.slice().sort((a, b) => Number(b.id) - Number(a.id));
      const filtered = filteredRequests();
      const limit = Math.max(1, Number(store.requestLimit) || 6);
      const shown = filtered.slice(0, limit);
      const selected = all.find(request => String(request.id) === String(store.selectedRequestId));
      body = j(
        '<div class="ext-page ext-requests-page">',
        tabs,
        requestFilterPanel(all, filtered),
        '<div class="ext-request-layout">',
        '<div class="ext-request-left">',
        requestRows(shown),
        filtered.length > limit
          ? '<button class="btn ghost sm ext-more" type="button" data-do="ext-request-more">加载更早记录</button>'
          : (filtered.length ? '<p class="ext-list-end">已显示全部 ' + filtered.length + ' 条记录。</p>' : ''),
        '</div>',
        requestDetail(selected),
        '</div>',
        '<p class="foot-note">详情保留本次请求的参与原因、引用材料与最终发送内容；只保留最近 7 天。</p>',
        '</div>'
      );
    }
    return P.page('插件管理', '加载、停用、导入与检查模型请求；功能页保留入口并说明本轮范围。', body, actions);
  }

  P.views.plugins = renderPlugins;

  P.action('ext-plugin-select', function (button) {
    pluginStore().selectedId = button.dataset.id;
    P.save();
    P.render();
  });

  P.action('ext-plugin-refresh', function () {
    pluginStore().lastRefresh = nowIso();
    P.save();
    P.render();
    P.toast('插件状态已刷新。');
  });

  P.action('ext-plugin-scan', function () {
    const store = pluginStore();
    let added = 0;
    for (const seed of REAL_PLUGIN_SEEDS) {
      if (!store.items.some(item => item.id === seed.id)) {
        store.items.push(clone(seed));
        added += 1;
      }
    }
    store.lastScan = nowIso();
    if (!store.selectedId && store.items.length) store.selectedId = store.items[0].id;
    P.save();
    P.render();
    P.toast(added ? '已发现 ' + added + ' 个插件。' : '没有发现新的插件。');
  });

  P.action('ext-plugin-import', function () {
    openImportDialog();
  });

  P.action('ext-plugin-op', function (button) {
    const plugin = pluginStore().items.find(item => String(item.id) === String(button.dataset.id));
    if (!plugin) return;
    const op = button.dataset.op;
    const status = String(plugin.status || '').toLowerCase();
    if (op === 'load') {
      loadPlugin(plugin);
      return;
    }
    if (op === 'unload') {
      if (!['ready', 'loading'].includes(status)) return;
      beginDrain(plugin, 'unload');
      return;
    }
    if (op === 'restart') {
      if (status !== 'ready') return;
      beginDrain(plugin, 'restart');
      return;
    }
    if (op === 'stop') {
      stopPluginCalls(plugin);
      return;
    }
    if (op === 'uninstall') {
      if (pluginBusy(plugin)) return;
      P.confirm('卸载插件', '卸载“' + plugin.name + '”？插件文件会从当前演示数据中移除，历史与资料保留。', function () {
        removePlugin(plugin);
      });
    }
  });

  P.action('ext-plugin-settings-save', function (button) {
    const plugin = pluginStore().items.find(item => String(item.id) === String(button.dataset.id));
    if (!plugin) return;
    const settings = collectPluginSettings(plugin);
    plugin.settings = settings;
    pluginSaveAndRender();
    P.toast('插件设置已保存。');
  });

  P.action('ext-plugin-page', function (button) {
    const plugin = pluginStore().items.find(item => String(item.id) === String(button.dataset.id));
    pluginPageDialog(plugin, button.dataset.page);
  });

  P.action('ext-request-apply', function () {
    const store = pluginStore();
    store.filters = {
      owner: String(document.getElementById('ext-request-owner')?.value || '').trim(),
      kind: String(document.getElementById('ext-request-kind')?.value || '').trim()
    };
    store.requestLimit = 6;
    const visible = filteredRequests();
    store.selectedRequestId = visible.length ? visible[0].id : null;
    P.save();
    P.render();
  });

  P.action('ext-request-clear', function () {
    const store = pluginStore();
    store.filters = { owner: '', kind: '' };
    store.requestLimit = 6;
    const visible = filteredRequests();
    store.selectedRequestId = visible.length ? visible[0].id : null;
    P.save();
    P.render();
  });

  P.action('ext-request-select', function (button) {
    pluginStore().selectedRequestId = Number(button.dataset.id);
    P.save();
    P.render();
  });

  P.action('ext-request-more', function () {
    const store = pluginStore();
    store.requestLimit = Math.max(6, Number(store.requestLimit) || 6) + 6;
    P.save();
    P.render();
  });

  setTimeout(function () {
    let changed = false;
    for (const plugin of pluginStore().items) {
      if (plugin.status === 'loading') {
        plugin.status = 'ready';
        plugin.progress = '';
        plugin.enabled = true;
        if (!plugin.active_calls && plugin.id === 'mobile-bridge') plugin.active_calls = 1;
        changed = true;
      }
    }
    if (changed) pluginSaveAndRender();
  }, 1000);

})();
