'use strict';
const $ = id => document.getElementById(id);
const stateNames = {pending:'等待预审',reviewing:'正在预审',held:'待处理',blocked:'已拦截',allowed:'等待转发',delivered:'已转发',ignored:'已忽略'};
const connectionNames = {online:'QQ 已连接',connecting:'正在连接 QQ',unconfigured:'请填写机器人凭据',disconnected:'QQ 未连接',error:'QQ 连接异常'};
let snapshot = null, dirty = false;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function notice(text, error = false) {
  $('notice').textContent = text;
  $('notice').classList.toggle('error', error);
}
async function call(action, values = {}) {
  const response = await fetch('/api/plugins/qq-bridge/call', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,values})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `请求失败：${response.status}`);
  return result;
}
async function run(button, action) {
  button.disabled = true;
  try { await action(); } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
}
function fillSettings() {
  const config = snapshot.settings;
  for (const key of ['app_id','review_url','review_model','risk_threshold','violation_threshold','speech_url','speech_model','group_mode']) {
    $(key.replaceAll('_','-')).value = config[key];
  }
  $('connected').checked = config.connected;
  $('groups').value = config.groups.join('\n');
  for (const key of ['app-secret','review-key','speech-key']) $(key).value = '';
  dirty = false;
  $('dirty-status').textContent = '';
}
function identityList(key) {
  const target = $(key);
  target.replaceChildren();
  if (!snapshot.settings[key].length) target.append(node('p','尚未绑定。从收件箱核对消息后添加。','empty'));
  for (const identity of snapshot.settings[key]) {
    const row = node('div',undefined,'identity');
    row.append(node('span',`${identity.kind === 'c2c' ? '单聊' : '群聊'} · ${identity.id}${identity.group_id ? ` · 群 ${identity.group_id}` : ''}`));
    const button = node('button','移除','btn quiet'); button.type = 'button';
    button.addEventListener('click', () => run(button,async () => {
      snapshot = await call('remove_identity',{list:key,identity}); render(); notice('已移除此身份。');
    }));
    row.append(button); target.append(row);
  }
}
function actionButton(label, action, values) {
  const button = node('button',label,'btn'); button.type = 'button';
  button.addEventListener('click', () => run(button,async () => {
    snapshot = await call(action,values);
    if (!dirty) fillSettings();
    render(); notice('操作已保存。重新处理消息时会应用当前身份和群设置。');
  }));
  return button;
}
function renderMessages() {
  const target = $('messages'), filter = $('filter').value;
  target.replaceChildren();
  const messages = snapshot.messages.filter(item => filter === 'all' || item.status === filter);
  if (!messages.length) target.append(node('p',filter === 'all' ? '还没有收到消息。连接机器人后，从 QQ 发一条消息开始。' : '没有符合此状态的消息。','empty'));
  for (const item of messages) {
    const envelope = item.envelope, verdict = item.review;
    const row = node('article',undefined,'receipt');
    const header = node('div',undefined,'receipt-head');
    header.append(node('strong',`${envelope.sender.name || envelope.sender.id} · ${envelope.conversation.kind === 'group' ? '群聊' : 'QQ 单聊'}`));
    const risk = verdict.status === 'exempt' ? '白名单免审' : typeof verdict.risk_percent === 'number' ? `攻击风险 ${verdict.risk_percent.toFixed(1)}%` : '尚无判断结果';
    header.append(node('span',risk,`risk ${item.status === 'blocked' ? 'blocked' : ''}`));
    row.append(header,node('p',`${stateNames[item.status] || item.status} · ${new Date(item.received_at).toLocaleString()}${verdict.owner ? ' · 已绑定本人' : ''}`,'receipt-meta'));
    row.append(node('p',envelope.text || (envelope.attachments.length ? `附件消息 · ${envelope.attachments.length} 个附件` : '引用或卡片消息'),'receipt-body'));
    if (item.error) row.append(node('p',item.error,'receipt-meta'));
    if (verdict.violation_categories?.length) row.append(node('p',`命中违规分类：${verdict.violation_categories.join('、')}`,'receipt-meta'));
    const details = node('details'), summary = node('summary','查看来源、预审与附件信息');
    details.append(summary,node('pre',JSON.stringify({receipt_id:item.id,sender:envelope.sender,conversation:envelope.conversation,review:verdict,prepared:item.prepared,quoted:envelope.quoted,card:envelope.card},null,2)));
    row.append(details);
    const actions = node('div',undefined,'receipt-actions');
    if (['held','blocked','ignored'].includes(item.status)) actions.append(actionButton('重新处理','retry',{id:item.id}));
    if (!envelope.sender.bot) {
      actions.append(actionButton('设为本人','bind',{id:item.id,list:'owners'}),actionButton('加入免审白名单','bind',{id:item.id,list:'whitelist'}));
    }
    if (envelope.conversation.kind === 'group' && !snapshot.settings.groups.includes(envelope.conversation.id)) actions.append(actionButton('启用此群','enable_group',{id:item.id}));
    row.append(actions); target.append(row);
  }
}
function render() {
  $('connection').textContent = connectionNames[snapshot.connection] || snapshot.connection;
  $('connection-detail').textContent = snapshot.error || '文字、图片、语音与文件输入 · 文字和图片回复';
  for (const [field,key] of [['app-secret-status','app_secret'],['review-key-status','review_key'],['speech-key-status','speech_key']]) {
    $(field).textContent = snapshot.credentials[key] ? '密钥已保存，页面不显示原值。' : '尚未保存密钥。';
  }
  identityList('owners'); identityList('whitelist'); renderMessages();
}
async function refresh() {
  snapshot = await call('snapshot');
  if (!dirty) fillSettings();
  render();
}
$('refresh').addEventListener('click',event => run(event.currentTarget,refresh));
$('filter').addEventListener('change',() => { if (snapshot) renderMessages(); });
for (const name of ['inbox','settings']) $(name+'-tab').addEventListener('click',() => {
  for (const current of ['inbox','settings']) {
    $(current+'-panel').hidden = current !== name;
    $(current+'-tab').setAttribute('aria-pressed',String(current === name));
  }
});
$('settings-form').addEventListener('input',() => {dirty = true; $('dirty-status').textContent = '有尚未保存的设置';});
$('settings-form').addEventListener('submit',event => {
  event.preventDefault();
  if (!snapshot) return;
  run($('save'),async () => {
    const config = {...snapshot.settings};
    for (const key of ['app_id','review_url','review_model','speech_url','speech_model','group_mode']) config[key] = $(key.replaceAll('_','-')).value.trim();
    for (const key of ['risk_threshold','violation_threshold']) config[key] = Number($(key.replaceAll('_','-')).value);
    config.connected = $('connected').checked;
    config.groups = $('groups').value.split(/\s+/).filter(Boolean);
    const credentials = {};
    for (const key of ['app_secret','review_key','speech_key']) credentials[key] = $(key.replaceAll('_','-')).value;
    snapshot = await call('configure',{settings:config,credentials}); fillSettings(); render(); notice('设置已保存。');
  });
});
for (const button of document.querySelectorAll('[data-clear]')) button.addEventListener('click',() => run(button,async () => {
  snapshot = await call('clear_credential',{key:button.dataset.clear}); render(); notice('已清除保存的密钥。');
}));
refresh().catch(error => notice(error.message,true));
