"use strict";

let editResolve = null;
let editSaving = false;

function editText({title, text = "", description = "保存后在后续请求中生效。", submitLabel = "保存修改", confirm = false, save}) {
  if (editResolve) return Promise.resolve(null);
  $("edit-title").textContent = title;
  $("edit-description").textContent = description;
  $("edit-input").value = text;
  $("edit-input").disabled = confirm;
  $("edit-label").hidden = confirm;
  $("edit-error").textContent = "";
  $("edit-submit").textContent = submitLabel;
  $("edit-dialog").showModal();
  (confirm ? $("edit-cancel") : $("edit-input")).focus();
  return new Promise(resolve => {
    editResolve = resolve;
    $("edit-form").onsubmit = async event => {
      event.preventDefault();
      if (editSaving) return;
      const value = confirm ? true : $("edit-input").value;
      if (!confirm && !value.trim()) return;
      editSaving = true;
      $("edit-submit").disabled = true;
      $("edit-cancel").disabled = $("edit-close").disabled = true;
      try {
        if (save) await save(value);
        finishEdit(value);
      } catch (error) {
        $("edit-error").textContent = error.message;
      } finally {
        editSaving = false;
        $("edit-submit").disabled = false;
        $("edit-cancel").disabled = $("edit-close").disabled = false;
      }
    };
  });
}

function finishEdit(value = null) {
  const resolve = editResolve;
  editResolve = null;
  $("edit-dialog").close();
  if (resolve) resolve(value);
}

function confirmEdit(options) {return editText({...options, confirm: true}).then(Boolean);}
$("edit-cancel").onclick = $("edit-close").onclick = () => {if (!editSaving) finishEdit();};
$("edit-dialog").addEventListener("cancel", event => {
  event.preventDefault();
  if (!editSaving) finishEdit();
});

async function messageOperation(item, operation) {
  const version = lingView.historyVersion;
  if (!version) return setStatus("会话尚未载入，请稍后重试。", true);
  const later = lingView.items.filter(value => value.index > item.index && ["user", "assistant"].includes(value.role)).length;
  await editText({
    title: operation === "resend" ? "编辑并重新发送" : "删除这条消息？",
    text: item.text || "",
    confirm: operation === "delete",
    submitLabel: operation === "resend" ? "重新发送" : "删除消息",
    description: operation === "resend"
      ? "从这条消息重新继续，将替换后续 " + later + " 条交流消息。保留本条附件与工作归属；已执行的工具和后台工作不会回滚。"
      : "删除此条记录及与它关联的工具调用记录。已执行的工作不会回滚，可在会话继续前撤销。",
    save: async text => {
      await post("/api/ling/history", {operation, indices: [item.index], text,
        version, request_id: lingRequestId("edit")});
      $("history-undo").hidden = false;
      await pollLing();
      setStatus(operation === "resend" ? "修改后的消息已发送" : "消息已删除");
    },
  });
}

function messageOperations(item) {
  const actions = el("div", "message-operations");
  if (!["user", "assistant"].includes(item.role)) return actions;
  const copy = el("button", "message-operation", "复制");
  copy.type = "button";
  copy.onclick = async () => {
    try {await navigator.clipboard.writeText(item.text || ""); setStatus("消息已复制");}
    catch {setStatus("复制失败，请选择文字后手动复制。", true);}
  };
  actions.appendChild(copy);
  if (item.compressed) {
    actions.appendChild(el("span", "small", "已压缩 · 在上下文管理中修改摘要"));
    return actions;
  }
  if (!Number.isInteger(item.index) || item.live) return actions;
  const add = (label, operation) => {
    const button = el("button", "message-operation" + (operation === "delete" ? " danger" : ""), label);
    button.type = "button";
    button.onclick = () => messageOperation(item, operation).catch(error => setStatus(error.message, true));
    actions.appendChild(button);
  };
  if (item.role === "user") add("编辑并重新发送", "resend");
  add("删除", "delete");
  return actions;
}

$("history-undo-button").onclick = async () => {
  try {
    await post("/api/ling/history", {operation: "undo", version: lingView.historyVersion});
    $("history-undo").hidden = true;
    await pollLing();
    setStatus("已撤销历史修改");
  } catch (error) {setStatus(error.message, true);}
};
