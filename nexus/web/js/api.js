/* ==========================================================================
   Nexus · 灵 — 后端接入层
   请求封装、快照映射、实时事件分发。
   界面偏好留在浏览器；所有业务状态都以后端为准。
   ========================================================================== */
'use strict';

(function (N) {
  const state = N.state;
  const BASE = '';

  /* ---------------------------------------------------------------- 请求 */
  let inflight = 0;

  async function request(path, { method = 'GET', body, headers, timeout = 20000 } = {}) {
    inflight++;
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = controller && timeout > 0 ? setTimeout(() => controller.abort(), timeout) : null;
    try {
      const init = { method, headers: { ...(headers || {}) } };
      if (controller) init.signal = controller.signal;
      if (body !== undefined) {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body);
      }
      const res = await fetch(BASE + path, init);
      const text = await res.text();
      let value = null;
      if (text) {
        try { value = JSON.parse(text); } catch (_) { value = { error: text }; }
      }
      if (!res.ok) {
        const error = new Error((value && (value.error || value.message)) || `请求失败（${res.status}）`);
        error.status = res.status;
        error.value = value;
        throw error;
      }
      state.connectionError = '';
      return value;
    } catch (error) {
      if (error?.name === 'AbortError') {
        const expired = new Error('请求超时');
        expired.timeout = true;
        throw expired;
      }
      if (error instanceof TypeError) {
        state.connectionError = '无法连接到 Nexus 服务';
        error.message = state.connectionError;
      }
      throw error;
    } finally {
      if (timer) clearTimeout(timer);
      inflight--;
    }
  }

  const get = path => request(path);
  const post = (path, body) => request(path, { method: 'POST', body: body || {} });
  const put = (path, body) => request(path, { method: 'PUT', body: body || {} });
  const del = (path, body) => request(path, { method: 'DELETE', body: body || {} });

  async function upload(name, blob) {
    const sid = state.sessionId;
    if (!sid) throw new Error('交流记录尚未就绪，请稍后再试');
    const res = await fetch(`${BASE}/api/sessions/${encodeURIComponent(sid)}/attachments?name=${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: { 'Content-Type': blob.type || 'application/octet-stream' },
      body: blob,
    });
    const text = await res.text();
    let value = null;
    try { value = text ? JSON.parse(text) : null; } catch (_) { value = null; }
    if (!res.ok) throw new Error((value && value.error) || '附件上传失败');
    return value;
  }

  /* ---------------------------------------------------------------- 映射 */
  const pad = n => String(n).padStart(2, '0');
  function clock(iso) {
    if (!iso) return N.nowHM();
    const value = String(iso);
    const direct = /T(\d{2}):(\d{2})/.exec(value);
    if (direct) return direct[1] + ':' + direct[2];
    const space = /\s(\d{2}):(\d{2})/.exec(value);
    if (space) return space[1] + ':' + space[2];
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? '' : pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function imageUrl(item) {
    if (!item) return '';
    if (typeof item === 'string') return item;
    if (item.url) return item.url;
    if (item.data) return `data:${item.mime || 'image/png'};base64,${item.data}`;
    return '';
  }

  function attachmentView(item) {
    if (!item) return null;
    if (typeof item === 'string') return { id: item, name: item, size: 0, kind: 'file', url: '' };
    const mime = String(item.mime || '');
    return {
      id: item.id, name: item.name || '附件', size: item.size || 0,
      kind: /^image\//.test(mime) ? 'image' : 'file',
      url: state.sessionId && item.id
        ? `/api/sessions/${encodeURIComponent(state.sessionId)}/attachments/${encodeURIComponent(item.id)}`
        : '',
    };
  }

  function itemKey(item, fallback) {
    if (item.message_id) return 'a:' + item.message_id;
    if (typeof item.index === 'number') return fallback + ':' + item.index;
    return fallback + ':' + Math.random().toString(36).slice(2, 8);
  }

  /* 后端记录 → 预览条目。thinking 单独成项，与处理过程折叠一致。 */
  function mapItems(items) {
    const out = [];
    for (const raw of Array.isArray(items) ? items : []) {
      if (!raw || typeof raw !== 'object') continue;
      const role = raw.role;
      if (role === 'user') {
        out.push({
          key: itemKey(raw, 'u'), role: 'user', text: raw.text || '',
          at: clock(raw.at), images: (raw.images || []).map(imageUrl).filter(Boolean),
          attachments: (raw.attachments || []).map(attachmentView).filter(Boolean),
          index: raw.index, message_id: raw.message_id, compressed: !!raw.compressed,
        });
        continue;
      }
      if (role === 'assistant') {
        const key = itemKey(raw, 'a');
        if (raw.thinking) {
          out.push({
            key: 't:' + key, role: 'thinking', thinking: raw.thinking,
            at: clock(raw.at), index: raw.index, message_id: raw.message_id,
          });
        }
        if (raw.text || raw.live) {
          out.push({
            key, role: 'assistant', text: raw.text || '', at: clock(raw.at),
            elapsed: raw.elapsed || null, live: !!raw.live,
            index: raw.index, message_id: raw.message_id, compressed: !!raw.compressed,
          });
        }
        continue;
      }
      if (role === 'tool') {
        out.push({
          key: 'x:' + (raw.tool_call_id || raw.index), role: 'tool',
          name: raw.name || 'tool', arguments: raw.arguments || null,
          text: raw.text || '', ok: raw.ok !== false, error: raw.error || '',
          live: !!raw.live, at: clock(raw.at), index: raw.index,
          tool_call_id: raw.tool_call_id,
        });
        continue;
      }
      if (role === 'notice') {
        out.push({ key: 'n:' + (raw.index ?? Math.random().toString(36).slice(2, 8)), role: 'notice', text: raw.text || '', at: clock(raw.at) });
      }
    }
    return out;
  }

  /* 实时增量 → 尾部条目。key 与持久化后的记录保持一致，
     这样回复结束时只需要替换节点内容，不用整段重排。 */
  function liveItems(live) {
    const out = [];
    if (!live || typeof live !== 'object') return out;
    const mid = live.message_id || 'current';
    const key = 'a:' + mid;
    if (live.thinking) {
      out.push({ key: 't:' + key, role: 'thinking', thinking: String(live.thinking), live: true, at: N.nowHM() });
    }
    if (live.text) {
      out.push({ key, role: 'assistant', text: String(live.text), live: true, at: N.nowHM() });
    }
    if (live.tool) {
      const tool = live.tool;
      out.push({
        key: 'x:' + (tool.id || tool.tool_call_id || 'current'), role: 'tool',
        name: tool.name || 'tool', arguments: tool.arguments || null,
        text: '', live: true, at: N.nowHM(),
      });
    }
    return out;
  }

  function mapTask(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const stateValue = raw.state || 'pending';
    return {
      id: raw.id, title: raw.title || '未命名工作', state: stateValue,
      goal: raw.goal || '', constraints: raw.constraints || '',
      work_summary: raw.work_summary || '', wait_reason: raw.wait_reason || '',
      next_step: raw.next_step || '', blocked: raw.blocked || '',
      model: raw.model_id || raw.model || '', effort: raw.effort || '',
      path: raw.workspace || raw.path || '',
      started: raw.created_at ? clock(raw.created_at) : (raw.updated_at ? clock(raw.updated_at) : ''),
      finished: stateValue === 'completed' && raw.updated_at ? clock(raw.updated_at) : '',
      progress_text: raw.progress_text || raw.work_summary || '暂无进展说明',
      state_label: raw.state_label || '', status_detail: raw.status_detail || '',
      steps: Array.isArray(raw.steps) ? raw.steps : [],
      evidence: Array.isArray(raw.evidence) ? raw.evidence : [],
      children: Array.isArray(raw.children) ? raw.children : [],
      pending_interactions: Array.isArray(raw.pending_interactions) ? raw.pending_interactions : [],
      unfinished_operations: Array.isArray(raw.unfinished_operations) ? raw.unfinished_operations : [],
      records: Array.isArray(raw.messages) ? raw.messages.map(recordView).filter(Boolean)
        : Array.isArray(raw.records) ? raw.records : [],
      parent_id: raw.parent_id || '', parent_kind: raw.parent_kind || '',
      archived: !!raw.archived,
      active: !!raw.active, queued: !!raw.queued, input_status: raw.input_status || null,
      control_version: raw.control_version ?? null,
      progress_version: raw.progress_version ?? null,
      _detail: !!(raw.steps || raw.messages),
    };
  }

  function recordView(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const role = raw.kind || raw.role;
    const at = clock(raw.created_at || raw.at);
    const key = 'w:' + (raw.seq ?? raw.index ?? Math.random().toString(36).slice(2, 8));
    if (role === 'user' || role === 'work_report') {
      return { key, role: 'user', text: raw.text || '', at };
    }
    if (role === 'assistant') {
      if (!raw.text) return null;
      return { key, role: 'assistant', text: raw.text, at, elapsed: raw.elapsed || null };
    }
    if (role === 'tool') {
      const call = Array.isArray(raw.tool_calls) ? raw.tool_calls[0] : null;
      return {
        key, role: 'tool',
        name: raw.name || call?.name || 'tool',
        arguments: raw.arguments || call?.arguments || null,
        text: raw.text || '', ok: raw.ok !== false, at,
      };
    }
    if (role === 'notice') return { key, role: 'notice', text: raw.text || '', at };
    return null;
  }

  function mapRequest(raw) {
    if (!raw || typeof raw !== 'object') return null;
    return {
      id: raw.id || raw.token, token: raw.id || raw.token,
      kind: raw.kind || 'question', status: raw.status || 'pending',
      payload: raw.payload || {}, owner: raw.owner || '灵',
      workId: raw.task_id || raw.workId || null,
      created_at: raw.created_at || '',
    };
  }

  function mapInputs(list) {
    return (Array.isArray(list) ? list : [])
      .filter(item => item && ['pending', 'running', 'held', 'interrupted', 'failed'].includes(item.status))
      .map(item => ({
        id: item.id, text: item.text || '', status: item.status,
        error: item.error || '', at: clock(item.created_at),
      }));
  }

  /* ---------------------------------------------------------------- 组装 */
  function compose() {
    state.items = [...(state.baseItems || []), ...(state.pending || []), ...(state.liveItems || [])];
  }

  const sync = {
    /* /api/state */
    settings(value) {
      if (!value) return;
      state.providers = (value.providers || []).map(p => ({
        ...p, models: Array.isArray(p.models) ? p.models : [],
      }));
      state.current = value.current || { provider: '', model: '', effort: '' };
      state.effort = value.effort || { levels: [], labels: {}, current: '', mapping: null };
      state.permission = value.permission || 'ask';
      state.workspace = value.workspace || '';
      state.contextWindow = value.context_window || 0;
      state.profile = value.profile || null;
      state.maxSteps = value.max_steps || null;
      if (!state.context?.window && value.context_window) {
        state.context = { ...(state.context || {}), window: value.context_window };
      }
    },

    /* /api/ling */
    chat(value) {
      if (!value) return;
      if (value.session_id) state.sessionId = value.session_id;
      state.baseItems = mapItems(value.items);
      state.liveItems = liveItems(value.live);
      state.live = value.live || {};
      state.liveRevision = Number(value.live_revision || 0);
      state.running = !!value.running;
      state.replying = !!value.running;
      state.historyVersion = value.history_version || '';
      state.queue = mapInputs(value.inputs);
      state.queueCount = Number(value.queue_count || 0);

      const requests = (value.main_requests || []).map(mapRequest).filter(Boolean);
      const active = mapRequest(value.interaction);
      if (active && !requests.some(r => r.id === active.id)) requests.unshift(active);
      state.requests = requests;

      if (Array.isArray(value.tasks)) {
        /* /api/ling 只带未归档的任务；已归档的保留在列表里，由工作页单独维护。 */
        const kept = (state.works || []).filter(w => w && w.archived);
        const seen = new Set();
        const next = value.tasks.map(mapTask).filter(t => {
          if (!t || seen.has(t.id)) return false;
          seen.add(t.id);
          return true;
        });
        for (const item of kept) if (!seen.has(item.id)) next.push(item);
        state.works = next;
      }
      if (value.usage) state.usage = value.usage;
      if (value.context) state.context = value.context;
      else if (!state.context) state.context = { system: 0, tools: 0, messages: 0, window: state.contextWindow || 0 };

      /* 后端已收录的乐观消息不再重复显示 */
      if (state.pending?.length) {
        const texts = new Set(state.baseItems.filter(i => i.role === 'user').map(i => i.text));
        state.pending = state.pending.filter(p => !texts.has(p.text));
      }
      compose();
    },

    /* /api/context */
    context(value) {
      if (!value) return;
      state.contextData = {
        owner_instructions: value.owner_instructions || [],
        roles: value.roles || [],
        sections: value.sections || [],
        summary: value.summary || null,
        messages: value.messages || [],
        version: value.version || '',
        running: !!value.running,
      };
      if (value.running !== undefined) {
        state.replying = !!value.running;
      }
    },

    /* /api/skills */
    skills(value) {
      const list = Array.isArray(value) ? value : (value?.skills || []);
      state.skills = list.map(s => ({
        name: s.name, source: s.source || 'personal',
        description: s.description || '', body: s.body || '',
        effective: s.effective !== false, read_only: !!s.read_only,
        path: s.path || '', overrides: s.overrides || '',
      }));
    },

    /* /api/mcp */
    mcp(value) {
      const list = Array.isArray(value) ? value : (value?.servers || []);
      state.mcp = list.map(s => ({
        name: s.name, transport: s.transport || 'stdio',
        command: s.command || '', args: s.args || [], env: s.env || {},
        url: s.url || '', enabled: s.enabled !== false,
        connected: !!s.connected, tool_count: s.tool_count ?? null,
        error: s.error || '', has_token: !!s.has_token,
        managed: !!s.managed, timeout: s.timeout || null,
      }));
    },

    /* /api/plugins */
    plugins(value) {
      if (!value) return;
      state.pluginRoot = value.root || '';
      state.pluginError = value.error || '';
      state.plugins = (value.plugins || []).map(p => ({
        ...p,
        capabilities: (p.capabilities || []).map(c => (typeof c === 'string' ? { title: c } : c)),
        pages: p.pages || [],
        settings_schema: p.settings_schema || {},
        settings: p.settings || {},
      }));
    },

    /* /api/tasks?archived=1 */
    works(value) {
      const list = Array.isArray(value) ? value : (value?.tasks || []);
      const keep = new Map((state.works || []).map(w => [w.id, w]));
      state.works = list.map(raw => {
        const mapped = mapTask(raw);
        if (!mapped) return null;
        const old = keep.get(mapped.id);
        if (old?._detail && !mapped._detail) {
          return { ...mapped, steps: old.steps, evidence: old.evidence, records: old.records, _detail: true };
        }
        return mapped;
      }).filter(Boolean);
    },

    /* /api/model-requests */
    modelRequests(value) {
      const list = Array.isArray(value) ? value : (value?.requests || []);
      state.modelRequests = list.map(r => ({
        ...r,
        id: r.id, created_at: r.created_at || '',
        kind: r.kind || 'ling', owner_id: r.owner_id || '',
        model: r.model || '', status: r.status || 'ok',
        protocol: r.protocol || '', plugins: r.plugins || [],
        materials: r.materials || {}, body: r.body || {},
      }));
    },

    /* 全量：启动时一次拉齐 */
    async all() {
      const results = await Promise.allSettled([
        get('/api/state'), get('/api/ling'), get('/api/context'),
        get('/api/skills'), get('/api/mcp'), get('/api/plugins'),
        get('/api/model-requests'), get('/api/instructions'),
        get('/api/tasks?archived=1'),
      ]);
      const [settings, ling, context, skills, mcp, plugins, requests, instructions, works] = results;
      if (settings.status === 'fulfilled') sync.settings(settings.value);
      if (ling.status === 'fulfilled') sync.chat(ling.value);
      if (context.status === 'fulfilled') sync.context(context.value);
      if (skills.status === 'fulfilled') sync.skills(skills.value);
      if (mcp.status === 'fulfilled') sync.mcp(mcp.value);
      if (plugins.status === 'fulfilled') sync.plugins(plugins.value);
      if (requests.status === 'fulfilled') sync.modelRequests(requests.value);
      if (instructions.status === 'fulfilled') state.instructions = instructions.value?.text || '';
      if (works.status === 'fulfilled') sync.works(works.value);
      const failed = results.find(r => r.status === 'rejected');
      if (failed) state.connectionError = failed.reason?.message || '部分数据未能加载';
      return results;
    },

    async chatOnly() {
      const value = await get('/api/ling');
      sync.chat(value);
      return value;
    },
    async settingsOnly() {
      const value = await get('/api/state');
      sync.settings(value);
      return value;
    },
    async contextOnly() {
      const value = await get('/api/context');
      sync.context(value);
      return value;
    },
    async worksOnly() {
      const value = await get('/api/tasks?archived=1');
      sync.works(value);
      return value;
    },
    async skillsOnly() {
      const value = await get('/api/skills');
      sync.skills(value);
      return value;
    },
    async mcpOnly() {
      const value = await get('/api/mcp');
      sync.mcp(value);
      return value;
    },
    async pluginsOnly() {
      const value = await get('/api/plugins');
      sync.plugins(value);
      return value;
    },
    async requestsOnly() {
      const value = await get('/api/model-requests');
      sync.modelRequests(value);
      return value;
    },
    async instructionsOnly() {
      const value = await get('/api/instructions');
      state.instructions = value?.text || '';
      return value;
    },

    /* 详情：把工作详情并进列表，灵岛与详情页共用 */
    async taskDetail(id) {
      const value = await get('/api/tasks/' + encodeURIComponent(id));
      const mapped = mapTask(value);
      if (!mapped) return null;
      const index = state.works.findIndex(w => w.id === id);
      if (index >= 0) state.works[index] = { ...state.works[index], ...mapped, _detail: true };
      else state.works.push(mapped);
      return mapped;
    },
  };

  /* ---------------------------------------------------------------- 实时 */
  let source = null;
  let taskTimer = 0;
  let lingTimer = 0;
  let finalizePending = false;

  function finalizeLast() {
    const last = [...(state.items || [])].reverse().find(i => i.role === 'assistant' && !i.live);
    if (last) N.finalizeAssistant?.(last);
  }

  function scheduleLing(delay = 120) {
    clearTimeout(lingTimer);
    lingTimer = setTimeout(async () => {
      try {
        await sync.chatOnly();
        N.renderChat?.();
        if (finalizePending) { finalizePending = false; finalizeLast(); }
        N.renderRequests?.();
        N.renderQueue?.();
        N.updateStats?.();
        N.syncTargets?.();
        N.refreshIsland?.();
        N.renderStatus?.();
        if (state.view === 'work') N.renderWork?.();
      } catch (_) {}
    }, delay);
  }

  function scheduleWorks(delay = 260) {
    clearTimeout(taskTimer);
    taskTimer = setTimeout(async () => {
      try {
        await sync.worksOnly();
        N.renderWork?.();
        N.syncTargets?.();
        N.refreshIsland?.();
        if (state.islandMode === 'detail' || document.querySelector('#island[data-mode="detail"]')) {
          N.renderIsland?.();
        }
      } catch (_) {}
    }, delay);
  }

  function applyLive(event) {
    const before = (state.liveItems || []).map(i => i.key).join('|');
    state.live = event.live && typeof event.live === 'object' ? event.live : {};
    state.liveItems = liveItems(state.live);
    compose();
    const after = state.liveItems.map(i => i.key).join('|');
    if (before === after) {
      const item = state.liveItems.find(i => i.role === 'assistant');
      if (item) N.patchLive?.(item);
      else N.renderChat?.();
      if (state.live.context) { state.context = state.live.context; N.updateStats?.(); }
      return;
    }
    N.renderChat?.();
    N.refreshIsland?.();
  }

  function onEvent(event) {
    if (!event || typeof event !== 'object') return;
    const scope = event.scope;
    if (scope === 'ling') {
      if (state.sessionId && event.owner_id && event.owner_id !== state.sessionId) return;
      const type = event.type;
      if (type === 'delta' || type === 'thinking' || type === 'tool_start' || type === 'tool_result') {
        state.running = true;
        state.replying = true;
        applyLive(event);
        N.updateSend?.();
        N.renderStatus?.();
        return;
      }
      if (type === 'context') {
        if (event.data) { state.context = event.data; N.updateStats?.(); }
        return;
      }
      if (type === 'error') {
        const message = String(event.message || '模型运行失败');
        state.baseItems = [...(state.baseItems || []), { key: 'n:err:' + Date.now(), role: 'notice', text: message, at: N.nowHM() }];
        state.liveItems = [];
        compose();
        N.renderChat?.();
        N.notify?.(message, 'cinnabar');
        return;
      }
      if (type === 'done' || type === 'refresh') {
        state.liveItems = [];
        state.live = {};
        if (type === 'done') { state.running = false; state.replying = false; finalizePending = true; }
        N.renderChat?.();
        N.updateSend?.();
        scheduleLing(60);
        return;
      }
      return;
    }
    if (scope === 'task') {
      scheduleWorks(200);
    }
  }

  function connect() {
    if (source) return source;
    if (typeof EventSource === 'undefined') return null;
    source = new EventSource(BASE + '/api/stream');
    source.onopen = () => {
      state.connected = true;
      N.setConnection?.(true);
    };
    source.onmessage = e => {
      if (!e.data) return;
      let event = null;
      try { event = JSON.parse(e.data); } catch (_) { return; }
      try { onEvent(event); } catch (error) { console.error(error); }
    };
    source.onerror = () => {
      state.connected = false;
      N.setConnection?.(false);
      /* 浏览器会自动重连；这里只保证界面提示，不重建连接 */
    };
    return source;
  }

  /* ---------------------------------------------------------------- 对外 */
  const api = {
    get, post, put, del, upload,
    state: () => get('/api/state'),
    ling: () => get('/api/ling'),
    context: () => get('/api/context'),
    tasks: () => get('/api/tasks'),
    task: id => get('/api/tasks/' + encodeURIComponent(id)),
    skills: () => get('/api/skills'),
    mcp: () => get('/api/mcp'),
    plugins: () => get('/api/plugins'),
    modelRequests: query => get('/api/model-requests' + (query ? '?' + query : '')),
    instructions: () => get('/api/instructions'),

    input: (text, options = {}) => post('/api/ling/input', {
      text, request_id: options.requestId || N.uid('req'),
      images: options.images || [], attachments: options.attachments || [],
      ...(options.taskId ? { task_id: options.taskId } : {}),
    }),
    stop: () => post('/api/ling/stop'),
    resume: () => post('/api/ling/resume'),
    pauseAll: () => post('/api/ling/pause-all'),
    history: body => post('/api/ling/history', body),
    approval: (token, allow) => post('/api/approval', { token, allowed: !!allow }),
    reviewUnknown: token => post('/api/approval', { token, reviewed_unknown: true }),
    answer: (token, answers) => post('/api/answer', { token, answers }),

    taskCommand: (id, body) => post(`/api/tasks/${encodeURIComponent(id)}/commands`, body),
    taskLifecycle: (id, body) => post(`/api/tasks/${encodeURIComponent(id)}/lifecycle`, body),
    taskAnswer: (id, interactionId, body) => post(`/api/tasks/${encodeURIComponent(id)}/interactions/${encodeURIComponent(interactionId)}/answer`, body),

    saveContext: (section, body) => put('/api/context/' + section, body),
    saveInstructions: text => put('/api/instructions', { text }),

    select: body => post('/api/select', body),
    addProvider: body => post('/api/providers', body),
    updateProvider: (id, body) => put('/api/providers/' + encodeURIComponent(id), body),
    deleteProvider: id => del('/api/providers/' + encodeURIComponent(id)),
    settings: body => post('/api/settings', body),
    workspace: workspace => post('/api/workspace', { workspace }),

    saveSkill: body => post('/api/skills', body),
    deleteSkill: body => post('/api/skills/delete', body),
    saveMcp: body => post('/api/mcp/servers', body),
    mcpCommand: (name, action) => post(`/api/mcp/servers/${encodeURIComponent(name)}/${action}`),

    pluginScan: () => post('/api/plugins/scan'),
    pluginCommand: (id, operation) => post(`/api/plugins/${encodeURIComponent(id)}/commands`, { operation }),
    pluginSettings: (id, settings) => post(`/api/plugins/${encodeURIComponent(id)}/settings`, { settings }),
    pluginCall: (id, body) => post(`/api/plugins/${encodeURIComponent(id)}/call`, body),
    importPlugin: (blob, replace) => fetch(`/api/plugins/import${replace ? '?replace=1' : ''}`, {
      method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: blob,
    }).then(async res => {
      const text = await res.text();
      let value = null;
      try { value = text ? JSON.parse(text) : null; } catch (_) {}
      if (!res.ok && !value?.conflict) throw new Error(value?.error || '插件导入失败');
      return value;
    }),
  };

  N.api = api;
  N.sync = sync;
  N.connectStream = connect;
  N.composeItems = compose;
  N.mapItems = mapItems;
  N.mapTask = mapTask;
  N.clock = clock;
  N.liveItems = liveItems;
  N.onRuntimeEvent = onEvent;
})(window.N);
