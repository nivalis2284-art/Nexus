/* 项目显示名称与历史记录；文件夹选择交给本机系统窗口。 */
'use strict';

(function (N) {
  const { $, $$, state, esc, icon } = N;
  const dialog = $('#projects-dialog'), editor = $('#project-editor');
  const name = $('#project-name'), picker = $('#project-folder-pick'), save = $('#project-save');
  let selectedPath = '', autoName = '', editing = false, saving = false, picking = false, generation = 0;

  function renderProjectButton() {
    const project = state.project || { name: 'Work', path: state.workspace || '' };
    $$('[data-project-name]').forEach(label => {
      label.textContent = project.name;
      const button = label.closest('button');
      button.title = `${project.name}\n${project.path}`;
      button.setAttribute('aria-label', `当前项目：${project.name}，选择项目`);
    });
  }

  function renderProjects() {
    if (!dialog.open) return;
    const records = state.projects || [];
    const fallback = state.defaultProject;
    const rows = fallback && !records.some(row => row.path === fallback.path) ? [fallback, ...records] : records;
    $('#project-list').innerHTML = rows.map((row, index) => {
      const active = row.path === state.workspace;
      const saved = records.includes(row);
      return `<div class="project-row${active ? ' is-current' : ''}">
        <button class="project-select" type="button" data-project-index="${index}" data-act="project-select" aria-pressed="${active}" title="${esc(row.path)}">
          ${icon('folder')}<span class="project-info"><strong>${esc(row.name)}</strong><small>${esc(row.path)}</small></span>
          ${active ? icon('check', 'i sm') : ''}
        </button>
        ${saved ? `<div class="project-actions"><button class="ic-btn" type="button" data-act="project-rename" data-project-index="${index}" aria-label="重命名 ${esc(row.name)}" title="重命名">${icon('edit')}</button><button class="ic-btn danger" type="button" data-act="project-delete" data-project-index="${index}" aria-label="移除 ${esc(row.name)}" title="移除记录">${icon('trash')}</button></div>` : '<span class="project-default">默认</span>'}
      </div>`;
    }).join('');
    dialog._projects = rows;
  }

  async function openProjects() {
    $('#projects-error').textContent = '';
    N.openDialog(dialog);
    renderProjects();
    try { await N.sync.settingsOnly(); }
    catch (error) { $('#projects-error').textContent = error.message; }
  }

  function updateSave() {
    save.disabled = saving || picking || !name.value.trim() || !selectedPath;
    picker.disabled = picking || saving;
  }

  function openEditor(project = null) {
    generation++;
    editing = !!project;
    selectedPath = project?.path || '';
    autoName = '';
    name.value = project?.name || '';
    $('#project-editor-title').textContent = editing ? '重命名项目' : '创建项目';
    save.textContent = editing ? '保存名称' : '创建项目';
    $('#project-source').hidden = editing;
    $('#project-folder-label').textContent = '在此电脑上选择文件夹';
    $('#project-folder-label').title = '';
    $('#project-editor-error').textContent = '';
    picker.querySelector('span').textContent = '添加';
    updateSave();
    N.openDialog(editor);
    name.focus();
  }

  async function pickFolder() {
    if (picking) return;
    const version = generation;
    picking = true;
    $('#project-editor-error').textContent = '';
    picker.querySelector('span').textContent = '等待选择…';
    updateSave();
    try {
      const result = await N.api.chooseProjectFolder();
      if (!editor.open || editor._closing || version !== generation || !result.path) return;
      selectedPath = result.path;
      const folderName = selectedPath.split(/[\\/]/).filter(Boolean).pop() || selectedPath;
      if (!name.value.trim() || name.value === autoName) name.value = folderName;
      autoName = folderName;
      $('#project-folder-label').textContent = selectedPath;
      $('#project-folder-label').title = selectedPath;
    } catch (error) {
      if (editor.open && version === generation) $('#project-editor-error').textContent = error.status === 404
        ? '当前服务缺少目录选择接口。请关闭所有旧的 Nexus 后端，再从当前项目重新启动。'
        : error.message;
    } finally {
      picking = false;
      picker.querySelector('span').textContent = selectedPath ? '更换文件夹' : '添加';
      updateSave();
    }
  }

  async function change(body) {
    const previous = state.workspace;
    const value = await N.api.projectCommand(body);
    N.sync.settings(value);
    if (state.workspace !== previous) N.sync.contextOnly().then(() => N.updateStats?.())
      .catch(error => N.notify('项目已切换，上下文信息暂未刷新：' + error.message, 'cinnabar'));
  }

  $('#project-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (saving || picking || !selectedPath || !name.value.trim()) return;
    saving = true;
    updateSave();
    $('#project-editor-error').textContent = '';
    try {
      await change({ action: editing ? 'rename' : 'save', path: selectedPath, name: name.value.trim() });
      N.closeDialog(editor);
      if (!editing) N.closeDialog(dialog);
      N.notify(editing ? '项目名称已更新' : '已切换到项目', 'jade');
    } catch (error) {
      $('#project-editor-error').textContent = error.message;
    } finally {
      saving = false;
      updateSave();
    }
  });
  name.addEventListener('input', updateSave);
  picker.addEventListener('click', pickFolder);

  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const action = button.dataset.act;
    if (action === 'projects') return openProjects();
    if (action === 'project-new') return openEditor();
    if (!['project-select', 'project-rename', 'project-delete'].includes(action) || button.disabled) return;
    const project = dialog._projects?.[Number(button.dataset.projectIndex)];
    if (!project) return;
    if (action === 'project-rename') return openEditor(project);
    if (action === 'project-delete' && !await N.confirmEdit({
      title: `移除「${project.name}」？`,
      description: '仅移除项目记录，文件夹、文件和已有任务都会保留。移除当前项目后将切回 Work。',
      submitLabel: '移除记录',
    })) return;
    button.disabled = true;
    $('#projects-error').textContent = '';
    try {
      await change({ action: action === 'project-select' ? 'select' : 'delete', path: project.path });
      if (action === 'project-select') N.closeDialog(dialog);
      else N.notify('项目记录已移除', 'jade');
    } catch (error) {
      $('#projects-error').textContent = error.message;
    } finally { button.disabled = false; }
  });

  N.renderProjectButton = renderProjectButton;
  N.renderProjects = renderProjects;
})(window.N);
