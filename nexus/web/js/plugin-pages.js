/* Host-owned rendering for the plugin UI v1 data protocol. */
'use strict';
(function (N) {
  const el = (tag, key, cls = '', text = null, children = [], attrs = {}) =>
    ({ tag, key, cls, text, children, attrs });
  const stringify = value => value == null ? '' : String(value);
  let active = null;

  class Page {
    constructor(root, pluginId, pageId) {
      this.root = root; this.pluginId = pluginId; this.pageId = pageId;
      this.alive = true; this.busy = false; this.context = {}; this.view = null;
      this.fields = new Map(); this.actions = new Map(); this.dialog = null;
      this.controller = null; this.error = ''; this.trigger = null;
      root.classList.add('hui-page');
      this.errorNode = document.createElement('p');
      this.errorNode.className = 'hui-notice hui-error'; this.errorNode.hidden = true;
      this.errorNode.setAttribute('role', 'alert');
      this.status = document.createElement('p'); this.status.className = 'help hui-loading';
      this.status.setAttribute('role', 'status'); this.status.setAttribute('aria-live', 'polite');
      this.body = document.createElement('div'); this.body.className = 'hui-components';
      this.retry = document.createElement('button'); this.retry.className = 'btn ghost sm';
      this.retry.textContent = '重新读取'; this.retry.hidden = true;
      this.retry.onclick = () => this.request('open');
      root.append(this.errorNode, this.status, this.retry, this.body);
      this.click = e => this.onClick(e);
      root.addEventListener('click', this.click);
      this.request('open');
    }

    dispose() {
      this.alive = false; this.controller?.abort();
      this.root.removeEventListener('click', this.click);
      if (this.dialog) { this.dialog.close(); this.dialog.remove(); this.dialog = null; }
      this.root.getAnimations({ subtree: true }).forEach(a => a.cancel());
    }

    setBusy(value) {
      this.busy = value;
      this.root.setAttribute('aria-busy', String(value));
      this.status.textContent = value ? (this.view ? '正在处理…' : '正在读取…') : '';
      if (this.dialog) {
        this.dialog.setAttribute('aria-busy', String(value));
        this.dialog.querySelector('.hui-dialog-status').textContent = value ? '正在处理…' : '';
      }
      for (const scope of [this.root, this.dialog].filter(Boolean)) {
        for (const control of scope.querySelectorAll('button,input,textarea,select')) {
          control.disabled = value || control.dataset.huiDisabled === 'true';
        }
      }
    }

    showError(message) {
      this.error = message;
      this.errorNode.textContent = message; this.errorNode.hidden = !message;
      if (this.dialog) {
        const node = this.dialog.querySelector('.hui-dialog-error');
        node.textContent = message; node.hidden = !message;
      }
      this.retry.hidden = !message || !!this.view;
    }

    collect() {
      const values = {};
      for (const [name, field] of this.fields) {
        const input = field.element;
        if (!input.reportValidity()) return null;
        if (field.node.control === 'switch') values[name] = input.checked;
        else if (field.node.control === 'select') values[name] = field.node.options?.[Number(input.value)]?.value ?? null;
        else if (field.node.control === 'number') values[name] = input.value === '' ? null : Number(input.value);
        else values[name] = input.value;
      }
      return values;
    }

    async onClick(event) {
      const button = event.target.closest('[data-hui-action]');
      if (!button || this.busy || button.disabled || button.closest('[data-hui-leaving]')) return;
      event.preventDefault();
      const action = this.actions.get(button.dataset.huiAction);
      if (!action) return;
      if (action.action === '$close') { this.dismissDialog(); return; }
      this.trigger = button;
      await this.request('action', action);
    }

    async request(event, action = {}) {
      if (!this.alive || this.busy) return;
      const values = event === 'open' ? {} : this.collect();
      if (values === null) return;
      const focus = document.activeElement;
      const selection = focus && typeof focus.selectionStart === 'number' ? [focus.selectionStart, focus.selectionEnd] : null;
      this.setBusy(true); this.showError('');
      try {
        this.controller = new AbortController();
        if (action.confirm && !await N.confirmEdit({ ...action.confirm, signal: this.controller.signal })) return;
        if (!this.alive) return;
        const response = await fetch(`/api/plugins/${encodeURIComponent(this.pluginId)}/pages/${encodeURIComponent(this.pageId)}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: this.controller.signal,
          body: JSON.stringify({ event, action: action.action, arguments: action.arguments || {},
            values, context: this.context, request_id: N.uid() }),
        });
        const view = await response.json();
        if (!response.ok) throw Error(view.error || '插件页面操作失败');
        if (!this.alive) return;
        if (view.ui_version !== 1) throw Error('不支持的插件页面协议版本');
        const initial = !this.view;
        this.view = view; this.context = view.context || {};
        this.render(initial);
      } catch (error) {
        if (this.alive && error.name !== 'AbortError') this.showError(error.message || String(error));
      } finally {
        if (this.alive) {
          this.setBusy(false);
          if (this.focusDialog && this.dialog) {
            this.focusDialog = false;
            (this.dialog.querySelector('input:not(:disabled),textarea:not(:disabled),select:not(:disabled)') || this.dialog.querySelector('button:not(:disabled)'))?.focus();
          } else if (focus?.isConnected && !focus.closest('[data-hui-closing]') && (!this.dialog || this.dialog.contains(focus))) {
            focus.focus({ preventScroll: true });
            if (selection) focus.setSelectionRange(...selection);
          }
        }
      }
    }

    render(initial = false) {
      this.fields.clear(); this.actions.clear();
      const nodes = this.view.components.map(node => this.component(node));
      if (this.view.notice) nodes.unshift(el('p', '$notice', `hui-notice hui-${this.view.notice.tone || 'success'}`, this.view.notice.text, [], { role: 'status' }));
      this.patchChildren(this.body, nodes);
      if (initial) N.cascade?.(this.body);
      const spec = this.view.dialog;
      if (!spec) { this.dismissDialog(); return; }
      if (this.dialog && this.dialog.dataset.dialogId !== spec.id) this.dismissDialog(false);
      let fresh = false;
      if (!this.dialog) {
        fresh = true;
        const d = document.createElement('dialog'); d.className = 'sheet hui-dialog';
        d.dataset.dialogId = spec.id;
        const titleId = 'hui-dialog-' + N.uid();
        d.setAttribute('aria-labelledby', titleId);
        const body = document.createElement('div'); body.className = 'sheet-body';
        const header = document.createElement('header'); header.className = 'sheet-head';
        const heading = document.createElement('div');
        const title = document.createElement('h2'); title.id = titleId;
        const note = document.createElement('p'); note.className = 'sheet-note';
        heading.append(title, note);
        const close = document.createElement('button'); close.className = 'ic-btn';
        close.type = 'button'; close.textContent = '×'; close.setAttribute('aria-label', '关闭');
        close.onclick = () => { if (!this.busy) this.dismissDialog(); };
        header.append(heading, close);
        const error = document.createElement('p'); error.className = 'sheet-error hui-dialog-error';
        error.setAttribute('role', 'alert'); error.hidden = true;
        const status = document.createElement('p'); status.className = 'help hui-dialog-status'; status.setAttribute('role', 'status');
        const content = document.createElement('div'); content.className = 'hui-components';
        body.append(header, error, status, content); d.append(body);
        d.addEventListener('click', this.click);
        d.addEventListener('click', e => { if (e.target === d && !this.busy) this.dismissDialog(); });
        d.addEventListener('cancel', e => { e.preventDefault(); if (!this.busy) this.dismissDialog(); });
        this.dialog = d; this.dialogOpener = this.trigger; document.body.append(d);
      }
      this.dialog.querySelector('h2').textContent = spec.title;
      this.dialog.querySelector('.sheet-note').textContent = spec.description || '';
      this.patchChildren(this.dialog.querySelector('.hui-components'), (spec.components || []).map(n => this.component(n)));
      if (fresh) {
        N.openDialog(this.dialog);
        this.focusDialog = true;
      }
      this.setBusy(this.busy);
    }

    dismissDialog(clear = true) {
      const d = this.dialog;
      if (!d) return;
      const opener = this.dialogOpener;
      this.dialog = null;
      d.dataset.huiClosing = 'true';
      if (clear && this.view) this.view.dialog = null;
      for (const [name, field] of this.fields) if (d.contains(field.element)) this.fields.delete(name);
      d.addEventListener('close', () => {
        d.remove();
        if (this.alive && !this.dialog && opener?.isConnected) opener.focus({ preventScroll: true });
      }, { once: true });
      N.closeDialog(d);
    }

    component(n) {
      const key = n.id;
      let v;
      const children = () => (n.children || []).map(c => this.component(c));
      if (n.type === 'section') {
        const content = [];
        if (n.title) content.push(el('h3', key + ':title', '', n.title));
        if (n.description) content.push(el('p', key + ':desc', 'help', n.description));
        content.push(el('div', key + ':body', 'hui-components', null, children()));
        v = el('section', key, 'hui-section', null, content);
      } else if (n.type === 'row' || n.type === 'column') {
        v = el('div', key, 'hui-' + n.type, null, children());
      } else if (n.type === 'text' || n.type === 'badge' || n.type === 'code') {
        v = el(n.type === 'code' ? 'pre' : n.type === 'badge' ? 'span' : 'p', key,
          `hui-${n.type} hui-${n.tone || 'default'}`, n.text);
      } else if (n.type === 'button') {
        this.actions.set(key, n);
        v = el('button', key, `btn ${n.variant === 'primary' ? 'solid' : 'ghost'}${n.variant === 'danger' ? ' danger' : ''}`, n.label, [],
          { type: 'button', 'data-hui-action': key, 'data-hui-disabled': String(!!n.disabled) });
        v.disabled = this.busy || n.disabled;
      } else if (n.type === 'field') {
        const control = n.control || 'text', inputId = 'hui-field-' + this.pluginId + '-' + key;
        const input = el(control === 'textarea' ? 'textarea' : control === 'select' ? 'select' : 'input', key + ':input', '', null, [],
            { id: inputId, name: n.name, placeholder: n.placeholder || '', 'data-hui-disabled': String(!!n.disabled || !!n.readonly && ['select', 'switch'].includes(control)),
            ...(control === 'switch' ? { type: 'checkbox' } : control === 'number' ? { type: 'number', step: n.step ?? 'any' } : control === 'text' ? { type: 'text' } : {}) });
        for (const attr of ['min', 'max', 'step']) if (n[attr] !== undefined) input.attrs[attr] = n[attr];
        input.field = n;
        if (control === 'select') input.children = (n.options || []).map((o, i) => el('option', key + ':o:' + i, '', o.label, [], { value: String(i) }));
        const error = this.view.errors?.[n.name] || '';
        input.attrs['aria-invalid'] = String(!!error);
        input.attrs['aria-describedby'] = key + ':help ' + key + ':error';
        const content = [el('label', key + ':label', control === 'switch' ? 'check' : '', null,
          control === 'switch' ? [input, el('span', key + ':caption', '', n.label)] : [el('span', key + ':caption', '', n.label), input])];
        content.push(el('p', key + ':help', 'help', n.help || '', [], { id: key + ':help' }));
        content.push(el('p', key + ':error', 'sheet-error', error, [], { id: key + ':error', role: 'alert' }));
        v = el('div', key, 'field hui-field', null, content);
      } else if (n.type === 'list') {
        v = el('div', key, 'hui-list', null, (n.items || []).map(item => {
          const content = [el('b', item.id + ':title', '', item.title)];
          if (item.text) content.push(el('p', item.id + ':text', 'hui-text', item.text));
          if (item.meta) content.push(el('p', item.id + ':meta', 'help', item.meta));
          content.push(el('div', item.id + ':actions', 'hui-row', null, (item.actions || []).map(b => this.component(b))));
          return { ...el('article', item.id, 'hui-item', null, content), animate: true };
        }));
        if (!v.children.length) v.children.push(el('p', key + ':empty', 'help', n.empty || '暂无内容。'));
      } else if (n.type === 'table') {
        const actions = (n.rows || []).some(r => r.actions?.length);
        const headings = n.columns.map(c => el('th', key + ':h:' + c.key, '', c.label, [], { scope: 'col' }));
        if (actions) headings.push(el('th', key + ':actions', '', '操作', [], { scope: 'col' }));
        const rows = (n.rows || []).map(row => {
          const cells = n.columns.map(c => el('td', row.id + ':' + c.key, '', stringify(row.cells[c.key])));
          if (actions) cells.push(el('td', row.id + ':actions', '', null, (row.actions || []).map(b => this.component(b))));
          return { ...el('tr', row.id, '', null, cells), animate: true };
        });
        if (!rows.length) rows.push(el('tr', key + ':empty', '', null, [el('td', key + ':empty-cell', 'help', n.empty || '暂无内容。', [], { colspan: n.columns.length + Number(actions) })]));
        v = el('div', key, 'hui-table-wrap', null, [el('table', key + ':table', 'hui-table', null,
          [el('thead', key + ':head', '', null, [el('tr', key + ':head-row', '', null, headings)]), el('tbody', key + ':body', '', null, rows)])]);
      } else if (n.type === 'pagination') {
        const button = (suffix, label, page, disabled) => this.component({ id: key + ':' + suffix, type: 'button', label, action: n.action,
          arguments: { ...n.arguments, page }, disabled });
        v = el('nav', key, 'hui-row hui-pagination', null, [button('prev', '上一页', n.page - 1, n.page <= 1),
          el('span', key + ':label', 'help', `${n.page} / ${n.total_pages}`), button('next', '下一页', n.page + 1, n.page >= n.total_pages)], { 'aria-label': '分页' });
      } else throw Error('未知插件页面组件：' + n.type);
      v.attrs['data-hui-component'] = key;
      return v;
    }

    patchChildren(parent, specs) {
      const existing = new Map([...parent.children].filter(n => !n.dataset.huiLeaving).map(n => [n._huiKey, n]));
      let cursor = parent.firstElementChild;
      for (const spec of specs) {
        let node = existing.get(spec.key);
        if (node && node.localName !== spec.tag) { node.remove(); node = null; }
        const fresh = !node;
        if (fresh) node = document.createElement(spec.tag);
        existing.delete(spec.key); node._huiKey = spec.key; node._huiAnimate = !!spec.animate;
        if (node !== cursor) parent.insertBefore(node, cursor);
        cursor = node.nextElementSibling;
        node.className = spec.cls;
        for (const a of [...node.attributes]) if (a.name !== 'class' && !Object.hasOwn(spec.attrs, a.name)) node.removeAttribute(a.name);
        for (const [name, value] of Object.entries(spec.attrs)) if (node.getAttribute(name) !== String(value)) node.setAttribute(name, value);
        const previousChoice = node._huiField?.control === 'select' ? node._huiField.options?.[Number(node.value)] : null;
        if (spec.text !== null) { if (node.textContent !== spec.text) node.textContent = spec.text; }
        else this.patchChildren(node, spec.children);
        if (spec.tag === 'button') node.disabled = !!spec.disabled;
        if (spec.field) {
          const f = spec.field;
          node.disabled = this.busy || node.dataset.huiDisabled === 'true'; node.readOnly = !!f.readonly;
          node.required = !!f.required;
          if (Object.hasOwn(f, 'value')) {
            if (f.control === 'switch') node.checked = f.value;
            else {
              const value = f.control === 'select' ? String((f.options || []).findIndex(o => Object.is(o.value, f.value))) : stringify(f.value);
              if (node.value !== value) node.value = value;
            }
          }
          else if (f.control === 'select' && previousChoice) node.value = String((f.options || []).findIndex(o => Object.is(o.value, previousChoice.value)));
          node._huiField = f;
          this.fields.set(f.name, { node: f, element: node });
        }
        if (fresh && spec.animate && !N.REDUCED) N.anim(node, [{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }], { duration: N.DUR.base, easing: N.SP.soft.e });
      }
      for (const node of existing.values()) {
        if (node._huiAnimate && !N.REDUCED) {
          node.dataset.huiLeaving = 'true'; node.inert = true;
          node.getAnimations().forEach(a => a.cancel());
          const animation = N.anim(node, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(-4px)' }], { duration: N.DUR.micro, easing: N.EASE_OUT });
          animation.onfinish = () => node.remove();
        } else node.remove();
      }
    }
  }

  N.hostPages = {
    mount(root, pluginId, pageId) {
      if (active?.root === root && active.pluginId === pluginId && active.pageId === pageId) return;
      this.dispose(); active = new Page(root, pluginId, pageId);
    },
    dispose() { active?.dispose(); active = null; },
  };
})(window.N);
