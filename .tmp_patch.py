import sys

path = r'H:/文档/LingNexus/Nexus/nexus/web/js/work.js'
raw = open(path, 'rb').read().decode('utf-8')
lines = raw.split('\r\n')


def block(text):
    return text.replace('\r\n', '\n').split('\n')


def check(index, expect):
    got = lines[index].strip()
    if not got.startswith(expect):
        print('MISMATCH at', index + 1, repr(got), 'expected', repr(expect))
        sys.exit(1)


check(8, 'const GROUPS')
check(67, '}')

body = '''  const GROUPS = [
    { id: 'active', title: '进行中', hint: '独立推进，不需要你盯着', open: true, locked: true },
    { id: 'paused', title: '已挂起', hint: '需要时恢复', open: true },
    { id: 'ended', title: '已结束', hint: '过程与结果保留', open: true },
    { id: 'archived', title: '已归档', hint: '默认收起，不占主视图', open: false },
  ];
  const ACTIVE_STATES = ['running', 'queued', 'pending', 'pausing', 'waiting_user'];
  const pendingCount = w => Array.isArray(w.pending_interactions) ? w.pending_interactions.length : 0;

  function groupOf(w) {
    if (w.archived) return 'archived';
    if (ACTIVE_STATES.includes(w.state)) return 'active';
    if (w.state === 'paused') return 'paused';
    return 'ended';
  }
  function groupOpen(id) {
    const group = GROUPS.find(g => g.id === id);
    const value = (state.workGroups || {})[id];
    return value === undefined ? !!group?.open : !!value;
  }

  function workCard(w) {
    const child = !!w.parent_id;
    const archived = !!w.archived;
    const ended = !archived && ['completed', 'cancelled'].includes(w.state);
    const acts = archived
      ? `<span class="wacts">
          <button type="button" data-act="work-unarchive" data-id="${esc(w.id)}">恢复</button>
          <button type="button" class="del" data-act="work-delete" data-id="${esc(w.id)}">删除</button>
        </span>`
      : ended
        ? `<span class="wacts">
            <button type="button" data-act="work-archive" data-id="${esc(w.id)}">归档</button>
            <button type="button" class="more" data-act="work-more" data-id="${esc(w.id)}" aria-label="更多操作">${icon('more', 'i xs')}</button>
          </span>`
        : `<span class="wend">${pendingCount(w) ? `<span class="pending">${pendingCount(w)} 项待处理</span>` : ''}${icon('chev', 'i go sm')}</span>`;
    return `<div class="work-item" data-s data-state="${w.state}" data-archived="${archived ? 1 : 0}" data-work-open="${esc(w.id)}" role="button" tabindex="0">
      <span class="wico">${icon(child ? 'orbit' : w.state === 'completed' ? 'check' : w.state === 'waiting_user' ? 'alert' : 'work', 'i sm')}</span>
      <span>
        <span class="wtitle">${child ? '<span class="child">↳</span>' : ''}${esc(w.title)}<span class="pill state-${w.state}">${esc(N.stateLabel(w))}</span></span>
        <span class="wsub">${esc(N.progressText(w))}</span>
        <span class="wmeta">
          ${w.model ? `<span class="mono">${esc(w.model)}</span>` : ''}
          <span>${esc(w.finished || w.started || '')}</span>
          ${w.path ? `<span class="mono">${esc(N.shortPath(w.path))}</span>` : ''}
        </span>
      </span>
      ${acts}
    </div>`;
  }

  function renderWork() {
    const view = $('#view-work');
    const keep = view.scrollTop;
    const works = state.works || [];
    const sig = JSON.stringify([works, state.workGroups]);
    const entering = N.takeEnter?.('work');
    if (!entering && view._sig === sig && view.innerHTML) return;
    view._sig = sig;

    const buckets = { active: [], paused: [], ended: [], archived: [] };
    for (const w of works) buckets[groupOf(w)].push(w);
    const waiting = buckets.active.filter(w => w.state === 'waiting_user').length;

    const sections = GROUPS.map(group => {
      const list = buckets[group.id];
      if (!list.length) return '';
      if (group.locked) {
        return `<section class="work-group" data-s data-g="${group.id}">
          <div class="work-group-h static"><h3>${group.title}</h3><span>${list.length} 项 · ${group.hint}</span></div>
          <div class="work-group-body">${list.map(workCard).join('')}</div>
        </section>`;
      }
      const open = groupOpen(group.id);
      return `<section class="work-group" data-s data-g="${group.id}">
        <button class="work-group-h" type="button" data-act="group-toggle" data-g="${group.id}" aria-expanded="${open}">
          ${icon('chev', 'i chev')}<h3>${group.title}</h3><span>${list.length} 项 · ${group.hint}</span>
        </button>
        <div class="work-group-body"${open ? '' : ' hidden'}>${list.map(workCard).join('')}</div>
      </section>`;
    }).join('');

    const quiet = buckets.active.length ? '' : `<div class="work-quiet" data-s>${icon('orbit', 'i sm')}<span>现在没有正在进行的工作。在主交流里交代一件具体的事，它会出现在这里。</span></div>`;
    const content = (quiet + sections).trim() || `<div class="card work-empty" data-s><span class="orb-mini"></span><b>还没有工作</b><span>在主交流里交代一件具体的事，它会独立推进。</span></div>`;

    view.innerHTML = `<div class="page work-page">
      <header class="page-head" data-s>
        <div><h1>工作</h1><p>每项工作的过程、证据与结果都留在这里；补充和控制从主交流交代。</p></div>
        <div class="work-head-actions">
          <span class="pill">${buckets.active.length} 项进行 · ${waiting} 项待你处理</span>
          ${buckets.active.length ? `<button class="btn ghost sm" data-act="pause-all">${icon('pause', 'i xs')}全部挂起</button>` : ''}
        </div>
      </header>
      ${content}
      <p class="foot-note" data-s>工作记录只读。开始、挂起、恢复或取消通过主交流交代；归档与删除只整理这里的列表。</p>
    </div>`;
    view.scrollTop = keep;
    if (entering) N.stagger?.(view);
  }

  function toggleGroup(id, btn) {
    const section = btn.closest('.work-group');
    const body = section && $('.work-group-body', section);
    if (!body) return;
    const open = btn.getAttribute('aria-expanded') === 'true';
    const next = !open;
    btn.setAttribute('aria-expanded', String(next));
    state.workGroups = { ...(state.workGroups || {}), [id]: next };
    N.save();

    if (N.REDUCED) { body.hidden = !next; return; }
    const from = body.hidden ? 0 : body.offsetHeight;
    if (next) body.hidden = false;
    const to = next ? body.scrollHeight : 0;
    body.style.overflow = 'hidden';
    body.getAnimations().forEach(a => a.cancel());
    N.anim(body, [
      { height: from + 'px', opacity: next ? 0 : 1 },
      { height: to + 'px', opacity: next ? 1 : 0 },
    ], { duration: next ? 360 : 260, easing: N.SP.soft.e }).onfinish = () => {
      body.style.height = '';
      body.style.overflow = '';
      body.hidden = !next;
      if (next) N.stagger?.(body);
    };
  }

  async function archiveWork(id, restore) {
    const w = (state.works || []).find(x => x.id === id);
    if (!w) return;
    const view = $('#view-work');
    const node = view && $(`.work-item[data-work-open="${CSS.escape(id)}"]`, view);
    if (node && !N.REDUCED) {
      N.anim(node, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(-4px) scale(.98)' }],
        { duration: 200, easing: 'ease-in', fill: 'forwards' });
    }
    try {
      await N.api.taskCommand(id, {
        operation: restore ? 'unarchive' : 'archive',
        request_id: N.uid('cmd'),
        expected_version: w.control_version,
      });
      await N.sync.worksOnly();
      view._sig = '';
      renderWork();
      N.notify(restore ? `已恢复「${w.title}」` : `已归档「${w.title}」`, 'jade');
      N.syncDock?.(true);
    } catch (error) {
      node?.getAnimations().forEach(a => a.cancel());
      N.notify(error?.message || '操作失败', 'cinnabar', 4200);
      await N.sync.worksOnly().catch(() => {});
      view._sig = '';
      renderWork();
    }
  }

  async function deleteWork(id) {
    const w = (state.works || []).find(x => x.id === id);
    if (!w) return;
    const ok = await N.confirmEdit({
      title: `删除「${w.title}」？`,
      description: '会移除这项工作的记录与它的对话历史；已经写入的文件不会回滚。',
      submitLabel: '删除工作',
    });
    if (!ok) return;
    const view = $('#view-work');
    try {
      await N.api.del('/api/tasks/' + encodeURIComponent(id), {
        expected_version: w.control_version,
        confirm: true,
      });
      await N.sync.worksOnly();
      view._sig = '';
      renderWork();
      N.notify('工作已删除', 'jade');
      N.syncDock?.(true);
    } catch (error) {
      N.notify(error?.message || '删除失败', 'cinnabar', 4200);
    }
  }

  function moreWork(id, anchor) {
    const w = (state.works || []).find(x => x.id === id);
    if (!w) return;
    N.menu(anchor, [
      { label: '归档这项工作', icon: 'check', onPick: () => archiveWork(id, false) },
      { sep: true },
      { label: '删除这项工作', onPick: () => deleteWork(id) },
    ], { title: w.title });
  }'''

lines[8:68] = block(body)
open(path, 'wb').write('\r\n'.join(lines).encode('utf-8'))
print('ok', len(lines))
