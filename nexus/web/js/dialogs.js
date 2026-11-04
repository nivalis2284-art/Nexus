/* ==========================================================================
   对话框、菜单、文件预览
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, anim } = N;

  /* ---------------------------------------------------------------- 通用菜单 */
  const menu = $('#menu');
  let menuAnchor = null;
  function closeMenu(instant) {
    if (!menuAnchor && !instant) return;
    menuAnchor?.setAttribute('aria-expanded', 'false');
    menuAnchor = null;
    if (instant || N.REDUCED) { menu._surfaceMotion?.cancel(); N.cancelMotion(menu); menu.hidden = true; return; }
    const current = getComputedStyle(menu);
    const from = { opacity: current.opacity, transform: current.transform };
    menu._surfaceMotion?.cancel();
    N.exitItems([...menu.children], menu);
    const motion = anim(menu, [from, { opacity: 0, transform: 'translateY(8px) scale(.98)' }],
      { duration: N.DUR.exit, easing: N.EASE_EXIT, fill: 'forwards' });
    menu._surfaceMotion = motion;
    motion.onfinish = () => { if (!menuAnchor && menu._surfaceMotion === motion) { menu.hidden = true; N.cancelMotion(menu); motion.cancel(); } };
  }
  function openMenu(anchor, items, opts = {}) {
    if (menuAnchor === anchor) { closeMenu(); return; }
    const previous = menu.hidden ? null : { opacity: getComputedStyle(menu).opacity, transform: getComputedStyle(menu).transform };
    closeMenu(true);
    menuAnchor = anchor;
    anchor.setAttribute('aria-expanded', 'true');
    const rows = items.map((it, i) => {
      if (it.group) return `<p class="m-group">${esc(it.group)}</p>`;
      if (it.sep) return '<div class="m-sep"></div>';
      return `<button role="menuitemradio" aria-checked="${!!it.on}" data-i="${i}">
        ${it.icon ? icon(it.icon, 'i sm') : it.dot ? '<i class="m-dot"></i>' : '<svg class="i chk"><use href="#i-check"/></svg>'}
        ${it.html || esc(it.label)}${it.sub ? `<small class="m-sub">${esc(it.sub)}</small>` : ''}
      </button>`;
    }).join('');
    menu.innerHTML = (opts.title ? `<p class="m-title">${esc(opts.title)}</p>` : '') + rows;
    menu.hidden = false;
    const r = anchor.getBoundingClientRect();
    const mw = menu.offsetWidth, mh = menu.offsetHeight;
    const left = Math.max(8, Math.min(r.left, innerWidth - mw - 12));
    let top = r.top - mh - 8;
    if (top < 8) top = Math.min(innerHeight - mh - 8, r.bottom + 8);
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
    menu.style.setProperty('--ox', (r.left + r.width / 2 - left) + 'px');
    menu.style.setProperty('--oy', (top > r.top ? '0%' : '100%') );
    menu._surfaceMotion = anim(menu, [previous || { opacity: 1, transform: 'translateY(12px) scale(.98)' }, { opacity: 1, transform: 'none' }],
      { duration: N.DUR.content, easing: N.EASE_ENTER });
    N.enterItems([...menu.children], menu, { profile: 'float' });
    menu.onclick = e => {
      const b = e.target.closest('button[data-i]');
      if (!b) return;
      const item = items[+b.dataset.i];
      closeMenu();
      item.onPick?.();
    };
  }
  document.addEventListener('pointerdown', e => {
    if (menuAnchor && !menu.contains(e.target) && !menuAnchor.contains(e.target)) closeMenu();
  });

  /* ---------------------------------------------------------------- 通用编辑 */
  const dlg = $('#editor'), form = $('#editor-form'), area = $('#editor-text');
  let resolveEdit = null, saving = false;
  function edit({ title, text = '', description = '保存后在后续请求中生效。', submitLabel = '保存修改', confirm = false }) {
    if (resolveEdit) return Promise.resolve(null);
    $('#editor-title').textContent = title;
    $('#editor-desc').textContent = description;
    area.value = text;
    area.disabled = confirm;
    area.hidden = confirm;
    area.required = !confirm;
    $('#editor-error').textContent = '';
    $('#editor-save').textContent = submitLabel;
    dlg.dataset.size = confirm ? 'small' : 'medium';
    openDialog(dlg);
    (confirm ? $('.btn.ghost', dlg) : area).focus();
    return new Promise(resolve => {
      resolveEdit = resolve;
      form.onsubmit = async e => {
        e.preventDefault();
        if (saving) return;
        const value = confirm ? true : area.value;
        if (!confirm && !String(value).trim()) { area.focus(); return; }
        saving = true;
        $('#editor-save').disabled = true;
        const save = edit._save;
        try {
          if (save) await save(value);
          finish(value);
        } catch (error) {
          $('#editor-error').textContent = error.message || String(error);
        } finally {
          saving = false;
          $('#editor-save').disabled = false;
        }
      };
    });
  }
  function finish(value = null) {
    const r = resolveEdit;
    resolveEdit = null;
    edit._save = null;
    closeDialog(dlg);
    r?.(value);
  }
  $('#editor-form').addEventListener('click', e => {
    if (e.target.value === 'cancel') { e.preventDefault(); if (!saving) finish(null); }
  });
  dlg.addEventListener('cancel', e => { e.preventDefault(); if (!saving) finish(null); });
  dlg.addEventListener('close', () => dlg.getAnimations().forEach(a => a.cancel()));

  function ask(options, save) {
    edit._save = save;
    return edit(options);
  }
  function confirmEdit({ title, description, submitLabel = '删除', signal }) {
    if (signal?.aborted) return Promise.resolve(false);
    edit._save = null;
    const promise = edit({ title, text: '', description, submitLabel, confirm: true });
    const pending = resolveEdit;
    const abort = () => { if (pending && resolveEdit === pending) finish(null); };
    signal?.addEventListener('abort', abort, { once: true });
    return promise.then(Boolean).finally(() => signal?.removeEventListener('abort', abort));
  }

  /* ---------------------------------------------------------------- 文件预览 */
  const fp = $('#file-preview'), fpBody = $('#fp-body');
  async function openFile(path, line = 0) {
    const label = String(path).split(/[\\/]/).pop() || '文件预览';
    $('#fp-title').textContent = label;
    $('#fp-meta').textContent = path + (line ? ` · 第 ${line} 行` : '');
    fpBody.replaceChildren();
    const loading = document.createElement('p');
    loading.className = 'fp-loading';
    loading.textContent = '正在读取…';
    fpBody.appendChild(loading);
    openDialog(fp);

    let text = '';
    try {
      const query = new URLSearchParams({
        path: String(path).replace('file://', ''),
        workspace: N.state.workspace || '',
        line: String(line || 1),
      });
      const value = await N.api.get('/api/workspace-file?' + query.toString());
      text = value?.text || '';
      $('#fp-meta').textContent = (value?.path || path) + (line ? ` · 第 ${line} 行` : '') + (value?.truncated ? ' · 已截断' : '');
    } catch (error) {
      text = `无法读取这个文件。\n\n${error?.message || error}`;
    }

    const lines = text.split('\n');
    if (line > lines.length) line = lines.length;
    fpBody.replaceChildren();
    const pre = document.createElement('pre');
    pre.className = 'fp-text';
    lines.forEach((content, index) => {
      const row = document.createElement('span');
      row.className = 'fp-line' + (line && index + 1 >= line && index + 1 <= line + 2 ? ' target' : '');
      row.dataset.line = String(index + 1);
      row.textContent = content || ' ';
      pre.appendChild(row);
    });
    fpBody.appendChild(pre);
    N.cascade?.(fpBody);
    if (line) setTimeout(() => $('.fp-line.target', fpBody)?.scrollIntoView({ block: 'center' }), 40);
  }

  function openImage(url, name = '图片') {
    $('#fp-title').textContent = name;
    $('#fp-meta').textContent = '图片预览';
    fpBody.replaceChildren();
    const img = document.createElement('img');
    img.className = 'fp-img';
    img.src = url;
    img.alt = name;
    fpBody.appendChild(img);
    openDialog(fp);
  }

  /* ---------------------------------------------------------------- 关闭委托 */
  function openDialog(d) {
    if (!d || d.open && !d._closing) return;
    const from = d.open ? { opacity: getComputedStyle(d).opacity, transform: getComputedStyle(d).transform }
      : { opacity: 1, transform: 'translateY(24px) scale(.97)' };
    d._surfaceMotion?.cancel();
    d._closing = false;
    d.classList.remove('is-closing');
    if (!d.open) d.showModal();
    d._surfaceMotion = anim(d, [from, { opacity: 1, transform: 'none' }],
      { duration: N.DUR.enter, easing: N.EASE_ENTER });
    const body = $('.sheet-body', d) || $('form', d) || d;
    N.enterItems([...body.children], d, { profile: 'float' });
  }

  function closeDialog(d) {
    if (!d || !d.open || d._closing) return;
    if (N.REDUCED) { d._surfaceMotion?.cancel(); N.cancelMotion(d); d.close(); return; }
    d._closing = true;
    d.classList.add('is-closing');
    const current = getComputedStyle(d);
    const from = { opacity: current.opacity, transform: current.transform };
    d._surfaceMotion?.cancel();
    const body = $('.sheet-body', d) || $('form', d) || d;
    N.exitItems([...body.children], d);
    const motion = anim(d, [from, { opacity: 0, transform: 'translateY(8px) scale(.98)' }],
      { duration: N.DUR.exit, easing: N.EASE_EXIT, fill: 'forwards' });
    d._surfaceMotion = motion;
    motion.onfinish = () => {
      if (d._surfaceMotion !== motion) return;
      d._closing = false;
      d.classList.remove('is-closing');
      d.close();
      N.cancelMotion(d);
      motion.cancel();
    };
  }

  document.addEventListener('click', e => {
    const t = e.target.closest('[data-close]');
    if (!t) return;
    closeDialog(document.getElementById(t.dataset.close));
  });
  $$('dialog').forEach(d => {
    d.addEventListener('click', e => {
      if (e.target !== d) return;
      if (d === dlg) { if (!saving) finish(null); }
      else closeDialog(d);
    });
    d.addEventListener('cancel', e => {
      if (d.id === 'editor') return;
      e.preventDefault();
      closeDialog(d);
    });
  });

  N.menu = openMenu; N.closeMenu = closeMenu;
  N.ask = ask; N.edit = edit; N.confirmEdit = confirmEdit;
  N.openFile = openFile; N.openImage = openImage; N.openDialog = openDialog; N.closeDialog = closeDialog;
})(window.N);
