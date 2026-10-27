"use strict";

// Markdown, local file previews and tool steps for the transcript.
// marked, DOMPurify and highlight.js are loaded as globals before this file.

function presentationEscape(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function presentationTruncate(value, size) {
  const text = String(value === null || value === undefined ? "" : value);
  return text.length > size ? text.slice(0, size) + "…" : text;
}

function presentationWorkspace(workspace) {
  return String(workspace || state.workspacePath || "");
}

function presentationFileUrl(path, workspace, line) {
  const params = ["path=" + encodeURIComponent(String(path))];
  if (workspace) params.push("workspace=" + encodeURIComponent(String(workspace)));
  if (line) params.push("line=" + encodeURIComponent(String(line)));
  return "/api/workspace-file?" + params.join("&");
}

function presentationParseFileReference(value) {
  let raw = String(value || "").trim();
  if (!raw || raw.charAt(0) === "#" || raw.indexOf("//") === 0) return null;
  if (raw.slice(0, 7).toLowerCase() === "file://") {
    raw = raw.slice(7);
    if (/^\/[a-zA-Z]:/.test(raw)) raw = raw.slice(1);
  }
  let line = 0;
  let endLine = 0;
  const hash = raw.match(/#L(\d+)(?:-L?(\d+))?$/i);
  if (hash) {
    line = Number(hash[1]);
    endLine = Number(hash[2] || hash[1]);
    raw = raw.slice(0, hash.index);
  } else {
    const colon = raw.match(/:(\d+)(?::\d+)?$/);
    if (colon && colon.index > 1) {
      line = Number(colon[1]);
      endLine = line;
      raw = raw.slice(0, colon.index);
    }
  }
  // Strip the line suffix first: "sample.py:2" would otherwise look like a
  // URI with the scheme "sample.py". Only drive letters may keep a colon.
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw) && !/^[a-zA-Z]:[\\/]/.test(raw)) {
    return null;
  }
  return raw ? { path: raw, line, endLine } : null;
}

function presentationExternal(link) {
  link.setAttribute("target", "_blank");
  link.setAttribute("rel", "noopener noreferrer");
}

function presentationMarkdownRenderer() {
  const renderer = new marked.Renderer();
  renderer.link = function (token) {
    const href = String(token.href || "");
    const text = token.tokens
      ? this.parser.parseInline(token.tokens)
      : presentationEscape(token.text || href);
    const title = token.title ? ' title="' + presentationEscape(token.title) + '"' : "";
    if (/^(https?:|mailto:|tel:)/i.test(href) || href.indexOf("//") === 0) {
      return '<a href="' + presentationEscape(href) + '" target="_blank" ' +
        'rel="noopener noreferrer"' + title + ">" + text + "</a>";
    }
    if (href.charAt(0) === "#") {
      return '<a href="' + presentationEscape(href) + '"' + title + ">" + text + "</a>";
    }
    if (/^(javascript|vbscript|data):/i.test(href)) return text;
    const local = presentationParseFileReference(href);
    if (!local) return text;
    return '<a href="#" class="file-link" data-file="' + presentationEscape(local.path) +
      '" data-line="' + local.line + '" data-end-line="' + local.endLine + '"' + title +
      ">" + text + "</a>";
  };
  renderer.image = function (token) {
    const href = String(token.href || "");
    const alt = presentationEscape(token.text || "");
    const title = token.title ? ' title="' + presentationEscape(token.title) + '"' : "";
    if (/^(https?:)?\/\//i.test(href) || /^data:image\//i.test(href)) {
      return '<img src="' + presentationEscape(href) + '" alt="' + alt + '"' + title + ">";
    }
    const local = presentationParseFileReference(href);
    if (!local) return alt;
    return '<img data-file="' + presentationEscape(local.path) + '" alt="' + alt + '"' +
      ' loading="lazy"' + title + ">";
  };
  return renderer;
}

function presentationDecorateCode(root) {
  root.querySelectorAll("pre").forEach((pre) => {
    const code = pre.querySelector("code");
    if (!code) return;
    const language = Array.from(code.classList)
      .find((name) => name.indexOf("language-") === 0);
    const name = language ? language.slice("language-".length) : "";
    if (name && hljs.getLanguage(name)) hljs.highlightElement(code);
    const block = el("div", "code-block");
    const head = el("div", "code-head");
    head.appendChild(el("span", "code-lang", name || "代码"));
    const copy = el("button", "code-copy", "复制");
    copy.type = "button";
    copy.title = "复制代码";
    copy.addEventListener("click", () => presentationCopy(code.textContent, copy));
    head.appendChild(copy);
    pre.replaceWith(block);
    block.appendChild(head);
    block.appendChild(pre);
  });
}

function presentationDecorate(root, workspace) {
  root.querySelectorAll("a[data-file]").forEach((link) => {
    const path = link.dataset.file;
    const line = Number(link.dataset.line) || 0;
    const endLine = Number(link.dataset.endLine) || line;
    link.setAttribute("href", "#");
    link.classList.add("file-link");
    link.title = "预览 " + path + (line ? ":" + line : "");
    link.addEventListener("click", (event) => {
      event.preventDefault();
      presentationOpenFile(path, workspace, line, endLine);
    });
  });
  root.querySelectorAll("a[href]").forEach((link) => {
    const href = String(link.getAttribute("href") || "");
    if (/^(https?:|mailto:|tel:)/i.test(href) || href.indexOf("//") === 0) {
      presentationExternal(link);
    } else if (/^(javascript|vbscript|data):/i.test(href)) {
      link.removeAttribute("href");
    }
  });
  root.querySelectorAll("img[data-file]").forEach((image) => {
    image.src = presentationFileUrl(image.dataset.file, workspace);
    image.classList.add("markdown-image");
  });
  presentationDecorateCode(root);
}

function renderContent(text, workspace) {
  const root = el("div", "markdown");
  const html = marked.parse(String(text || ""), {
    gfm: true,
    breaks: true,
    renderer: presentationMarkdownRenderer(),
  });
  root.innerHTML = DOMPurify.sanitize(html, {
    ADD_ATTR: ["target", "rel", "data-file", "data-line", "data-end-line"],
    FORBID_TAGS: ["style", "iframe", "object", "embed", "form", "input", "button",
      "textarea", "select", "link", "meta", "base"],
    FORBID_ATTR: ["style"],
  });
  presentationDecorate(root, presentationWorkspace(workspace));
  return root;
}

function presentationCopy(value, button) {
  const previous = button.textContent;
  const show = (label, failed) => {
    button.textContent = label;
    if (failed) button.classList.add("copy-failed");
    setTimeout(() => {
      button.textContent = previous;
      button.classList.remove("copy-failed");
    }, 1200);
  };
  navigator.clipboard.writeText(String(value)).then(
    () => show("已复制", false),
    () => show("复制失败", true),
  );
}

let presentationPreview = null;

function presentationPreviewUi() {
  if (presentationPreview) return presentationPreview;
  const dialog = el("dialog", "file-preview");
  dialog.setAttribute("aria-label", "文件预览");
  const head = el("div", "file-preview-head");
  const title = el("strong", "file-preview-title", "文件预览");
  head.appendChild(title);
  const close = el("button", "file-preview-close", "×");
  close.type = "button";
  close.title = "关闭预览";
  close.setAttribute("aria-label", "关闭预览");
  head.appendChild(close);
  const meta = el("div", "file-preview-meta");
  const body = el("div", "file-preview-body");
  dialog.appendChild(head);
  dialog.appendChild(meta);
  dialog.appendChild(body);
  document.body.appendChild(dialog);
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    if (presentationPreview && presentationPreview.url) {
      URL.revokeObjectURL(presentationPreview.url);
      presentationPreview.url = "";
    }
    body.replaceChildren();
  });
  presentationPreview = { dialog, title, meta, body, url: "" };
  return presentationPreview;
}

function presentationBaseName(path) {
  const parts = String(path || "").split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : String(path || "文件");
}

function presentationShowText(ui, value, line, endLine) {
  const text = value.text || "";
  if (value.truncated) ui.meta.textContent += " · 内容已截断";
  if (!text) {
    ui.body.replaceChildren(el("div", "file-preview-empty", "空文件"));
    return;
  }
  const pre = el("pre", "file-preview-text");
  text.split("\n").forEach((content, index) => {
    const row = el("span", "file-line", content || " ");
    const numbered = content.match(/^(\d+):/);
    const number = numbered ? Number(numbered[1]) : index + 1;
    row.dataset.line = String(number);
    if (line && number >= line && number <= (endLine || line)) row.classList.add("target");
    pre.appendChild(row);
  });
  ui.body.replaceChildren(pre);
  const target = pre.querySelector(".file-line.target");
  if (target) setTimeout(() => target.scrollIntoView({ block: "center" }), 0);
}

async function presentationOpenFile(path, workspace, line, endLine) {
  const ui = presentationPreviewUi();
  ui.title.textContent = presentationBaseName(path);
  ui.meta.textContent = path + (line ? " · 第 " + line + " 行" : "");
  ui.body.replaceChildren(el("div", "file-preview-loading", "正在读取…"));
  if (!ui.dialog.open) ui.dialog.showModal();
  try {
    const response = await fetch(presentationFileUrl(path, workspace, line));
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(payload.error || response.statusText || "读取失败");
    }
    const type = String(response.headers.get("Content-Type") || "").toLowerCase();
    if (type.indexOf("image/") === 0) {
      const objectUrl = URL.createObjectURL(await response.blob());
      if (ui.url) URL.revokeObjectURL(ui.url);
      ui.url = objectUrl;
      const image = el("img", "file-preview-image");
      image.src = objectUrl;
      image.alt = path;
      ui.body.replaceChildren(image);
      return;
    }
    presentationShowText(ui, await response.json(), line, endLine);
  } catch (error) {
    ui.body.replaceChildren(el("div", "file-preview-error", error.message || "读取失败"));
  }
}

function presentationToolStatus(item) {
  if (item.live) return "running";
  if (item.ok === false || item.error) return "error";
  return "done";
}

function presentationToolLabel(item, args) {
  const name = String(item.name || "tool");
  if (args.command) return String(args.command);
  if (args.path && /read/.test(name)) return "读取 " + args.path;
  if (args.path && /write/.test(name)) return "写入 " + args.path;
  if (args.path && /(edit|patch)/.test(name)) return "修改 " + args.path;
  if (args.path) return String(args.path);
  for (const key of ["pattern", "query", "url", "title", "goal", "name"]) {
    if (args[key]) return String(args[key]);
  }
  return "";
}

function presentationFirstLine(value) {
  return String(value || "").split(/\r?\n/).find((line) => line.trim()) || "";
}

function toolNode(item) {
  const status = presentationToolStatus(item);
  const args = item.arguments || {};
  const output = String(item.text || "");
  const details = el("details", "step tool-step " + status);
  const summary = el("summary", "tool-summary " + status);
  summary.appendChild(el("span", "sicon",
    status === "error" ? "✕" : status === "running" ? "◌" : "✓"));
  summary.appendChild(el("span", "sname", String(item.name || "tool")));
  const label = presentationToolLabel(item, args);
  if (label) summary.appendChild(el("span", "sargs", presentationTruncate(label, 90)));
  const preview = status === "running" ? "正在执行…"
    : status === "error" ? presentationFirstLine(item.error || output) || "执行失败"
    : presentationFirstLine(output) || "已完成";
  summary.appendChild(el("span", "spreview", presentationTruncate(preview, 80)));
  summary.appendChild(el("span",
    "sstate " + (status === "error" ? "bad" : status === "running" ? "running" : "good"),
    status === "error" ? "失败" : status === "running" ? "进行中" : "完成"));
  details.appendChild(summary);

  const body = el("div", "sbody");
  if (item.error) body.appendChild(el("div", "tool-error", String(item.error)));
  if (output) {
    const head = el("div", "tool-output-head");
    head.appendChild(el("span", "tool-output-label", "输出"));
    const copy = el("button", "tool-copy", "复制");
    copy.type = "button";
    copy.title = "复制输出";
    copy.addEventListener("click", () => presentationCopy(output, copy));
    head.appendChild(copy);
    body.appendChild(head);
    body.appendChild(el("pre", "tool-output", output));
  } else {
    body.appendChild(el("div", "tool-empty",
      status === "running" ? "正在执行，尚无输出。" : "没有输出。"));
  }
  if (Object.keys(args).length) {
    const box = el("details", "tool-args");
    box.appendChild(el("summary", "", "参数"));
    box.appendChild(el("pre", "tool-args-text", JSON.stringify(args, null, 2)));
    body.appendChild(box);
  }
  details.appendChild(body);
  return details;
}
