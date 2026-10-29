/* ==========================================================================
   技能：个人 / 项目 / 内置，覆盖关系与编辑
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, state } = N;
  const SOURCE = { personal: '个人', project: '项目', builtin: '内置' };
  const COLOR = { personal: 'var(--jade)', project: '#4d8dff', builtin: '#8b8d94' };
  let editing = null;
  let saving = false;

  function effectiveList() {
    const list = state.skills;
    return list.map(s => ({
      ...s,
      effective: s.effective,
      shadowed: !s.error && !s.effective,
    }));
  }

  function renderSkills() {
    const view = $('#view-skills');
    const keep = view.scrollTop;
    const list = effectiveList();
    view.innerHTML = `<div class="page">
      <header class="page-head" data-s>
        <div><h1>技能</h1><p>项目技能会覆盖同名的个人技能；内置技能只读。</p></div>
        <div class="row" style="gap:8px"><button class="btn ghost" data-act="skills-refresh">${icon('refresh', 'i xs')}刷新</button><button class="btn solid" data-act="new-skill">${icon('plus', 'i xs')}新建技能</button></div>
      </header>
      <div class="skills-toolbar" data-s><span class="count">${list.length ? list.length + ' 个技能' : ''}</span><span class="skill-status" id="skills-status"></span></div>
      <div class="skill-grid">
        ${list.map((s, i) => `<div class="card lift skill" data-s data-state="${s.effective ? 'on' : 'off'}">
          <span class="glyph" style="background:${COLOR[s.source] || '#8b8d94'}">${esc((s.name || '?').slice(0, 1).toUpperCase())}</span>
          <h3 class="${/^[a-z0-9-]+$/.test(s.name) ? 'mono' : ''}">${esc(s.name)}</h3>
          <span class="src ${s.source}">${SOURCE[s.source] || s.source}</span>
          <p>${esc(s.description || '暂无摘要')}</p>
          <span class="meta-line ${s.shadowed ? 'over' : ''}">${s.error ? '读取失败 · 原文件未修改' : s.shadowed ? '被更高优先级的同名技能覆盖' : s.effective ? '当前生效' : '未生效'}</span>
          <footer><span class="pill">${s.read_only ? '只读' : '可编辑'}</span><div class="acts">
            ${s.error ? '' : `<button data-act="view-skill" data-i="${i}">${s.read_only ? '查看' : '编辑'}</button>`}
            ${s.read_only ? '' : `<button class="del" data-act="del-skill" data-i="${i}">删除</button>`}
          </div></footer>
        </div>`).join('')}
        <button class="card skill new" data-s data-act="new-skill"><span class="plus">${icon('plus')}</span><span>新建技能</span></button>
      </div>
      <p class="foot-note" data-s>技能按名称匹配；项目技能覆盖同名个人技能，内置技能始终只读。</p>
    </div>`;
    view.scrollTop = keep;
  }

  /* ---------------------------------------------------------------- 编辑器 */
  const dlg = $('#skill-dialog');
  function openEditor(skill, readonly) {
    editing = skill ? { source: skill.source, name: skill.name, path: skill.path } : null;
    $('#skill-dialog-title').textContent = readonly ? '查看技能' : skill ? '编辑技能' : '新建技能';
    $('#skill-name').value = skill?.name || '';
    $('#skill-description').value = skill?.description || '';
    $('#skill-body').value = skill?.body || '';
    $('#skill-source').value = skill?.source || 'personal';
    $('#skill-error').hidden = true;
    $('#skill-error').textContent = '';
    for (const id of ['skill-name', 'skill-description', 'skill-body', 'skill-source']) {
      const el = document.getElementById(id);
      el.readOnly = !!readonly;
      el.disabled = !!readonly && id === 'skill-source';
    }
    $('#skill-save').hidden = !!readonly;
    $('#skill-save').textContent = '保存技能';
    $('#skill-save').disabled = false;
    dlg.showModal();
    N.anim(dlg, [{ opacity: 0, transform: 'translateY(24px) scale(.92)' }, { opacity: 1, transform: 'none' }],
      { duration: N.SP.settle.d, easing: N.SP.settle.e });
    if (!readonly) $('#skill-name').focus();
  }
  function closeEditor() {
    if (saving) return;
    if (dlg.open) N.closeDialog(dlg);
    editing = null;
  }
  function error(text) {
    const el = $('#skill-error');
    el.textContent = text;
    el.hidden = !text;
  }
  async function submit(e) {
    e.preventDefault();
    if (saving || $('#skill-save').hidden) return;
    const name = $('#skill-name').value.trim();
    const desc = $('#skill-description').value.trim();
    const body = $('#skill-body').value;
    const source = editing ? editing.source : $('#skill-source').value;
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) { error('名称需为英文 slug，例如 writing-style。'); $('#skill-name').focus(); return; }
    if (!desc) { error('请填写摘要，摘要不能为空。'); $('#skill-description').focus(); return; }
    if (!['personal', 'project'].includes(source)) { error('请选择个人或项目来源。'); return; }
    error('');
    saving = true;
    $('#skill-save').disabled = true;
    $('#skill-save').textContent = '保存中…';
    try {
      await N.api.saveSkill({
        source, name, description: desc, body,
        path: editing ? editing.path : '',
      });
      await N.sync.skillsOnly();
      saving = false;
      N.closeDialog(dlg);
      editing = null;
      renderSkills();
      N.notify('技能已保存', 'jade');
    } catch (failure) {
      saving = false;
      $('#skill-save').disabled = false;
      $('#skill-save').textContent = '保存技能';
      error(failure?.message || '保存技能失败');
    }
  }
  async function remove(i) {
    const s = effectiveList()[i];
    if (!s || s.read_only) return;
    const ok = await N.confirmEdit({
      title: `删除技能「${s.name}」？`,
      description: s.source === 'project' ? '删除项目技能后，同名的个人技能会重新生效。' : '删除后无法在界面中恢复，需要重新创建。',
      submitLabel: '删除技能',
    });
    if (!ok) return;
    const card = $$('#view-skills .skill')[i];
    if (card && !N.REDUCED) {
      await N.anim(card, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(.94) translateY(-4px)' }],
        { duration: N.DUR.base, easing: 'ease-in', fill: 'forwards' }).finished;
    }
    try {
      await N.api.deleteSkill({ source: s.source, path: s.path });
      await N.sync.skillsOnly();
      renderSkills(); N.notify('技能已删除', 'jade');
    } catch (failure) {
      N.notify(failure?.message || '删除技能失败', 'cinnabar', 4200);
    }
  }
  async function refresh() {
    const st = $('#skills-status');
    if (st) { st.className = 'skill-status'; st.textContent = '正在载入技能…'; }
    try {
      await N.sync.skillsOnly();
      renderSkills();
      const next = $('#skills-status');
      if (next) { next.className = 'skill-status ok'; next.textContent = '已载入技能。'; }
    } catch (failure) {
      const next = $('#skills-status');
      if (next) { next.className = 'skill-status'; next.textContent = failure?.message || '载入失败'; }
    }
  }

  document.addEventListener('click', e => {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const act = t.dataset.act;
    if (act === 'new-skill') return openEditor(null, false);
    if (act === 'view-skill') { const s = effectiveList()[+t.dataset.i]; return openEditor(s, !!s.read_only); }
    if (act === 'del-skill') return remove(+t.dataset.i);
    if (act === 'skills-refresh') return refresh();
  });
  $('#skill-form').addEventListener('submit', submit);
  $('#skill-cancel').addEventListener('click', closeEditor);
  $('#skill-dialog').addEventListener('cancel', e => { e.preventDefault(); closeEditor(); });
  $('#skill-dialog').addEventListener('close', () => { if (!saving) editing = null; });

  N.renderSkills = renderSkills;
})(window.N);
