'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let offset=0,total=0,results=[],editing=null,searching=false;
async function api(action,values={}){
  const r=await fetch('/api/plugins/memory/call',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,values})});
  const data=await r.json();if(!r.ok)throw Error(data.error||'操作失败');return data;
}
function notice(text,error=false){const el=$('#notice');el.textContent=text;el.className='message '+(error?'error ':'')+'show';}
async function guarded(button,fn){button.disabled=true;try{await fn();}catch(e){notice(e.message,true);}finally{button.disabled=false;if(button.id==='prev')button.disabled=searching||offset===0;if(button.id==='next')button.disabled=searching||offset+30>=total;}}
function openDialog(id){const d=$(id);d.showModal();const n=window.parent!==window?window.parent.N:null;if(n)n.anim(d,[{opacity:0,transform:'translateY(24px) scale(.92)'},{opacity:1,transform:'none'}],{duration:n.SP.settle.d,easing:n.SP.settle.e});}
let resolveConfirm=null;
function confirmAction(text){$('#confirmation-text').textContent=text;openDialog('#confirmation');return new Promise(resolve=>{resolveConfirm=resolve;});}
$('#confirm-yes').onclick=()=>finishConfirm(true);$('#confirm-no').onclick=()=>finishConfirm(false);
function finishConfirm(value){const resolve=resolveConfirm;resolveConfirm=null;closeDialog('confirmation');resolve?.(value);}
function closeDialog(id){const d=$('#'+id);const n=window.parent!==window?window.parent.N:null;if(n?.closeDialog)n.closeDialog(d);else d.close();}
const labels={pending:'待整理',selected:'待提炼',processing:'处理中',failed:'失败',done:'已完成',ready:'已就绪'};
async function status(){const s=await api('status');$('#summary').textContent=`粒子 ${s.particles} 条 · ${s.running?'正在处理':'空闲'} · 向量 ${Object.entries(s.embeddings).map(([k,v])=>(labels[k]||k)+' '+v).join(' / ')||'暂无'} · 基线 ${s.baseline_at||'未建立'}`;
  $('#batches').innerHTML=s.batches.length?s.batches.map(b=>`<div class="batch"><b>${esc(labels[b.status]||b.status)}</b><span class="meta"> · ${esc(b.created_at)} · ${esc(b.id)}</span>${b.error?`<p class="error-text">${esc(b.error)}</p>`:''}${b.status==='failed'?`<button class="btn ghost" data-retry="${esc(b.id)}">重试</button>`:''}</div>`).join(''):'<p class="empty">暂无待处理记录。</p>';
  if(s.error||s.bridge_error)notice(s.error||s.bridge_error,true);
}
function renderParticles(data){results=data.particles;total=data.total;$('#count').textContent=`${total} 条${searching?'匹配':''}`;
 $('#particles').innerHTML=results.length?results.map((p,i)=>`<article class="particle"><p>${esc(p.content)}</p><div class="meta">${esc(p.id)} · ${esc(p.source.label)} · ${esc(p.role)} · ${p.manually_edited?'人工修订 · ':''}向量${esc(labels[p.embedding_status]||p.embedding_status)}${p.similarity===undefined?'':` · 相似度 ${p.similarity.toFixed(3)}`}</div><div class="meta">事件时间：${esc(p.event_time?`${p.event_time.start} ～ ${p.event_time.end}`:'未知或不适用')} · 来源时间：${esc(p.provenance.occurred_at||'未知')}</div>${p.embedding_error?`<p class="error-text">${esc(p.embedding_error)}</p>`:''}<div class="actions"><button class="btn ghost" data-edit="${i}">编辑纠错</button><button class="btn ghost" data-expand="${i}">展开原文</button><button class="btn ghost" data-delete="${i}">删除</button></div></article>`).join(''):'<p class="empty">没有找到记忆。可以换一种问法，或切换精确检索。</p>';
 $('#prev').disabled=searching||offset===0;$('#next').disabled=searching||offset+30>=total;$('#page').textContent=searching?'检索结果':`${total?offset+1:0}–${Math.min(offset+30,total)} / ${total}`;
}
async function list(){searching=false;renderParticles(await api('list',{offset,limit:30}));}
async function search(){const query=$('#query').value.trim();if(!query){offset=0;return list();}searching=true;renderParticles(await api('search',{query,mode:$('#mode').value,limit:Number($('#limit').value)}));}
const fields=[
 ['llm_url','提炼 API 地址','url'],['llm_model','提炼模型','text'],['llm_key','提炼 API 密钥','password'],
 ['embedding_url','嵌入 API 地址','url'],['embedding_model','嵌入模型','text'],['embedding_key','嵌入 API 密钥','password'],
 ['timezone','时区（例如 Asia/Shanghai）','text'],['timezone_confirmed','确认使用此时区','checkbox'],
 ['capture_compression','压缩后自动采集','checkbox'],['capture_mode','定时采集',['interval','daily','off']],['capture_interval_minutes','采集间隔（分钟）','number'],['capture_daily','每天采集时间','time'],
 ['process_mode','处理方式',['arrival','interval','daily','off']],['process_interval_minutes','处理间隔（分钟）','number'],['process_daily','每天处理时间','time'],
 ['batch_chars','单批输入字符上限','number'],['passive_enabled','启用被动注入','checkbox'],['passive_limit','被动注入最多粒子数','number'],['passive_chars','被动注入字符预算','number'],['similarity_threshold','语义检索相似度门槛','number']];
const modeLabel={interval:'间隔执行',daily:'每天定时',off:'关闭',arrival:'新快照到达后处理'};
async function settings(){const s=await api('settings.get');$('#fields').innerHTML=fields.map(([key,label,type])=>{
 if(type==='checkbox')return `<label class="check"><input name="${key}" type="checkbox" ${s[key]?'checked':''}>${label}</label>`;
 if(Array.isArray(type))return `<label class="field">${label}<select name="${key}">${type.map(v=>`<option value="${v}" ${s[key]===v?'selected':''}>${modeLabel[v]}</option>`).join('')}</select></label>`;
 const secret=type==='password';return `<label class="field">${label}<input name="${key}" type="${type}" ${type==='number'?`min="${key==='similarity_threshold'?0:1}" ${key==='similarity_threshold'?'step="0.01" max="1"':''}`:''} value="${secret?'':esc(s[key])}" ${secret?'autocomplete="new-password"':''} placeholder="${secret?(s['has_'+key]?'已配置，留空不变':'尚未配置'):''}">${secret?`<span class="check"><input type="checkbox" name="clear_${key}">清除此密钥</span>`:''}</label>`;}).join('');
 if(!s.timezone_confirmed){$('#settings').elements.timezone.value=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';}
}
$('#settings').onsubmit=e=>{e.preventDefault();guarded(e.submitter,async()=>{const form=e.currentTarget,values={};for(const [key,,type]of fields){const input=form.elements[key];if(type==='password'){if(form.elements['clear_'+key].checked)values[key]='';else if(input.value)values[key]=input.value;}else values[key]=type==='checkbox'?input.checked:type==='number'?Number(input.value):input.value;}await api('settings.save',values);await settings();notice('配置已保存');await status();});};
$('#search').onsubmit=e=>{e.preventDefault();guarded(e.submitter,search);};
$('#browse').onclick=e=>guarded(e.currentTarget,async()=>{offset=0;await list();});
$('#prev').onclick=e=>guarded(e.currentTarget,async()=>{offset=Math.max(0,offset-30);await list();});
$('#next').onclick=e=>guarded(e.currentTarget,async()=>{offset+=30;await list();});
$('#refresh').onclick=e=>guarded(e.currentTarget,status);
$('#reload-settings').onclick=e=>guarded(e.currentTarget,settings);
$('#reindex').onclick=e=>guarded(e.currentTarget,async()=>{if(await confirmAction('重建全部向量索引？完成前语义检索可能不可用。')){const r=await api('reindex');notice(r.started?'已开始重建':'已有任务运行，请稍后再试');await status();}});
$('#cleanup').onclick=e=>guarded(e.currentTarget,async()=>{const r=await api('cleanup');if(await confirmAction(`清理 ${r.unreferenced_originals} 份无引用原文？此操作无法撤销。`)){await api('cleanup',{execute:true});notice('原文清理完成');}});
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.close){closeDialog(b.dataset.close);return;}if(b.dataset.action)return guarded(b,async()=>{const r=await api(b.dataset.action);notice(b.dataset.action==='collect'?`已采集 ${r.captured} 条记录`:r.started?'已安排处理':'已有任务运行或尚未开始');await status();});if(b.dataset.retry)return guarded(b,async()=>{await api('retry',{batch_id:b.dataset.retry});await status();});
 if(b.dataset.edit!==undefined){editing=results[Number(b.dataset.edit)];$('#edit-content').value=editing.content;$('#edit-start').value=editing.event_time?.start||'';$('#edit-end').value=editing.event_time?.end||'';$('#edit-error').className='message error';openDialog('#editor');}
 if(b.dataset.expand!==undefined)return guarded(b,async()=>{const r=await api('expand',{particle_id:results[Number(b.dataset.expand)].id});$('#original-body').innerHTML=r.segments.length?r.segments.map(s=>`<article class="particle"><p class="meta">${esc(s.kind)} · ${esc(s.name||'')} · ${esc(s.at||'')}</p><div class="original-text">${esc(s.text)}</div>${s.tool_calls?`<pre class="original-text">${esc(JSON.stringify(s.tool_calls,null,2))}</pre>`:''}</article>`).join(''):'原文不可用';openDialog('#original');});
 if(b.dataset.delete!==undefined)return guarded(b,async()=>{if(await confirmAction('删除这条粒子？原文不会连带清理。')){await api('delete',{particle_id:results[Number(b.dataset.delete)].id});await(searching?search():list());await status();}});
});
$('#edit-form').onsubmit=e=>{e.preventDefault();guarded(e.submitter,async()=>{const start=$('#edit-start').value,end=$('#edit-end').value;try{if(Boolean(start)!==Boolean(end))throw Error('请填写完整时间范围，或同时留空');const updated=await api('edit',{particle_id:editing.id,content:$('#edit-content').value,event_time:start?{start,end}:null});results=results.map(p=>p.id===updated.id?updated:p);renderParticles({particles:results,total});closeDialog('editor');notice('修订已保存，向量将重新生成');await status();}catch(error){$('#edit-error').textContent=error.message;$('#edit-error').className='message error show';}});};
for(const d of document.querySelectorAll('dialog')){d.addEventListener('cancel',e=>{e.preventDefault();if(d.id==='confirmation')finishConfirm(false);else closeDialog(d.id);});d.addEventListener('click',e=>{if(e.target===d){if(d.id==='confirmation')finishConfirm(false);else closeDialog(d.id);}});}
Promise.all([status(),settings(),list()]).catch(e=>notice(e.message,true));
setInterval(()=>{if(!document.hidden)status().catch(e=>notice(e.message,true));},5000);
