/* Host SDK management uses the existing sheets, controls and spring animation. */
'use strict';
(function(N){
 const {$,esc}=N;let owner=null;
 async function request(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'操作失败');return data;}
 function open(id){N.openDialog($(id));}
 async function runtime(){
  $('#host-runtime-error').textContent='';
  try{const r=await request(`/api/plugins/${encodeURIComponent(owner)}/runtime`);const text=v=>esc(JSON.stringify(v,null,2));
   $('#host-runtime-body').innerHTML=`<div class="host-runtime-sections"><section><h3>宿主能力</h3><p class="help">${Object.entries(r.capabilities).map(([k,v])=>`${esc(k)} v${v}`).join(' · ')}</p><p class="help">必要依赖：${Object.entries(r.requires_host).map(([k,v])=>`${esc(k)} v${v}（${r.capabilities[k]===v?'已满足':'未满足'}）`).join(' · ')||'未声明'}</p></section><section><h3>公开服务</h3><pre class="host-runtime-json">${text(r.services)}</pre></section><section><h3>插件作业</h3>${r.jobs.length?r.jobs.map(j=>`<div class="host-runtime-row"><b>${esc(j.name)}</b><p class="help">${esc(j.id)} · ${esc(j.status)}</p>${j.error?`<p class="sheet-error">${esc(j.error)}</p>`:''}<p class="help">进度：${text(j.progress)}</p>${['queued','running','recovering'].includes(j.status)?`<button class="btn ghost sm" data-host-method="jobs.cancel" data-resource="${esc(j.id)}">取消</button>`:''}${['failed','paused','interrupted'].includes(j.status)?`<button class="btn ghost sm" data-host-method="jobs.retry" data-resource="${esc(j.id)}">重试</button>`:''}</div>`).join(''):'<p class="help">暂无作业。</p>'}</section><section><h3>独立对话</h3>${r.dialogues.length?r.dialogues.map(d=>`<div class="host-runtime-row"><b>${esc(d.title||d.id)}</b><p class="help">${esc(d.id)} · ${esc(d.status)}</p>${d.error?`<p class="sheet-error">${esc(d.error)}</p>`:''}<button class="btn ghost sm" data-host-method="dialogues.history" data-resource="${esc(d.id)}">查看历史</button>${d.status==='generating'?`<button class="btn ghost sm" data-host-method="dialogues.cancel" data-resource="${esc(d.id)}">取消生成</button>`:''}</div>`).join(''):'<p class="help">暂无独立对话。</p>'}</section><section><h3>最近调用</h3><pre class="host-runtime-json">${text(r.operations)}</pre></section></div>`;
  }catch(e){$('#host-runtime-error').textContent=e.message;}
 }
 document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;const act=b.dataset.act;
  if(!['host-runtime','host-runtime-refresh'].includes(act)&&!b.dataset.hostMethod)return;
  try{
   if(act==='host-runtime'){owner=b.dataset.id;$('#host-runtime-body').textContent='正在读取…';open('#host-runtime-dialog');await runtime();}
   if(act==='host-runtime-refresh')await runtime();
   if(b.dataset.hostMethod){b.disabled=true;const method=b.dataset.hostMethod;const params=method.startsWith('jobs.')?{job_id:b.dataset.resource}:{dialogue_id:b.dataset.resource};const r=await request(`/api/plugins/${encodeURIComponent(owner)}/runtime`,{method,params});if(method==='dialogues.history'){const box=document.createElement('pre');box.className='host-runtime-json';box.textContent=JSON.stringify(r,null,2);b.parentElement.append(box);}else await runtime();}
  }catch(error){$('#host-runtime-error').textContent=error.message;}finally{b.disabled=false;}
 });
})(window.N);
